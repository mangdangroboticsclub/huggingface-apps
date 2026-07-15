# Mini Pupper Dance Machine 🕺

Make your Mini Pupper v2 robot dog dance to any YouTube song. Download -> beat analysis -> genre detection -> choreography -> robot dance -- all automated.

```
youtube.com/watch?v=...  --->  yt-dlp  --->  ffmpeg WAV  --->  HF Space (librosa)
                                                                   |
                         robot activates <--- choreography <--- beats + BPM
                         ffplay plays audio
                         LCD cycles faces
                         auto-cooldown
```

---

## File Map

| File | Role |
|------|------|
| hf_dance_to_audio.py | CLI entry point -- all subcommands, pipeline orchestration |
| local_choreography.py | Genre-aware move selection, compound expansion, angle mapping |
| dance_face.py | LCD face cycling, tilt display for lean genre |
| robot/robot_control.py | Robot movement API -- 30+ moves via FPC MovementLib |
| robot/continuous_control.py | Real-time velocity controller for continuous tasks |
| robot/__init__.py | Python package marker (empty) |
| display/ | LCD face images (base image for tilt rotation, variants) |
| DEVELOPER.md | Full developer guide -- architecture, extending, replication |
| LICENSE | Apache 2.0 |

---

## Onboarding -- First Dance in 5 Minutes

```bash
# 1. Connect to your Mini Pupper via VS Code Remote-SSH
#    (Cmd/Ctrl+Shift+P -> Remote-SSH: Connect to Host -> ubuntu@<your-pi-ip>)
#    Then open a terminal inside VS Code (Ctrl+`)

# 2. Clone this repo
git clone https://github.com/mangdangroboticsclub/huggingface-apps.git -b Minipupper_Dance

# 3. Update StanfordQuadruped to the matching dance-enabled branch
cd ~/StanfordQuadruped/
git remote add hf https://github.com/mangdangroboticsclub/huggingface-apps.git
git fetch hf Minipupper_Dance_StanfordQuadruped
git reset --hard FETCH_HEAD

# 4. Install Python dependencies
cd ..
cd ~/huggingface-apps/
pip install yt-dlp

# 5. Run your first dance!
python3 hf_dance_to_audio.py dance "https://youtu.be/Aq5WXmQQooo?si=AzTpH95a65cpANmL"
```

> ⚠️ **Not all YouTube videos playable.** Some are region-locked or require a logged-in session. If you hit errors, see [Troubleshooting](#youtube-restrictions) for the fix.

> First run takes ~30-50s (downloading + HF beat analysis). Subsequent dance commands with new songs will also need download + analysis time.

---

## Voice Control via OpenClaw

This demo also works with the OpenClaw voice-agent framework. To use voice commands instead of the CLI, set up the OpenClaw environment from [`mangdangroboticsclub/openclaw-app`](https://github.com/mangdangroboticsclub/openclaw-app.git) (`-b master`). OpenClaw handles intent detection, genre resolution, and task orchestration -- the dance pipeline itself (download -> HF beat analysis -> choreography -> execution) remains unchanged.

Once set up, you can speak naturally:
- "Dance to Bohemian Rhapsody"
- "Search for Shape of You by Ed Sheeran"
- "Stop dancing"

See [DEVELOPER.md](DEVELOPER.md) for how the agent bridge works.

---

## Quick Start

```bash
# Search YouTube
python3 hf_dance_to_audio.py search "Bohemian Rhapsody"

# Full pipeline: download, HF analysis, genre detection, dance (background)
python3 hf_dance_to_audio.py dance "https://youtube.com/watch?v=..."

# Detect genre only (no download, no dance)
python3 hf_dance_to_audio.py classify "https://youtube.com/watch?v=..."

# Override genre (skip YouTube metadata scan)
python3 hf_dance_to_audio.py dance "https://youtube.com/watch?v=..." --genre rock

# Debug: print seed, genre pool, every move (dances normally)
python3 hf_dance_to_audio.py dance "https://youtube.com/watch?v=..." --debug

# Agent bridge: read task JSON with url + genre already resolved
python3 hf_dance_to_audio.py process-task /path/to/task.json

# Stop immediately
python3 hf_dance_to_audio.py stop

# Check if dancing
python3 hf_dance_to_audio.py status
```

### Subcommand Reference

| Subcommand | What it does |
|------------|-------------|
| search <query> | Search YouTube, return JSON of top results |
| dance <url> | Full pipeline, background dance |
| dance <url> --genre <name> | Same, skip YouTube metadata, use specified genre |
| dance <url> --debug | Same + print seed/genre/moves to stdout, save debug JSON |
| classify <url> | Detect genre from YouTube metadata only |
| process-task <file> | Read task JSON with url + genre, dance (agent bridge) |
| stop | Kill audio, deactivate robot, clear flags |
| status | Check if dancing, get process/PID info |
| execute <state_file> | (Internal) Load saved state, run choreography |

---

## Requirements

### Hardware
- Mini Pupper v2 (Raspberry Pi 4 / CM4)
- Speaker connected (bcm2835 Headphones ALSA device)
- ST7789 LCD display

### Software (Pi side)
- Python 3.9+
- yt-dlp, requests, Pillow, numpy
- StanfordQuadruped repo at /home/ubuntu/StanfordQuadruped/
- System services: joystick.service, robot.service

### Cloud
- Beat analysis runs on Hugging Face Space -- no librosa on the Pi

---

## Troubleshooting

| Problem | Fix |
|---------|------|
| Music won't stop | python3 hf_dance_to_audio.py stop |
| Robot not moving | Flat surface + powered. Check joystick.service and robot.service. |
| Dance looks wrong genre | Override: python3 hf_dance_to_audio.py dance <url> --genre rock |
| yt-dlp fails | Refresh cookies.txt -- YouTube cookies expire periodically |
| HF Space timeout | Large WAVs may exceed the Space's request timeout |

### YouTube Restrictions

Not all YouTube videos can be downloaded. Some are region-locked, age-restricted, or require a logged-in YouTube session. You may see errors like:

- `HTTP Error 400: Bad Request`
- `YouTube said: ERROR - Precondition check failed`
- `Unable to download webpage: The read operation timed out`

**Fix -- Use cookies:**

If you have a working `cookies.txt` from your desktop browser, copy it to the Pi and add it to the yt-dlp command:

```bash
# 1. Copy cookies from your PC to the Pi:
scp cookies.txt ubuntu@<your-pi-ip>:~/huggingface-apps/

# 2. Edit hf_dance_to_audio.py to use the cookies permanently:
sed -i 's|"--no-playlist",|"--cookies", "cookies.txt",\n                 "--no-playlist",|' ~/huggingface-apps/hf_dance_to_audio.py

# 3. Done -- all future dance commands will use cookies automatically
```

To get the `cookies.txt` file from your browser, run on your computer (WSL, PowerShell, or CMD):

```bash
yt-dlp --cookies-from-browser edge --cookies cookies.txt
```

Replace `edge` with `chrome` or `firefox` depending on your browser.

> 💡 Cookies expire periodically. If downloads stop working after a few weeks, regenerate `cookies.txt` and copy it again.

---

## Limitations

- YouTube only -- supports YouTube URLs via yt-dlp
- Requires internet -- HF Space beat analysis needs internet access
- ~20-50s cold start -- download + HF analysis before dancing
- YouTube authentication -- cookies.txt needs periodic renewal
- Audio sync -- beat timing computed upfront; variable-tempo songs may drift
- One dance at a time -- concurrent dances not supported

---

## Going Further

This README covers just the surface. For the full picture -- architecture deep-dive, how the choreography engine works, how to add new genres and moves, how the robot control layer operates, known issues, and architecture decisions -- see DEVELOPER.md.