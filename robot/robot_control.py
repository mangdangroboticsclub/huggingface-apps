#!/usr/bin/env python3
"""
Mini Pupper Robot Control Script
Uses the FPC (Flexible Programmable Choreography) API from StanfordQuadruped.
This replaces the buggy UDP-joystick-based minipupper_control.py.

The FPC API builds a MovementLib (a playlist of choreographed movements)
and executes them through the direct Controller + HardwareInterface path.

Usage:
  python3 robot/robot_control.py forward 1.0
  python3 robot/robot_control.py backward 2.0
  python3 robot/robot_control.py right 1.5
  python3 robot/robot_control.py left 0.5
  python3 robot/robot_control.py cw                  # rotate clockwise 30deg
  python3 robot/robot_control.py ccw 45              # rotate CCW 45deg
  python3 robot/robot_control.py look-up
  python3 robot/robot_control.py look-down
  python3 robot/robot_control.py look-right
  python3 robot/robot_control.py look-left
  python3 robot/robot_control.py raise-body          # ascend 3cm
  python3 robot/robot_control.py lower-body          # descend 3cm
  python3 robot/robot_control.py activate            # stand up
  python3 robot/robot_control.py deactivate          # sit down
  python3 robot/robot_control.py stop                # return to idle
  python3 robot/robot_control.py dance               # run a dance sequence
  python3 robot/robot_control.py greet               # wave a leg
"""

import os
import sys
import time
import argparse
import numpy as np

# ── Add StanfordQuadruped to path ───────────────────────────────
sys.path.insert(0, "/home/ubuntu/StanfordQuadruped")

from src.MovementGroup import MovementGroups
from src.MovementScheme import MovementScheme, LocationStanding, SpeedStanding, AttitudeStanding
from src.Controller import Controller
from src.State import State
from src.Command import Command
from MangDang.mini_pupper.HardwareInterface import HardwareInterface
from MangDang.mini_pupper.Config import Configuration
from MangDang.mini_pupper.display import Display
from pupper.Kinematics import four_legs_inverse_kinematics

# ── Global hardware objects (reused across runs) ────────────────
_config = Configuration()
_hardware = HardwareInterface()
_controller = Controller(_config, four_legs_inverse_kinematics)
_state = State()
_display = Display()


# ═══════════════════════════════════════════════════════════════════
#  MovementLib Builders
# ═══════════════════════════════════════════════════════════════════

def _build_movement(command: str, duration: float, angle: float, time_acc: float = 0.5):
    """
    Build a MovementLib list for a single command.

    Args:
        command: Movement name (forward, backward, etc.)
        duration: How long to execute (seconds)
        angle: Angle for rotation commands (degrees)

    Returns:
        List of Movement objects (MovementLib)
    """
    move = MovementGroups()

    # ── Linear Movement (Level 2: gait_uni) ──
    # v_x: forward velocity (m/s, + = forward)
    # v_y: lateral velocity (m/s, + = left)
    # speed caps: vxcap=0.5, vycap=0.5
    SPEED = 0.2  # m/s, conservative default
    ACCEL = 0.3  # seconds to reach speed

    if command in ("forward", "f"):
        move.gait_uni(v_x=SPEED, v_y=0, time_uni=duration, time_acc=ACCEL)

    elif command in ("backward", "b"):
        move.gait_uni(v_x=-SPEED, v_y=0, time_uni=1, time_acc=ACCEL)

    elif command in ("right", "r"):
        move.gait_uni(v_x=0, v_y=-SPEED, time_uni=duration, time_acc=ACCEL)

    elif command in ("left", "l"):
        move.gait_uni(v_x=0, v_y=SPEED, time_uni=duration, time_acc=ACCEL)

    # ── Rotation ──
    elif command in ("cw", "rotate_cw", "turn_right"):
        move.rotate(angle=(angle if angle else 30))

    elif command in ("ccw", "rotate_ccw", "turn_left"):
        move.rotate(angle=-(angle if angle else 30))

    # ── Posture (Level 2) ──
    elif command in ("look-up", "look_up"):
        move.head_move(pitch_deg=20, yaw_deg=0, time_uni=duration, time_acc=time_acc)

    elif command in ("look-down", "look_down"):
        move.head_move(pitch_deg=-20, yaw_deg=0, time_uni=duration, time_acc=time_acc)
        # move.stop(time=0.1)

    elif command in ("look-right", "look_right"):
        move.head_move(pitch_deg=0, yaw_deg=30, time_uni=duration, time_acc=time_acc)

    elif command in ("look-left", "look_left"):
        move.head_move(pitch_deg=0, yaw_deg=-30, time_uni=duration, time_acc=time_acc)

    elif command in ("look-upper-left", "look_upperleft", "upper-left", "upperleft"):
        move.head_move(pitch_deg=15, yaw_deg=-20, time_uni=duration, time_acc=time_acc)

    elif command in ("look-lower-left", "look_lowerleft", "lower-left", "lowerleft"):
        move.head_move(pitch_deg=-15, yaw_deg=-20, time_uni=duration, time_acc=time_acc)

    elif command in ("look-lower-right", "look_lowerright", "lower-right", "lowerright"):
        move.head_move(pitch_deg=-15, yaw_deg=20, time_uni=duration, time_acc=time_acc)

        
        
    elif command in ("disco1"):
        _n_subs = 2
        _sub_tic = max(time_acc / _n_subs, 0.015)
        # reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        _sub_hold = duration / (1 * _n_subs) if (1 * _n_subs) > 0 else 0.05
        for _ in range(1):
            move.head_move(pitch_deg=15, yaw_deg=20, time_uni=sub_hold, time_acc=_sub_tic)
            move.head_move(pitch_deg=15, yaw_deg=-20, time_uni=sub_hold, time_acc=_sub_tic)
            # move.head_move(pitch_deg=-15, yaw_deg=20, time_uni=duration, time_acc=time_acc)
            # move.head_move(pitch_deg=-15, yaw_deg=-20, time_uni=duration, time_acc=time_acc)
        
    elif command in ("disco2"):
        move.head_move(pitch_deg=15, yaw_deg=20, time_uni=duration, time_acc=time_acc)
        move.head_move(pitch_deg=-15, yaw_deg=-20, time_uni=duration, time_acc=time_acc)        
        # move.head_move(pitch_deg=-15, yaw_deg=20, time_uni=duration, time_acc=time_acc)
        # move.head_move(pitch_deg=15, yaw_deg=-20, time_uni=duration, time_acc=time_acc)

    elif command in ("disco3"):
        move.head_move(pitch_deg=15, yaw_deg=20, time_uni=duration, time_acc=time_acc)
        move.head_move(pitch_deg=-15, yaw_deg=20, time_uni=duration, time_acc=time_acc)
        # move.head_move(pitch_deg=15, yaw_deg=-20, time_uni=duration, time_acc=time_acc)
        # move.head_move(pitch_deg=-15, yaw_deg=-20, time_uni=duration, time_acc=time_acc)     

    elif command in ("seek"):
        move.head_move(pitch_deg=-8, yaw_deg=30, time_uni=duration, time_acc=time_acc)
        # move.stop(time=0.1)
        move.head_move(pitch_deg=8, yaw_deg=-30, time_uni=duration, time_acc=time_acc)
        
     
        

    elif command in ("look-upper-right", "look_upperright", "upper-right", "upperright"):
        move.head_move(pitch_deg=15, yaw_deg=20, time_uni=duration, time_acc=time_acc)
        

    elif command in ("raise-body", "raise_body", "raise"):
        move.height_move(ht=0.03, time_uni=duration, time_acc=time_acc)

    elif command in ("lower-body", "lower_body", "lower"):
        move.height_move(ht=-0.03, time_uni=duration, time_acc=time_acc)

    elif command == "squat":
        move.height_move(ht=-0.04, time_uni=duration, time_acc=time_acc)
        move.stop(time=0.1)
        move.height_move(ht=-0.04, time_uni=duration, time_acc=time_acc)
        move.stop(time=0.1)

    elif command in ("body-row", "body_row", "roll"):
        move.body_row(row_deg=angle if angle else 20, time_uni=duration, time_acc=time_acc)

    # ── Standing / Activation ──
    elif command in ("stop", "idle"):
        move.stop(time=max(duration, 1.0))

    elif command in ("activate", "init", "stand"):
        move.height_move(ht=0.03, time_uni=duration, time_acc=time_acc)
        move.stop(time=0.3)

    elif command in ("deactivate", "sit", "rest"):
        move.height_move(ht=-0.04, time_uni=duration, time_acc=time_acc)
        move.stop(time=0.3)

    # ── Sequences ──
    elif command in ("follow-person", "follow_person", "follow"):
        # Run the person follower (uses ContinuousController internally)
        # This just activates and stands - the actual follower runs separately
        move.gait_uni(v_x=0.05, v_y=0, time_uni=0.5, time_acc=0.5)
        move.stop(time=0.5)

    elif command == "dance":
        # move.move_forward()
        # move.move_backward()
        # move.move_right()
        # move.move_left()
        move.look_up()
        move.look_down()
        move.stop()
        # move.rotate(angle=90)
        # move.rotate(angle=-90)

    elif command == "disco":
        move.look_upperright()
        move.look_upperleft() 
        # move.look_upperright()
        # move.look_upperleft() 
        # move.look_upperright()
        # move.look_upperleft() 
        # move.look_upperright()
        # move.look_upperleft() 
    
    elif command == "fall":
        move.height_move(ht=0.05, time_uni=0.5, time_acc=0.5)
        # move.foreleg_lift("left", ht=0.03, time_uni=1.0, time_acc=0.5)
        # move.height_move(ht=-0.02, time_uni=max(duration, 0.5), time_acc=0.5)
        # move.right(v_x=0, v_y=-0.2, time_uni=duration, time_acc=0.5)
        move.foreleg_lift("left", ht=0.5, time_uni=1, time_acc=0.05)
        move.stop(time=0.1)
        # move.stop(time=0.3)
        # move.foreleg_lift("right", ht=0.06, time_uni=1.0, time_acc=0.5)

    elif command == "left_kick": 
        # move.look_up() 
        # move.stop(time=0.1)      
        # move.gait_uni(v_x=0.2, v_y=0, time_uni=0.1, time_acc=0.5)        
        # move.stop(time=0.3)
        move.kick("left", ht=0.1, time_uni=0.05, time_acc=0.05)
        move.stop(time=0.1)

    elif command == "right_kick":
        # move.lift_kick ("right", ht=0.05, time_uni=1, time_acc=0.5)
        move.kick("right", ht=0.1, time_uni=0.05, time_acc=0.05)
        # move.stop(time=0.1)
              
        
    elif command == "backleg_lift":
        # _n_subs = 2
        # _sub_tic = max(time_acc / _n_subs, 0.015)
        # reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        # _sub_hold = duration / (reps * _n_subs) if (reps * _n_subs) > 0 else 0.05 
        # for _ in range(reps):
        move.backleg_lift("right", ht=0.01, time_uni=duration, time_acc=time_acc)
        # move.stop(time=0.1)   
        move.backleg_lift("left", ht=0.01, time_uni=duration, time_acc=time_acc) 
        # move.stop(time=0.1) 
        
    elif command == "greet":
        move.foreleg_lift("right", ht=0.005, time_uni=duration, time_acc=time_acc)
        move.stop(time=0.1)
        move.foreleg_lift("left", ht=0.005, time_uni=duration, time_acc=time_acc)
        move.stop(time=0.1)

    

    # ── Dance Moves (10 choreographed sequences) ────────────────────

    elif command == "headbang":
        """Rapid body pitch oscillation — looks like human headbanging."""
        _n_subs = 2
        _sub_tic = max(time_acc / _n_subs, 0.015)
        reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        _sub_hold = duration / (reps * _n_subs) if (reps * _n_subs) > 0 else 0.05
        for _ in range(reps):
            move.head_move(pitch_deg=15, yaw_deg=0, time_uni=_sub_hold, time_acc=_sub_tic)
            move.head_move(pitch_deg=-15, yaw_deg=0, time_uni=_sub_hold, time_acc=_sub_tic)

    elif command == "bounce":
        """Body bob — raise on upbeats, lower on downbeats."""
        # _n_subs = 2
        # _sub_tic = max(time_acc / _n_subs, 0.015)
        # reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        # _sub_hold = duration / (reps * _n_subs) if (reps * _n_subs) > 0 else 0.05
        # for _ in range(reps):
        move.height_move(ht=0.02, time_uni=duration, time_acc=time_acc)
        move.height_move(ht=-0.01, time_uni=duration, time_acc=time_acc)
        

    elif command in ("swagger", "body-roll"):
        """Groovy body roll — side-to-side tilt."""
        _n_subs = 2
        _sub_tic = max(time_acc / _n_subs, 0.015)
        # reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        _sub_hold = duration / (1 * _n_subs) if (1 * _n_subs) > 0 else 0.05
        for _ in range(1):
            move.body_row(row_deg=15, time_uni=_sub_hold, time_acc=_sub_tic)
            move.body_row(row_deg=-15, time_uni=_sub_hold, time_acc=_sub_tic)

    elif command == "spin": 
        """Dramatic rotation — 180 deg spin then 90 deg back."""
        spin_angle = angle if angle else 180
        move.rotate(angle=spin_angle)
        move.stop(time=0.3)
        move.rotate(angle=-(spin_angle // 2))
        move.stop(time=0.2)

    elif command == "wave":
        """Alternating all-4-legs wave — front legs then back legs."""
        reps = max(1, int(duration / 3.0)) if duration > 0 else 1
        for _ in range(reps):
            # Right front
            move.foreleg_lift(leg_index="right", ht=0.005, time_uni=time_acc, time_acc=time_acc)
            move.stop(time=0.15)            
            # Left front
            move.foreleg_lift(leg_index="left", ht=0.005, time_uni=time_acc, time_acc=time_acc)
            move.stop(time=0.15)
            # Right back (diagonal)
            move.backleg_lift(leg_index="right", ht=0.005, time_uni=time_acc, time_acc=time_acc)
            move.stop(time=0.15)
            # Left back (diagonal)
            move.backleg_lift(leg_index="left", ht=0.005, time_uni=time_acc, time_acc=time_acc)
            move.stop(time=0.15)
            

    elif command == "shuffle":
        """Side-step left/right — classic disco / pop shuffle."""
        reps = max(1, int(duration / 1.2)) if duration > 0 else 2
        for _ in range(reps):
            move.gait_uni(v_x=0, v_y=0.2, time_uni=0.4, time_acc=0.2)
            move.stop(time=0.15)
            move.gait_uni(v_x=0, v_y=-0.2, time_uni=0.4, time_acc=0.2)
            move.stop(time=0.15)

    elif command == "dip":
        """Slow dramatic lean — look down + tilt + lower body."""
        move.height_move(ht=-0.02, time_uni=duration, time_acc=time_acc)
        move.head_move(pitch_deg=-25, yaw_deg=0, time_uni=duration, time_acc=time_acc)
        move.body_row(row_deg=-15, time_uni=duration, time_acc=time_acc)
        move.stop(time=0.5)
        # Return to upright
        move.head_move(pitch_deg=25, yaw_deg=0, time_uni=duration, time_acc=time_acc)
        move.body_row(row_deg=0, time_uni=duration, time_acc=time_acc)
        move.height_move(ht=0.02, time_uni=duration, time_acc=time_acc)
        move.stop(time=0.2)

    elif command == "nod":
        """Subtle head nod — small 5 deg pitch oscillation."""
        _n_subs = 2
        _sub_tic = max(time_acc / _n_subs, 0.015)
        reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        _sub_hold = duration / (reps * _n_subs) if (reps * _n_subs) > 0 else 0.05
        for _ in range(reps):
            move.head_move(pitch_deg=5, yaw_deg=0, time_uni=_sub_hold, time_acc=_sub_tic)
            move.head_move(pitch_deg=-5, yaw_deg=0, time_uni=_sub_hold, time_acc=_sub_tic)
            

    # elif command == "lean":
    #     """Slow controlled body tilt in one direction, then return."""
    #     lean_angle = angle if angle else 25
    #     stages = max(3, int(abs(lean_angle) / 5))
    #     step = lean_angle / stages
    #     for s in range(stages, 0, -1):
    #         move.body_row(row_deg=s * step, time_uni=time_acc, time_acc=0.1)
    #     move.stop(time=0.1)
    #     for s in range(1, stages + 1):
    #         move.body_row(row_deg=-s * step, time_uni=time_acc, time_acc=0.1)

    elif command == "lean":
        """Slow controlled body tilt in one direction, then return."""
        _n_subs = 2
        _sub_tic = max(time_acc / _n_subs, 0.015)
        reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        _sub_hold = max(duration / (reps * _n_subs), 0.025) if (reps * _n_subs) > 0 else 0.05
        for _ in range(reps):            
            move.body_row(row_deg=20, time_uni=duration, time_acc=time_acc)        
            move.body_row(row_deg=10, time_uni=duration, time_acc=time_acc)        
            move.body_row(row_deg=-10, time_uni=duration, time_acc=time_acc)
            move.body_row(row_deg=-20, time_uni=duration, time_acc=time_acc)

    elif command == "flourish":
        """Multi-axis showstopper: look up + rise + spin + sink + tilt + finish."""
        move.head_move(pitch_deg=20, yaw_deg=0, time_uni=0.4, time_acc=0.2)
        move.height_move(ht=0.03, time_uni=0.3, time_acc=0.15)
        move.rotate(angle=180)
        move.head_move(pitch_deg=-15, yaw_deg=0, time_uni=0.4, time_acc=0.2)
        move.height_move(ht=-0.03, time_uni=0.3, time_acc=0.15)
        move.body_row(row_deg=15, time_uni=0.4, time_acc=0.2)
        move.body_row(row_deg=0, time_uni=0.3, time_acc=0.15)
        move.rotate(angle=-30)
        move.stop(time=0.3)


    # elif command in ("front-kick", "front_kick"):
    #     """Both front legs kick up — snap up, hold, return."""
    #     # Phase 1: Lower body slightly, lift right front leg
    #     move.height_move(ht=-0.01, time_uni=0.3, time_acc=0.2)
    #     move.foreleg_lift(leg_index="right", ht=0.06, time_uni=0.4, time_acc=0.15)
    #     move.stop(time=0.1)
    #     # Phase 2: Snap left front leg up too
    #     move.foreleg_lift(leg_index="left", ht=0.06, time_uni=0.4, time_acc=0.15)
    #     move.stop(time=0.3)
    #     # Phase 3: Return to standing
    #     move.height_move(ht=0.01, time_uni=0.3, time_acc=0.2)
    #     move.stop(time=0.2)

    elif command in ("front_kick", "rear_up"):
        # Phase 1: Snap front legs up with max height + pitch back
        move.front_kick(ht=0.06, pitch_deg=25, time_uni=duration, time_acc=time_acc)
        # Phase 2: Return to default standing
        move.front_kick_to_stand(time_uni=duration, time_acc=time_acc)
        # Phase 3: Settle
        move.stop(time=0.1)

    # ── Genre Signatures ────────────────────────────────────────
    elif command == "head_ellipse":
        """🤘 Head Oscillation — head traces a fast ellipse (head_ellipse)."""
        move.head_ellipse(interp_num = time_acc*24)
        
        # move.stop(time=0.1)

    elif command == "body_ellipse":
        """🎤 Swim — all 4 legs trace circles, body swims in place (body_cycle)."""
        move.body_ellipse(interp_num = time_acc*24)
        # move.stop(time=0.1)

    elif command == "head_cycle":
        """🤘 Quick head oscillation — 8-point ellipse in ~0.6s."""
        from src.MovementScheme import Movements as _Mv
        _h = _Mv('head_cycle')
        _h.setTransitionTic(3)
        _h.setInterpolationNumber(5)
        _h.setLegsSequence(move.default_stand)
        _h.setAttitudeSequence([
            [0, 0,    15], [0, 10,  10], [0, 15,  0],
            [0, 10,  -10], [0, 0,  -15], [0, -10,-10],
            [0, -15,  0],  [0, -10, 10],
        ], "single", 1)
        _h.setSpeedSequence([[0,0,0]], "single", 1)
        _h.setTurnSequence([[0,0,0]])
        move.MovementLib.append(_h)
    
    elif command == "swim":
        """🎤 Quick body cycle — 8-point leg circle in ~0.6s."""
        from src.MovementScheme import Movements as _Mv
        import numpy as np
        _r = 0.04
        _legs = []
        for _lx, _ly in [(0.06,-0.05),(0.06,0.05),(-0.06,-0.05),(-0.06,0.05)]:
            _legs.append([[ _lx+np.cos(a*0.785)*_r, _ly+np.sin(a*0.785)*_r, -0.07] for a in range(1, 9)])
        _s = _Mv('swim')
        _s.setTransitionTic(3)
        _s.setInterpolationNumber(5)
        _s.setLegsSequence(_legs, "single", 1)
        _s.setSpeedSequence([[0,0,0]]*8, "single", 1)
        _s.setAttitudeSequence([[0,0,0]]*8, "single", 1)
        _s.setTurnSequence([[0,0,0]])
        move.MovementLib.append(_s)

    elif command == "step_move":
        move.step_move (ht=-0.025, time_uni=duration/2, time_acc=0.015)
        # move.stop (time=0.1)

    elif command == "twerk":
        """🍑 Twerk — single-pose hold + stop for smooth return."""
        _n_subs = 2
        _sub_tic = max(time_acc / _n_subs, 0.015)
        reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        _sub_hold = duration / (reps * _n_subs) if (reps * _n_subs) > 0 else 0.05 
        for _ in range(1):
            move.twerk(ht=0.02, time_uni=_sub_hold, time_acc=_sub_tic)                   
            move.twerk(ht=-0.02, time_uni=_sub_hold, time_acc=_sub_tic)
            
          

    elif command == "wiggle":
        """🍑 Multi-pose wiggle — same beat control as butt_shrug.
        interp_num auto-derived from time_uni inside wiggle_left/right."""
        move.wiggle_left(time_uni=duration, time_acc=time_acc)
        move.wiggle_right(time_uni=duration, time_acc=time_acc)

    elif command == "left_wiggle":
        """🍑 Multi-pose wiggle — same beat control as butt_shrug.
        interp_num auto-derived from time_uni inside wiggle_left/right."""
        move.wiggle_left(time_uni=duration, time_acc=time_acc)
        # move.wiggle_right(time_uni=duration, time_acc=time_acc)

    elif command == "right_wiggle":
        """🍑 Multi-pose wiggle — same beat control as butt_shrug.
        interp_num auto-derived from time_uni inside wiggle_left/right."""
        # move.wiggle_left(time_uni=duration, time_acc=time_acc)
        move.wiggle_right(time_uni=duration, time_acc=time_acc)
        
    
    elif command == "shoulder_shrug":
        _n_subs = 2
        _sub_tic = max(time_acc / _n_subs, 0.015)
        reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        _sub_hold = duration / (reps * _n_subs) if (reps * _n_subs) > 0 else 0.05
        for _ in range(1):
            # move.head_move(pitch_deg=-25, yaw_deg=0, time_uni=duration, time_acc=time_acc)
            move.head_move(pitch_deg=0, yaw_deg=15, time_uni=duration, time_acc=time_acc)
            # move.stop (time=0.1)
            move.head_move(pitch_deg=0, yaw_deg=-15, time_uni=duration, time_acc=time_acc)
            # move.stop (time=0.1)
            # move.head_move(pitch_deg=0, yaw_deg=-15, time_uni=duration, time_acc=time_acc)
            # move.stop (time=0.1)

    elif command == "left_shoulder_shrug":
        _n_subs = 2
        _sub_tic = max(time_acc / _n_subs, 0.015)
        reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        _sub_hold = duration / (reps * _n_subs) if (reps * _n_subs) > 0 else 0.05
        for _ in range(1):            
            move.head_move(pitch_deg=0, yaw_deg=15, time_uni=duration, time_acc=time_acc)
            

    elif command == "right_shoulder_shrug":
        _n_subs = 2
        _sub_tic = max(time_acc / _n_subs, 0.015)
        reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        _sub_hold = duration / (reps * _n_subs) if (reps * _n_subs) > 0 else 0.05
        for _ in range(1):            
            move.head_move(pitch_deg=0, yaw_deg=-15, time_uni=duration, time_acc=time_acc)
        

    

    elif command == "butt_shrug": 
        # _n_subs = 2
        # _sub_tic = max(time_acc / _n_subs, 0.015)
        # reps = max(1, int((time_acc + duration) / (time_acc * _n_subs))) if duration > 0 else 1
        # _sub_hold = duration / (reps * _n_subs) if (reps * _n_subs) > 0 else 0.05 
        # for _ in range(reps):
        move.butt_shrug_left(time_uni=duration, time_acc=time_acc)
        move.butt_shrug_right(time_uni=duration, time_acc=time_acc)
        # move.butt_shrug_left(time_uni=duration, time_acc=time_acc)
        # move.stop_butt_shrug (time=0.1)
        # move.butt_shrug_right(time_uni=duration, time_acc=time_acc)
        # move.stop_butt_shrug (time=0.1)
        # move.butt_shrug_trajectory(time_uni=duration, time_acc=time_acc)
    
    elif command == "left_butt_shrug":
        move.butt_shrug_left(time_uni=duration, time_acc=time_acc)
    
    elif command == "right_butt_shrug":
        move.butt_shrug_right(time_uni=duration, time_acc=time_acc)

        

    else:
        raise ValueError(f"Unknown command: {command}")

    return move.MovementLib


# ═══════════════════════════════════════════════════════════════════
#  Execution Engine
# ═══════════════════════════════════════════════════════════════════

def run_movement(movement_lib, timeout=30.0, initial_state=None,
             stop_flag_path=None, progress_callback=None,
             tilt_state=None):
    """
    Execute a MovementLib directly on the robot hardware.

    This is the same control loop that run_danceActionList.py uses.
    Falls back to time-based exit if tick counting doesn't terminate.

    Args:
        movement_lib: List of Movements (from MovementGroups)
        timeout: Maximum wall-clock execution time (seconds)
        initial_state: Optional dict with 'legs_location', 'speed',
                       'attitude', 'turn' from the previous movement.
                       Keeps transitions smooth instead of snapping
                       back to standing between moves.

    Returns:
        (True, state_dict) on success, (False, state_dict) on timeout/error/stop
        state_dict has keys: legs_location, speed, attitude, turn
    """
    movement_ctl = MovementScheme(movement_lib)

    # Override initial state for smooth transition from previous pose
    if initial_state:
        movement_ctl.legs_location_pre = np.array(initial_state.get('legs_location', LocationStanding))
        movement_ctl.legs_location_now = np.array(initial_state.get('legs_location', LocationStanding))
        movement_ctl.speed_pre = np.array(initial_state.get('speed', [0,0,0]))
        movement_ctl.speed_now = np.array(initial_state.get('speed', [0,0,0]))
        movement_ctl.attitude_pre = np.array(initial_state.get('attitude', [0,0,0]))
        movement_ctl.attitude_now = np.array(initial_state.get('attitude', [0,0,0]))
        movement_ctl.turn_pre = np.array(initial_state.get('turn', [0,0,0]))
        movement_ctl.turn_now = np.array(initial_state.get('turn', [0,0,0]))
        # Skip the initial Exit phase — go straight to Entry
        movement_ctl.ststus = 'Entry'
        movement_ctl.entry_down = False
        movement_ctl.entry_down1 = False
        movement_ctl.entry_down2 = False
        movement_ctl.entry_down3 = False
        movement_ctl.getAccCommand = True
        # Prevent the state machine from redirecting to Exit-standing
        # on first tick by matching movement_now_name to the first input
        movement_ctl.movement_now_name = ' '

    lib_length = len(movement_lib)

    command = Command()
    command.pseudo_dance_event = True

    last_loop = time.time()
    start_time = time.time()

    # Estimate duration — use timeout as floor, lib estimate as ceiling
    est_duration = max(timeout, lib_length * 0.2 + 3.0)
    hard_timeout = est_duration + 5.0
    last_flag_check = 0
    last_progress_call = 0
    stopped_by_flag = False

    while True:
        now = time.time()
        if now - last_loop < _config.dt:
            continue
        last_loop = now

        elapsed = now - start_time

        # Safety timeout
        if elapsed > hard_timeout:
            print(f"ERROR: Execution timed out after {elapsed:.1f}s", file=sys.stderr)
            return False, {
        'legs_location': list(list(x) for x in movement_ctl.legs_location_now),
        'speed': list(movement_ctl.speed_now),
        'attitude': list(movement_ctl.attitude_now),
        'turn': list(movement_ctl.turn_now),
    }

        # Orientation (no IMU for CLI commands)
        _state.quat_orientation = np.array([1, 0, 0, 0])

        # Advance the movement scheme by one tick
        movement_ctl.runMovementScheme()

        # Populate command from current movement state
        command.legslocation = movement_ctl.getMovemenLegsLocation()
        command.horizontal_velocity = movement_ctl.getMovemenSpeed()
        command.roll = movement_ctl.attitude_now[0]
        if tilt_state is not None:
            tilt_state.roll_deg = float(movement_ctl.attitude_now[0])
        command.pitch = movement_ctl.attitude_now[1]
        command.yaw = movement_ctl.attitude_now[2]
        command.yaw_rate = movement_ctl.getMovemenTurn()

        # Run inverse kinematics + controller
        _controller.run(_state, command, _display)

        # Send joint angles to servos via ESP32
        _hardware.set_actuator_postions(_state.joint_angles)

        # Check stop flag periodically (every ~1.5s elapsed)
        if stop_flag_path and elapsed - last_flag_check >= 1.5:
            last_flag_check = elapsed
            if not os.path.exists(stop_flag_path):
                print("Dance stopped via flag", file=sys.stderr)
                stopped_by_flag = True
                break

        # Call progress callback periodically (every ~3s elapsed)
        if progress_callback and elapsed - last_progress_call >= 3.0:
            last_progress_call = elapsed
            progress_callback(movement_ctl.movement_now_number, lib_length, elapsed)

        # Check if all movements played (tick logic) OR time-based fallback
        scheme_done = (movement_ctl.movement_now_number >= lib_length - 1
                       and movement_ctl.tick >= movement_ctl.now_ticks)
        time_done = elapsed >= est_duration - 2.0

        if scheme_done or time_done:
            break

    ok = not stopped_by_flag
    return ok, {
        'legs_location': list(list(x) for x in movement_ctl.legs_location_now),
        'speed': list(movement_ctl.speed_now),
        'attitude': list(movement_ctl.attitude_now),
        'turn': list(movement_ctl.turn_now),
    }


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Mini Pupper Robot Control (FPC API)"
    )
    parser.add_argument(
        "command",
        nargs="*",
        help="Command and optional arguments: {cmd} {duration|angle}",
    )
    parser.add_argument(
        "--duration", type=float, default=0.05,
        help="Duration in seconds (default: 0.5, for movement commands)",
    )
    parser.add_argument(
        "--angle", type=float, default=None,
        help="Angle in degrees (for rotation/roll commands)",
    )
    parser.add_argument(
        "--list", action="store_true", dest="list_cmds",
        help="List all available commands",
    )

    args = parser.parse_args()

    if args.list_cmds:
        print("Available commands:")
        print("  forward, backward, right, left          # Movement (arg: duration)")
        print("  cw, ccw                                  # Rotate (arg: degrees)")
        print("  look-up, look-down, look-left, look-right")
        print("  raise-body, lower-body                   # Height (arg: duration)")
        print("  body-row, roll                           # Body roll (arg: degrees)")
        print("  activate, deactivate, stop               # Standing")
        print("  front-kick, rear-up                      # Both front legs up")
        print("  dance, greet                             # Sequences")
        return 0

    if not args.command:
        parser.print_help()
        return 1

    cmd = args.command[0]
    duration = args.duration
    angle = args.angle

    # Parse positional arguments for convenience
    if len(args.command) >= 2:
        try:
            val = float(args.command[1])
            # Determine if it's a duration or angle based on command
            if cmd in ("cw", "ccw", "rotate_cw", "rotate_ccw",
                       "body-row", "body_row", "roll"):
                angle = val
            else:
                duration = val
        except ValueError:
            pass

    try:
        print(f"Building movement: {cmd} (duration={duration}s, angle={angle})")
        lib = _build_movement(cmd, duration, angle)
        print(f"MovementLib: {len(lib)} action(s)")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print("Executing...")
    success, _ = run_movement(lib)

    if success:
        print(f"ok: {cmd}")
        return 0
    else:
        print(f"FAIL: {cmd}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
