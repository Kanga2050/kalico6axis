"""Run G-code through the real Kalico in batch mode with the printer's config,
set up the way the twin runs it (twin.cfg included, stand-ins for plugins
that are not installed), and report any move Kalico refuses."""
import os
import re
import shutil
import site
import subprocess
import sys
import tempfile

TWIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "twin", "sim")


def prepare(config_dir, kalico_dir, work):
    sys.path.insert(0, os.path.abspath(TWIN))
    from simconfig import read_config
    cfg = os.path.join(work, "config")
    shutil.copytree(config_dir, cfg)
    kal = os.path.join(work, "kalico")
    shutil.copytree(kalico_dir, kal, ignore=shutil.ignore_patterns(".git", "out", "docs", "test", "*.pyc"))
    path = os.path.join(cfg, "printer.cfg")
    lines = open(path, encoding="utf-8", errors="ignore").read().split("\n")
    at = next((i for i, line in enumerate(lines) if line.startswith("#*#")), len(lines))
    extra = ["[include twin.cfg]"] if os.path.exists(os.path.join(cfg, "twin.cfg")) else []
    lines[at:at] = extra + [""]
    open(path, "w").write("\n".join(lines))
    stub = "class Stub:\n    def __init__(self, config):\n        for o in config.get_prefix_options(''):\n            config.get(o, None)\n\n\ndef load_config(config):\n    return Stub(config)\n\n\nload_config_prefix = load_config\n"
    core = re.compile(r"^(printer|mcu|stepper_\w+|extruder\d*|rotary_[abc]|include)$")
    for mod in sorted(set(n.split()[0] for n, _ in read_config(path))):
        if core.match(mod):
            continue
        if not any(os.path.exists(os.path.join(kal, "klippy", d, mod + e))
                   for d in ("extras", "plugins") for e in (".py", "/__init__.py")):
            open(os.path.join(kal, "klippy", "plugins", mod + ".py"), "w").write(stub)
    home = os.path.join(work, "home")
    os.makedirs(os.path.join(home, "printer_data"), exist_ok=True)
    os.symlink(cfg, os.path.join(home, "printer_data", "config"))
    for name in os.listdir(os.path.join(cfg, "twin_home")) if os.path.isdir(os.path.join(cfg, "twin_home")) else []:
        shutil.copy(os.path.join(cfg, "twin_home", name), os.path.join(home, name))
    mcus = [n.split()[1] if len(n.split()) > 1 else None for n, _ in read_config(path) if n.split()[0] == "mcu"]
    return path, kal, home, mcus


def run(gcode, config_dir, kalico_dir, dicts, timeout=3600):
    work = tempfile.mkdtemp(prefix="split5-fw-")
    try:
        cfg, kal, home, mcus = prepare(config_dir, kalico_dir, work)
        args = [sys.executable, os.path.join(kal, "klippy", "klippy.py"), cfg, "-i", gcode,
                "-o", os.devnull, "-l", os.path.join(work, "klippy.log")]
        for name in mcus:
            args += ["-d", (name + "=" if name else "") + dicts[name or "mcu"]]
        res = subprocess.run(args, env=dict(os.environ, HOME=home, PYTHONUSERBASE=site.getuserbase()), capture_output=True, text=True, timeout=timeout)
        log = open(os.path.join(work, "klippy.log"), errors="ignore").read()
        run_log = log.split("=======================\n")[-1]
        errors = [line for line in run_log.split("\n") if re.search(
            r"out of range|Must home|Z floor|Internal error|Traceback|Error|Exception|!!", line)]
        return res.returncode, errors, log
    finally:
        shutil.rmtree(work, ignore_errors=True)
