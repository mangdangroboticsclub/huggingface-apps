# Mini Pupper Dance Machine — Developer Guide

Build your own dancing robot. This guide covers architecture, how to extend the system, and how to replicate the demo.

---

## System Architecture

```
User Speech
    │
    ▼
┌─────────────────────────┐
│   LLM (Gemini/Ollama)   │  — Detects dance intent, writes task JSON
└─────────┬───────────────┘
          │ file-based task (tasks.json)
          ▼
┌─────────────────────────┐
│  Gateway Agent (cron)   │  — Isolated agentTurn processes the task
└─────────┬───────────────┘
          │ exec (node)
          ▼
┌─────────────────────────────────────────────────────┐
│  hf_dance_to_audio.py                               │
│                                                     │
│  1. yt-dlp download MP3                             │
│  2. ffmpeg → WAV                                    │
│  3. Upload WAV → HF Space (librosa beat analysis)   │
│  4. Genre detection (YouTube metadata)               │
│  5. local_choreography → seed RNG pick moves         │
│  6. Background subprocess:                           │
│     ├─ activate robot (UDP joystick)                 │
│     ├─ ffplay audio (ALSA)                           │
│     ├─ dance_face (LCD face sync)                    │
│     └─ choreography loop (Movement API)              │
└─────────────────────────────────────────────────────┘
```

---

## File Map

All files live in `~/minipupper-app/custom/hf_dance/`.

| File | Role | Key Classes / Functions |
|------|------|------------------------|
| `hf_dance_to_audio.py` | CLI entry point. 6 subcommands. Full pipeline orchestration | `cmd_dance()`, `cmd_execute()`, `_choreography_loop()`, `_hf_detect_beats()`, `_detect_genre_from_url()`, `_audio_monitor` |
| `local_choreography.py` | Genre-aware move selection + compound expansion | `enrich_choreography()`, `_expand_compounds()`, `_map_angle()`, `_resolve_genre()`, `GENRE_POOLS`, `COMPOUND_EXPANSIONS`, `ANGLE_MOVES`, `GENRE_ALIASES` |
| `dance_face.py` | LCD face display synced to choreography | `DanceFace` (class), `generate_face_cues()`, `face_cues_from_choreography()`, `tilt_display_poller()`, `TiltState` |
| `space_app.py` | **HF Space server** (deployed on Hugging Face, not on Pi) | `analyze_audio()` — librosa beat tracking, per-beat BPM, RMS energy envelope → beat_slots |
| `gif.py` | Utility — animated GIF player on LCD | `play()`, `stop()`, `show()` |
| `space_app.py` | **HF Space server** (deployed on Hugging Face, not on Pi) | `analyze_audio()` — librosa beat tracking, per-beat BPM, RMS energy envelope → beat_slots |
| `gif.py` | Utility — animated GIF player on LCD | `play()`, `stop()`, `show()` |
| `display/` | Single base face image for smooth tilt rotation | `dog_straight face-bgrmv.png` |

---

## Prerequisites

### Hardware
- **Mini Pupper v2** (Raspberry Pi 4 / CM4)
- Speaker connected (bcm2835 Headphones ALSA device)
- ST7789 LCD display

### Raspberry Pi Software
- Python 3.9+
- System services: `joystick.service`, `robot.service`, `web-controller.service`
- `minipupper-app` repo cloned at `/home/ubuntu/minipupper-app/`
- ALSA configured: `/etc/asound.conf` routes default → Headphones

### Python Dependencies (Pi side)
```
yt-dlp
requests
Pillow
numpy
```

### Python Dependencies (HF Space side)
```
gradio
librosa
numpy
```

### HF Space (Beat Analysis Backend)

The beat analysis runs on a Hugging Face Space — **not on the Pi**. You have two options:

**Option A — Use the existing public Space (no setup required):**

The code defaults to `Minipupper/Minipupper-Dance-Rhythm-Analysis`, a public Gradio Space. No token, no API key needed. The Pi communicates via a simple 3-step HTTP flow:

```python
import requests

# Step 1: Upload WAV file to Space
with open("/path/to/song.wav", "rb") as f:
    resp = requests.post(
        "https://Minipupper-Minipupper-Dance-Rhythm-Analysis.hf.space/gradio_api/upload",
        files={"files": ("audio.wav", f, "audio/wav")},
        timeout=120,
    )
uploaded_path = resp.json()[0]  # e.g., "/tmp/gradio/abc123.wav"

# Step 2: Trigger prediction (returns event_id for polling)
resp = requests.post(
    "https://Minipupper-Minipupper-Dance-Rhythm-Analysis.hf.space/gradio_api/call/predict",
    json={"data": [
        {"path": uploaded_path, "meta": {"_type": "gradio.FileData"}},
        180.0,  # song duration in seconds
    ]},
    timeout=120,
)
event_id = resp.json()["event_id"]

# Step 3: Poll for result via SSE stream
resp = requests.get(
    f"https://Minipupper-Minipupper-Dance-Rhythm-Analysis.hf.space/gradio_api/call/predict/{event_id}",
    headers={"Accept": "text/event-stream"},
    stream=True,
)
for line in resp.iter_lines(decode_unicode=True):
    if line and line.startswith("data:"):
        raw = line[5:].strip()
        if raw and raw != "null":
            result_json = raw  # ← beat_slots payload
            break
```

The Space is free to use and always accessible as long as it's running on Hugging Face's infrastructure.

**Option B — Deploy your own Space:**
1. Deploy `space_app.py` as a Gradio app to Hugging Face Spaces
2. Set the `HF_DANCE_SPACE` env var on the Pi to your Space name:
   ```bash
   export HF_DANCE_SPACE="YourUserName/YourSpaceName"
   ```
3. The same 3-step HTTP flow works identically — just the hostname changes.

### YouTube Authentication
yt-dlp needs cookies to download some YouTube videos:
```bash
yt-dlp --cookies-from-browser chromium  # or firefox
# Save cookies to ~/minipupper-app/custom/hf_dance/cookies.txt
```
Cookies expire and need periodic renewal.

---

## Component Deep-Dives

### 1. HF Space Beat Analysis (`space_app.py`)

**Runs on Hugging Face, not on the Pi.** It's a Gradio app with a single `/predict` endpoint.

**Algorithm:**
1. **Onset strength envelope** via `librosa.onset.onset_strength()`
2. **Dual-bias beat tracking** — runs `librosa.beat.beat_track()` at both 60 BPM and 120 BPM start biases, picks the winner by onset alignment score
3. **Per-beat BPM** — calculated from inter-beat-intervals (IBI), smoothed with a sliding window (max 3 or N/30)
4. **RMS energy envelope** — every beat gets a `time_acc` value based on RMS at that timestamp:
   - RMS > 0.3 → `time_acc = 0.25` (high energy = faster subsequent moves)
   - RMS ≤ 0.3 → `time_acc = 0.6` (low energy = slower moves = more idle time)
   - BPM > 109 → `time_acc` capped at 0.25 regardless
5. Every 2nd beat is emitted (no redundancy)

**Returns:** `{bpm, duration_sec, beat_slots: [{start_time, time_acc, local_bpm}]}`

### 2. Choreography Engine (`local_choreography.py`)

**Three-pass pipeline:**

- **Pass 1 — Genre replacement (1:1 slot mapping):** For each HF Space beat slot, uses a deterministic RNG (SHA-256 of song URL) to pick a move from the detected genre's pool, weighted by each move's probability. The angle from HF Space is mapped to the specific move type's expected parameter.

- **Pass 2 — Compound expansion:** Moves like `dip` and `spin` are replaced with multiple atomic sub-moves:
  - `dip` → lower-body + look-down + body-row + stop + look-up + body-row + raise-body + stop (8 sub-moves)
  - `spin` → rotate_cw 180° + stop + rotate_ccw 90° + stop (4 sub-moves)
  All subsequent entries are time-shifted right by the expansion length.

- **Pass 3 — Truncation:** Entries that overshoot the song-end ceiling are removed.

**Angle mapping (`_map_angle`):**
| Type | Scale | Bounds |
|------|-------|--------|
| Rotation | ×3 | 10–360° |
| Roll | 1:1 | 5–30° |
| Pitch | 1:1 | 5–30° |
| Yaw | 1:1 | 5–45° |
| Height | ×2 | 5–30 units |
| Strafe | 1:1 | 5–30 units |

### 3. Deterministic Seeding

Every song URL is hashed (SHA-256) to create a seed. This means:
- Same URL → **exact same dance** every time
- Useful for debugging, reproducible demos, A/B testing choreography changes
- Seed doesn't change across genre overrides

### 4. LCD Face Sync (`dance_face.py`)

Two strategies:
- **`generate_face_cues()`** — Legacy: generates timestamps based on BPM × genre pacing. Pacing = beats per face change (classical=8, rock=2). Good for steady-tempo songs.
- **face_cues_from_choreography()`** — New: samples every Nth entry from the actual choreography timetable. Naturally tracks variable tempo since it uses the actual move timeStamps.

**Lean genre special treatment:** `tilt_display_poller()` is a background thread that monitors the robot's roll angle via `TiltState` and rotates a single base image (`dog_straight face-bgrmv.png`) smoothly to match the body lean angle. Uses a pre-rendered oversized canvas so rotation never clips the viewport. Polls at ~12 Hz with zero file I/O per frame (direct SPI display).

**Face cycle:** REST (calm) → TROT (walking) → HOP (leaping) → FINISHHOP (landing), repeated.

### 5. Audio Monitor (`_audio_monitor`)

Dual-condition detection for when the song ends:
- **Condition 1:** ffplay process exits (`player.poll()`)
- **Condition 2:** ALSA CS,S no longer RUNNING (checked via `/proc/asound/card*/pcm*p/sub*/status`), with configurable debounce (default 2 consecutive misses)

Startup grace period of 5 seconds before ALSA monitoring begins (gives ffplay time to open the PCM device).

When either condition triggers: removes the active flag → `_choreography_loop` exits → robot deactivates.

---

## How to Add New Features

### Add a New Genre

1. Add a new entry to `GENRE_POOLS` in `local_choreography.py`:
```python
"mygenre": {
    "moves": ["move_a", "move_b", "move_c"],
    "weights": [0.4, 0.35, 0.25],
}
```
2. Add genre aliases to `GENRE_ALIASES` if needed
3. Add pacing to `GENRE_PACING` in `dance_face.py` (beats per face change)
4. Add genre display name to `GENRE_DISPLAY_NAMES` in `hf_dance_to_audio.py`
5. Add genre-specific `CHOREOGRAPHY_*` constant in `hf_dance_to_audio.py`
   → _Note: the `hf_dance_to_audio.py` choreography constants are **legacy** — the active path uses `local_choreography.py` pools._

### Add a New Atomic Move

Adding a new atomic move requires changes in **two layers**:

**Layer 1 — Robot-side implementation** in `~/minipupper-app/robot/robot_control.py`'s `_build_movement()` function (or directly in `StanfordQuadruped/src/MovementGroups.py` / `MovementScheme.py` for more complex moves).

Add an `elif` branch:
```python
elif command in ("my-move", "my_move"):
    move.head_move(pitch_deg=10, yaw_deg=0,
                   time_uni=duration, time_acc=time_acc)
```
The `time_acc` (transition time) and `time_uni` (hold time) parameters control execution timing — see compound moves below for the critical nuance.

**Layer 2 — Choreography routing** in `local_choreography.py`:
1. Register in `ANGLE_MOVES` so the angle mapper knows how to handle it:
```python
"my-move": {"type": "pitch", "default": 10},
```
2. Add to any genre pool's `moves` list with an appropriate weight.

**Important:** The move command string must match exactly between `_build_movement()` and `local_choreography.py`.

### Add a New Compound Move

Same two-layer approach as atomic moves:

**Layer 1 — Robot-side** in `robot_control.py`'s `_build_movement()`:
```python
elif command in ("my-combo", "my_combo"):
    move.head_move(pitch_deg=15, yaw_deg=0, time_uni=..., time_acc=...)
    move.body_row(row_deg=10, time_uni=..., time_acc=...)
    move.stop(time=0.2)
```

**Layer 2 — Choreography** in `local_choreography.py`, register in `COMPOUND_EXPANSIONS` for angle routing.

**⚠️ Critical timing nuance (`time_uni` vs `time_acc`):**

The `time_uni` (hold time) and `time_acc` (transition time) parameters control whether a compound spans multiple beats or fits within one:

| Pattern | `time_uni` | Effect |
|---------|-----------|--------|
| **Span N beats** | `time_uni = duration` (the slot's full duration) | Each sub-move gets one full beat. A 3-part compound takes 3 beats. |
| **Fit in 1 beat** | `time_uni = _sub_hold` where `_sub_hold = duration / n_subs` | All sub-moves packed into 1 beat, each getting 1/N of the beat. |

**Example — Span N beats (`disco2`):**
```python
move.head_move(pitch_deg=15, yaw_deg=20, time_uni=duration, time_acc=time_acc)
move.head_move(pitch_deg=-15, yaw_deg=-20, time_uni=duration, time_acc=time_acc)
```
Takes 2 beats total. Good for slow, dramatic compounds.

**Example — Fit in 1 beat (`headbang`):**
```python
_n_subs = 2
_sub_tic = max(time_acc / _n_subs, 0.015)
reps = max(1, int((time_acc + duration) / (time_acc * _n_subs)))
_sub_hold = duration / (reps * _n_subs)
for _ in range(reps):
    move.head_move(pitch_deg=15, yaw_deg=0, time_uni=_sub_hold, time_acc=_sub_tic)
    move.head_move(pitch_deg=-15, yaw_deg=0, time_uni=_sub_hold, time_acc=_sub_tic)
```
Both head bobs fit in 1 beat. `reps` lets you repeat the pattern within the slot.

**Note on `COMPOUND_EXPANSIONS` vs robot-side compounds:** `COMPOUND_EXPANSIONS` in `local_choreography.py` handles *which* atomic commands get called and their default angles — but the actual `time_uni`/`time_acc` logic lives in `robot_control.py`'s `_build_movement()`. Both files must be updated together for a new compound move to work correctly.

### Add LCD Face Art

1. Place `.webp` image in the `hf_dance/` directory
2. Use `_show_on_lcd()` for static display, or follow the `tilt_display_poller()` pattern for animated/polled display:
   - Pre-cache PIL `Image` objects at startup (resize, flip, paste on black bg)
   - Use `Display().disp.display(img)` directly (SPI, no file I/O)
   - Poll at desired rate (12 Hz in the lean example)

### Add a New CLI Subcommand

In `hf_dance_to_audio.py`:
1. Add a subparser via `argparse`
2. Implement a `cmd_*()` function that returns `{"ok": bool, ...}`
3. Wire into the `main()` dispatch block
4. If needed, add to `_CREATE_TASK_HANDLER` in `task_handler.py` (if integrating with the agent task system)

---

## Testing & Debugging

### Quick Commands
```bash
# Test genre detection only
python3 ~/minipupper-app/custom/hf_dance/hf_dance_to_audio.py classify "https://youtube.com/watch?v=..."

# Check dance process state
python3 ~/minipupper-app/custom/hf_dance/hf_dance_to_audio.py status

# Stop if stuck
python3 ~/minipupper-app/custom/hf_dance/hf_dance_to_audio.py stop
```

### Debug Logs & Files
| File | Contents |
|-------|----------|
| `/tmp/minipupper_dance_player.log` | Human-readable timeline of dance phases, HF response times, errors |
| `/tmp/minipupper_dance_state.json` | Full state snapshot during execution (file paths, genre, total moves, timestamps) |
| `/tmp/minipupper_dance_result.json` | Final result dict from last run |
| `/tmp/minipupper_dance_cache.json` | Cached HF Space beat data (avoid re-analysis on same song) |
| `/tmp/minipupper_dance_active` | Flag file — exists while dance is running |
| `/tmp/minipupper_dance_audio.pid` | PID of ffplay audio process |
| `/tmp/minipupper_dance_pid` | PID of the execute subprocess |

### HF Space Debugging
- Live logs: check your Space's dashboard on huggingface.co
- Test locally: `python3 space_app.py` runs the Gradio interface

---

## Known Issues & Race Conditions to Watch

| Issue | Cause | Workaround |
|-------|--------|------------|
| HF Space timeout on large files | Large WAV uploads can exceed Space's request timeout | Reduce `full_duration` cap, or use shorter audio clips |
| Audio card reordering | `bcm2835 Headphones` vs `simple-card` can flip on reboot | Use `audio_util.py` auto-detection (search by control name, not card number) |
| Dance stops early | ALSA during buffer underrun triggers false PCM idle detection | Increase `debounce` count in `_audio_monitor` |
| Two active flag files race | `cmd_stop()` and `_audio_monitor` can both try to remove flag | Use existence check + try/except on removal |
| yt-dlp fails | Expired cookies, region-blocked video | Refresh cookies.txt, or try a different video |
| Robot not moving | Joystick service not running, or robot not activated | `sudo systemctl status joystick.service` and `robot.service` |
| Announced flag race | TaskWatcher sets `announced` while agent writes result | Non-destructive archive pattern (archive, don't delete-while-writing) |

---

## Running on the Actual Robot

All scripts execute on the Pi via the Gateway agent:

```python
# In the agent, exec on the node:
exec(host="node", node="minipupperv2",
     command="python3 ~/minipupper-app/custom/hf_dance/hf_dance_to_audio.py dance <url>")
```

For app-triggered flows, the task system writes a JSON file to `|~/minipupper-app/tasks/pending/`, which the cron processor picks up and routes to `cmd_process_task()`.
e.g.
open 3 terminals:
1. terminal 1 to connect the pupper as a node
```python
export OPENCLAW_GATEWAY_TOKEN="0761ef748b8f0d837b871ec908b5d1f840c792040c8b9f36"
```
```python
export openclaw node run --host "instance-20260506-083731.tail2df607.ts.net" --port 443 --tls
```
2. terminal 2 to monitor the task progress
```python
watch -n 2 'cat ~/minipupper-app/tasks.json | tail -n $((LINES-2))' 
```
3. terminal 3 to run the minipupper operator
```python
cd minipupper-app
```
- via mic mode (TTS)
```python
minipupper_operator.py 
```
- via keyboard mode
```python
minipupper_operator.py -k
```
- via mute mode
```python
minipupper_operator.py -m
```


---

## Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| HF Space for beat (not local) | Avoids `librosa + numpy` dependency on Pi (resource-constrained), faster cloud GPU |
| Deterministic RNG seeding | Reproducible dances for debugging, demos, A/B testing |
| Genre detection on Pi (not HF) | HF Space returns pure timing; genre is orthogonal and faster to classify locally |
| Compound expansion in local choreography | Keeps HF Space stateless, moves are purely a Pi concern |
| `time_acc` dual-derived (RMS + BPM) | High energy → faster moves, low energy → slower. BPM cap prevents excessively fast slots |
| Separate process for audio playback | Allows ffplay to run independently; dance loop monitors it as a completion signal |
| Pre-cached PIL images for tilt display | Eliminates file I/O per frame; 12 Hz polling is sustainable on Pi |
