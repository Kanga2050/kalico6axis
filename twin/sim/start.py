#!/usr/bin/env python3
# Printer twin entrypoint: simulated MCUs + real Kalico + Moonraker + Mainsail
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from simconfig import read_config  # noqa: E402
from build_dict import build  # noqa: E402

DATA = "/data/printer_data"
CFG = os.path.join(DATA, "config")
CACHE = "/data/cache"
KALICO = "/opt/printer/kalico"
MOONRAKER = "/opt/printer/moonraker"
SIM = os.path.dirname(os.path.abspath(__file__))
KLIPPY_PY = "/opt/klippy-env/bin/python"
MOON_PY = "/opt/moonraker-env/bin/python"

MOONRAKER_CONF = """[server]
host: 0.0.0.0
port: 7125
klippy_uds_address: {data}/comms/klippy.sock

[authorization]
trusted_clients:
    0.0.0.0/0
    ::/0
cors_domains:
    *

[machine]
provider: none
validate_service: False

[file_manager]
enable_object_processing: False

[history]
"""


def log(*a):
    print("twin:", *a, flush=True)


def find_mcus():
    mcus = []
    overrides = {}
    path = os.path.join(CFG, "twin.json")
    if os.path.exists(path):
        overrides = json.load(open(path)).get("mcus", {})
    for name, opts in read_config(os.path.join(CFG, "printer.cfg")):
        parts = name.split()
        if parts[0] != "mcu":
            continue
        mname = parts[1] if len(parts) > 1 else "mcu"
        serial = opts.get("serial")
        chip = overrides.get(mname, {}).get("chip")
        if serial is None:
            if "canbus_uuid" in opts:
                log("mcu '%s' uses CAN; set its chip in twin.json and a serial path to simulate it" % mname)
            serial = overrides.get(mname, {}).get("serial")
            if serial is None:
                continue
        if chip is None:
            if "klipper_host_mcu" in serial:
                chip = "linux"
            else:
                m = re.search(r"Klipper_([a-z0-9]+?)(?:xx)?_", os.path.basename(serial), re.I)
                chip = m.group(1).lower() if m else None
        if chip is None:
            log("can't tell the chip of mcu '%s' from %s; add it to twin.json" % (mname, serial))
            continue
        mcus.append((mname, serial, chip))
    return mcus


STUB = '''# Stand-in created by the printer twin for "{name}", which is not installed here.
class Stub:
    def __init__(self, config):
        for option in config.get_prefix_options(""):
            config.get(option, None)


def load_config(config):
    return Stub(config)


def load_config_prefix(config):
    return Stub(config)
'''
CORE = re.compile(r"^(printer|mcu|stepper_\w+|extruder\d*|rotary_[abc]|include)$")


def stub_missing_modules():
    klippy = os.path.join(KALICO, "klippy")
    names = set(name.split()[0] for name, _ in read_config(os.path.join(CFG, "printer.cfg")))
    for mod in sorted(names):
        if CORE.match(mod):
            continue
        found = any(os.path.exists(os.path.join(klippy, d, mod + ext))
                    for d in ("extras", "plugins") for ext in (".py", "/__init__.py"))
        if not found:
            log("config section [%s] needs a plugin that isn't installed; using a stand-in" % mod)
            with open(os.path.join(klippy, "plugins", mod + ".py"), "w") as f:
                f.write(STUB.format(name=mod))


def link_home_paths():
    """Configs refer to ~/printer_data or /home/<user>/...; point those here."""
    home = os.path.expanduser("~")
    # state the printer keeps outside its config folder (e.g. save_variables) can be
    # dropped into config/twin_home and appears in the twin's home folder
    extra = os.path.join(CFG, "twin_home")
    if os.path.isdir(extra):
        for name in os.listdir(extra):
            dst = os.path.join(home, name)
            if not os.path.lexists(dst):
                shutil.copy(os.path.join(extra, name), dst)
    if not os.path.lexists(os.path.join(home, "printer_data")):
        os.symlink(DATA, os.path.join(home, "printer_data"))
    users = set()
    for root, _, files in os.walk(CFG):
        for f in files:
            if f.endswith((".cfg", ".conf")):
                try:
                    users |= set(re.findall(r"/home/([A-Za-z0-9_.-]+)/", open(os.path.join(root, f), errors="ignore").read()))
                except OSError:
                    pass
    for user in users:
        path = "/home/" + user
        if not os.path.lexists(path):
            os.makedirs("/home", exist_ok=True)
            os.symlink(home, path)


def main():
    for d in ("config", "gcodes", "logs", "comms", "database"):
        os.makedirs(os.path.join(DATA, d), exist_ok=True)
    link_home_paths()
    if not os.path.exists(os.path.join(CFG, "printer.cfg")):
        log("no printer.cfg in the config folder")
        sys.exit(1)
    procs = []
    os.makedirs("/tmp/twin", exist_ok=True)
    with open("/tmp/twin/host_temp", "w") as f:
        f.write("45000\n")
    env = dict(os.environ, KLIPPY_DIR=KALICO, KLIPPY_SIM_HOST_TEMP_FILE="/tmp/twin/host_temp", PYTHONPATH=KALICO)
    specs = []
    for name, serial, chip in find_mcus():
        dict_path = os.path.join(CACHE, chip + ".dict")
        if not os.path.exists(dict_path):
            log("building %s firmware dictionary (first run only)" % chip)
            build(chip, dict_path)
        log("simulating mcu '%s' (%s) at %s" % (name, chip, serial))
        specs += ["--mcu", "%s:%s:%s" % (name, dict_path, serial)]
    if specs:
        procs.append(subprocess.Popen([KLIPPY_PY, os.path.join(SIM, "fakemcu.py"), "--config",
                                       os.path.join(CFG, "printer.cfg")] + specs, env=env))
    stub_missing_modules()
    time.sleep(1.0)
    with open("/tmp/twin/moonraker.conf", "w") as f:
        f.write(MOONRAKER_CONF.format(data=DATA))
    subprocess.run(["nginx"], check=True)
    klippy_cmd = [KLIPPY_PY, os.path.join(KALICO, "klippy", "klippy.py"), os.path.join(CFG, "printer.cfg"),
                  "-I", os.path.join(DATA, "comms", "klippy.serial"), "-l", os.path.join(DATA, "logs", "klippy.log"),
                  "-a", os.path.join(DATA, "comms", "klippy.sock")]
    moon_cmd = [MOON_PY, os.path.join(MOONRAKER, "moonraker", "moonraker.py"), "-d", DATA, "-c", "/tmp/twin/moonraker.conf",
                "-l", os.path.join(DATA, "logs", "moonraker.log")]
    klippy = subprocess.Popen(klippy_cmd, env=env, cwd=KALICO)
    moon = subprocess.Popen(moon_cmd, env=env)
    log("ready: open http://localhost:8080")

    def stop(*_):
        for p in [klippy, moon] + procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        time.sleep(2)
        if klippy.poll() is not None:
            log("klippy exited, restarting")
            klippy = subprocess.Popen(klippy_cmd, env=env, cwd=KALICO)
        if moon.poll() is not None:
            log("moonraker exited, restarting")
            moon = subprocess.Popen(moon_cmd, env=env)
        for i, p in enumerate(procs):
            if p.poll() is not None:
                log("a simulated mcu exited (code %s)" % p.returncode)
                procs.pop(i)
                break


if __name__ == "__main__":
    main()
