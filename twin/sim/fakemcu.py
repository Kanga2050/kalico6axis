#!/usr/bin/env python3
# Simulated Klipper/Kalico MCU.
#
# Speaks the real serial protocol over a pty using the data dictionary of a
# real firmware build, so the host checks pins and commands exactly as it
# would on the printer. Steppers, endstops, trsync, heaters, ADC, TMC UART
# and SPI, PWM and neopixels are emulated; no physical sensors.
#
# usage: fakemcu.py --dict klipper.dict --link /dev/serial/by-id/... \
#                   --name mcu --config printer.cfg [--sensors sensors.json]
import argparse
import bisect
import heapq
import inspect
import json
import logging
import math
import os
import pty
import select
import sys
import termios
import time
import tty
import zlib

KLIPPY = os.environ.get("KLIPPY_DIR", "/opt/printer/kalico")
sys.path.insert(0, KLIPPY)
from klippy import msgproto  # noqa: E402

from simconfig import SimConfig  # noqa: E402

TRIGGER_DELAY = 0.30  # first homing of an endstop triggers after this much motion (s)
START = time.monotonic()  # shared time base so all MCUs agree on wall time
WORLD = []  # all simulated MCUs in this process
REFS = {}  # endstop -> (linked steppers, summed step position where it triggers)
REASON_ENDSTOP_HIT = 1


def crc16(buf):
    return list(msgproto.crc16_ccitt(buf))


class Stepper:
    """Tracks queued steps with the firmware's interval maths."""

    def __init__(self, oid):
        self.oid = oid
        self.base = 0
        self.moves = []  # [start, I, A, A2, count, sign, end]
        self.last = 0.0
        self.next_dir = 1
        self.trsync = None
        self.reset32 = None

    @staticmethod
    def offset(move, k):
        _, i, a, a2, _, _, _ = move
        return k * i + a * k * (k - 1) / 2.0 + a2 * k * (k - 1) * (k - 2) / 6.0

    def queue(self, interval, count, add, add2=0, shift=0):
        scale = 2.0 ** (-shift)
        move = [self.last, interval * scale, add * scale, add2 * scale, count, 1 if self.next_dir else -1, 0.0]
        move[6] = self.last + self.offset(move, count)
        self.last = move[6]
        self.moves.append(move)

    def reset_clock(self, clock):
        self.last = float(clock)

    def steps_at(self, move, clock):
        if clock >= move[6]:
            return move[4]
        if clock < move[0] + move[1]:
            return 0
        lo, hi = 0, move[4]
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if move[0] + self.offset(move, mid) <= clock:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def position(self, clock):
        pos = self.base
        for m in self.moves:
            pos += m[5] * self.steps_at(m, clock)
        return pos

    def fold(self, clock):
        keep = []
        for m in self.moves:
            if m[6] <= clock:
                self.base += m[5] * m[4]
            else:
                keep.append(m)
        self.moves = keep

    def stop(self, clock):
        self.base = self.position(clock)
        self.moves = []
        self.last = 0.0

    def first_step_after(self, clock):
        for m in self.moves:
            t = m[0] + m[1]
            if m[6] > clock:
                return max(t, clock) if t < clock else t
        return None


class Trsync:
    def __init__(self, oid):
        self.oid = oid
        self.active = False
        self.triggered_reason = 0
        self.trigger_clock = 0
        self.report_clock = 0
        self.report_ticks = 0
        self.expire_clock = None
        self.expire_reason = 0
        self.steppers = []


class TMC:
    """Register file shared by the UART and SPI front ends."""

    def __init__(self, kind):
        self.kind = kind
        self.regs = {}
        self.ifcnt = 0
        self.last_read = 0
        version = {"2209": 0x21, "2208": 0x20, "2226": 0x21, "5160": 0x30, "2130": 0x11, "2240": 0x40, "2660": 0x00}.get(kind, 0x21)
        ioin = 0x06 if kind in ("2208", "2209", "2226") else 0x04
        self.regs[ioin] = version << 24

    def read(self, reg):
        if reg == 0x02 and self.kind in ("2208", "2209", "2226"):
            return self.ifcnt & 0xFF
        if reg in (0x01,):  # GSTAT
            return 0
        return self.regs.get(reg, 0)

    def write(self, reg, val):
        self.regs[reg] = val
        self.ifcnt += 1


def uart_bytes_to_frames(data):
    mval = 0
    for i, d in enumerate(data):
        mval |= d << (8 * i)
    return [(mval >> (10 * i + 1)) & 0xFF for i in range(len(data) * 8 // 10)]


def uart_add_bits(data):
    out = pos = 0
    for d in data:
        out |= ((d << 1) | 0x200) << pos
        pos += 10
    return bytes((out >> (i * 8)) & 0xFF for i in range((pos + 7) // 8))


def crc8(data):
    crc = 0
    for b in data:
        for _ in range(8):
            if (crc >> 7) ^ (b & 0x01):
                crc = (crc << 1) ^ 0x07
            else:
                crc <<= 1
            crc &= 0xFF
            b >>= 1
    return crc


class FakeMCU:
    def __init__(self, name, dict_path, link, config):
        self.name = name
        raw = open(dict_path, "rb").read()
        self.identify = zlib.compress(raw)
        self.parser = msgproto.MessageParser()
        self.parser.process_identify(raw, decompress=False)
        self.freq = float(self.parser.get_constant_float("CLOCK_FREQ"))
        self.adc_max = float(self.parser.get_constant_float("ADC_MAX", 4095))
        self.pwm_max = float(self.parser.get_constant_float("PWM_MAX", 255))
        self.pins = {v: k for k, v in self.parser.get_enumerations().get("pin", {}).items()}
        self.sim = SimConfig(config, self.name, self.parser.get_constant("MCU", ""))
        self.t0 = START
        self.reset_state()
        self.master, slave = pty.openpty()
        tty.setraw(self.master)
        attrs = termios.tcgetattr(self.master)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(self.master, termios.TCSANOW, attrs)
        self.slave_name = os.ttyname(slave)
        self.link = link
        os.makedirs(os.path.dirname(self.link), exist_ok=True)
        if os.path.lexists(self.link):
            os.unlink(self.link)
        os.symlink(self.slave_name, self.link)
        self.slave = slave
        logging.info("%s: %s -> %s (%.0f Hz)", self.name, self.link, self.slave_name, self.freq)

    def pin_name(self, pin):
        return pin if isinstance(pin, str) else self.pins.get(pin)

    def reset_state(self):
        self.seq = 0x10
        self.inbuf = b""
        self.is_config = 0
        self.crc = 0
        self.shutdown = 0
        self.steppers = {}
        self.trsyncs = {}
        self.endstops = {}
        self.adcs = {}
        self.pwms = {}
        self.uarts = {}
        self.spis = {}
        self.neopixels = set()
        self.timers = []
        self.timer_seq = 0
        self.oid_pins = {}

    # clocks
    def now(self):
        return (time.monotonic() - self.t0) * self.freq

    def c32(self, clock):
        return int(clock) & 0xFFFFFFFF

    def c64(self, clock32):
        now = int(self.now())
        diff = (clock32 - now) & 0xFFFFFFFF
        if diff & 0x80000000:
            diff -= 0x100000000
        return now + diff

    def schedule(self, clock, func):
        self.timer_seq += 1
        heapq.heappush(self.timers, (clock, self.timer_seq, func))

    # wire format
    def send(self, name, **params):
        fmt = self.parser.messages_by_name[name]
        payload = fmt.encode_by_name(**params)
        self.write_block(payload)

    def write_block(self, payload):
        block = [len(payload) + 5, self.seq] + payload
        block += crc16(block) + [0x7E]
        os.write(self.master, bytes(block))

    def receive(self, data):
        self.inbuf += data
        while self.inbuf:
            n = self.parser.check_packet(self.inbuf)
            if n == 0:
                return
            if n < 0:
                idx = self.inbuf.find(b"\x7e")
                self.inbuf = self.inbuf[idx + 1:] if idx >= 0 else b""
                continue
            block, self.inbuf = self.inbuf[:n], self.inbuf[n:]
            seq = block[1]
            if seq != self.seq:
                self.write_block([])
                continue
            self.seq = ((seq + 1) & 0x0F) | 0x10
            pos = 2
            msgs = []
            while pos < n - 3:
                msgid, _ = self.parser.msgid_parser.parse(block, pos)
                fmt = self.parser.messages_by_id.get(msgid)
                if fmt is None:
                    logging.warning("%s: unknown msgid %d", self.name, msgid)
                    break
                params, pos = fmt.parse(block, pos)
                msgs.append((fmt.name, params))
            for name, params in msgs:
                handler = getattr(self, "cmd_" + name, None)
                if handler:
                    try:
                        sig = inspect.signature(handler).parameters
                        if not any(p.kind == p.VAR_KEYWORD for p in sig.values()):
                            params = {k: v for k, v in params.items() if k in sig}
                        else:
                            params = {k: v for k, v in params.items() if not k.startswith("#")}
                        handler(**params)
                    except Exception:
                        logging.exception("%s: %s failed", self.name, name)
            self.write_block([])

    # base commands
    def cmd_identify(self, offset, count):
        self.send("identify_response", offset=offset, data=self.identify[offset:offset + count])

    def cmd_get_uptime(self):
        c = int(self.now())
        self.send("uptime", high=c >> 32, clock=c & 0xFFFFFFFF)

    def cmd_get_clock(self):
        self.send("clock", clock=self.c32(self.now()))

    def cmd_get_config(self):
        self.send("config", is_config=self.is_config, crc=self.crc, is_shutdown=self.shutdown, move_count=4096)

    def cmd_allocate_oids(self, count):
        pass

    def cmd_finalize_config(self, crc):
        self.crc = crc
        self.is_config = 1

    def cmd_emergency_stop(self):
        self.do_shutdown()

    def cmd_clear_shutdown(self):
        self.shutdown = 0

    def cmd_reset(self):
        logging.info("%s: reset", self.name)
        self.reset_state()

    # factory temperature calibration words that temperature_mcu reads from flash
    CALIBRATION = {
        0x1FFF7A2C: 943, 0x1FFF7A2E: 1218,      # STM32F4/F7 TS_CAL1, TS_CAL2
        0x1FFFF7B8: 1771, 0x1FFFF7C2: 1340,     # STM32F0
        0x1FFF75A8: 1036, 0x1FFF75CA: 1366,     # STM32G0/G4/L4
        0x1FF1E820: 12520, 0x1FF1E840: 15960,   # STM32H7 (16-bit)
        0x1FF1E860: 12520, 0x1FF800FA: 1218,
    }

    def cmd_debug_read(self, order, addr):
        val = self.CALIBRATION.get(addr, 1000 if order == 1 else 0)
        self.send("debug_result", val=val)

    def cmd_debug_ping(self, data):
        self.send("pong", data=data)

    def do_shutdown(self):
        if not self.shutdown:
            self.shutdown = 1
            self.send("shutdown", clock=self.c32(self.now()), static_string_id=0)

    # steppers
    def cmd_config_stepper(self, oid, step_pin, dir_pin, invert_step, step_pulse_ticks=0):
        self.steppers[oid] = Stepper(oid)

    def cmd_set_next_step_dir(self, oid, dir):
        self.steppers[oid].next_dir = dir

    def cmd_reset_step_clock(self, oid, clock):
        self.steppers[oid].reset32 = clock

    def resync(self, st, interval, shift=0):
        # after reset_step_clock the first interval is relative to a raw 32-bit clock
        if st.reset32 is not None:
            first = int(interval * 2.0 ** (-shift))
            st.last = self.c64((st.reset32 + first) & 0xFFFFFFFF) - first
            st.reset32 = None

    def cmd_queue_step(self, oid, interval, count, add):
        st = self.steppers[oid]
        self.resync(st, interval)
        st.queue(interval, count, add)

    def cmd_queue_step_hp(self, oid, interval, count, add, add2, shift):
        st = self.steppers[oid]
        self.resync(st, interval, shift)
        st.queue(interval, count, add, add2, shift)

    def cmd_stepper_get_position(self, oid):
        s = self.steppers[oid]
        pos = s.position(self.now())
        self.send("stepper_position", oid=oid, pos=int(pos))

    def cmd_stepper_stop_on_trigger(self, oid, trsync_oid):
        self.trsyncs.setdefault(trsync_oid, Trsync(trsync_oid)).steppers.append(oid)

    # trsync
    def cmd_config_trsync(self, oid):
        self.trsyncs[oid] = Trsync(oid)

    def cmd_trsync_start(self, oid, report_clock, report_ticks, expire_reason):
        ts = self.trsyncs.setdefault(oid, Trsync(oid))
        ts.active = True
        ts.started = time.monotonic()
        ts.triggered_reason = 0
        ts.steppers = []
        ts.expire_clock = None
        ts.expire_reason = expire_reason
        ts.report_ticks = report_ticks
        ts.gen = getattr(ts, "gen", 0) + 1
        if report_ticks:
            ts.report_clock = self.c64(report_clock)
            gen = ts.gen
            self.schedule(ts.report_clock, lambda: self.trsync_report(ts, gen))

    def trsync_report(self, ts, gen):
        if not ts.active or ts.gen != gen:
            return
        self.send("trsync_state", oid=ts.oid, can_trigger=1, trigger_reason=0, clock=self.c32(self.now()))
        ts.report_clock += ts.report_ticks
        self.schedule(ts.report_clock, lambda: self.trsync_report(ts, gen))

    def cmd_trsync_set_timeout(self, oid, clock):
        ts = self.trsyncs.setdefault(oid, Trsync(oid))
        ts.expire_clock = self.c64(clock)
        deadline = ts.expire_clock
        self.schedule(deadline, lambda: self.trsync_expire(ts, deadline))

    def trsync_expire(self, ts, clock):
        if ts.active and ts.expire_clock == clock:
            self.trsync_trigger(ts, ts.expire_reason, self.now())

    def cmd_trsync_trigger(self, oid, reason):
        ts = self.trsyncs.setdefault(oid, Trsync(oid))
        if ts.active:
            self.trsync_trigger(ts, reason, self.now())
        self.send("trsync_state", oid=oid, can_trigger=0, trigger_reason=ts.triggered_reason, clock=self.c32(ts.trigger_clock))

    def trsync_trigger(self, ts, reason, clock):
        logging.debug("%s trsync %d trigger reason=%d", self.name, ts.oid, reason)
        ts.active = False
        ts.triggered_reason = reason
        ts.trigger_clock = clock
        for soid in ts.steppers:
            if soid in self.steppers:
                self.steppers[soid].stop(clock)
        for es in self.endstops.values():
            if es.get("trsync") == ts.oid:
                es["homing"] = False
        self.send("trsync_state", oid=ts.oid, can_trigger=0, trigger_reason=reason, clock=self.c32(clock))

    # endstops and probes
    def cmd_config_endstop(self, oid, pin, pull_up):
        self.endstops[oid] = {"pin": self.pin_name(pin), "homing": False, "trigger_value": 1, "next_clock": 0, "rest": 0}

    def cmd_endstop_home(self, oid, clock, sample_ticks, sample_count, rest_ticks, pin_value, trsync_oid, trigger_reason):
        es = self.endstops[oid]
        if not sample_count:
            es["homing"] = False
            return
        start_w = max(time.monotonic(), START + self.c64(clock) / self.freq)
        es.update(homing=True, trigger_value=pin_value, trsync=trsync_oid, reason=trigger_reason, rest=rest_ticks,
                  start_w=start_w, first=None, motion_w=None, last_w=None)
        self.schedule(self.now() + 0.01 * self.freq, lambda: self.endstop_poll(oid))

    def linked_steppers(self, ts):
        """Steppers stopped by this homing, on this and the other MCUs."""
        out = [(self, s) for s in ts.steppers if s in self.steppers]
        for m in WORLD:
            if m is self:
                continue
            for t in m.trsyncs.values():
                if t.active and t.steppers:
                    out += [(m, s) for s in t.steppers if s in m.steppers]
        return out

    @staticmethod
    def step_vec(links, wall):
        return [m.steppers[s].position((wall - START) * m.freq) for m, s in links]

    @staticmethod
    def along(ref, vec):
        _, p_ref, d = ref
        return sum((v - r) * k for v, r, k in zip(vec, p_ref, d))

    def endstop_poll(self, oid):
        es = self.endstops.get(oid)
        if not es or not es["homing"]:
            return
        ts = self.trsyncs.get(es["trsync"])
        if ts is None or not ts.active:
            es["homing"] = False
            return
        now_w = time.monotonic()
        if now_w < es["start_w"]:
            self.schedule(self.now() + 0.003 * self.freq, lambda: self.endstop_poll(oid))
            return
        links = sorted(self.linked_steppers(ts), key=lambda l: (l[0].name, l[1]))
        if not links and now_w - es["start_w"] < 1.0:
            self.schedule(self.now() + 0.005 * self.freq, lambda: self.endstop_poll(oid))
            return
        sig = tuple((m.name, s) for m, s in links)
        cur = self.step_vec(links, now_w)
        if es["first"] is None:
            es["first"], es["last_w"] = self.step_vec(links, es["start_w"]), es["start_w"]
        trig_w = None
        delta = [c - f for c, f in zip(cur, es["first"])]
        norm = math.sqrt(sum(d * d for d in delta))
        ref = None
        if links and norm > 2:
            direction = [d / norm for d in delta]
            # endstops that drive the same steppers the same way share one physical trigger point
            for r in REFS.get(sig, []):
                if sum(x * y for x, y in zip(r[2], direction)) > 0.9:
                    ref = r
                    break
        if ref is not None:
            if self.along(ref, es["first"]) < 0 <= self.along(ref, cur):
                lo, hi = es["last_w"], now_w
                for _ in range(24):
                    mid = (lo + hi) / 2
                    if self.along(ref, self.step_vec(links, mid)) >= 0:
                        hi = mid
                    else:
                        lo = mid
                trig_w = hi
        else:
            if es["motion_w"] is None and (norm > 0 or not links):
                es["motion_w"] = now_w
            if es["motion_w"] is not None and now_w >= es["motion_w"] + TRIGGER_DELAY:
                trig_w = now_w
                if links and norm:
                    REFS.setdefault(sig, []).append((sig, cur, [d / norm for d in delta]))
        es["last_w"] = now_w
        if now_w - es.get("dbg", 0) > 0.25:
            es["dbg"] = now_w
            logging.debug("%s poll es%d links=%d cur=%s", self.name, oid, len(links), cur)
        if trig_w is not None:
            logging.debug("%s endstop %d trigger", self.name, oid)
            trig = (trig_w - START) * self.freq
            es["homing"] = False
            es["next_clock"] = trig + es["rest"]
            self.trsync_trigger(ts, es["reason"], trig)
            return
        self.schedule(self.now() + 0.003 * self.freq, lambda: self.endstop_poll(oid))

    def cmd_endstop_query_state(self, oid):
        es = self.endstops[oid]
        self.send("endstop_state", oid=oid, homing=1 if es["homing"] else 0, next_clock=self.c32(es["next_clock"]),
                  pin_value=0 if es["trigger_value"] else 1)

    # outputs
    def cmd_config_digital_out(self, oid, pin, value, default_value, max_duration):
        self.pwms[oid] = {"pin": self.pin_name(pin), "cycle": 0, "duty": float(value)}

    def cmd_set_digital_out_pwm_cycle(self, oid, cycle_ticks):
        self.pwms[oid]["cycle"] = cycle_ticks

    def cmd_queue_digital_out(self, oid, clock, on_ticks):
        p = self.pwms[oid]
        duty = on_ticks / p["cycle"] if p["cycle"] else (1.0 if on_ticks else 0.0)
        self.schedule(self.c64(clock), lambda: self.set_duty(oid, duty))

    def cmd_update_digital_out(self, oid, value):
        self.set_duty(oid, float(value))

    def cmd_set_digital_out(self, pin, value):
        pass

    def cmd_config_pwm_out(self, oid, pin, cycle_ticks, value, default_value, max_duration):
        self.pwms[oid] = {"pin": self.pin_name(pin), "cycle": cycle_ticks, "duty": value / self.pwm_max}

    def cmd_queue_pwm_out(self, oid, clock, value):
        self.schedule(self.c64(clock), lambda: self.set_duty(oid, value / self.pwm_max))

    def set_duty(self, oid, duty):
        p = self.pwms.get(oid)
        if p is None:
            return
        p["duty"] = max(0.0, min(1.0, duty))
        self.sim.set_heater_duty(p["pin"], p["duty"])

    # analog inputs
    def cmd_config_analog_in(self, oid, pin):
        self.adcs[oid] = {"pin": self.pin_name(pin)}

    def cmd_query_analog_in(self, oid, clock, sample_ticks, sample_count, rest_ticks, min_value, max_value, range_check_count):
        a = self.adcs[oid]
        a.update(count=sample_count, rest=rest_ticks, minv=min_value, maxv=max_value, next=self.c64(clock))
        if rest_ticks:
            self.schedule(a["next"], lambda: self.adc_report(oid))

    def adc_report(self, oid):
        a = self.adcs.get(oid)
        if not a or not a.get("rest"):
            return
        frac = self.sim.adc_fraction(a["pin"], time.monotonic())
        lo, hi = a["minv"], a["maxv"]
        if frac is None:
            value = (lo + hi) // 2 if hi > lo else int(self.adc_max * a["count"] / 2)
        else:
            value = int(round(frac * self.adc_max * a["count"]))
            if hi > lo:
                value = max(lo, min(hi, value))
        a["next"] += a["rest"]
        self.send("analog_in_state", oid=oid, next_clock=self.c32(a["next"]), value=value)
        self.schedule(a["next"], lambda: self.adc_report(oid))

    # TMC over UART
    def cmd_config_tmcuart(self, oid, rx_pin, pull_up, tx_pin, bit_time):
        self.uarts[oid] = {"pin": self.pin_name(rx_pin)}

    def cmd_tmcuart_send(self, oid, write, read):
        frames = uart_bytes_to_frames(bytearray(write))
        bus = self.uarts.setdefault(oid, {})
        reply = b""
        if len(frames) >= 4:
            addr, reg = frames[1], frames[2]
            chip = bus.setdefault(addr, TMC(self.sim.uart_driver(self.name, bus.get("pin"), addr)))
            if reg & 0x80 and len(frames) >= 8:
                val = (frames[3] << 24) | (frames[4] << 16) | (frames[5] << 8) | frames[6]
                chip.write(reg & 0x7F, val)
            elif read:
                val = chip.read(reg)
                msg = bytearray([0x05, 0xFF, reg, (val >> 24) & 0xFF, (val >> 16) & 0xFF, (val >> 8) & 0xFF, val & 0xFF])
                msg.append(crc8(msg))
                reply = uart_add_bits(msg)
        self.send("tmcuart_response", oid=oid, read=reply)

    # SPI
    def cmd_config_spi(self, oid, pin, cs_active_high):
        self.spis[oid] = {"pin": self.pin_name(pin), "dev": None}

    def cmd_config_spi_without_cs(self, oid):
        self.spis[oid] = {"pin": None, "dev": None}

    def spi_device(self, oid):
        s = self.spis.setdefault(oid, {"pin": None, "dev": None})
        if s["dev"] is None:
            kind = self.sim.spi_device(self.name, s["pin"])
            s["dev"] = TMC(kind) if kind.isdigit() else {"kind": kind}
        return s["dev"]

    def spi_xfer(self, oid, data):
        dev = self.spi_device(oid)
        data = bytearray(data)
        if isinstance(dev, TMC):
            out = bytearray()
            for i in range(0, len(data), 5):
                chunk = data[i:i + 5]
                val = dev.read(dev.last_read)
                out += bytes([0x00, (val >> 24) & 0xFF, (val >> 16) & 0xFF, (val >> 8) & 0xFF, val & 0xFF])[:len(chunk)]
                reg = chunk[0]
                if reg & 0x80 and len(chunk) == 5:
                    dev.write(reg & 0x7F, int.from_bytes(chunk[1:5], "big"))
                dev.last_read = reg & 0x7F
            return bytes(out)
        if dev.get("kind") == "adxl345" and data and data[0] & 0x80:
            reg = data[0] & 0x3F
            regs = {0x00: 0xE5}
            return bytes([0] + [regs.get(reg + i, 0) for i in range(len(data) - 1)])
        return bytes(len(data))

    def cmd_spi_set_bus(self, oid, spi_bus, mode, rate):
        pass

    def cmd_spi_set_sw_bus(self, oid, miso_pin, mosi_pin, sclk_pin, mode, pulse_ticks):
        pass

    def cmd_spi_send(self, oid, data):
        self.spi_xfer(oid, data)

    def cmd_spi_transfer(self, oid, data):
        self.send("spi_transfer_response", oid=oid, response=self.spi_xfer(oid, data))

    # misc peripherals
    def cmd_config_neopixel(self, oid, pin, **kw):
        self.neopixels.add(oid)

    def cmd_neopixel_update(self, oid, pos, data):
        pass

    def cmd_neopixel_send(self, oid):
        self.send("neopixel_result", oid=oid, success=1)

    def cmd_i2c_read(self, oid, reg, read_len):
        self.send("i2c_read_response", oid=oid, response=bytes(read_len))

    def cmd_query_adxl345_status(self, oid):
        self.send("sensor_bulk_status", oid=oid, clock=self.c32(self.now()), query_ticks=0, next_sequence=0, buffered=0,
                  possible_overflows=0)

    # main loop
    def poll(self):
        now = self.now()
        while self.timers and self.timers[0][0] <= now:
            _, _, func = heapq.heappop(self.timers)
            func()
        self.sim.step(time.monotonic())
        for s in self.steppers.values():
            if len(s.moves) > 64:
                s.fold(now)
        if self.timers:
            return max(0.0, (self.timers[0][0] - now) / self.freq)
        return 0.05

    def read(self):
        try:
            data = os.read(self.master, 4096)
        except OSError:
            data = b""
        if data:
            self.receive(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mcu", action="append", required=True, help="name:dict_path:serial_link")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s fakemcu %(message)s")
    for spec in args.mcu:
        name, dict_path, link = spec.split(":", 2)
        WORLD.append(FakeMCU(name, dict_path, link, args.config))
    by_fd = {m.master: m for m in WORLD}
    while True:
        wait = min([0.05] + [m.poll() for m in WORLD])
        r, _, _ = select.select(list(by_fd), [], [], wait)
        for fd in r:
            by_fd[fd].read()


if __name__ == "__main__":
    main()
