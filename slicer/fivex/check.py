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


def support(path, near=0.8, far=2.0, step=0.4):
    from scipy.spatial import cKDTree
    pos = {"A": 0.0, "C": 0.0}
    layers, cur, last, bed = [], [], None, None
    for line in open(path):
        if line.startswith(";Z:") and bed is None:
            bed = float(line[3:]) + 0.2
        if line.startswith(";LAYER_CHANGE"):
            if cur:
                layers.append(np.vstack(cur))
            cur = []
            continue
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
        p = np.array([pos["X"], pos["Y"], pos["Z"]])
        if e > 0 and last is not None:
            n = max(1, int(np.ceil(np.linalg.norm(p - last) / step)))
            cur.append(last + np.outer(np.arange(1, n + 1) / n, p - last))
        last = p
    if cur:
        layers.append(np.vstack(cur))
    old, recent, midair, steep, total = None, [], 0, 0, 0
    for pts in layers:
        dist = np.full(len(pts), np.inf)
        for tree in (old, cKDTree(np.vstack(recent)) if recent else None):
            if tree is not None:
                dist = np.minimum(dist, tree.query(pts, distance_upper_bound=far + 1)[0])
        dist[pts[:, 2] < (bed or 0.5)] = 0.0
        midair += int((dist > far).sum())
        steep += int(((dist > near) & (dist <= far)).sum())
        total += len(pts)
        recent.append(pts)
        if len(recent) > 30:
            old = cKDTree(np.vstack(([old.data] if old is not None else []) + recent))
            recent = []
    return {"points": total, "midair": midair, "steep": steep, "step": step}
