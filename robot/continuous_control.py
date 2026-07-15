"""
Mini Pupper — Continuous Robot Control

Provides real-time velocity control for continuous tasks like person following.
Unlike the discrete MovementLib API (robot_control.py), this keeps the hardware
interface open and accepts live velocity updates each tick.

Uses the same Controller + HardwareInterface as minipupper_operator's movement
worker, but with live-updating velocities instead of pre-built sequences.

Usage:
    from robot.continuous_control import ContinuousController

    ctrl = ContinuousController()
    ctrl.activate()                     # Stand up, engage trot gait

    # Loop at 10-20Hz:
    ctrl.set_velocity(vx=0.2, vy=0)     # Move forward 0.2 m/s
    ctrl.set_yaw_rate(rate=-0.5)        # Turn right
    ctrl.tick()                         # Advance one control step
    ctrl.hardware.sync()                # Send to servos

    ctrl.stop()                         # Zero velocities
    ctrl.deactivate()                   # Sit down
"""

import sys
import time
import numpy as np

sys.path.insert(0, "/home/ubuntu/StanfordQuadruped")

from src.Controller import Controller
from src.State import State, BehaviorState
from src.Command import Command
from MangDang.mini_pupper.HardwareInterface import HardwareInterface
from MangDang.mini_pupper.Config import Configuration
from MangDang.mini_pupper.display import Display
from pupper.Kinematics import four_legs_inverse_kinematics


class ContinuousController:
    """
    Real-time robot controller for live-velocity tasks.

    Keeps the robot in TROT gait and accepts velocity updates
    each tick via .set_velocity() / .set_yaw_rate().
    """

    def __init__(self):
        self.config = Configuration()
        self.hardware = HardwareInterface()
        self.controller = Controller(self.config, four_legs_inverse_kinematics)
        self.state = State()
        self.state.behavior_state = BehaviorState.DEACTIVATED
        self.command = Command()

        self.display = Display()
        self._last_loop = 0.0

    def activate(self):
        """Stand up and engage trot gait."""
        self.command.activate_event = True
        self.state.behavior_state = BehaviorState.DEACTIVATED

        # Run controller until activation completes
        start = time.time()
        while time.time() - start < 5.0:
            now = time.time()
            if now - self._last_loop < self.config.dt:
                time.sleep(0.0005)
                continue
            self._last_loop = now

            self.state.quat_orientation = np.array([1, 0, 0, 0])
            self.controller.run(self.state, self.command, self.display)
            self.hardware.set_actuator_postions(self.state.joint_angles)

            if self.state.behavior_state == BehaviorState.TROT:
                break
            self.command.activate_event = False

        # Engage trot gait
        self.command.trot_event = True
        start = time.time()
        while time.time() - start < 5.0:
            now = time.time()
            if now - self._last_loop < self.config.dt:
                time.sleep(0.0005)
                continue
            self._last_loop = now

            self.state.quat_orientation = np.array([1, 0, 0, 0])
            self.controller.run(self.state, self.command, self.display)
            self.hardware.set_actuator_postions(self.state.joint_angles)

            if self.state.behavior_state == BehaviorState.TROT:
                self.command.trot_event = False
                break
            self.command.trot_event = False

    def deactivate(self):
        """Lower body and return to rest."""
        # First transition TROT → REST via trot_event
        if self.state.behavior_state == BehaviorState.TROT:
            self.command.trot_event = True
            start = time.time()
            while time.time() - start < 3.0:
                now = time.time()
                if now - self._last_loop < self.config.dt:
                    time.sleep(0.0005)
                    continue
                self._last_loop = now
                self.state.quat_orientation = np.array([1, 0, 0, 0])
                self.command.horizontal_velocity = np.zeros(2)
                self.command.yaw_rate = 0.0
                self.controller.run(self.state, self.command, self.display)
                self.hardware.set_actuator_postions(self.state.joint_angles)
                if self.state.behavior_state == BehaviorState.REST:
                    break
                self.command.trot_event = False

        # Now transition REST → DEACTIVATED
        self.command.activate_event = True
        start = time.time()
        while time.time() - start < 5.0:
            now = time.time()
            if now - self._last_loop < self.config.dt:
                time.sleep(0.0005)
                continue
            self._last_loop = now

            self.state.quat_orientation = np.array([1, 0, 0, 0])
            self.command.horizontal_velocity = np.zeros(2)
            self.command.yaw_rate = 0.0
            self.controller.run(self.state, self.command, self.display)
            self.hardware.set_actuator_postions(self.state.joint_angles)

            if self.state.behavior_state == BehaviorState.DEACTIVATED:
                break
            self.command.activate_event = False

    def set_velocity(self, vx=0.0, vy=0.0):
        """
        Set horizontal velocity for the next tick.

        Args:
            vx: Forward velocity (m/s, + = forward, clamped ±0.5)
            vy: Lateral velocity (m/s, + = left, clamped ±0.5)
        """
        self.command.horizontal_velocity = np.array([
            max(-0.5, min(0.5, vx)),
            max(-0.5, min(0.5, vy)),
        ], dtype=np.float64)

    def set_yaw_rate(self, rate=0.0):
        """Set yaw rotation rate (rad/s, + = CCW, clamped ±1.0)."""
        self.command.yaw_rate = max(-1.0, min(1.0, rate))

    def set_height(self, height=-0.07):
        """Set body height (m, default -0.07 = standing)."""
        self.command.height = height

    def set_pitch(self, pitch=0.0):
        """Set body pitch (degrees)."""
        self.command.pitch = pitch

    def set_roll(self, roll=0.0):
        """Set body roll (degrees)."""
        self.command.roll = roll

    def tick(self):
        """
        Advance the control loop one tick at the native hardware rate (~66Hz).

        This blocks to maintain the gait timing. Call it in your loop
        and it will return when the next hardware tick is due.
        """
        now = time.time()
        if now - self._last_loop < self.config.dt:
            time.sleep(self.config.dt - (now - self._last_loop) + 0.001)
            now = time.time()
        self._last_loop = now

        self.state.quat_orientation = np.array([1, 0, 0, 0])
        self.controller.run(self.state, self.command, self.display)
        self.hardware.set_actuator_postions(self.state.joint_angles)

    def sync(self):
        """Force-send joint angles to servos (alias for convenience)."""
        self.hardware.set_actuator_postions(self.state.joint_angles)

    def stop(self):
        """Zero all velocities immediately."""
        self.set_velocity(0, 0)
        self.set_yaw_rate(0)

    def close(self):
        """Stop and release hardware."""
        self.stop()
        self.deactivate()
        self.hardware = None


# ── Quick test ─────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Continuous Robot Control Test")
    parser.add_argument("--test", action="store_true", help="Run test sequence")
    args = parser.parse_args()

    if args.test:
        ctrl = ContinuousController()
        print("Activating...")
        ctrl.activate()
        print("Standing. Forward 2s...")
        ctrl.set_velocity(vx=0.2)
        for _ in range(200):
            ctrl.tick()
        print("Stop.")
        ctrl.stop()
        time.sleep(0.5)
        print("Deactivating...")
        ctrl.deactivate()
        print("Done")
    else:
        print("Import and use in your code:")
        print("  from robot.continuous_control import ContinuousController")
