# Lowest Z the toolhead may move to once Z is homed
#
# [z_floor]
# z: 12.5
#
# After homing, any move that ends below z is refused, except straight lifts
# (no X/Y travel, Z not going down). G28's own homing moves are exempt, so a
# Z endstop below the floor still works. Probing moves are not exempt.
#
# This file may be distributed under the terms of the GNU GPLv3 license.


class ZFloor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.z = config.getfloat("z")
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
        return {"z": self.z}


def load_config(config):
    return ZFloor(config)
