# Reads a printer.cfg (with includes) to tell the simulated MCUs which pins
# are heaters, thermistors, TMC drivers and SPI devices, and runs a simple
# thermal model so temperatures respond to heater power.
import configparser
import glob
import logging
import math
import os
import re

KLIPPY = os.environ.get("KLIPPY_DIR", "/opt/printer/kalico")
AMBIENT = 25.0
KELVIN = -273.15

# heater power (W), heat capacity (J/K) and loss to ambient (W/K) by heater kind,
# used unless the config has MPC values for that heater
HEATER_MODEL = {"extruder": (40.0, 8.0, 0.1), "heater_bed": (300.0, 900.0, 1.6), "default": (100.0, 300.0, 0.6)}


def read_config(path, seen=None):
    seen = seen if seen is not None else set()
    path = os.path.abspath(path)
    if path in seen:
        return []
    seen.add(path)
    out = []
    section = None
    for raw in open(path, encoding="utf-8", errors="ignore").read().split("\n"):
        if raw.startswith("#*#"):
            # SAVE_CONFIG block: values Klipper merges into the sections above
            raw = raw[3:].strip()
            if raw.startswith("<") or raw.startswith("DO NOT EDIT"):
                continue
        line = re.split(r"(?<!\\)[#;]", raw, 1)[0].rstrip()
        if not line.strip():
            continue
        m = re.match(r"^\[(.+)\]\s*$", line)
        if m:
            section = m.group(1).strip()
            if section.startswith("include "):
                pattern = os.path.join(os.path.dirname(path), section[8:].strip())
                for f in sorted(glob.glob(pattern)):
                    out += read_config(f, seen)
                section = None
                continue
            out.append([section, {}])
            continue
        if section is None:
            continue
        if line[0].isspace() and out and out[-1][1]:
            key = list(out[-1][1])[-1]
            out[-1][1][key] += "\n" + line.strip()
            continue
        m = re.match(r"^([^:=]+?)\s*[:=]\s*(.*)$", line)
        if m:
            out[-1][1][m.group(1).strip().lower()] = m.group(2).strip()
    return out


def sections(path):
    merged = {}
    for name, opts in read_config(path):
        merged.setdefault(name, {}).update(opts)
    return merged


class Thermistor:
    def __init__(self, pullup, inline, params):
        self.pullup = pullup
        self.inline = inline
        if "beta" in params:
            t1, r1, beta = params["t1"] - KELVIN, params["r1"], params["beta"]
            self.c3 = 0.0
            self.c2 = 1.0 / beta
            self.c1 = 1.0 / t1 - self.c2 * math.log(r1)
        else:
            (t1, r1), (t2, r2), (t3, r3) = sorted([(params["t1"], params["r1"]), (params["t2"], params["r2"]), (params["t3"], params["r3"])])
            inv1, inv2, inv3 = [1.0 / (t - KELVIN) for t in (t1, t2, t3)]
            ln1, ln2, ln3 = math.log(r1), math.log(r2), math.log(r3)
            ln3_1, ln3_2, ln3_3 = ln1 ** 3, ln2 ** 3, ln3 ** 3
            inv1_2, inv1_3 = inv1 - inv2, inv1 - inv3
            ln1_2, ln1_3 = ln1 - ln2, ln1 - ln3
            ln3_1_2, ln3_1_3 = ln3_1 - ln3_2, ln3_1 - ln3_3
            self.c3 = (inv1_2 - inv1_3 * ln1_2 / ln1_3) / (ln3_1_2 - ln3_1_3 * ln1_2 / ln1_3)
            if self.c3 <= 0.0:
                beta = ln1_3 / inv1_3
                self.c3 = 0.0
                self.c2 = 1.0 / beta
                self.c1 = inv1 - self.c2 * ln1
            else:
                self.c2 = (inv1_2 - self.c3 * ln3_1_2) / ln1_2
                self.c1 = inv1 - self.c2 * ln1 - self.c3 * ln3_1

    def fraction(self, temp):
        inv_t = 1.0 / (temp - KELVIN)
        if self.c3:
            y = (self.c1 - inv_t) / (2.0 * self.c3)
            x = math.sqrt((self.c2 / (3.0 * self.c3)) ** 3 + y ** 2)
            ln_r = math.copysign(abs(x - y) ** (1.0 / 3.0), x - y) - math.copysign(abs(x + y) ** (1.0 / 3.0), x + y)
        else:
            ln_r = (inv_t - self.c1) / self.c2
        r = math.exp(ln_r) + self.inline
        return r / (self.pullup + r)


class Resistance:
    def __init__(self, pullup, base):
        self.pullup = pullup
        self.base = base

    def fraction(self, temp):
        a, b = 3.9083e-3, -5.775e-7
        r = self.base * (1.0 + a * temp + b * temp * temp)
        return r / (self.pullup + r)


class McuTemp:
    """Inverse of temperature_mcu's linear formulas for common chips."""

    def __init__(self, chip):
        self.chip = (chip or "").lower()

    def fraction(self, temp):
        c = self.chip
        if c.startswith(("stm32f4", "stm32f7")):
            return (943 + (1218 - 943) * (temp - 30.0) / 80.0) / 4095.0
        if c.startswith("rp2040"):
            return (0.706 - 0.001721 * (temp - 27.0)) / 3.3
        if c.startswith("stm32f1") or c.startswith("stm32f0"):
            return (1.43 - 0.0043 * (temp - 25.0)) / 3.3
        if c.startswith("stm32f2"):
            return (0.76 + 0.0025 * (temp - 25.0)) / 3.3
        return None


class Heater:
    def __init__(self, name, opts):
        power, capacity, loss = HEATER_MODEL.get(name, HEATER_MODEL["default"])
        if opts.get("control") == "mpc" or "block_heat_capacity" in opts:
            power = float(opts.get("heater_power", power))
            capacity = float(opts.get("block_heat_capacity", capacity))
            loss = float(opts.get("ambient_transfer", loss))
        self.power, self.capacity, self.loss = power, capacity, max(loss, 1e-3)
        self.temp = AMBIENT
        self.duty = 0.0


class SimConfig:
    def __init__(self, cfg_path, mcu_name, chip=""):
        self.mcu = mcu_name
        self.chip = chip
        self.cfg = sections(cfg_path)
        builtin = os.path.join(KLIPPY, "klippy", "extras", "temperature_sensors.cfg")
        self.builtin = sections(builtin) if os.path.exists(builtin) else {}
        self.aliases = {}
        for name, opts in self.cfg.items():
            if name.split()[0] == "board_pins":
                for part in re.split(r"[,\n]", opts.get("aliases", "")):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        self.aliases[k.strip()] = v.strip()
        self.heaters = {}
        self.heater_pins = {}
        self.sensors = {}
        self.uart_kinds = {}
        self.spi_kinds = {}
        self.last = None
        for name, opts in self.cfg.items():
            kind = name.split()[0]
            if "heater_pin" in opts and kind in ("extruder", "heater_bed", "heater_generic") or name.startswith("extruder"):
                if "heater_pin" not in opts:
                    continue
                h = Heater("extruder" if kind.startswith("extruder") else kind, opts)
                self.heaters[name] = h
                pin = self.local_pin(opts["heater_pin"])
                if pin:
                    self.heater_pins[pin] = h
                self.add_sensor(name, opts, h)
            elif kind in ("temperature_sensor", "temperature_fan"):
                self.add_sensor(name, opts, None)
            if kind.startswith("tmc"):
                chip = kind[3:]
                if "uart_pin" in opts:
                    pin = self.local_pin(opts["uart_pin"])
                    if pin:
                        self.uart_kinds[pin] = chip
                if "cs_pin" in opts:
                    pin = self.local_pin(opts["cs_pin"])
                    if pin:
                        self.spi_kinds[pin] = chip
            if kind in ("adxl345", "lis2dw", "mpu9250") and "cs_pin" in opts:
                pin = self.local_pin(opts["cs_pin"])
                if pin:
                    self.spi_kinds[pin] = kind

    def local_pin(self, desc):
        desc = desc.strip().lstrip("!^~").strip()
        mcu = "mcu"
        if ":" in desc:
            mcu, desc = desc.split(":", 1)
            desc = desc.strip().lstrip("!^~")
        if mcu != self.mcu:
            return None
        return self.aliases.get(desc, desc)

    def thermistor_params(self, sensor_type):
        for src in (self.cfg, self.builtin):
            sec = src.get("thermistor " + sensor_type)
            if sec:
                p = {}
                if "beta" in sec:
                    return {"t1": float(sec["temperature1"]), "r1": float(sec["resistance1"]), "beta": float(sec["beta"])}
                for i in (1, 2, 3):
                    p["t%d" % i] = float(sec["temperature%d" % i])
                    p["r%d" % i] = float(sec["resistance%d" % i])
                return p
        return None

    def add_sensor(self, name, opts, heater):
        if opts.get("sensor_type") == "temperature_mcu":
            if opts.get("sensor_mcu", "mcu") == self.mcu:
                self.sensors["ADC_TEMPERATURE"] = (McuTemp(self.chip), None)
            return
        pin = self.local_pin(opts.get("sensor_pin", "")) if "sensor_pin" in opts else None
        if not pin:
            return
        stype = opts.get("sensor_type", "")
        pullup = float(opts.get("pullup_resistor", 4700.0))
        inline = float(opts.get("inline_resistor", 0.0))
        model = None
        params = self.thermistor_params(stype)
        if params:
            model = Thermistor(pullup, inline, params)
        elif stype in ("PT1000", "PT100"):
            model = Resistance(pullup, 1000.0 if stype == "PT1000" else 100.0)
        if model is None:
            logging.info("sim: sensor %s (%s) is not simulated", name, stype)
            return
        self.sensors[pin] = (model, heater)

    # called by the MCU
    def set_heater_duty(self, pin, duty):
        h = self.heater_pins.get(pin)
        if h is not None:
            h.duty = duty

    def step(self, now):
        if self.last is None:
            self.last = now
            return
        dt = now - self.last
        if dt < 0.05:
            return
        self.last = now
        for h in self.heaters.values():
            target = AMBIENT + h.power * h.duty / h.loss
            h.temp += (target - h.temp) * (1.0 - math.exp(-dt * h.loss / h.capacity))

    def adc_fraction(self, pin, now):
        s = self.sensors.get(pin)
        if s is None:
            return None
        model, heater = s
        temp = heater.temp if heater else (40.0 if isinstance(model, McuTemp) else AMBIENT)
        return model.fraction(temp)

    def uart_driver(self, mcu, oid_pin, addr):
        return self.uart_kinds.get(oid_pin, "2209")

    def spi_device(self, mcu, pin):
        return self.spi_kinds.get(pin, "5160")
