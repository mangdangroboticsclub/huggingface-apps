"""
local_choreography.py — Local choreography generator for Mini Pupper Dance

Replaces HF Space's basic commands with richer, genre-appropriate moves
generated locally. Uses the song URL as a deterministic seed so the same
song always gets the same dance.

pip install yt-dlpImport chain:
    hf_dance_to_audio.py  →  local_choreography.enrich_choreography()
                                  ↓
                          robot_control.py._build_movement()
"""

import hashlib
import random


# ═══════════════════════════════════════════════════════════════════
#  Genre Move Pools
#  Each genre has 15-20 moves with probability weights.
#  Higher weight = more likely to be picked by the seeded RNG.
# ═══════════════════════════════════════════════════════════════════

GENRE_POOLS = {
    # ═══════════════════════════════════════════════════════════════
    #  Redistributed weights (2026-06-12)
    #  Goal: every move gets ≥4-5% chance. Top moves still define
    #  the genre vibe but no longer dominate (>16% max).
    #  Tail moves raised to ~4-6% so they actually appear in dances.
    # ═══════════════════════════════════════════════════════════════
    "rock": {
        # Vibe: aggressive headbanging, swagger, rear energy
        # Signature: headbang (still #1), dip (headbanger body roll), spin (mosh pit)
        "moves": [
            # "headbang", "bounce", "swagger",
            # "dip", "spin", "butt_shrug",
            # "nod", "backleg_lift", "twerk",
            # "wiggle",
            "headbang", "bounce", "dip",
        ],
        "weights": [
            0.35, 0.35, 0.3,
        ],
    },
    "classical": {
        # Vibe: graceful bowing, elegant swagger, ballet-like leg lifts
        # Signature: greet (bow), swagger (graceful sway)
        "moves": [
            "greet", "squat", "body_ellipse",
            
        ],
        "weights": [
            0.4, 0.35, 0.25,           
        ],
    },
    "pop": {
        # Vibe: energetic, varied, body waves, twerks, sassy
        # Signature: sig:pop (body wave), twerk, wiggle
        "moves": [
            # "backleg_lift", "bounce", "body_ellipse"
            # "look_up", "look_down", "body_row", "backleg_lift", "twerk", "butt_shrug",
            # "butt_shrug", "wiggle", "swagger"
            "twerk", "swagger", "wiggle", 
            # "lean"
            # "step_move"
        ],
        "weights": [
            # 0.14, 0.11, 0.10, 0.09,
            # 0.09, 0.08, 0.08, 0.07, 0.06,
            # 0.06, 0.05, 0.07, 
            # 0.5, 0.5, # All weight on wiggle for testing
            0.29, 0.36, 0.35
            # 1
            
        ],
    },
    "lean": {
        # Vibe: energetic, varied, body waves, twerks, sassy
        # Signature: sig:pop (body wave), twerk, wiggle
        "moves": [
            # "backleg_lift", "bounce", "body_ellipse"
            # "look_up", "look_down", "body_row", "backleg_lift", "twerk", "butt_shrug",
            # "butt_shrug", "wiggle", "swagger"
            # "twerk", "swagger", "wiggle", "step_move"
            "lean"
        ],
        "weights": [
            # 0.14, 0.11, 0.10, 0.09,
            # 0.09, 0.08, 0.08, 0.07, 0.06,
            # 0.06, 0.05, 0.07, 
            # 0.5, 0.5, # All weight on wiggle for testing
           # 0.18, 0.3, 0.3, 0.22
           1
            
        ],
    },
    "hiphop": {
        # Vibe: groovy shoulder shrugs, bouncy, funky body rolls
        # Signature: shoulder_shrug (the John Travolta move)
        "moves": [
            "front_kick", "backleg_lift", "nod",
        ],
        "weights": [
            0.3, 0.3, 0.4
        ],
    },
    "disco": {
        # Vibe: bouncy head moves, assertive twerks, swagger
        # Signature: disco2 (assertive head pattern), twerk, wiggle
        "moves": [
            "shoulder_shrug", "head_ellipse", "look_right", "look_upperright"
        ],
        "weights": [
            0.35, 0.15, 0.25, 0.25
        ],
    },
    "electronic": {
        # Vibe: fast rhythmic head patterns, body rolls, repetitive
        # Signature: disco3 (rapid quadrant scan), body_row, backleg_lift
        "moves": [
            "seek", "look_up", "right_wiggle",
            ],
        "weights": [
            0.45, 0.35, 0.2
        ],
    },
    "jazz": {
        # Vibe: smooth squats, cool nods, relaxed leans
        # Signature: squat (jazz crouch), nod (cool jazz nod)
        "moves": [
            "look_upperleft", "right_shoulder_shrug", "look_down"
        ],
        "weights": [
           0.35, 0.35, 0.3
        ],
    },
    "latin": {
        # Vibe: hip wiggles, rear action, passionate kicks
        # Signature: front_kick (rearing kick), wiggle, butt_shrug
        "moves": [
            "butt_shrug", "body_row", "left_wiggle"
        ],
        "weights": [
            0.4, 0.35, 0.25
        ],
    },
    "reggae": {
        # Vibe: laid-back elevation, chill nods, gentle rocks
        # Signature: raise-body (elevated chill), nod, bounce
        "moves": [
            "left_butt_shrug", "look_lowerleft", "raise-body", "right_butt_shrug"
        ],
        "weights": [
            0.25, 0.25, 0.25, 0.25]
    },
    "folk": {
        # Vibe: organic scanning, gentle bounces, earthy nods
        # Signature: seek (looking around at nature), bounce, nod
        "moves": [
            "look_lowerright", "left_shoulder_shrug", "look_left",
        ],
        "weights": [
            0.3, 0.35, 0.35 
        ],
    },
    "complete": {
        # Vibe: organic scanning, gentle bounces, earthy nods
        # Signature: seek (looking around at nature), bounce, nod
        "moves": [
            "backleg_lift", "body_ellipse", "bounce", "butt_shrug", "dip",
            "front_kick", "greet", "head_ellipse", "look_lowerleft", "look_lowerright",
            "look_upperleft", "look_upperright", "shoulder_shrug", "twerk", "wiggle",
            "swagger"        ],
        "weights": [
            0.050, 0.052, 0.054, 0.056, 0.058,
            0.060, 0.062, 0.064, 0.066, 0.068,
            0.070, 0.072, 0.074, 0.076, 0.078,
            0.040
        ],
    },
}

# ── Compound Move Registry ─────────────────────────────────────
# Moves that expand into multiple atomic sub-moves across consecutive slots.
# Each entry: (atomic_command, direct_angle)
# Angles are in degrees / height units, passed directly to _build_movement,
# bypassing _map_angle (which maps the Space's 0-100 abstract values).
# Only include moves whose sub-moves are all atomic robot_control.py commands.
COMPOUND_EXPANSIONS = {
    "dip": [
        ("lower-body", 0),      # sink body down
        ("look-down", -15),     # lower gaze
        ("body-row", -15),      # tilt body left
        ("stop", 0),            # hold pose
        ("look-up", 0),         # return gaze
        ("body-row", 0),        # level body
        ("raise-body", 2),      # return height
        ("stop", 0),            # settle
    ],
    "spin": [
        ("rotate_cw", 180),     # spin 180 CW
        ("stop", 0),            # pause at apex
        ("rotate_ccw", 90),     # half spin back
        ("stop", 0),            # settle
    ],
}

# Genre aliases for HF Space / user input normalization
GENRE_ALIASES = {
    "hip-hop": "hiphop",
    "hiphop": "hiphop",
    "reggaeton": "latin",
    "salsa": "latin",
    "tango": "latin",
    "blues": "jazz",
    "r&b": "jazz",
    "soul": "jazz",
    "metal": "rock",
    "punk": "rock",
    "alternative": "rock",
    "edm": "electronic",
    "techno": "electronic",
    "house": "electronic",
    "trance": "electronic",
    "dubstep": "electronic",
    "k-pop": "pop",
    "j-pop": "pop",
    "showtunes": "pop",
    "bluegrass": "country",
    "indie": "folk",
    "acoustic": "folk",
}

# Moves that use the angle parameter
# Each entry defines the type of angle and a sensible default
ANGLE_MOVES = {
    "rotate_cw":     {"type": "rotation", "default": 30},
    "rotate_ccw":    {"type": "rotation", "default": 30},
    "body-row":      {"type": "roll",     "default": 10},
    "swagger":       {"type": "roll",     "default": 10},
    "look-up":       {"type": "pitch",    "default": 20},
    "look-down":     {"type": "pitch",    "default": 20},
    "look-right":    {"type": "yaw",      "default": 30},
    "look-left":     {"type": "yaw",      "default": 30},
    "spin":          {"type": "rotation", "default": 180},
    "lean":          {"type": "roll",     "default": 15},
    "raise-body":    {"type": "height",   "default": 10},
    "lower-body":    {"type": "height",   "default": 10},
    "squat":         {"type": "height",   "default": 15},
    "right":         {"type": "strafe",   "default": 10},
    "left":          {"type": "strafe",   "default": 10},
}


def _resolve_genre(raw_genre: str) -> str:
    """Resolve genre aliases to canonical genre names."""
    genre = raw_genre.strip().lower()
    if genre in GENRE_POOLS:
        return genre
    return GENRE_ALIASES.get(genre, "pop")


def _map_angle(move_name: str, space_angle: float) -> float:
    """Map the Space's abstract angle to what this move expects, or return None."""
    if move_name not in ANGLE_MOVES:
        return None
    info = ANGLE_MOVES[move_name]
    angle = space_angle if (space_angle and space_angle != 0) else info["default"]

    # Scale per move type
    if info["type"] == "rotation":
        return max(10, min(360, angle * 3))
    elif info["type"] == "roll":
        return max(5, min(30, angle))
    elif info["type"] == "pitch":
        return max(5, min(30, angle))
    elif info["type"] == "yaw":
        return max(5, min(45, angle))
    elif info["type"] == "height":
        return max(5, min(30, angle * 2))
    elif info["type"] == "strafe":
        return max(5, min(30, angle))
    return angle


def _expand_compounds(choreo: list, seed: str) -> list:
    """
    Expand compound moves into atomic sub-moves across consecutive slots.

    For each compound move found, replaces it with its N sub-moves
    (spaced at time_acc intervals). All subsequent entries are shifted
    right by (N - 1) * time_acc. Entries past the original song-end
    ceiling are truncated.

    Args:
        choreo: List of (cmd, time_acc, angle, start_time) from RNG pass.
        seed: Song seed string (for logging only).

    Returns:
        Expanded list with compound moves decomposed.
    """
    if not choreo:
        return choreo

    # Song-end ceiling: use the last entry's end time
    last_end = choreo[-1][3] + choreo[-1][1]

    expanded = []
    shift = 0.0  # cumulative time shift for entries after a compound

    for cmd, time_acc, angle, start_time in choreo:
        # Guard against degenerate time_acc
        slot_dur = max(time_acc, 0.1)

        adjusted_start = start_time + shift

        if cmd in COMPOUND_EXPANSIONS:
            sub_moves = COMPOUND_EXPANSIONS[cmd]
            n = len(sub_moves)
            # Emit sub-moves spaced at slot_dur intervals
            for j, (sub_cmd, fixed_angle) in enumerate(sub_moves):
                sub_start = adjusted_start + j * slot_dur
                expanded.append((sub_cmd, slot_dur, fixed_angle, sub_start))
            # Shift all subsequent entries right by (n - 1) slots
            shift += (n - 1) * slot_dur
        else:
            expanded.append((cmd, slot_dur, angle, adjusted_start))

    # Truncate entries that now start past the song-end ceiling
    expanded = [e for e in expanded if e[3] < last_end]

    return expanded


def enrich_choreography(
    hf_timed: list,
    genre: str,
    song_seed: str,
) -> list:
    """Replace HF Space commands with seed-based genre-appropriate moves.

    Pass 1: Genre-aware replacement (1:1 slot mapping).
    Pass 2: Expand compound moves into atomic sub-moves, shift remainder.
    Pass 3: Truncate entries past song-end ceiling.

    Args:
        hf_timed: List of (cmd, time_acc, angle, start_time) from HF Space.
        genre: Detected genre string.
        song_seed: Deterministic seed string (song URL or title).

    Returns:
        List of (cmd, time_acc, angle, start_time) with replaced commands.
    """
    if not hf_timed:
        return hf_timed

    # Resolve genre
    canonical_genre = _resolve_genre(genre)
    pool = GENRE_POOLS.get(canonical_genre, GENRE_POOLS["pop"])

    # Create deterministic seed from song identifier
    seed_int = int(hashlib.sha256(song_seed.encode()).hexdigest(), 16)
    rng = random.Random(seed_int)

    # ── Pass 1: genre-aware replacement (1:1) ──
    raw_choreo = []
    for entry in hf_timed:
        if len(entry) < 4:
            raw_choreo.append(entry)
            continue

        cmd, time_acc, angle, start_time = entry[:4]

        # Pick a move from the genre pool
        move = rng.choices(pool["moves"], weights=pool["weights"], k=1)[0]

        # Map the angle for this move
        move_angle = _map_angle(move, angle)

        # Negate angle for CCW rotation / left strafe
        if move in ("rotate_ccw", "left") and move_angle is not None:
            move_angle = -move_angle

        raw_choreo.append((move, time_acc, move_angle, start_time))

    # ── Pass 2: expand compounds, shift remainder ──
    expanded = _expand_compounds(raw_choreo, song_seed)

    # Log via injected logger if available
    _log_fn = getattr(enrich_choreography, "_log", None)
    if _log_fn:
        _log_fn(
            f"Local choreography: {len(expanded)} moves "
            f"(expanded from {len(raw_choreo)}), "
            f"genre={canonical_genre}, seed={seed_int & 0xFFFF:04x}"
        )

    return expanded


# Allow external code to inject a log function
enrich_choreography._log = None


def set_logger(log_func):
    """Set a logging function for this module."""
    enrich_choreography._log = log_func
