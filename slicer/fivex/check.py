"""Read back G-code written for corexy_ac and work out where the machine goes."""
import re

import numpy as np

WORD = re.compile(r"([XYZACEF])(-?[\d.]+)")


def read(path):
    pos = {"A": 0.0, "C": 0.0}
    poses, extrude = [], []
    for line in open(path):
        if not line.startswith("G1 "):
            continue
        words = dict(WORD.findall(line))
        e = float(words.pop("E", 0))
        words.pop("F", None)
        if not words:
            continue
        pos.update({k: float(v) for k, v in words.items()})
        if len(pos) < 5:
            continue
        poses.append([pos[k] for k in "XYZAC"])
        extrude.append(e)
    return np.array(poses), np.array(extrude)


def check(path, machine):
    poses, extrude = read(path)
    m = machine.to_machine(poses[:, :3], poses[:, 3], poses[:, 4])
    printing = extrude > 0
    tilt = np.degrees(np.arccos(np.clip(machine.tool_axis(poses[:, 3], poses[:, 4])[:, 2], -1, 1)))
    return {
        "moves": len(poses),
        "extruding moves": int(printing.sum()),
        "filament mm": float(extrude[printing].sum()),
        "A range": (float(poses[:, 3].min()), float(poses[:, 3].max())),
        "C range": (float(poses[:, 4].min()), float(poses[:, 4].max())),
        "largest tilt while printing": float(tilt[printing].max()) if printing.any() else 0.0,
        "largest A step": float(np.abs(np.diff(poses[:, 3])).max()) if len(poses) > 1 else 0.0,
        "largest C step": float(np.abs(np.diff(poses[:, 4])).max()) if len(poses) > 1 else 0.0,
        "machine min": m.min(0).round(2).tolist(),
        "machine max": m.max(0).round(2).tolist(),
        "outside limits": int((~machine.in_limits(m)).sum()),
        "A outside limits": int(((poses[:, 3] < machine.a_min) | (poses[:, 3] > machine.a_max)).sum()),
    }
