# Checks the simulated MCU against Kalico's own code.
# Run inside the twin:  docker exec printer-twin /opt/klippy-env/bin/python /opt/printer/tests/test_sim.py
import math
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "sim"))
KLIPPY = os.environ.get("KLIPPY_DIR", "/opt/printer/kalico")
sys.path.insert(0, KLIPPY)

import fakemcu  # noqa: E402
import simconfig  # noqa: E402
from klippy.extras import thermistor, tmc_uart  # noqa: E402


def firmware_steps(start, interval, count, add, add2=0, shift=0):
    """Step times by replaying the firmware's per-step arithmetic in floats."""
    scale = 2.0 ** (-shift)
    i, a, a2 = interval * scale, add * scale, add2 * scale
    t, out = start, []
    for _ in range(count):
        t += i
        out.append(t)
        i += a
        a += a2
    return out


class StepperTiming(unittest.TestCase):
    def test_positions_match_firmware(self):
        rng = random.Random(4)
        for _ in range(200):
            st = fakemcu.Stepper(0)
            st.reset_clock(1000.0)
            moves, last = [], 1000.0
            for _ in range(rng.randint(1, 4)):
                count = rng.randint(1, 400)
                interval = rng.randint(2000, 60000)
                add = rng.randint(-20, 20)
                add2 = rng.randint(-1, 1) if rng.random() < 0.5 else 0
                shift = rng.choice([0, 0, 4, 8, 16, -2])
                if interval + add * count < 100 or interval + (add + add2 * count) * count < 100:
                    add = add2 = 0
                st.next_dir = rng.randint(0, 1)
                times = firmware_steps(last, interval, count, add, add2, shift)
                last = times[-1]
                moves.append((times, 1 if st.next_dir else -1))
                st.queue(interval, count, add, add2, shift)
            all_steps = sorted((t, d) for times, d in moves for t in times)
            for probe in [rng.uniform(1000, last + 10) for _ in range(20)]:
                expect = sum(d for t, d in all_steps if t <= probe + 1e-6)
                got = st.position(probe)
                self.assertLessEqual(abs(got - expect), 1, (probe, got, expect))

    def test_stop_keeps_position(self):
        st = fakemcu.Stepper(0)
        st.reset_clock(0.0)
        st.next_dir = 1
        st.queue(1000, 100, 0)
        st.stop(50500.0)
        self.assertEqual(st.position(10 ** 9), 50)
        self.assertEqual(st.moves, [])


class TmcUart(unittest.TestCase):
    def setUp(self):
        self.k = tmc_uart.MCU_TMC_uart_bitbang.__new__(tmc_uart.MCU_TMC_uart_bitbang)

    def test_decodes_host_write(self):
        raw = self.k._encode_write(0xF5, 3, 0x10 | 0x80, 0x00071703)
        frames = fakemcu.uart_bytes_to_frames(bytearray(raw))
        self.assertEqual(frames[1:3], [3, 0x90])
        val = (frames[3] << 24) | (frames[4] << 16) | (frames[5] << 8) | frames[6]
        self.assertEqual(val, 0x00071703)

    def test_reply_is_accepted_by_host(self):
        for reg, val in ((0x06, 0x21000000), (0x02, 7), (0x6F, 0xC0000000)):
            msg = bytearray([0x05, 0xFF, reg, (val >> 24) & 0xFF, (val >> 16) & 0xFF, (val >> 8) & 0xFF, val & 0xFF])
            msg.append(fakemcu.crc8(msg))
            reply = fakemcu.uart_add_bits(msg)
            self.assertEqual(self.k._decode_read(reg, reply), val)

    def test_register_file(self):
        chip = fakemcu.TMC("2209")
        self.assertEqual(chip.read(0x06) >> 24, 0x21)
        before = chip.read(0x02)
        chip.write(0x00, 0x1C0)
        self.assertEqual(chip.read(0x02), before + 1)
        self.assertEqual(chip.read(0x00), 0x1C0)


class Thermistors(unittest.TestCase):
    def test_matches_kalico(self):
        sensors = simconfig.sections(os.path.join(KLIPPY, "klippy", "extras", "temperature_sensors.cfg"))
        checked = 0
        for name, sec in sensors.items():
            if not name.startswith("thermistor ") or "temperature1" not in sec:
                continue
            ours_cfg = simconfig.SimConfig.__new__(simconfig.SimConfig)
            ours_cfg.cfg, ours_cfg.builtin = {}, sensors
            params = ours_cfg.thermistor_params(name[len("thermistor "):])
            ours = simconfig.Thermistor(4700.0, 0.0, params)
            ref = thermistor.Thermistor(4700.0, 0.0)
            if "beta" in params:
                ref.setup_coefficients_beta(params["t1"], params["r1"], params["beta"])
            else:
                ref.setup_coefficients(params["t1"], params["r1"], params["t2"], params["r2"], params["t3"], params["r3"])
            for temp in (25.0, 60.0, 110.0, 215.0, 260.0):
                self.assertAlmostEqual(ours.fraction(temp), ref.calc_adc(temp), places=6, msg=(name, temp))
            checked += 1
        self.assertGreater(checked, 5)

    def test_mcu_temperature_inverse(self):
        m = simconfig.McuTemp("stm32f446xx")
        frac = m.fraction(40.0)
        cal30, cal110 = 943 / 4095.0, 1218 / 4095.0
        temp = 30.0 + (frac - cal30) * (80.0 / (cal110 - cal30))
        self.assertAlmostEqual(temp, 40.0, places=6)


class ConfigReading(unittest.TestCase):
    def test_save_config_block_and_includes(self):
        import tempfile
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "printer.cfg"), "w") as f:
            f.write("[include parts/*.cfg]\n[extruder]\nheater_pin: PA1\nsensor_pin: PA2  # hot\ncontrol: mpc\n"
                    "#*# <---------------------- SAVE_CONFIG ---------------------->\n"
                    "#*# [extruder]\n#*# block_heat_capacity = 7.5\n#*# ambient_transfer = 0.05\n")
        os.makedirs(os.path.join(d, "parts"))
        with open(os.path.join(d, "parts", "bed.cfg"), "w") as f:
            f.write("[heater_bed]\nheater_pin: PB1\nsensor_pin: PB2\n")
        cfg = simconfig.sections(os.path.join(d, "printer.cfg"))
        self.assertEqual(cfg["extruder"]["sensor_pin"], "PA2")
        self.assertEqual(cfg["extruder"]["block_heat_capacity"], "7.5")
        self.assertIn("heater_bed", cfg)
        h = simconfig.Heater("extruder", cfg["extruder"])
        self.assertEqual((h.capacity, h.loss), (7.5, 0.05))


if __name__ == "__main__":
    unittest.main()
