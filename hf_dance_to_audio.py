#!/usr/bin/env python3
"""
hf_dance_to_audio.py — Mini Pupper Dance Machine (HF-powered)

Same flow as dance_to_audio.py, but beat detection comes from
Hugging Face (Bedrock510/music-metadata-app Space) instead of local aubio.

HF dependency: Every dance downloads audio, sends a segment to
HF Space for BPM analysis, then generates choreography.

Subcommands:
    search <query>        — Search YouTube, return top results as JSON
    dance <url>           — Download audio, HF analysis, dance (background)
    stop                  — Stop dancing and kill audio immediately
    status                — Check if currently dancing
    execute <state_file>  — Internal: run choreography in subprocess

Usage:
    python3 custom/hf_dance_to_audio.py search "Shape of You Ed Sheeran"
    python3 custom/hf_dance_to_audio.py dance "https://youtube.com/watch?v=..."
    python3 custom/hf_dance_to_audio.py stop
    python3 custom/hf_dance_to_audio.py status
"""

import argparse
import json
import os
# Hugging Face Space for beat analysis
HF_SPACE = os.environ.get("HF_DANCE_SPACE", "Minipupper/Minipupper-Dance-Rhythm-Analysis")
import signal

import subprocess
import sys
import time
from local_choreography import enrich_choreography, set_logger, _resolve_genre, _map_angle, GENRE_POOLS
from dance_face import face_cues_from_choreography, DanceFace, TiltState, tilt_display_poller
import random
import hashlib
import threading

# -- Constants ---------------------------------------------------------------
DANCE_ACTIVE_FLAG = "/tmp/minipupper_dance_active"
AUDIO_PID_FILE = "/tmp/minipupper_dance_audio.pid"
DANCE_PID_FILE = "/tmp/minipupper_dance_pid"
DANCE_STATE_FILE = "/tmp/minipupper_dance_state.json"
MUSIC_ACTIVE_FLAG = "/tmp/minipupper_music_active"
DANCE_RESULT_FILE = "/tmp/minipupper_dance_result.json"
LOG_FILE = "/tmp/minipupper_dance_player.log"
DOWNLOAD_DIR = "/tmp/minipupper_music"
WAV_DIR = "/tmp/minipupper_dance_wav"
CACHE_FILE = "/tmp/minipupper_dance_cache.json"

# -- Logging -----------------------------------------------------------------

def _log(msg: str):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except OSError:
        pass


# Inject logger into local choreography module
set_logger(_log)


# ── Audio Monitor ────────────────────────────────────────────────────────────
ALSA_STATUS_GLOB = "/proc/asound/card*/pcm*p/sub*/status"


def _find_active_pcm():
    """Check if any ALSA PCM device is in RUNNING state.

    Returns True if active playback detected.
    """
    import glob
    for path in glob.glob(ALSA_STATUS_GLOB):
        try:
            with open(path) as f:
                text = f.read()
            if "state: RUNNING" in text:
                return True
        except (OSError, IOError):
            continue
    return False


def _audio_monitor(player, stop_flag_path, poll_interval=1.0, debounce=2,
                     startup_grace=5.0):
    """Background thread: delete stop_flag when audio playback ends.

    Checks two conditions:
    1. ffplay process exited (player.poll())
    2. ALSA PCM no longer RUNNING (with debounce to avoid false positives)

    Skips ALSA checks during startup_grace window to allow ffplay
    to open the PCM device before monitoring begins.
    """
    start_time = time.time()
    misses = 0
    while os.path.exists(stop_flag_path):
        time.sleep(poll_interval)
        elapsed = time.time() - start_time

        # Condition 1: ffplay process exited (always checked)
        if player.poll() is not None:
            _log("Audio monitor: ffplay process ended")
            break

        # Skip ALSA checks during startup grace period
        if elapsed < startup_grace:
            misses = 0
            continue

        # Condition 2: ALSA not playing
        if not _find_active_pcm():
            misses += 1
            if misses >= debounce:
                _log(f"Audio monitor: ALSA silent for {debounce} checks — stopping dance")
                break
        else:
            misses = 0

    # Signal dance to stop by removing the active flag
    try:
        os.remove(stop_flag_path)
    except OSError:
        pass


# -- PID Tracking ------------------------------------------------------------

def _pid_from_file(path: str):
    """Return PID from a PID file, or None if not found/alive."""
    try:
        if os.path.exists(path):
            with open(path) as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                return pid
            except (OSError, ProcessLookupError):
                pass
    except (ValueError, OSError):
        pass
    return None

def _graceful_kill(pid: int, label: str = "process"):
    """SIGTERM, wait up to 2s, SIGKILL if still alive."""
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.2)
            except ProcessLookupError:
                return
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except Exception as e:
        _log(f"Error killing {label} (PID {pid}): {e}")


def _stop_audio():
    """Kill audio player and clear flags."""
    pid = _pid_from_file(AUDIO_PID_FILE)
    if pid is not None:
        _graceful_kill(pid, "audio player")
    for f in [AUDIO_PID_FILE, MUSIC_ACTIVE_FLAG]:
        try:
            os.remove(f)
        except OSError:
            pass

    _log("Audio stopped.")

# -- Volume Control ----------------------------------------------------------

def _set_music_active(active: bool):
    """Create or remove the music-active flag so the app doesn't mute."""
    if active:
        try:
            open(MUSIC_ACTIVE_FLAG, "w").close()
        except OSError:
            pass

    else:
        try:
            os.remove(MUSIC_ACTIVE_FLAG)
        except OSError:
            pass

def _set_volume(pct: str = "90%"):
    """Set speaker volume via shared audio_util (auto-detects card),
    falling back to direct amixer."""
    try:
        sys.path.insert(0, "/home/ubuntu/minipupper-app")
        from src.audio.audio_util import set_volume as _av
        _av(pct)
        return
    except Exception:
        pass
    # Fallback: direct ALSA amixer
    try:
        subprocess.run(["amixer", "sset", "Headphone", pct],
                       capture_output=True, timeout=3)
    except Exception:
        pass


# -- Task Status Updater (LCD display) ---------------------------------------

def _update_dance_status(phase: str, progress: float, message: str):
    """Update the dance task's individual file in tasks/active/ so LCD and TaskWatcher show status.
    
    Set MINIPUPPER_TASKS_DIR env var to enable task tracking (standalone mode).
    """
    tdir = os.environ.get("MINIPUPPER_TASKS_DIR", "")
    if not tdir:
        return  # no task tracking available
    try:
        for subdir in ("pending", "active"):
            d = os.path.join(tdir, subdir)
            if not os.path.isdir(d):
                continue
            for fname in sorted(os.listdir(d)):
                if not fname.endswith(".json"):
                    continue
                fp = os.path.join(d, fname)
                with open(fp) as f:
                    t = json.load(f)
                s = t.get("status", "")
                if s in ("pending", "processing", "running") and t.get("action", "").startswith("robot.dance"):
                    t["phase"] = phase
                    t["progress"] = progress
                    t["message"] = message
                    t["updatedAt"] = time.time()
                    # Move to active dir if in pending
                    if subdir == "pending":
                        os.makedirs(os.path.join(tdir, "active"), exist_ok=True)
                        new_fp = os.path.join(tdir, "active", fname)
                        os.rename(fp, new_fp)
                        fp = new_fp
                        t["status"] = "processing"
                    with open(fp, "w") as f:
                        json.dump(t, f, indent=2)
                    break
    except Exception:
        pass

# -- HF Beat Detection (via Custom HF Space) --------------------------------

def _hf_detect_beats(wav_path: str) -> dict:
    """
    Analyze WAV file via HF Space for BPM and beat timing.
    Returns dict with bpm, duration, and beat_slots (list of {start_time, time_acc, local_bpm}).
    """
    _log(f"HF beat analysis: {wav_path}")

    # Get full song duration from the original WAV (not just the 5s segment)
    full_duration = 180.0
    try:
        import wave as _wave
        with _wave.open(wav_path, 'rb') as _wf:
            _frames = _wf.getnframes()
            _sr = _wf.getframerate()
            full_duration = _frames / _sr if _sr > 0 else 180.0
        _log(f"Full WAV duration: {full_duration:.1f}s")
    except Exception:
        pass

    _log(f"Calling HF Space via raw API (duration={full_duration:.0f}s)...")
    import requests
    _update_dance_status("uploading", 10, "Uploading audio to Hugging Face Space...")

    # Upload full WAV to Space for variable tempo analysis
    with open(wav_path, "rb") as f:
        upload_resp = requests.post(
            f"https://{HF_SPACE.replace(chr(47), chr(45))}.hf.space/gradio_api/upload",
            files={"files": ("audio.wav", f, "audio/wav")},
            timeout=120,
        )
    upload_data = upload_resp.json()
    uploaded_path = upload_data[0] if isinstance(upload_data, list) else upload_data.get("path")
    _log(f"Uploaded: {uploaded_path}")
    _update_dance_status("analyzing", 30, "Analyzing beats from Hugging Face Space...")

    # Predict using uploaded file path
    t0 = time.time()
    predict_resp = requests.post(
        f"https://{HF_SPACE.replace(chr(47), chr(45))}.hf.space/gradio_api/call/predict",
        json={"data": [
            {"path": uploaded_path, "meta": {"_type": "gradio.FileData"}},
            full_duration,
        ]},
        timeout=120,
    )
    event_id = predict_resp.json().get("event_id")
    _log(f"Event: {event_id}")

    # Poll for result (SSE streaming - reads line by line, breaks immediately on data)
    deadline = time.time() + 180
    result = None
    try:
        while time.time() < deadline and result is None:
            try:
                r = requests.get(
                    f"https://{HF_SPACE.replace(chr(47), chr(45))}.hf.space/gradio_api/call/predict/{event_id}",
                    headers={"Accept": "text/event-stream"},
                    stream=True,
                    timeout=180,
                )
                for line in r.iter_lines(decode_unicode=True):
                    if line and line.startswith("data:"):
                        raw = line[5:].strip()
                        if raw and raw != "null":
                            elapsed = time.time() - t0
                            _log(f"HF response received in {elapsed:.1f}s")
                            _update_dance_status("choreography", 60, "Generating dance choreography...")
                            result = raw
                            break
                r.close()
            except requests.exceptions.RequestException:
                pass
    except Exception as e:
        _log(f"FATAL poll error: {type(e).__name__}: {e}")
        raise

    result = _parse_hf_result(result or "{}", full_duration)
    result["duration"] = full_duration  # full song duration for choreography scheduling
    return result

GENRE_DISPLAY_NAMES = {
    "rock": "🤘 Rock",
    "classical": "🎵 Classical",
    "pop": "🎤 Pop",
    "jazz": "🎷 Jazz",
    "hiphop": "🎧 Hip-Hop",
    "disco": "🕺 Disco",
    "electronic": "⚡ Electronic",
    "latin": "💃 Latin",
    "reggae": "🌴 Reggae",
    "folk": "🪕 Folk",
}

def _parse_hf_result(result_text, duration: float) -> dict:
    """
    Parse JSON response from HF Space.

    Expected Space JSON: {bpm, duration_sec, beat_slots}
    beat_slots: [{start_time, time_acc, local_bpm}, ...]

    Returns dict with bpm, duration, and beat_slots.
    """
    text = str(result_text)

    # Unwrap Gradio event-stream wrapper
    parsed = {}
    try:
        raw = json.loads(text)
        if isinstance(raw, list) and len(raw) > 0:
            inner = raw[0]
            if isinstance(inner, str):
                parsed = json.loads(inner)
            elif isinstance(inner, dict):
                parsed = inner
        elif isinstance(raw, dict):
            if "data" in raw:
                inner = raw["data"]
                if isinstance(inner, list) and len(inner) > 0:
                    inner_val = inner[0]
                    if isinstance(inner_val, str):
                        parsed = json.loads(inner_val)
                    elif isinstance(inner_val, dict):
                        parsed = inner_val
            else:
                parsed = raw
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        _log(f"HF non-JSON response: {text[:100]}")

    if not parsed or "error" in parsed:
        return {"error": parsed.get("error", "Could not parse HF Space response")}

    # Extract beat slots (timing only, no choreography)
    beat_slots = parsed.get("beat_slots", [])
    if not beat_slots or not isinstance(beat_slots, list):
        return {"error": "HF Space did not return beat_slots"}

    bpm = parsed.get("bpm") or parsed.get("tempo", 120)
    total_dur = parsed.get("duration_sec", duration)

    _log(f"HF Space: {len(beat_slots)} beat slots, BPM={bpm}, total={total_dur:.0f}s")

    return {
        "bpm": float(bpm) if bpm else 120,
        "duration": total_dur,
        "beat_slots": beat_slots,
        "source": "hf_space",
    }

def _init_robot():
    """Import robot control functions (no auto-activation)."""
    robot_dir = robot_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "robot")
    if robot_dir not in sys.path:
        sys.path.insert(0, robot_dir)
    from robot_control import _build_movement, run_movement

    return _build_movement, run_movement

def _activate_robot(build_movement, run_movement):
    """Stand the robot up."""
    _log("Activating robot...")
    lib = build_movement("activate", 0.5, None)
    run_movement(lib, timeout=5.0)
    _log("Robot activated.")

def _choreography_loop(build_movement, run_movement,
                       beat_info: dict, timed_choreography: list,
                       wav_file: str, title: str,
                       genre: str = "unknown", genre_display: str = "Generic", dance_face=None, face_cues=None) -> dict:
    """
    Main dance loop: play audio while executing timed moves.
    timed_choreography: list of (cmd, duration, angle, start_time) tuples.
    Checks DANCE_ACTIVE_FLAG on each iteration for immediate stop.
    Returns result dict.
    """
    bpm = beat_info.get("bpm", 120)

    _log(f"Timed choreography: {len(timed_choreography)} moves, BPM={bpm}")

    # Write active flag
    try:
        open(DANCE_ACTIVE_FLAG, "w").close()
    except OSError:
        pass

    # Set volume
    _set_volume("90%")
    _set_music_active(True)

    # Pre-sort moves so we can calculate timing before audio starts
    sorted_moves = sorted(timed_choreography, key=lambda m: m[3])

    if sorted_moves:
        first_time_acc = sorted_moves[0][1]
        # Defer audio so robot reaches first pose before first beat
        _log(f"Audio delayed by {first_time_acc:.1f}s (first move time_acc)")

        time.sleep(first_time_acc)

    # Start face display thread (if DanceFace provided)
    # If genre is "lean", use alternating tilt/inverted-tilt image cycle
    tilt_state = None
    if dance_face:
        if genre == "lean":
            tilt_state = TiltState()
            tilt_thread = threading.Thread(
                target=tilt_display_poller,
                args=(tilt_state, DANCE_ACTIVE_FLAG),
                daemon=True
            )
            tilt_thread.start()
            _log("Tilt display poller started (genre=lean)")
        elif face_cues:
            try:
                dance_face.start(face_cues, audio_delay=0.0, stop_flag_path=DANCE_ACTIVE_FLAG)
                _log(f"Face display thread started: {len(face_cues)} cues")
            except Exception as e:
                _log(f"Face display start failed (non-fatal): {e}")


    # Start audio playback
    if wav_file:
        subprocess.run(
            ["pkill", "-f", f"ffplay.*{os.path.basename(wav_file)}"],
            capture_output=True, timeout=3
        )
        time.sleep(0.15)
        subprocess.run(
            ["pkill", "-f", f"ffplay.*{os.path.basename(wav_file)}"],
            capture_output=True, timeout=3
        )
    player = subprocess.Popen(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
         "-af", "volume=0.90", wav_file],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    with open(AUDIO_PID_FILE, "w") as f:
        f.write(str(player.pid))
    _log(f"Audio player PID: {player.pid}")

    # Start audio monitor thread
    monitor = threading.Thread(
        target=_audio_monitor,
        args=(player, DANCE_ACTIVE_FLAG),
        daemon=True,
        name="DanceAudioMonitor"
    )
    monitor.start()
    _log("Audio monitor started")

    # Execute timed moves

    # ── One-shot: build full MovementLib then execute ──
    full_lib = []
    moves_built = 0

    for i, move in enumerate(sorted_moves):
        cmd, time_acc, angle, start_time = move

        if not os.path.exists(DANCE_ACTIVE_FLAG):
            _log("Dance stopped during build - abort.")
            break

        # Stop when audio finishes playing
        if player.poll() is not None:
            _log(f"Audio ended at {start_time:.1f}s — stopping dance.")
            break

        # HF Space baked timing:
        # Each move = Entry(time_acc) + Movement(hold) = gap between start times
        # So hold = gap - time_acc. With fixed single-phase moves, this is accurate
        # to ~0.01s quantization error per move.
        if i + 1 < len(sorted_moves):
            gap = sorted_moves[i + 1][3] - start_time
            next_time_acc = sorted_moves[i + 1][1]
            hold = max(gap - next_time_acc, 0.05)
        else:
            hold = max(time_acc + 0.5, 0.05)

        try:
            lib = build_movement(cmd, hold, angle, time_acc)
            if lib:
                full_lib.extend(lib)
                moves_built += 1
        except Exception as e:
            _log(f"Move build error ({cmd}): {e}")

    if not full_lib:
        _log("No moves built — skipping execution")
        return {
            "ok": False,
            "error": "No moves were built",
        }

    # Get audio duration for timeout calculation
    audio_duration = 0.0
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", wav_file],
            capture_output=True, text=True, timeout=5
        )
        audio_duration = float(result.stdout.strip())
    except Exception:
        if sorted_moves:
            last = sorted_moves[-1]
            audio_duration = last[3] + last[1] + 1.0
        else:
            audio_duration = 30.0

    timeout = audio_duration + 10.0
    _log(f"One-shot: {moves_built} moves, {len(full_lib)} Movement objects, "
         f"audio={audio_duration:.0f}s, timeout={timeout:.0f}s")

    # Progress callback for periodic logging
    def _progress(move_idx, total, elapsed):
        pct_moves = move_idx / total * 100 if total > 0 else 0
        pct_time = elapsed / timeout * 100
        diff = pct_moves - pct_time
        _log(f"[{move_idx}/{total}] {elapsed:.1f}s — moves {pct_moves:.0f}% vs time {pct_time:.0f}% ({diff:+.0f}%)")

    # Execute all moves in a single run_movement call
    ok, last_state = run_movement(
        full_lib,
        timeout=timeout,
        initial_state=None,
        stop_flag_path=DANCE_ACTIVE_FLAG,
        progress_callback=_progress,
        tilt_state=tilt_state,
    )

    _log(f"Dance {'completed' if ok else 'stopped'} ({moves_built} moves)")

    # Wait for audio to finish naturally
    if player.poll() is None:
        try:
            player.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass

    return {
        "ok": ok,
        "message": f"Danced to '{title}' - {moves_built} moves at {bpm} BPM ({genre_display})",
        "title": title,
        "bpm": bpm,
        "moves_executed": moves_built,
        "genre": genre,
        "genre_display": genre_display,
        "source": "hf_space",
    }


def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_cache(cache: dict):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

def _detect_genre_from_url(url: str) -> dict:
    """Detect song genre from YouTube metadata (tags, channel, title keywords)."""
    _log(f'Detecting genre for: {url}')
    try:
        meta = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-playlist", url],
            capture_output=True, text=True, timeout=15,
        )
        if meta.returncode != 0:
            return {"genre": "pop", "genre_display": "Pop"}

        data = json.loads(meta.stdout)
        tags = [t.lower() for t in data.get("tags", []) or []]
        categories = [c.lower() for c in data.get("categories", []) or []]
        channel = (data.get("channel", "") or "").lower()
        title = (data.get("title", "") or "").lower()

        genre_keywords = {
            "rock": ["rock", "metal", "punk", "alternative", "grunge", "hard rock", "indie rock"],
            "classical": ["classical", "orchestra", "symphony", "piano", "violin", "mozart", "beethoven", "bach", "chopin"],
            "jazz": ["jazz", "blues", "bebop", "swing", "smooth jazz"],
            "electronic": ["electronic", "edm", "techno", "house", "dubstep", "trance", "dance", "remix"],
            "hiphop": ["hip hop", "hip-hop", "rap", "r&b", "rnb", "trap", "drill"],
            "disco": ["disco", "funk", "groove", "70s", "boogie"],
            "latin": ["latin", "salsa", "bachata", "reggaeton", "merengue", "cumbia", "samba", "rumba"],
            "reggae": ["reggae", "ska", "dub", "reggaeton"],
            "folk": ["folk", "acoustic", "indie", "singer songwriter", "banjo", "mandolin", "bluegrass"],
            "pop": ["pop", "k-pop", "j-pop", "chart", "mainstream"],
        }

        scores = {g: 0 for g in genre_keywords}
        for genre, keywords in genre_keywords.items():
            for kw in keywords:
                for tag in tags:
                    if kw in tag:
                        scores[genre] += 10
                for cat in categories:
                    if kw in cat:
                        scores[genre] += 8
                if kw in channel:
                    scores[genre] += 6
                if kw in title:
                    scores[genre] += 4

        best_genre = max(scores, key=scores.get)
        if scores[best_genre] == 0:
            best_genre = "pop"

        _log(f'Detected genre: {best_genre} (scores: {scores})')
        return {"genre": best_genre, "genre_display": GENRE_DISPLAY_NAMES.get(best_genre, "Pop")}
    except Exception as e:
        _log(f'Genre detection error: {e}')
        return {"genre": "pop", "genre_display": "Pop"}


def cmd_classify(url: str) -> dict:
    """Detect genre for a YouTube URL without dancing."""
    result = _detect_genre_from_url(url)
    return {
        "ok": True,
        "url": url,
        "genre": result.get("genre", "pop"),
        "genre_display": result.get("genre_display", "Pop"),
    }


def cmd_search(query: str) -> dict:
    """Search YouTube and return top matches."""
    _log(f"Searching: {query}")
    try:
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist", "-f", "bestaudio/best",
             "--default-search", "ytsearch1",
             "--print", "%(id)s\t%(title)s\t%(duration)s\t%(channel)s", query],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Search timed out after 30 seconds."}
    except FileNotFoundError:
        return {"ok": False, "error": "yt-dlp not found."}
    if result.returncode != 0:
        return {"ok": False, "error": f"Search failed: {result.stderr.strip()[:500]}"}
    results = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip() or "\t" not in line:
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            vid, title, dur = parts[0], parts[1], parts[2]
            channel = parts[3] if len(parts) > 3 else ""
            try:
                dur = int(dur)
            except ValueError:
                dur = 0
            results.append({
                "id": vid, "title": title, "channel": channel,
                "duration": dur,
                "url": f"https://youtube.com/watch?v={vid}",
            })
    if not results:
        return {"ok": False, "error": "No results found."}
    # Genre is determined by Gemini from song context during the dance step.
    # No yt-dlp metadata scrape needed - saves ~9s latency.
    if results:
        results[0]["genre"] = "unknown"
        results[0]["genre_display"] = "Unknown"
    return {"ok": True, "results": results, "count": len(results)}

def _generate_choreography_from_slots(beat_slots: list, genre: str, seed: str) -> list:
    """Convert Space timing slots into genre-appropriate timed moves.

    Each slot becomes one move from the genre pool, preserving the
    Space's time_acc and start_time. Uses deterministic seed RNG
    so the same song always gets the same dance.

    Args:
        beat_slots: list of {start_time, time_acc, local_bpm} from HF Space
        genre: canonical genre string (e.g. "classical", "rock")
        seed: deterministic seed (song URL or title)

    Returns:
        List of (cmd, time_acc, angle, start_time) tuples for enrich_choreography.
    """
    canonical_genre = _resolve_genre(genre)
    pool = GENRE_POOLS.get(canonical_genre, GENRE_POOLS["pop"])
    seed_int = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    rng = random.Random(seed_int)

    timed = []
    for slot in beat_slots:
        move = rng.choices(pool["moves"], weights=pool["weights"], k=1)[0]
        angle = _map_angle(move, slot.get("angle", 0) or 0)
        timed.append((move, slot["time_acc"], angle, slot["start_time"]))

    _log(f"Local choreography from slots: {len(timed)} moves, genre={canonical_genre}")
    return timed

DEBUG_FILE = "/tmp/minipupper_dance_debug.json"


def _log_debug_choreography(timed: list, genre: str, seed: str, seed_int: int,
                             beat_info: dict, pool: dict):
    """Print deterministic seed, genre pool, and every move for debugging."""
    canonical = _resolve_genre(genre)
    bpm = beat_info.get("bpm", "?")
    duration = beat_info.get("duration", "?")

    header = [
        "=" * 55,
        f"  Seed:       {seed}",
        f"  SHA-256:    {seed_int}",
        f"  Genre:      {genre} ({canonical})",
        f"  BPM:        {bpm}",
        f"  Duration:   {duration:.1f}s" if isinstance(duration, (int, float)) else f"  Duration:   {duration}",
        f"  Total moves:{len(timed)}",
        f"  Pool moves: {', '.join(pool.get('moves', []))}",
        f"  Weights:    {pool.get('weights', [])}",
        "=" * 55,
    ]
    # Pad the seed line if it's a URL to keep it readable
    print("\n".join(header))
    print(f"  {'Time':>8s}  {'Move':<20s}  {'Angle':>6s}  {'TimeAcc':>7s}")
    print("  " + "-" * 48)
    for cmd, time_acc, angle, start_time in timed:
        angle_str = f"{angle:.0f}°" if isinstance(angle, (int, float)) else str(angle)
        print(f"  {start_time:>7.2f}s  {cmd:<20s}  {angle_str:>6s}  {time_acc:<7.3f}")
    print("=" * 55)
    print()

    # Save full debug JSON
    try:
        debug_data = {
            "seed": seed,
            "seed_sha256_hex": hex(seed_int),
            "genre": genre,
            "canonical_genre": canonical,
            "bpm": bpm,
            "duration": duration,
            "pool_moves": pool.get("moves", []),
            "pool_weights": pool.get("weights", []),
            "moves": [
                {
                    "time": start_time,
                    "move": cmd,
                    "angle": angle,
                    "time_acc": time_acc,
                }
                for cmd, time_acc, angle, start_time in timed
            ],
        }
        with open(DEBUG_FILE, "w") as f:
            json.dump(debug_data, f, indent=2)
    except OSError:
        pass


def cmd_dance(url: str, genre_override: str = None, no_activate: bool = True, debug: bool = False) -> dict:
    """
    Download -> HF beat analysis -> launch background dance -> return.

    1. Download audio (same as dance_to_audio.py)
    2. Convert to WAV
    3. Send 5s segment to Bedrock510 HF Space for BPM
    4. Generate choreography
    5. Launch background subprocess for dance execution
    6. Return immediately
    """
    _log(f"HFDance requested: {url}")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(WAV_DIR, exist_ok=True)

    # Shotgun cleanup: kill any stray audio and previous dance processes
    subprocess.run(["pkill", "-9", "-f", "ffplay"], capture_output=True, timeout=5)
    subprocess.run(["pkill", "-9", "-f", "hf_dance_to_audio.py.*execute"], capture_output=True, timeout=5)

    # Stop any existing dance
    _stop_background_dance()
    _stop_audio()
    for f in [DANCE_ACTIVE_FLAG, DANCE_PID_FILE]:
        try:
            os.remove(f)
        except OSError:
            pass

    cache = _load_cache()
    audio_file = cache.get(url)
    if audio_file and os.path.exists(audio_file):
        _log(f"Using cached audio: {audio_file}")
    else:
        _log("Downloading audio...")
        try:
            dl = subprocess.run(
                ["yt-dlp", "-f", "bestaudio/best", "--extract-audio",
                 "--audio-format", "mp3", "--audio-quality", "0",
                 "-o", f"{DOWNLOAD_DIR}/%(title)s.%(ext)s", "--no-playlist",
                 "--print", "after_move:filename", url],
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Download timed out after 120 seconds."}
        if dl.returncode != 0:
            return {"ok": False, "error": f"Download failed: {dl.stderr.strip()[:500]}"}
        audio_file = None
        for line in dl.stdout.strip().split("\n"):
            line = line.strip()
            if line.endswith((".mp3", ".m4a")) and os.path.exists(line):
                audio_file = line
                break
        if not audio_file:
            mp3_files = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR)
                         if f.endswith(".mp3")]
            if mp3_files:
                audio_file = max(mp3_files, key=os.path.getmtime)
        if not audio_file or not os.path.exists(audio_file):
            return {"ok": False, "error": "Download completed but output file not found."}
        _log(f"Downloaded: {audio_file} ({os.path.getsize(audio_file)} bytes)")
        cache[url] = audio_file
        _save_cache(cache)

    title = os.path.splitext(os.path.basename(audio_file))[0]

    # Convert to WAV for analysis
    _log("Converting to WAV for analysis...")
    safe_title = title.replace("/", "_").replace(" ", "_")
    wav_path = os.path.join(WAV_DIR, f"{safe_title}.wav")
    if not os.path.exists(wav_path):
        try:
            ffmpeg = subprocess.run(
                ["ffmpeg", "-y", "-i", audio_file,
                 "-ac", "1", "-ar", "22050", "-f", "wav", wav_path],
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Audio conversion timed out."}
        if ffmpeg.returncode != 0:
            return {"ok": False, "error": f"Conversion failed: {ffmpeg.stderr.strip()[:300]}"}
    _log(f"WAV ready: {wav_path}")

    # Detect beats + timing via Hugging Face (no genre, no choreography)
    beat_info = _hf_detect_beats(wav_path)
    if "error" in beat_info:
        return beat_info
    beat_slots = beat_info.get("beat_slots")
    if not beat_slots:
        return {"ok": False, "error": "HF Space did not return beat_slots"}

    if genre_override:
        genre = genre_override
    else:
        detected = _detect_genre_from_url(url)
        genre = detected.get("genre", "pop")
    genre_display = GENRE_DISPLAY_NAMES.get(genre, f"\U0001f3a4 {genre.capitalize()}")

    # Generate genre-appropriate timed moves from Space timing slots
    timed = _generate_choreography_from_slots(beat_slots, genre, url or title)

    # Expand compound moves (dip, spin) into atomic sub-moves
    timed = enrich_choreography(timed, genre, url or title)
    _log(f"Final choreography: {len(timed)} moves, genre: {genre_display}")

    # Debug output
    if debug:
        pool = GENRE_POOLS.get(_resolve_genre(genre), GENRE_POOLS["pop"])
        seed_int = int(hashlib.sha256((url or title).encode()).hexdigest(), 16)
        _log_debug_choreography(timed, genre, url or title, seed_int,
                                beat_info, pool)

    # Generate face-display cues from actual choreography timestamps
    duration = beat_info.get("duration", 180.0)
    face_cues_dance = face_cues_from_choreography(timed, genre)
    _log(f"Face cues: {len(face_cues_dance)} display changes across {duration:.0f}s")

    # Save state for background process
    state = {
        "beat_info": beat_info,
        "wav_path": wav_path,
        "title": title,
        "timed_choreography": timed,
        "genre": genre,
        "genre_display": genre_display,
        "no_activate": no_activate,
        "face_cues": face_cues_dance,
    }
    with open(DANCE_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    # Write pre-start result so caller can mark task completed early
    # Robot hasn't moved yet — choreography is fully ready
    pre_result = {
        "status": "dancing",
        "ok": True,
        "message": f"Dancing to '{title}' ({genre_display})...",
        "title": title,
        "genre": genre,
        "genre_display": genre_display,
        "pid": None,
    }
    with open(DANCE_RESULT_FILE, "w") as f:
        json.dump(pre_result, f, indent=2)

    # Launch dance in background
    script_path = os.path.abspath(__file__)
    proc = subprocess.Popen(
        ["python3", script_path, "execute", DANCE_STATE_FILE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    with open(DANCE_PID_FILE, "w") as f:
        f.write(str(proc.pid))

    _log(f"Dance background PID: {proc.pid}")

    # Mark the task as processing so cron doesn't re-trigger (file-per-task)
    try:
        pending_dir = os.path.join("/home/ubuntu/openclaw-app/tasks", "pending")
        active_dir = os.path.join("/home/ubuntu/openclaw-app/tasks", "active")
        if os.path.isdir(pending_dir):
            for fname in sorted(os.listdir(pending_dir)):
                if not fname.endswith(".json"):
                    continue
                fp = os.path.join(pending_dir, fname)
                with open(fp) as f:
                    t = json.load(f)
                if t.get("status") == "pending" and t.get("action", "").startswith("robot.dance"):
                    t["status"] = "processing"
                    t["phase"] = "dancing"
                    t["progress"] = 50
                    t["message"] = f"Dancing to '{title}' ({genre_display})..."
                    t["result"] = {
                        "summary": f"Dancing to {title}",
                        "title": title,
                        "genre": genre,
                        "genre_display": genre_display,
                    }
                    t["updatedAt"] = time.time()
                    os.makedirs(active_dir, exist_ok=True)
                    with open(os.path.join(active_dir, fname), "w") as f:
                        json.dump(t, f, indent=2)
                    os.remove(fp)
                    _log(f"Marked task {fname} as processing")
                    break
    except Exception as e:
        _log(f"Failed to mark task processing: {e}")

    return {
        "ok": True,
        "message": f"Dancing to '{title}' ({genre_display})...",
        "title": title,
        "genre": genre,
        "genre_display": genre_display,
        "pid": proc.pid,
        "bpm": beat_info["bpm"],
        "source": "hf_space",
    }

def cmd_execute(state_path: str) -> dict:
    """
    Internal: run the choreography in a subprocess.

    Loads state, activates robot, dances, deactivates, writes result.
    Checks DANCE_ACTIVE_FLAG throughout for immediate stop.
    """
    _log(f"Executing dance from state: {state_path}")
    with open(state_path) as f:
        state = json.load(f)

    beat_info = state["beat_info"]
    wav_path = state["wav_path"]
    title = state["title"]
    timed_choreo = [tuple(m) for m in state.get("timed_choreography", [])]
    genre = state.get("genre", "unknown")
    genre_display = state.get("genre_display", "Generic")
    no_activate = state.get("no_activate", True)

    # Initialize robot
    try:
        build_movement, run_movement = _init_robot()
    except Exception as e:
        result = {"ok": False, "error": f"Robot init failed: {e}"}
        with open(DANCE_RESULT_FILE, "w") as f:
            json.dump(result, f, indent=2)
        return result

    # Init face display
    face_cues = state.get("face_cues", [])
    dance_face = None
    if face_cues:
        try:
            dance_face = DanceFace()
            _log(f"Face display ready: {len(face_cues)} cues")
        except Exception as e:
            _log(f"Face init failed (non-fatal): {e}")
            dance_face = None

    # Dance!
    try:
        result = _choreography_loop(build_movement, run_movement,
                                     beat_info, timed_choreo, wav_path, title,
                                     genre=genre, genre_display=genre_display, dance_face=dance_face, face_cues=face_cues)
    except Exception as e:
        result = {"ok": False, "error": f"Dance loop crashed: {e}"}
        _log(f"Dance loop error: {e}")

    result["status"] = "completed"
    # Deactivate robot (only if we activated it)
    if not no_activate:
        try:
            _log("Deactivating robot...")
            lib = build_movement("deactivate", 0.5, None)
            run_movement(lib, timeout=5.0)
            _log("Robot deactivated.")
        except Exception as e:
            _log(f"Deactivation error: {e}")

    # Cleanup face display
    if dance_face:
        dance_face.stop()
        dance_face.show_now(0)  # REST after dance
        _log("Face display stopped.")

    # Write result
    with open(DANCE_RESULT_FILE, "w") as f:
        json.dump(result, f, indent=2)
    _log(f"Result written: {result.get('message', '?')}")

    # Update task to completed (file-per-task, move to completed dir)
    try:
        active_dir = os.path.join("/home/ubuntu/openclaw-app/tasks", "active")
        completed_dir = os.path.join("/home/ubuntu/openclaw-app/tasks", "completed")
        if os.path.isdir(active_dir):
            for fname in sorted(os.listdir(active_dir)):
                if not fname.endswith(".json"):
                    continue
                fp = os.path.join(active_dir, fname)
                with open(fp) as f:
                    t = json.load(f)
                if t.get("status") == "processing" and t.get("action", "").startswith("robot.dance"):
                    tid = t.get("taskId", "?")
                    t["status"] = "completed"
                    t["phase"] = "done"
                    t["progress"] = 100
                    t["message"] = result.get("message", "Dance completed")
                    t["result"] = result
                    t["updatedAt"] = time.time()
                    os.makedirs(completed_dir, exist_ok=True)
                    with open(os.path.join(completed_dir, fname), "w") as f:
                        json.dump(t, f, indent=2)
                    os.remove(fp)
                    _log(f"Updated task {tid[:8]} to completed")
                    break
    except Exception as e:
        _log(f"Failed to update task to completed: {e}")

    # Clean up flag files
    for f in [DANCE_ACTIVE_FLAG, DANCE_PID_FILE]:
        try:
            os.remove(f)
        except OSError:
            pass

    # Clear music active flag
    _set_music_active(False)

    return result

def _stop_background_dance():
    """Kill the dance background subprocess immediately."""
    pid = _pid_from_file(DANCE_PID_FILE)
    if pid is not None:
        _log(f"Killing dance process PID {pid}")
        _graceful_kill(pid, "dance process")

def cmd_stop() -> dict:
    """Stop dancing and audio playback immediately."""
    _log("Stop requested.")

    # FIRST: Remove the active flag — choreography loop checks this
    # on every iteration for immediate abort
    # Kill any stray audio processes (even untracked orphans)
    subprocess.run(["pkill", "-9", "-f", "ffplay"], capture_output=True, timeout=5)
    subprocess.run(["pkill", "-9", "-f", "mpg321"], capture_output=True, timeout=5)

    # Clean up any processing dance task (file-per-task, move to completed)
    try:
        active_dir = os.path.join("/home/ubuntu/openclaw-app/tasks", "active")
        completed_dir = os.path.join("/home/ubuntu/openclaw-app/tasks", "completed")
        if os.path.isdir(active_dir):
            for fname in sorted(os.listdir(active_dir)):
                if not fname.endswith(".json"):
                    continue
                fp = os.path.join(active_dir, fname)
                with open(fp) as f:
                    t = json.load(f)
                if t.get("status") == "processing" and t.get("action", "").startswith("robot.dance"):
                    tid = t.get("taskId", "?")
                    t["status"] = "stopped"
                    t["phase"] = "stopped"
                    t["progress"] = 100
                    t["message"] = "Dance stopped by user"
                    t["updatedAt"] = time.time()
                    os.makedirs(completed_dir, exist_ok=True)
                    with open(os.path.join(completed_dir, fname), "w") as f:
                        json.dump(t, f, indent=2)
                    os.remove(fp)
                    _log(f"Cleaned up processing dance task: {tid[:8]}")
    except Exception as e:
        _log(f"Cleanup error: {e}")

    # Deactivation handled by cmd_execute background process
    _log("Deactivating robot...")

    # THEN: Kill the background process
    _stop_background_dance()

    # THEN: Stop audio
    _stop_audio()

    # Clean up remaining flags
    for f in [DANCE_PID_FILE, DANCE_ACTIVE_FLAG]:
        try:
            os.remove(f)
        except OSError:
            pass

    _set_music_active(False)

    _log("Dance stopped.")
    return {"ok": True, "message": "Dance stopped."}

def cmd_status() -> dict:
    """Check dance status."""
    dance_pid = _pid_from_file(DANCE_PID_FILE)
    audio_pid = _pid_from_file(AUDIO_PID_FILE)
    flag_active = os.path.exists(DANCE_ACTIVE_FLAG)
    result_exists = os.path.exists(DANCE_RESULT_FILE)
    return {
        "ok": True,
        "dancing": dance_pid is not None,
        "dance_pid": dance_pid,
        "audio_pid": audio_pid,
        "flag_active": flag_active,
        "result_ready": result_exists,
    }

# -- CLI ---------------------------------------------------------------------


def cmd_process_task(task_file: str) -> dict:
    """Read a task JSON file and execute the dance with exact params.

    This bypasses any LLM-side genre re-classification — reads params.genre
    directly from the task file and passes it as genre_override to cmd_dance().
    """
    try:
        with open(task_file) as f:
            task = json.load(f)
    except (json.JSONDecodeError, OSError, IOError) as e:
        return {"ok": False, "error": f"Cannot read task file: {e}"}

    url = task.get("params", {}).get("url", "")
    if not url:
        return {"ok": False, "error": "No url in task params"}

    genre = task.get("params", {}).get("genre")
    no_activate = bool(task.get("params", {}).get("no_activate", False))

    _log(f"Processing task: url={url}, genre_override={genre}, no_activate={no_activate}")
    return cmd_dance(url, genre_override=genre, no_activate=no_activate)


def main():
    parser = argparse.ArgumentParser(description="Mini Pupper Dance Machine (HF-powered)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_search = subparsers.add_parser("search", help="Search YouTube")
    p_search.add_argument("query", nargs="+")

    p_classify = subparsers.add_parser("classify", help="Test genre detection for a URL without dancing")
    p_classify.add_argument("url", help="YouTube URL")

    p_dance = subparsers.add_parser("dance", help="Download audio and dance via HF (background)")
    p_dance.add_argument("url", help="YouTube URL")
    p_dance.add_argument("--genre", "-g", default=None,
                        help="Genre override (rock, classical, pop, jazz, electronic, hiphop, chill)")
    p_dance.add_argument("--no-activate", action="store_true",
                        help="Skip robot activation (assume already standing)")
    p_dance.add_argument("--debug", "-d", action="store_true",
                        help="Print seed, genre pool, and every move for debugging")

    p_exec = subparsers.add_parser("execute", help="Internal: run choreography")
    p_exec.add_argument("state_file", help="Dance state JSON path")

    subparsers.add_parser("stop", help="Stop dancing")

    p_task = subparsers.add_parser("process-task", help="Process a task JSON file directly (bypasses LLM genre guesswork)")
    p_task.add_argument("task_file", help="Path to task JSON file")

    subparsers.add_parser("status", help="Check dance status")

    args = parser.parse_args()

    if args.command == "search":
        result = cmd_search(" ".join(args.query))
    elif args.command == "classify":
        result = cmd_classify(args.url)
    elif args.command == "dance":
        result = cmd_dance(args.url, genre_override=args.genre, no_activate=args.no_activate, debug=args.debug)
    elif args.command == "process-task":
        result = cmd_process_task(args.task_file)
    elif args.command == "execute":
        result = cmd_execute(args.state_file)
    elif args.command == "stop":
        result = cmd_stop()
    elif args.command == "status":
        result = cmd_status()
    else:
        result = {"ok": False, "error": f"Unknown: {args.command}"}

    print(json.dumps(result, indent=2))
    try:
        with open(DANCE_RESULT_FILE, "w") as f:
            json.dump(result, f, indent=2)
    except OSError:
        pass

    sys.exit(0 if result.get("ok") else 1)

if __name__ == "__main__":
    main()