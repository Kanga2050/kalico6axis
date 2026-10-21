"""Split an STL into chunks and slice each for the A/C trunnion.

    python slicer/split5.py model.stl model.gcode
    python slicer/split5.py model.stl model.gcode --method convex --lean 10

The model is split along planes so that every chunk prints without supports
from its own build direction, each chunk growing on a cut face of what was
printed before it. --method overhang cuts where overhangs meet the body,
convex cuts between the parts of a convex decomposition, and hybrid tries
both and a mix and keeps the best. Inside a chunk layers are flat; the
nozzle leans toward each wall's outside and across infill lines, so A and C
move while printing. Every pose is checked against the printer's CAD and
the lean is reduced where the toolhead would hit something. G-code X Y Z
are part coordinates and corexy_ac moves the table and gantry to match.
"""
import argparse
import os
import sys
import time

import numpy as np
import trimesh

from fivex.check import check
from fivex.decompose import Planner
from fivex.gcode import Writer
from fivex.machine import Machine
from fivex.toolpath import ChunkSlicer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path, center):
    mesh = trimesh.load(path, force="mesh")
    if not mesh.is_watertight:
        trimesh.repair.fill_holes(mesh)
        trimesh.repair.fix_normals(mesh)
    if not mesh.is_watertight:
        sys.exit("%s is not a closed mesh" % path)
    lo, hi = mesh.bounds
    mesh.apply_translation((center[0] - (lo[0] + hi[0]) / 2, center[1] - (lo[1] + hi[1]) / 2, -lo[2]))
    return mesh


def main():
    p = argparse.ArgumentParser()
    p.add_argument("stl")
    p.add_argument("out")
    p.add_argument("--method", choices=("hybrid", "overhang", "convex"), default="hybrid")
    p.add_argument("--overhang", type=float, default=50, help="steepest overhang printed without a cut, degrees from vertical")
    p.add_argument("--max-tilt", type=float, default=90, help="largest chunk tilt, degrees")
    p.add_argument("--max-chunks", type=int, default=12)
    p.add_argument("--layer", type=float, default=0.3)
    p.add_argument("--width", type=float, default=0.45)
    p.add_argument("--walls", type=int, default=2)
    p.add_argument("--infill", type=float, default=0.2, help="0 to 1")
    p.add_argument("--lean", type=float, default=15, help="nozzle lean, degrees")
    p.add_argument("--ramp", type=float, default=3, help="mm over which the lean builds up in each chunk")
    p.add_argument("--speed", type=float, default=30)
    p.add_argument("--infill-speed", type=float, default=45)
    p.add_argument("--center", type=float, nargs=2)
    p.add_argument("--config", default=os.path.join(REPO, "config"))
    p.add_argument("--no-collisions", action="store_true")
    p.add_argument("--material", default="PLA")
    p.add_argument("--filament", type=float, default=1.75)
    g = p.parse_args()

    t0 = time.time()
    machine = Machine.from_config(g.config)
    if not g.no_collisions:
        machine.load_head(g.config)
    mesh = load(g.stl, g.center or (machine.cx, machine.cy))
    chunks = Planner(machine, g.method, g.overhang, g.max_tilt, g.max_chunks).plan(mesh)
    slicer = ChunkSlicer(g.layer, g.width, g.walls, g.infill, wall_speed=g.speed, infill_speed=g.infill_speed)
    sliced = [(c,) + slicer.slice(c) for c in chunks]
    total = sum(1 for _, _, _, layers in sliced for lay in layers if lay.paths)

    name = os.path.splitext(os.path.basename(g.stl))[0]
    hull = trimesh.convex.convex_hull(np.column_stack((mesh.vertices[:, :2], np.zeros(len(mesh.vertices)))))
    ring = hull.vertices[:, :2][np.argsort(np.arctan2(*(hull.vertices[:, :2] - hull.vertices[:, :2].mean(0)).T[::-1]))]
    cx, cy = mesh.bounds[:, :2].mean(0)
    w = Writer(machine, g.lean, g.ramp, g.filament)
    head = ["; split5 %s: %d chunks (%s), layer %.2f mm, lean %.0f deg" % (name, len(chunks), g.method, g.layer, g.lean)]
    for i, (c, _, _, layers) in enumerate(sliced):
        head.append("; chunk %d: build direction %.3f %.3f %.3f, %.0f mm3, %d layers, overhang left %.1f mm2"
                    % ((i + 1,) + tuple(c.d) + (c.mesh.volume, sum(1 for lay in layers if lay.paths), c.overhang)))
    head += ["EXCLUDE_OBJECT_DEFINE NAME=%s CENTER=%.1f,%.1f POLYGON=[%s]"
             % (name, cx, cy, ",".join("[%.1f,%.1f]" % tuple(v) for v in ring)),
             "SET_PRINT_STATS_INFO TOTAL_LAYER=%d" % total,
             "PRINT_START MATERIAL=%s" % g.material, "G90", "M83", "G92 E0",
             "EXCLUDE_OBJECT_START NAME=%s" % name]
    for i, (c, rot, _, layers) in enumerate(sliced):
        w.out.append("; chunk %d" % (i + 1))
        w.chunk(c, layers, rot, i == 0)
    w.out.append("EXCLUDE_OBJECT_END NAME=%s" % name)
    w.finish()
    w.out.append("PRINT_END")
    with open(g.out, "w") as f:
        f.write("\n".join(head + w.out) + "\n")
    print("%s: %d chunks, %d layers, %d lines -> %s (%.0fs)" % (name, len(chunks), total, len(w.out), g.out, time.time() - t0))
    for i, (c, _, _, layers) in enumerate(sliced):
        print("  chunk %d: direction %s, %.0f mm3, overhang left %.1f mm2" % (i + 1, np.round(c.d, 3), c.mesh.volume, c.overhang))
    left = sum(c.overhang for c in chunks)
    if left > 0.01 * mesh.area:
        print("  warning: %.0f mm2 of overhang is left without support; try --overhang, --max-tilt or another --method" % left)
    print("  lean reduced on %d paths, A flipped on %d, collisions left %d" % (w.reduced, w.flipped, len(w.collisions)))
    for kind, layer, hit in w.collisions[:10]:
        print("    %s layer %d hits %s" % (kind, layer + 1, hit))
    r = check(g.out, machine)
    print("  tilt up to %.0f deg while printing, A %.0f..%.0f, C %.0f..%.0f, machine %s..%s, %d moves outside limits"
          % ((r["largest tilt while printing"],) + r["A range"] + r["C range"]
             + (r["machine min"], r["machine max"], r["outside limits"] + r["A outside limits"])))


if __name__ == "__main__":
    main()
