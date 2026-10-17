"""Slice a cube for the A/C trunnion with the nozzle leaning as it prints.

    python slicer/slice5.py cube.gcode --size 20 --tilt 20
    python slicer/slice5.py solid.gcode --size 15 --infill 1

Layers stay flat. Along every wall the part is tilted so the nozzle leans
--tilt degrees toward that wall's outside, so A holds the tilt and C turns
a full circle each loop; loops alternate direction so C unwinds. With
--infill, each layer is filled with straight lines with the nozzle leaning
across them, and the lines turn 90 degrees every layer, so C swings between
layers too. G-code X Y Z are part coordinates and corexy_ac moves the table
to match.
"""
import argparse
import math


def walls(s, step, blend):
    out = []
    sides = [((s, 0), (s, s), 0), ((s, s), (-s, s), 90), ((-s, s), (-s, -s), 180),
             ((-s, -s), (s, -s), 270), ((s, -s), (s, 0), 0)]
    for (x0, y0), (x1, y1), psi in sides:
        n = max(1, round(math.hypot(x1 - x0, y1 - y0) / step))
        for i in range(n):
            u = i / n
            x, y = x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
            to_end = math.hypot(x1 - x, y1 - y)
            to_start = math.hypot(x - x0, y - y0)
            p = psi
            if to_end < blend and (x1, y1) != (s, 0):
                p += 45 * (1 - to_end / blend)
            if to_start < blend and (x0, y0) != (s, 0):
                p -= 45 * (1 - to_start / blend)
            out.append((x, y, p))
    out.append((s, 0, 0))
    return out


def infill(s, gap, along_x):
    # zigzag of straight lines across a square of half-size s, nozzle leaning across them
    n = max(1, math.floor(2 * s / gap))
    out = []
    for j in range(n + 1):
        v = -s + 2 * s * j / n
        ends = (-s, s) if j % 2 == 0 else (s, -s)
        for u in ends:
            out.append((u, v, 90) if along_x else (v, u, 0))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("out")
    p.add_argument("--size", type=float, default=20)
    p.add_argument("--layer", type=float, default=0.3)
    p.add_argument("--width", type=float, default=0.45)
    p.add_argument("--walls", type=int, default=2)
    p.add_argument("--infill", type=float, default=0, help="0 hollow, 1 solid")
    p.add_argument("--tilt", type=float, default=20)
    p.add_argument("--ramp", type=float, default=3, help="mm over which the tilt builds up")
    p.add_argument("--center", type=float, nargs=2, default=(175, 175))
    p.add_argument("--speed", type=float, default=30)
    p.add_argument("--filament", type=float, default=1.75)
    p.add_argument("--material", default="PLA")
    g = p.parse_args()

    cx, cy = g.center
    k = g.width * g.layer / (math.pi * g.filament ** 2 / 4)
    f = g.speed * 60
    layers = round(g.size / g.layer)
    h = g.size / 2
    box = ",".join(f"[{cx + x:.1f},{cy + y:.1f}]" for x, y in ((-h, -h), (h, -h), (h, h), (-h, h)))
    kind = "solid" if g.infill >= 1 else ("infill %d%%" % (g.infill * 100) if g.infill else "hollow")
    o = [f"; 5-axis cube {g.size} mm, {kind}, tilt {g.tilt} deg, layer {g.layer} mm",
         f"EXCLUDE_OBJECT_DEFINE NAME=cube CENTER={cx:.1f},{cy:.1f} POLYGON=[{box}]",
         f"SET_PRINT_STATS_INFO TOTAL_LAYER={layers}",
         f"PRINT_START MATERIAL={g.material}", "G90", "M83", "G92 E0", "EXCLUDE_OBJECT_START NAME=cube"]
    c_last = 0.0

    def path(pts, z, tilt):
        nonlocal c_last
        prev = None
        for x, y, psi in pts:
            c = 90 - psi
            c += 360 * round((c_last - c) / 360)
            c_last = c
            line = f"X{cx + x:.3f} Y{cy + y:.3f} Z{z:.3f} A{tilt:.3f} C{c:.3f}"
            if prev is None:
                o.append(f"G1 Z{z + 1:.3f} F600\nG1 {line} F{f}")
            else:
                o.append(f"G1 {line} E{math.hypot(x - prev[0], y - prev[1]) * k:.5f}")
            prev = (x, y)

    for li in range(layers):
        z = (li + 1) * g.layer
        tilt = g.tilt * min(1.0, z / g.ramp)
        o.append(f";LAYER_CHANGE\n;Z:{z:.3f}\nSET_PRINT_STATS_INFO CURRENT_LAYER={li + 1}")
        for w in range(g.walls):
            pts = walls(h - g.width * (w + 0.5), 0.5, min(2.0, h / 2))
            path(pts[::-1] if (li * g.walls + w) % 2 else pts, z, tilt)
        if g.infill:
            path(infill(h - g.width * g.walls, g.width / g.infill, li % 2 == 0), z, tilt)
    o += ["EXCLUDE_OBJECT_END NAME=cube", f"G1 Z{g.size + 10:.3f} F600", f"G1 A0 C{360 * round(c_last / 360):.0f} F3000", "PRINT_END"]
    open(g.out, "w").write("\n".join(o) + "\n")
    print(f"{layers} layers -> {g.out}")


if __name__ == "__main__":
    main()
