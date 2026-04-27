# Code for handling the kinematics of corexy robots with an A/C rotary table
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
from klippy import stepper
from . import corexy

DEG = math.pi / 180.0


class RotaryAxis:
    def __init__(self, config, axis):
        self.axis = axis
        self.stepper = stepper.PrinterStepper(config)
        self.stepper.setup_itersolve(
            "rotary_table_stepper_alloc", axis.encode(), 0.0, 0.0, 0.0, 0.0
        )
        self.pivot = config.getfloatlist("pivot", count=2)
        self.max_velocity = config.getfloat("max_velocity", above=0.0)
        self.max_accel = config.getfloat("max_accel", above=0.0)
        self.instant_corner_v = config.getfloat(
            "instantaneous_corner_velocity", 1.0, minval=0.0
        )
        self.pos_min = config.getfloat("position_min", None)
        self.pos_max = config.getfloat("position_max", None)

    def get_axis_gcode_id(self):
        return self.axis.upper()

    def get_trapq(self):
        return None

    def process_move(self, print_time, move, ea_index):
        pass

    def check_move(self, move, ea_index):
        movepos = move.end_pos[ea_index]
        if (self.pos_min is not None and movepos < self.pos_min) or (
            self.pos_max is not None and movepos > self.pos_max
        ):
            raise move.move_error()
        axis_ratio = move.move_d / abs(move.axes_d[ea_index])
        move.limit_speed(
            self.max_velocity * axis_ratio, self.max_accel * axis_ratio
        )

    def calc_junction(self, prev_move, move, ea_index):
        diff_r = move.axes_r[ea_index] - prev_move.axes_r[ea_index]
        if diff_r:
            return (self.instant_corner_v / abs(diff_r)) ** 2
        return move.max_cruise_v2


class CoreXYACKinematics(corexy.CoreXYKinematics):
    def __init__(self, toolhead, config):
        self.rotary = []
        corexy.CoreXYKinematics.__init__(self, toolhead, config)
        self.printer = config.get_printer()
        self.toolhead = toolhead
        self.rotary = [
            RotaryAxis(config.getsection("rotary_" + a), a) for a in "ac"
        ]
        self.ay, self.az = self.rotary[0].pivot
        self.cx, self.cy = self.rotary[1].pivot
        pivots = (self.cx, self.cy, self.ay, self.az)
        for rail, t in zip(self.rails, (b"+", b"-", b"z")):
            rail.setup_itersolve("rotary_table_stepper_alloc", t, *pivots)
        for ra, slot in zip(self.rotary, (0, 2)):
            ra.stepper.set_trapq(toolhead.get_trapq())
            toolhead.register_step_generator(ra.stepper.generate_steps)
            toolhead.add_rotary_axis(ra, slot, 0.0)

    def get_steppers(self):
        return corexy.CoreXYKinematics.get_steppers(self) + [
            ra.stepper for ra in self.rotary
        ]

    def _to_machine(self, pose):
        sa, ca = math.sin(pose[3] * DEG), math.cos(pose[3] * DEG)
        sc, cc = math.sin(pose[5] * DEG), math.cos(pose[5] * DEG)
        dx, dy = pose[0] - self.cx, pose[1] - self.cy
        qy = self.cy + dx * sc + dy * cc - self.ay
        qz = pose[2] - self.az
        return (
            self.cx + dx * cc - dy * sc,
            self.ay + qy * ca - qz * sa,
            self.az + qy * sa + qz * ca,
        )

    def _to_part(self, pos, a, c):
        sa, ca = math.sin(a * DEG), math.cos(a * DEG)
        sc, cc = math.sin(c * DEG), math.cos(c * DEG)
        my, mz = pos[1] - self.ay, pos[2] - self.az
        dx, dy = pos[0] - self.cx, self.ay + my * ca + mz * sa - self.cy
        return [
            self.cx + dx * cc + dy * sc,
            self.cy - dx * sc + dy * cc,
            self.az - my * sa + mz * ca,
        ]

    def _rotate(self, vec, pose):
        sa, ca = math.sin(pose[3] * DEG), math.cos(pose[3] * DEG)
        sc, cc = math.sin(pose[5] * DEG), math.cos(pose[5] * DEG)
        vy = vec[0] * sc + vec[1] * cc
        return (
            vec[0] * cc - vec[1] * sc,
            vy * ca - vec[2] * sa,
            vy * sa + vec[2] * ca,
        )

    def calc_position(self, stepper_positions):
        pos = [stepper_positions[rail.get_name()] for rail in self.rails]
        a, c = [stepper_positions[ra.stepper.get_name()] for ra in self.rotary]
        return self._to_part(
            [0.5 * (pos[0] + pos[1]), 0.5 * (pos[0] - pos[1]), pos[2]], a, c
        )

    def set_position(self, newpos, homing_axes):
        pose = self.toolhead.get_pose()
        corexy.CoreXYKinematics.set_position(self, pose, homing_axes)
        for ra in self.rotary:
            ra.stepper.set_position(pose)

    def home(self, homing_state):
        pose = self.toolhead.get_pose()
        if pose[3] or math.remainder(pose[5], 360.0):
            raise self.printer.command_error(
                "Rotary axes must be at zero to home"
            )
        corexy.CoreXYKinematics.home(self, homing_state)

    def check_move(self, move):
        start = self.toolhead.get_pose(move.start_pos)
        end = self.toolhead.get_pose(move.end_pos)
        samples = [end]
        if start[3:] != end[3:]:
            samples = [
                [s + (e - s) * i / 8.0 for s, e in zip(start, end)]
                for i in range(1, 9)
            ]
        mstart = self._to_machine(start)
        for mpos in map(self._to_machine, samples):
            for i, (low, high) in enumerate(self.limits):
                if mpos[i] != mstart[i] and not low <= mpos[i] <= high:
                    if low > high:
                        raise move.move_error("Must home axis first")
                    raise move.move_error()
        rot_r = self.toolhead.get_pose(move.axes_r)
        wa, wc = abs(rot_r[3]) * DEG, abs(rot_r[5]) * DEG
        rc = max(
            [math.hypot(p[0] - self.cx, p[1] - self.cy) for p in (start, end)]
        )
        dz = max([abs(p[2] - self.az) for p in (start, end)])
        ra = math.hypot(abs(self.cy - self.ay) + rc, dz)
        swing = wa * ra + wc * rc
        z_r = swing + max(
            [abs(self._rotate(rot_r, p)[2]) for p in (start, end)]
        )
        if swing:
            toolhead = self.toolhead
            k = math.sqrt(sum([r * r for r in rot_r[:3]])) + swing
            move.limit_speed(toolhead.max_velocity / k, toolhead.max_accel / k)
            centripetal = wa * wa * ra + wc * wc * rc
            if centripetal:
                move.limit_speed(
                    math.sqrt(0.5 * toolhead.max_accel / centripetal),
                    move.accel,
                )
        if z_r:
            move.limit_speed(self.max_z_velocity / z_r, self.max_z_accel / z_r)


def load_kinematics(toolhead, config):
    return CoreXYACKinematics(toolhead, config)
