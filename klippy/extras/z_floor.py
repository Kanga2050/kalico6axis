# Lowest Z the toolhead may move to once Z is homed
#
# [z_floor]
# z: 12.5
# clearance: 10    # G28 ends with Z lifted to z + clearance
# lift_speed: 15
#
# After homing, any move that ends below z is refused, except straight lifts
# (no X/Y travel, Z not going down). G28's own homing moves are exempt, so a
# Z endstop below the floor still works, and every G28 finishes by lifting Z
# above the floor. Probing moves are not exempt.
#
# This file may be distributed under the terms of the GNU GPLv3 license.


class ZFloor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.z = config.getfloat("z")
        self.clearance = config.getfloat("clearance", 10.0, minval=0.0)
        self.lift_speed = config.getfloat("lift_speed", 15.0, above=0.0)
        self.homing = False
        self.g28 = None
        self.printer.register_event_handler("klippy:connect", self._connect)

    def _connect(self):
        gcode = self.printer.lookup_object("gcode")
        self.g28 = gcode.register_command("G28", None)
        gcode.register_command("G28", self.cmd_G28)
        kin = self.printer.lookup_object("toolhead").get_kinematics()
        check_move = kin.check_move

        def checked(move):
            check_move(move)
            self._check(kin, move)

        kin.check_move = checked

    def cmd_G28(self, gcmd):
        self.homing = True
        try:
            self.g28(gcmd)
        finally:
            self.homing = False
        toolhead = self.printer.lookup_object("toolhead")
        low, high = toolhead.get_kinematics().limits[2]
        top = min(self.z + self.clearance, high)
        if low <= high and toolhead.get_position()[2] < top:
            toolhead.manual_move([None, None, top], self.lift_speed)

    def _check(self, kin, move):
        low, high = kin.limits[2]
        if self.homing or low > high:
            return
        start, end = move.start_pos[2], move.end_pos[2]
        if end >= self.z:
            return
        if not move.axes_d[0] and not move.axes_d[1] and end >= start:
            return
        raise move.move_error(
            "Below the Z floor (%.3f); lift Z first" % (self.z,)
        )

    def get_status(self, eventtime):
        return {"z": self.z, "clearance": self.clearance}


def load_config(config):
    return ZFloor(config)
