"""
dance_face.py — Mini Pupper LCD Face Display for Dance Choreography

Cycles through REST → TROT → HOP → FINISHHOP faces on the robot's
LCD display, synced to the song's BPM and genre.

    from dance_face import face_cues_from_choreography, DanceFace

    cues = face_cues_from_choreography(timed_moves, genre="pop")
    df = DanceFace()
    df.start(cues, stop_flag_path="/tmp/minipupper_dance_active")
    # ... dance loop runs ...
    df.stop()
    # ... dance loop runs ...
    df.stop()
"""

import os
import time
import threading
from enum import Enum
from MangDang.mini_pupper.display import Display

# Lean choreo image directory (relative to this file)
LEAN_IMG_DIR = os.path.dirname(os.path.abspath(__file__))


def _show_on_lcd(img_path: str, rotate_deg: float = 0.0):
    """Display a static image on the ST7789 LCD, resized, centered, and optionally rotated.

    Args:
        img_path: Path to the image file.
        rotate_deg: Rotation angle in degrees (positive = CW, negative = CCW).
                    0 = no rotation (original behavior preserved).
    """
    from PIL import Image
    try:
        d = Display()
        w, h = d.disp.width, d.disp.height
        img = Image.open(img_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        thumb = img.copy()
        thumb.thumbnail((w, h), Image.LANCZOS)
        thumb = thumb.transpose(Image.FLIP_LEFT_RIGHT)

        if abs(rotate_deg) > 0.5:
            # Build a square canvas large enough so rotation doesn't clip.
            CANVAS_SIZE = int(((w ** 2 + h ** 2) ** 0.5) * 1.2)
            canvas = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0))
            offset = ((CANVAS_SIZE - thumb.width) // 2,
                      (CANVAS_SIZE - thumb.height) // 2)
            canvas.paste(thumb, offset)
            cx = cy = CANVAS_SIZE // 2
            rotated = canvas.rotate(-rotate_deg, resample=Image.BICUBIC,
                                    center=(cx, cy), fillcolor=(0, 0, 0))
            bg = rotated.crop((cx - w // 2, cy - h // 2,
                               cx + w // 2, cy + h // 2))
        else:
            bg = Image.new("RGB", (w, h), (0, 0, 0))
            offset = ((w - thumb.width) // 2, (h - thumb.height) // 2)
            bg.paste(thumb, offset)

        tmp = "/tmp/minipupper_lean_face.png"
        bg.save(tmp)
        d.show_image(tmp)
    except Exception:
        pass



class TiltState:
    """Thread-safe shared roll angle — written by control loop, read by display thread."""
    def __init__(self):
        self.roll_deg = 0.0


LEAN_IMG_NORMAL = "display/dog_straight face-bgrmv.png"


def tilt_display_poller(tilt_state, stop_flag_path):
    """Poll tilt_state roll and update LCD with smooth rotation.

    Pre-processes the base image once at startup (resize to a safe square
    canvas). On each poll cycle, rotates the image by the current roll
    angle and center-crops back to display size, so the puppy image
    smoothly follows the robot's body lean.

    Reuses a single Display() — no file I/O per frame (direct SPI).
    Polls at ~12 Hz for smooth tracking.
    """
    from PIL import Image
    d = Display()
    w, h = d.disp.width, d.disp.height  # typically 320 x 240

    # Load single base image (no more inverted flip)
    base_path = os.path.join(LEAN_IMG_DIR, LEAN_IMG_NORMAL)
    if not os.path.exists(base_path):
        return

    img = Image.open(base_path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    thumb = img.copy()
    thumb.thumbnail((w, h), Image.LANCZOS)
    thumb = thumb.transpose(Image.FLIP_LEFT_RIGHT)

    # Build a square canvas large enough so rotation never clips the viewport.
    CANVAS_SIZE = int(((w ** 2 + h ** 2) ** 0.5) * 1.2)  # ~480px
    base_canvas = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0))
    ox, oy = (CANVAS_SIZE - thumb.width) // 2, (CANVAS_SIZE - thumb.height) // 2
    base_canvas.paste(thumb, (ox, oy))

    cx = cy = CANVAS_SIZE // 2
    half_w, half_h = w // 2, h // 2

    last_angle = None
    while os.path.exists(stop_flag_path):
        roll = tilt_state.roll_deg

        # Small deadzone to avoid jitter at neutral
        angle = 0.0 if abs(roll) < 1.0 else float(roll)

        # Only update when angle changes by more than 0.5 deg
        if last_angle is not None and abs(angle - last_angle) < 0.5:
            time.sleep(0.08)
            continue

        # Rotate the entire canvas (image stays centered, corners stay black)
        rotated = base_canvas.rotate(-angle, resample=Image.BICUBIC,
                                     center=(cx, cy), fillcolor=(0, 0, 0))
        # Center-crop back to display size
        crop = rotated.crop((cx - half_w, cy - half_h,
                             cx + half_w, cy + half_h))
        try:
            d.disp.display(crop)
        except Exception:
            pass
        last_angle = angle
        time.sleep(0.08)




class BehaviorState(Enum):
    DEACTIVATED = -1
    REST = 0
    TROT = 1
    HOP = 2
    FINISHHOP = 3
    SHUTDOWN = 96
    IP = 97
    TEST = 98
    LOWBATTERY = 99


# The face cycle: calm -> moving -> leap -> landing -> repeat
FACE_CYCLE = [
    BehaviorState.REST,       # calm
    BehaviorState.TROT,       # walking
    BehaviorState.HOP,        # leaping!
    BehaviorState.FINISHHOP,  # landing
]

# Genre -> beats per face change (higher = slower / more relaxed)
GENRE_PACING = {
    "classical":   8,   # slow, graceful
    "jazz":        6,   # smooth
    "chill":       8,   # relaxed
    "reggae":      6,   # laid-back
    "folk":        6,   # unhurried
    "pop":         4,   # energetic 4-beat
    "hiphop":      4,   # bouncy
    "country":     4,   # cheerful
    "disco":       4,   # groovy
    "latin":       3,   # faster 3-beat
    "rock":        2,   # aggressive 2-beat
    "electronic":  2,   # rapid-fire
}
DEFAULT_PACING = 4


def _resolve_state(val: float) -> BehaviorState:
    """Convert a stored float back to a BehaviorState enum member."""
    try:
        return BehaviorState(int(val))
    except (ValueError, TypeError):
        return BehaviorState.REST


class DanceFace:
    """
    Manages the Mini Pupper LCD display during a dance session.

    Runs face changes in a lightweight daemon thread that sleeps until
    each cue's timestamp, then calls Display.show_state().

    The thread checks stop_flag_path on every sleep cycle so it dies
    cleanly when cmd_stop() is invoked.
    """

    def __init__(self):
        self.disp = Display()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -----------------------------------------------------------------
    def start(
        self,
        cues: list,
        audio_delay: float = 0.0,
        stop_flag_path: str = "/tmp/minipupper_dance_active",
    ) -> None:
        """
        Start the face-display thread.

        Args:
            cues: (cmd, time_acc, angle, start_time) tuples from
                  face_cues_from_choreography(), OR simpler (state_val, time) pairs.
            audio_delay: Seconds to offset all cue times by.
            stop_flag_path: Thread stops if this file disappears.
        """
        if not cues:
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(cues, audio_delay, stop_flag_path),
            daemon=True,
        )
        self._thread.start()

    # -----------------------------------------------------------------
    def stop(self) -> None:
        """Signal the face thread to stop and wait up to 2s."""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # -----------------------------------------------------------------
    def show_now(self, state_val: int) -> None:
        """Immediately show a face (useful for post-dance)."""
        try:
            self.disp.show_state(_resolve_state(state_val))
        except Exception:
            pass

    # -----------------------------------------------------------------
    #  Internal
    # -----------------------------------------------------------------
    def _run(self, cues: list, audio_delay: float, stop_flag_path: str) -> None:
        t0 = time.time()

        # Normalise to (cmd_or_val, start_time) pairs
        # Supports face cues (cmd="display", angle=state_val) and
        # image cues (cmd="image", angle=image_path)
        pairs = []
        for c in cues:
            if len(c) == 2:
                pairs.append((c[0], c[1]))
            elif len(c) >= 4:
                if isinstance(c[0], str) and c[0] == "image":
                    pairs.append((c[2], c[3]))  # (image_path, start_time)
                else:
                    pairs.append((int(c[2]), c[3]))  # (state_val, start_time)
            else:
                continue

        if not pairs:
            return

        for val_or_path, cue_time in pairs:
            if self._stop.is_set() or not os.path.exists(stop_flag_path):
                break

            elapsed = time.time() - t0
            remaining = audio_delay + cue_time - elapsed

            if remaining > 0:
                while remaining > 0 and not self._stop.is_set() and os.path.exists(stop_flag_path):
                    chunk = min(remaining, 0.5)
                    time.sleep(chunk)
                    remaining -= chunk

            if self._stop.is_set() or not os.path.exists(stop_flag_path):
                break

            try:
                if isinstance(val_or_path, int):
                    self.disp.show_state(_resolve_state(val_or_path))
                elif isinstance(val_or_path, dict):
                    # Dict cue: {"path": ..., "angle": ...} for rotated images
                    img_path = val_or_path.get("path", "")
                    angle = val_or_path.get("angle", 0.0)
                    full_path = os.path.join(LEAN_IMG_DIR, img_path) if not os.path.isabs(img_path) else img_path
                    if os.path.exists(full_path):
                        _show_on_lcd(full_path, rotate_deg=float(angle))
                else:
                    # Image cue: val_or_path is a file path (string)
                    full_path = os.path.join(LEAN_IMG_DIR, val_or_path) if not os.path.isabs(val_or_path) else val_or_path
                    if os.path.exists(full_path):
                        _show_on_lcd(full_path)
            except Exception:
                pass


def face_cues_from_choreography(timed_moves: list, genre: str = "pop") -> list:
    """
    Generate face-change cues from the actual choreography timestamps.

    Instead of synthesizing timestamps from BPM, this uses the start_time
    of every Nth entry in the timed choreography. This naturally tracks
    variable tempo because it follows whatever timing the HF Space baked in.

    Args:
        timed_moves: List of (cmd, duration, angle, start_time) tuples.
        genre: Genre string for pacing selection.

    Returns:
        List of (cmd, time_acc, angle, start_time) tuples in the same
        format as face_cues_from_choreography().
    """
    if not timed_moves or not isinstance(timed_moves, list):
        return []

    pacing = GENRE_PACING.get(genre.lower(), DEFAULT_PACING)
    cycle_len = len(FACE_CYCLE)

    cues = []
    for i, entry in enumerate(timed_moves):
        if len(entry) < 4:
            continue
        if i % pacing == 0:
            face_idx = (i // pacing) % cycle_len
            state_val = float(FACE_CYCLE[face_idx].value)
            # Use the choreography entry's actual start_time
            cues.append(("display", 0.0, state_val, entry[3]))

    return cues
