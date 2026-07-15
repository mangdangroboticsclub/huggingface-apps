# Mini Pupper Dance Machine — Developer Guide

Build your own dancing robot. This guide covers architecture, how to extend the system, and how to replicate the demo.

> **New here?** Start with [README.md](README.md) for the quick-start CLI commands. This guide assumes you can already run a dance and want to understand the internals or extend the system.

> **OpenClaw Integration:** This demo also works with the OpenClaw voice-agent framework. To use voice commands instead of the CLI, set up the OpenClaw environment from [`mangdangroboticsclub/openclaw-app`](https://github.com/mangdangroboticsclub/openclaw-app.git) (`-b master`). OpenClaw handles intent detection, genre resolution, and task orchestration — the dance pipeline itself (download → HF beat analysis → choreography → execution) remains unchanged.

---

## Repository Layout

All files in this repository:

| File | Role | Key Classes / Functions |
|------|------|------------------------|
| `hf_dance_to_audio.py` | CLI entry point — subcommand dispatch, full pipeline orchestration | `cmd_dance()`, `cmd_execute()`, `cmd_classify()`, `cmd_search()`, `cmd_process_task()`, `_choreography_loop()`, `_hf_detect_beats()`, `_detect_genre_from_url()`, `_audio_monitor` |
| `local_choreography.py` | Genre-aware move selection + compound expansion | `enrich_choreography()`, `_expand_compounds()`, `_map_angle()`, `_resolve_genre()`, `GENRE_POOLS`, `COMPOUND_EXPANSIONS`, `ANGLE_MOVES`, `GENRE_ALIASES` |
| `dance_face.py` | LCD face display synced to choreography | `DanceFace` (class), `face_cues_from_choreography()`, `tilt_display_poller()`, `TiltState` |
| `robot/__init__.py` | Python package marker | — |
| `robot/robot_control.py` | Robot movement API — builds and runs FPC MovementLib sequences | `_build_movement()`, `run_movement()`, 30+ atomic/compound commands |
| `robot/continuous_control.py` | Real-time velocity controller for continuous tasks | `ContinuousController` class — `.activate()`, `.set_velocity()`, `.tick()`, `.deactivate()` |
| `robot/robot_control.py.bak` | Backup of an earlier version | — |
| `display/dog_straight face-bgrmv.png` | Base image for tilt LCD rotation | Used by `tilt_display_poller()` |
| `display/dog_straight face.jpg` | Face image variant | Unused in current code |
| `display/dog_tilt.gif` | Animated GIF | Unused in current code |
| `display/dog_tilt2.webp` | Face image variant | Unused in current code |
| `display/dog_tilt2inv.webp` | Inverted face image | Unused in current code |
| `DEVELOPER.md` | This file | — |
| `LICENSE` | Apache 2.0 | — |
| `README.md` | Quick-start user guide | — |

> **Note:** `display/dog_tilt.gif`, `dog_tilt2.webp`, `dog_tilt2inv.webp`, and `dog_straight face.jpg` are present in the repository but not referenced by any Python code. They may be planned for future face-display features.

---

## System Architecture

```
YouTube URL
    │
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
│     ├─ activate robot (FPC MovementLib API)          │
│     ├─ ffplay audio (ALSA)                           │
│     ├─ dance_face (LCD face sync)                    │
│     └─ choreography loop (Movement API)              │
└─────────────────────────────────────────────────────┘
```

The beat analysis runs on a Hugging Face Space — **not on the Pi**. The Space source code is open:
- 🔗 https://huggingface.co/spaces/Minipupper/Minipupper-Dance-Rhythm-Analysis
- Uses `librosa` for onset-strength beat tracking
- Fork it to customize the beat algorithm

---

## Component Deep-Dives

### 1. HF Space Beat Analysis

The Pi communicates with the Space via a 3-step Gradio HTTP flow:

```python
import requests

# Step 1: Upload WAV file
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

**Algorithm (on the Space side):**
1. **Onset strength envelope** via `librosa.onset.onset_strength()`
2. **Dual-bias beat tracking** — runs `librosa.beat.beat_track()` at both 60 BPM and 120 BPM start biases, picks the winner by onset alignment score
3. **Per-beat BPM** — calculated from inter-beat-intervals (IBI), smoothed with a sliding window (max 3 or N/30)
4. **RMS energy envelope** — every beat gets a `time_acc` value:
   - RMS > 0.3 → `time_acc = 0.25` (high energy = faster moves)
   - RMS ≤ 0.3 → `time_acc = 0.6` (low energy = slower moves)
   - BPM > 109 → `time_acc` capped at 0.25 regardless
5. Every 2nd beat is emitted (no redundancy)

**Returns:** `{bpm, duration_sec, beat_slots: [{start_time, time_acc, local_bpm}]}`

To use a custom Space:
```bash
export HF_DANCE_SPACE="YourUserName/YourSpaceName"
```

---

### 2. Genre Detection

YouTube metadata (tags, title, channel name) is scanned to classify the song into a genre pool. The detection function `_detect_genre_from_url()` scores signals from:
- **Tags** (weight 10): yt-dlp's `tags` field
- **Channel name** (weight 8): keyword match on channel
- **Title** (weight 6): keyword match on title

Any song can be overridden via `--genre <name>` at the CLI, which skips the metadata scan entirely.

#### Genre Aliases

The `GENRE_ALIASES` dict in `local_choreography.py` maps non-canonical names to genre pools:

| Input Alias | Resolves To |
|------------|-------------|
| `hip-hop` | `hiphop` |
| `reggaeton`, `salsa`, `tango` | `latin` |
| `blues`, `r&b`, `soul` | `jazz` |
| `metal`, `punk`, `alternative` | `rock` |
| `edm`, `techno`, `house`, `trance`, `dubstep` | `electronic` |
| `k-pop`, `j-pop`, `showtunes` | `pop` |
| `indie`, `acoustic` | `folk` |
| `bluegrass` | `country` ⚠️ |

> **⚠️ Dead alias:** `country` has no `GENRE_POOLS` entry — `bluegrass` mapping lands on a non-existent pool. Any song classified as `country` falls through to the `pop` default.

#### Genre Pools (12)

| Pool | Moves | Weight Distribution |
|------|-------|-------------------|
| `rock` | headbang, bounce, dip | 0.35 / 0.35 / 0.3 |
| `classical` | greet, squat, body_ellipse | 0.4 / 0.35 / 0.25 |
| `pop` | twerk, swagger, wiggle | 0.29 / 0.36 / 0.35 |
| `hiphop` | front_kick, backleg_lift, nod | 0.3 / 0.3 / 0.4 |
| `disco` | shoulder_shrug, head_ellipse, look_right, look_upperright | 0.35 / 0.15 / 0.25 / 0.25 |
| `electronic` | seek, look_up, right_wiggle | 0.45 / 0.35 / 0.2 |
| `jazz` | look_upperleft, right_shoulder_shrug, look_down | 0.35 / 0.35 / 0.3 |
| `latin` | butt_shrug, body_row, left_wiggle | 0.4 / 0.35 / 0.25 |
| `reggae` | left_butt_shrug, look_lowerleft, raise-body, right_butt_shrug | 0.25 / 0.25 / 0.25 / 0.25 |
| `folk` | look_lowerright, left_shoulder_shrug, look_left | 0.3 / 0.35 / 0.35 |
| `lean` | lean (single move, weight 1.0) | Uses `tilt_display_poller()` for LCD |
| `complete` | All 16 moves evenly weighted (0.04–0.078) | Meta-genre for full variety |

---

### 3. Choreography Engine (`local_choreography.py`)

#### Deterministic Seeding

Every song URL is hashed via SHA-256 to produce an integer seed:
```python
seed_int = int(hashlib.sha256(song_seed.encode()).hexdigest(), 16)
rng = random.Random(seed_int)
```
- Same URL → **exact same dance** every time
- Useful for debugging, reproducible demos, A/B testing
- Seed doesn't change when `--genre` is overridden

#### Three-Pass Pipeline

**Pass 1 — Genre replacement (1:1 slot mapping):**
For each HF Space beat slot, the seeded RNG picks a move from the genre's weighted pool. The angle from HF Space is mapped via `_map_angle()` per move type.

**Pass 2 — Compound expansion:**
Compound moves are expanded into atomic sub-moves across consecutive slots:

| Compound | Sub-moves | Slots used |
|----------|-----------|------------|
| `dip` | lower-body → look-down → body-row → stop → look-up → body-row → raise-body → stop | 8 |
| `spin` | rotate_cw 180° → stop → rotate_ccw 90° → stop | 4 |

All subsequent entries are time-shifted right by the expansion. Entries past the song-end ceiling are truncated (Pass 3).

**Pass 3 — Truncation:**
Entries that overshoot the last beat slot's end time are removed.

#### Angle Mapping

`_map_angle()` translates the abstract angle from HF Space into move-specific parameters:

| Type | Scale | Bounds | Moves |
|------|-------|--------|-------|
| Rotation | ×3 | 10–360° | `rotate_cw`, `rotate_ccw` |
| Roll | 1:1 | 5–30° | `body-row`, `swagger`, `lean` |
| Pitch | 1:1 | 5–30° | `look-up`, `look-down` |
| Yaw | 1:1 | 5–45° | `look-right`, `look-left` |
| Height | ×2 | 5–30 units | `raise-body`, `lower-body`, `squat` |
| Strafe | 1:1 | 5–30 units | `right`, `left` |

CW rotation (`rotate_cw`) uses positive angle; CCW (`rotate_ccw`) negates it. Same for `right` vs `left`.

---

### 4. Robot Control Layer

#### `robot/robot_control.py` — FPC MovementLib API

This module uses the **FPC (Flexible Programmable Choreography) API** from the StanfordQuadruped library. It builds a `MovementLib` (a list of `MovementGroup` objects) and executes them through the direct `Controller` + `HardwareInterface` path — **not the UDP joystick protocol**.

**Core functions:**
```python
_build_movement(command, duration, angle, time_acc) → list[MovementGroup]
run_movement(movement_lib, timeout=10.0) → None
```

**All supported commands (30+):**

| Category | Commands |
|----------|----------|
| **Gait** | `forward`, `backward`, `right`, `left` |
| **Rotation** | `rotate_cw`, `rotate_ccw` |
| **Head positioning** | `look-up`, `look-down`, `look-right`, `look-left`, `upper-left`, `upper-right`, `lower-left`, `lower-right` |
| **Body posture** | `raise-body`, `lower-body`, `squat`, `body-row` |
| **Leg lifts** | `front_kick` (both front legs), `backleg_lift` (alternating), `greet` (alternating forelegs) |
| **Compound signatures** | `dip` (8-part body sink/rise), `spin` (180°→90° rotation), `lean` (sustained body roll), `flourish` (multi-axis showstopper) |
| **Rhythm moves** | `headbang` (rapid pitch oscillation), `bounce` (body bob), `head_ellipse` (head traces ellipse), `body_ellipse` (body circular motion), `disco1`/`disco2`/`disco3` (quadrant head patterns), `seek` (left-right looking) |
| **Energy moves** | `swagger` (body roll), `nod` (small pitch oscillation), `wave` (4-leg alternating), `shuffle` (side-step left/right) |
| **State** | `activate` (stand up), `deactivate` (sit down), `stop` (idle) |

**`time_uni` vs `time_acc` critical nuance:**
- `time_acc` — transition time (how long to reach the pose)
- `time_uni` — hold time (how long to maintain the pose)
- For moves that should fit within one beat: `_sub_hold = duration / n_subs`
- For moves that span multiple beats: `time_uni = duration` (full slot)

Example — **headbang** (fits in 1 beat, 2 sub-moves):
```python
_n_subs = 2
_sub_tic = max(time_acc / _n_subs, 0.015)
_sub_hold = duration / (reps * _n_subs)
for _ in range(reps):
    move.head_move(pitch_deg=15, yaw_deg=0, time_uni=_sub_hold, time_acc=_sub_tic)
    move.head_move(pitch_deg=-15, yaw_deg=0, time_uni=_sub_hold, time_acc=_sub_tic)
```

Example — **disco2** (spans 2 beats):
```python
move.head_move(pitch_deg=15, yaw_deg=20, time_uni=duration, time_acc=time_acc)
move.head_move(pitch_deg=-15, yaw_deg=-20, time_uni=duration, time_acc=time_acc)
```

#### `robot/continuous_control.py` — Real-Time API

For continuous tasks (person-following, live teleoperation), use the `ContinuousController` class. Unlike the MovementLib API which builds pre-computed sequences, this keeps the hardware loop open and accepts live velocity updates each tick.

```python
from robot.continuous_control import ContinuousController

ctrl = ContinuousController()
ctrl.activate()                     # Stand up, engage trot gait

# Loop at 10-20Hz:
ctrl.set_velocity(vx=0.2, vy=0)     # Forward 0.2 m/s
ctrl.set_yaw_rate(rate=-0.5)        # Turn right
ctrl.tick()                         # Advance one control step
ctrl.hardware.sync()                # Send to servos

ctrl.stop()                         # Zero velocities
ctrl.deactivate()                   # Sit down
```

**Key methods:**
| Method | Description |
|--------|-------------|
| `.activate()` | Stand up + transition to TROT gait |
| `.deactivate()` | TROT → REST → DEACTIVATED |
| `.set_velocity(vx, vy)` | Forward/lateral velocity (±0.5 m/s clamped) |
| `.set_yaw_rate(rate)` | Yaw rotation rate (±1.0 rad/s clamped) |
| `.set_height(height)` | Body height (default -0.07m standing) |
| `.set_pitch(roll)` | Body pitch (degrees) |
| `.set_roll(roll)` | Body roll (degrees) |
| `.tick()` | Wait for next hardware tick (~66 Hz), run control loop, send to servos |
| `.stop()` | Zero all velocities |
| `.close()` | Stop + deactivate + release hardware |

---

### 5. LCD Face System (`dance_face.py`)

#### `DanceFace` Class

Manages the LCD display during a dance session via a daemon thread.

```python
df = DanceFace()
cues = face_cues_from_choreography(timed_moves, genre="pop")
df.start(cues, stop_flag_path="/tmp/minipupper_dance_active")
# ... dance runs ...
df.stop()
```

**Methods:**
| Method | Description |
|--------|-------------|
| `.start(cues, audio_delay, stop_flag_path)` | Launch face-display daemon thread |
| `.stop()` | Signal thread to stop (wait up to 2s) |
| `.show_now(state_val)` | Immediately show a face state |

#### Face Cue Generation

`face_cues_from_choreography()` samples every Nth entry from the actual choreography timetable, naturally tracking variable tempo. N is determined by `GENRE_PACING`:

| Genre | Beats per face change |
|-------|----------------------|
| classical | 8 |
| jazz, reggae, folk | 6 |
| pop, hiphop, country, disco | 4 |
| latin | 3 |
| rock, electronic | 2 |

**Face cycle:** REST (calm) → TROT (walking) → HOP (leaping) → FINISHHOP (landing) → repeat.

#### Tilt Display Poller (Lean Genre)

For the `lean` genre only, `tilt_display_poller()` runs as a background thread:
- Monitors the robot's roll angle via a shared `TiltState` object
- Rotates `display/dog_straight face-bgrmv.png` smoothly to match the body lean
- Uses a pre-rendered oversized canvas so rotation never clips the viewport
- Polls at ~12 Hz with zero file I/O per frame (direct SPI display via `Display().disp.display(img)`)

---

### 6. Audio Monitor (`_audio_monitor`)

A daemon thread checks two conditions for when the song ends:

1. **ffplay process exits** (`player.poll() is not None`) — always checked
2. **ALSA PCM no longer RUNNING** — checks `/proc/asound/card*/pcm*p/sub*/status` for `state: RUNNING`, with configurable debounce (default 2 consecutive misses)

A 5-second startup grace period prevents false positives before ffplay opens the PCM device. When either condition triggers, the active flag is removed and `_choreography_loop` exits.

**PID management:**
- `_pid_from_file()` — reads PID from a file, checks if alive via `os.kill(pid, 0)`
- `_graceful_kill()` — SIGTERM → 2s wait (10×200ms) → SIGKILL fallback

---

### 7. Flag Files & Debugging

All state files live in `/tmp/`:

| File | Contents |
|------|----------|
| `minipupper_dance_active` | Exists while dance is running (flag) |
| `minipupper_dance_audio.pid` | PID of ffplay audio process |
| `minipupper_dance_pid` | PID of the execute subprocess |
| `minipupper_dance_state.json` | Full execution state snapshot (file paths, genre, total moves, timestamps) |
| `minipupper_dance_result.json` | Final result dict from last run (`{ok, error?, result?}`) |
| `minipupper_dance_debug.json` | Debug mode — full choreography dump (seed, genre, every move with timestamp+angle) |
| `minipupper_dance_cache.json` | Cached HF Space beat data — avoids re-analysis on same song |
| `minipupper_dance_player.log` | Human-readable timeline log — dance phases, HF response times, errors |
| `minipupper_music_active` | Flag for external systems — prevents auto-mute |

**Volume control:**
- `_set_volume()` tries `audio_util.set_volume()` first (auto-detects ALSA card by control name), falls back to `amixer sset Headphone`
- `_set_music_active(True)` creates the `MUSIC_ACTIVE_FLAG` so external daemons don't auto-mute the speaker during a dance

---

## Extending the System

### Add a New Genre

1. Add an entry to `GENRE_POOLS` in `local_choreography.py`:
```python
"mygenre": {
    "moves": ["move_a", "move_b", "move_c"],
    "weights": [0.4, 0.35, 0.25],
}
```
2. Add aliases to `GENRE_ALIASES` in `local_choreography.py` if needed
3. Add pacing to `GENRE_PACING` in `dance_face.py` (beats per face change)
4. Add display name to `GENRE_DISPLAY_NAMES` in `hf_dance_to_audio.py`
5. If the genre needs special LCD behavior, add a branch in `_choreography_loop()` (like the `genre == "lean"` check for `tilt_display_poller`)

> **Note on legacy constants:** `hf_dance_to_audio.py` may contain `CHOREOGRAPHY_*` constants from an earlier version. The active choreography path uses `local_choreography.py` pools only — those constants are vestigial.

### Add a New Atomic Move

Requires changes in **two layers**:

**Layer 1 — Robot-side** in `robot/robot_control.py`'s `_build_movement()`:
```python
elif command in ("my-move", "my_move"):
    move.head_move(pitch_deg=10, yaw_deg=0,
                   time_uni=duration, time_acc=time_acc)
```

**Layer 2 — Choreography routing** in `local_choreography.py`:
1. Register in `ANGLE_MOVES`:
```python
"my-move": {"type": "pitch", "default": 10},
```
2. Add to any genre pool's `moves` list with an appropriate weight.

**Important:** The command string must match exactly between `_build_movement()` and `local_choreography.py`.

### Add a New Compound Move

Same two-layer approach:

**Layer 1 — Robot-side** in `robot_control.py`'s `_build_movement()`:
```python
elif command in ("my-combo", "my_combo"):
    move.head_move(pitch_deg=15, yaw_deg=0, time_uni=..., time_acc=...)
    move.body_row(row_deg=10, time_uni=..., time_acc=...)
    move.stop(time=0.2)
```

**Layer 2 — Choreography** in `local_choreography.py`:
```python
COMPOUND_EXPANSIONS = {
    "my-combo": [
        ("head_move_15", 15),
        ("body-row", 10),
        ("stop", 0),
    ],
    # ... existing compounds
}
```

**`COMPOUND_EXPANSIONS` vs robot-side compounds:** The expansions dict in `local_choreography.py` handles *which* atomic commands get called and their default angles. The actual `time_uni`/`time_acc` logic lives in `_build_movement()`. Both files must be updated together.

### Add LCD Face Art

1. Place `.webp` or `.png` image in the `display/` directory
2. Use `_show_on_lcd()` for static display, or follow the `tilt_display_poller()` pattern for animated/polled display:
   - Pre-cache PIL `Image` objects at startup (resize, flip, paste on black bg)
   - Use `Display().disp.display(img)` directly (SPI, no file I/O)
   - Poll at desired rate

### Add a New CLI Subcommand

In `hf_dance_to_audio.py`:
1. Add a subparser via `argparse`
2. Implement a `cmd_*()` function that returns `{"ok": bool, ...}`
3. Wire into the `main()` dispatch block

---

## Testing & Debugging

```bash
# Test genre detection only (no download, no dance)
python3 hf_dance_to_audio.py classify "https://youtube.com/watch?v=..."

# Check dance process state
python3 hf_dance_to_audio.py status

# Stop if stuck
python3 hf_dance_to_audio.py stop

# View debug log
cat /tmp/minipupper_dance_player.log

# Inspect last result
cat /tmp/minipupper_dance_result.json

# View full choreography (after --debug run)
cat /tmp/minipupper_dance_debug.json
```

---

## Prerequisites for Replication

### Raspberry Pi Setup
- Python 3.9+
- Install dependencies: `pip install yt-dlp requests Pillow numpy`
- Clone `StanfordQuadruped` to `/home/ubuntu/StanfordQuadruped/`
- Enable system services: `joystick.service`, `robot.service`
- Configure ALSA in `/etc/asound.conf` — routes `default` → Headphones card by name

### YouTube Authentication
yt-dlp needs cookies for some videos:
```bash
yt-dlp --cookies-from-browser chromium  # or firefox
# Save cookies to cookies.txt in the working directory
```
Cookies expire and need periodic renewal.

### HF Space
The default Space is `Minipupper/Minipupper-Dance-Rhythm-Analysis` — public, no token needed.
- Space source: https://huggingface.co/spaces/Minipupper/Minipupper-Dance-Rhythm-Analysis/tree/main
- Deploy your own: fork the Space, copy its `app.py`, set `HF_DANCE_SPACE` env var

---

## Known Issues & Race Conditions

| Issue | Cause | Workaround |
|-------|--------|------------|
| HF Space timeout on large files | Large WAV uploads exceed Space's request timeout | Reduce `full_duration` cap, or use shorter audio clips |
| Audio card reordering | `bcm2835 Headphones` vs `simple-card` can flip on reboot | Use `audio_util.py` auto-detection (search by control name, not card number) |
| Dance stops early | ALSA buffer underrun triggers false PCM idle detection | Increase `debounce` count in `_audio_monitor` |
| Two active flag files race | `cmd_stop()` and `_audio_monitor` can both try to remove flag | Use existence check + try/except on removal |
| yt-dlp fails | Expired cookies, region-blocked video | Refresh cookies.txt, or try a different video |
| Robot not moving | Joystick service not running, or robot not activated | `sudo systemctl status joystick.service` and `robot.service` |

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
| FPC API over UDP joystick | FPC builds pre-computed sequences; avoids UDP timing jitter and joystick service bugs |
| File-based agent bridge (`process-task`) | Decouples voice agent (external) from dance engine — the repo needs no agent infrastructure |