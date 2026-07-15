# Mini Pupper Dance Machine 🕺

Make your robot dog dance to any song on YouTube! It analyzes the beat, picks genre-appropriate moves, plays the music through its speaker, and cycles faces on its LCD — all automatically.

## Quick Start

### Option 1 — Voice (TTS / OpenClaw)

Talk to your robot. The LLM handles everything end-to-end.

**Turn 1 — Request a dance:**

> **"Dance to Bohemian Rhapsody"**

The robot searches YouTube and replies: *"I found Queen – Bohemian Rhapsody (Official Video Remastered), should I dance to it?"*

**Turn 2 — Confirm:**

> **"Yes, dance to it!"**

Behind the scenes:
1. Download the song (yt-dlp)
2. Analyze the beat via Hugging Face (librosa)
3. Detect genre from YouTube metadata
4. Generate deterministic choreography (same URL = same dance)
5. Activate robot, play music, run moves synced to beats, cycle LCD faces
6. Auto-cooldown when song ends

**To stop mid-dance:** Say **"Stop dancing"** or **"Stop the music."**

> ⏱️ Setup takes ~30–50 seconds (download + analysis), then the show starts.

---

### Option 2 — Python CLI (Direct)

Bypass the voice layer entirely. Run these commands directly on the Pi:

```bash
# Search YouTube
python3 hf_dance_to_audio.py search "Bohemian Rhapsody"

# Full pipeline: download, HF analysis, genre detection, dance (background)
python3 hf_dance_to_audio.py dance "https://youtube.com/watch?v=..."

# Returns genre without downloading or dancing
python3 hf_dance_to_audio.py classify "https://youtube.com/watch?v=..."

# Override genre detection (skip YouTube metadata scan)
python3 hf_dance_to_audio.py dance "https://youtube.com/watch?v=..." --genre rock

# Debug: print seed, genre pool, and every move selected (no robot dance)
python3 hf_dance_to_audio.py dance "https://youtube.com/watch?v=..." (--genre override) --debug

# Agent pipeline entry point — read a json task file (HF space analysis) and dance with its exact params
python3 hf_dance_to_audio.py process-task /path/to/task.json

# Stop everything immediately
python3 hf_dance_to_audio.py stop

# Check if currently dancing
python3 hf_dance_to_audio.py status
```

| Subcommand | What it does |
|------------|-------------|
| `search <query>` | Search YouTube, return JSON of top results |
| `dance <url>` | Full pipeline: download → HF beat analysis → genre detection (YouTube tags) → choreography → launch background dance |
| `dance <url> --genre <name>` | Same but skip YouTube metadata scan — use specified genre directly |
| `dance <url> --debug` | Same pipeline but print deterministic seed, genre pool, and every move to stdout + save debug JSON to `/tmp/minipupper_dance_debug.json` |
| `process-task <file>` | Agent bridge: read a task JSON file, pass its url + genre to the dance pipeline |
| `stop` | Kill audio, deactivate robot, clear all state flags |
| `status` | Check if a dance is running and get process/PID info |
| `execute <state>` | **(Internal — called by `dance`)** Load saved state and run choreography |

**`process-task` in detail:** This is the bridge between the voice agent (Gemini) and the dance machine. When you say "dance to Bohemian Rhapsody" via voice, Gemini writes a task JSON file to `~/minipupper-app/tasks/active/` with the song URL and genre already decided. The agent cron then calls `process-task` on that file. The key difference from `dance`: the genre was already figured out by Gemini — `process-task` passes it straight through as an override, **skipping the 5–15 second YouTube metadata scrape** that would normally happen. Everything else follows the same pipeline: download, HF beat analysis, choreography, background execution.

```
Gemini wrote: { "params": { "url": "...", "genre": "rock" } }
                   ↓
 process-task reads url + genre, passes to cmd_dance()
                   ↓
         genre_override="rock" → skips _detect_genre_from_url()
                   ↓
         download → HF beats → choreography → background execute
```

**`execute`** is not meant to be run manually. When you run `dance <url>`, it saves all analysis results to `/tmp/minipupper_dance_state.json` and spawns a background subprocess running `execute` on that state file. The subprocess activates the robot, plays audio, runs the choreography on beat timestamps, cycles LCD faces, and deactivates when the song ends.

**`--debug` flag** Adding `--debug` (or `-d`) to any `dance` command prints the deterministic seed (SHA-256 of the song URL), the resolved genre, the genre pool moves/weights, and every move selected for each beat slot — with its timestamp, angle, and time_acc. The full choreography is also saved to `/tmp/minipupper_dance_debug.json` as structured JSON. This doesn't affect the robot — the dance still runs normally in the background; the debug info prints to stdout during the setup phase.

Same pipeline as voice — just skipping the TTS/agent middleman.

---

## What Happens Behind the Scenes

1. **Download** — MP3 downloaded from YouTube via yt-dlp
2. **Convert** — MP3 → WAV (wave audio)
3. **Beat Analysis** — WAV uploaded to Hugging Face Space → librosa detects BPM, beat timing, and energy per beat
4. **Genre Detection** — YouTube metadata (tags, title, channel) is scanned to classify genre
5. **Choreography** — Seed based on song URL → deterministic RNG picks moves from the genre's move pool
6. **Execution** — Robot activates → ffplay plays audio → movement commands fire at beat timestamps → LCD cycles through REST→TROT→HOP→FINISHHOP faces
7. **Cooldown** — When audio ends (or ALSA goes silent), robot deactivates automatically

---

## Supported Genres (11)

| Genre | Vibe |
|-------|-------|
| 🚨 **Rock** | Headbang, robot bouncing, lean forward and head tilt |
| 🍵 **Classical** | Slow and graceful - bows, squat, elegant body circular move |
| 🏤 **Pop** | Energetic — twerks, swagger, wiggles, stepping |
| 🌀 **Lean** | Special mode — full-body sustained leans with tilt LCD face |
| 🏧 **Hip-Hop** | Bouncy — front kicks, leg lifts, head nods |
| 🕺 **Disco** | Groovy — shoulder shrugs, head ellipses, assertive looks to the right |
| ⚡ **Electronic** | Fast rhythmic — seeking scans (looking right and left), sharp looking up, wiggle butt to the right |
| 🍷 **Jazz** | Smooth — cool looking upper-left, right shoulder shrugs, downward gaze |
| 💃 **Latin** | Passionate — butt shrugs, body rows, left hip wiggles |
| 🌴 **Reggae** | Laid-back — chill elevation, left butt shrugs, relaxed looking lower-left |
| 🪕 **Folk** | Organic — look lower-right, left shoulder shrugs, looking left |


---

## Stopping / Troubleshooting

| Problem | Fix |
|---------|------|
| Music won't stop | Say **"stop dancing"** or **"stop the music"** or **stop** |
| Robot not moving | Make sure the robot is on a flat surface and powered |
| Dance looks wrong genre | Override with `robot.dance <url> --genre rock` |
| Audio cuts out early | Check speaker volume, robot may have auto-muted |

---

## Limitations

- **YouTube only** — currently supports YouTube URLs via yt-dlp
- **Requires internet** — HF Space beat analysis needs internet access
- **~20–30s cold start** — download + HF analysis take time before dancing begins
- **YouTube authentication** — `cookies.txt` must be maintained for yt-dlp downloads (cookies expire and need periodic renewal)
- **Audio sync** — beat timing is computed upfront (not live), so variable-tempo songs may drift
- **One dance at a time** — concurrent dances are not supported
