"""Turn sliced chunks into G-code for Kalico corexy_ac: X Y Z are part
coordinates, A and C orient the table so the nozzle points along each move's
tool axis, and the table only turns far from the part between chunks."""
import math

import numpy as np
import shapely
from scipy.spatial import cKDTree

from .machine import sample


class Obstacles:
    def __init__(self, machine):
        self.machine = machine
        self.done = []
        self.fixed = None
        self.current = None

    def finish(self, mesh):
        self.done.append(sample(mesh, 1.4))
        self.fixed = None

    def start(self, chunk, layers, rot, spacing=1.6):
        pts, heights = [sample(chunk.mesh, 1.4)], []
        heights.append((pts[0] - chunk.origin) @ chunk.d)
        for lay in layers:
            solid = lay.region.union(lay.support)
            if solid.is_empty:
                continue
            x0, y0, x1, y1 = solid.bounds
            gx, gy = np.meshgrid(np.arange(x0, x1, spacing), np.arange(y0, y1, spacing))
            grid = np.column_stack((gx.ravel(), gy.ravel()))
            grid = grid[shapely.contains_xy(solid, grid[:, 0], grid[:, 1])]
            if len(grid):
                pts.append(chunk.origin + np.column_stack((grid, np.full(len(grid), lay.h))) @ rot)
                heights.append(np.full(len(grid), lay.h))
        pts = np.vstack(pts)
        self.current = (pts, cKDTree(pts), np.concatenate(heights))

    def at(self, h):
        if self.fixed is None and self.done:
            pts = np.vstack(self.done)
            self.fixed = ("printed", pts, cKDTree(pts), None)
        out = [self.fixed] if self.fixed else []
        if self.current is not None:
            pts, tree, heights = self.current
            out.append(("layer", pts, tree, lambda idx: heights[idx] <= h + 1e-6))
        return out


class Writer:
    def __init__(self, machine, lean=15.0, ramp=3.0, filament=1.75, travel=150.0,
                 hop=0.6, retract=0.8, check_every=4.0):
        self.m = machine
        self.lean = lean
        self.ramp = ramp
        self.area = math.pi * filament ** 2 / 4
        self.travel_f = travel * 60
        self.hop = hop
        self.retract = retract
        self.check_every = check_every
        self.out = []
        self.p = None
        self.a = 0.0
        self.c = 0.0
        self.retracted = False
        self.collisions = []
        self.reduced = 0
        self.flipped = 0
        self.skipped = 0
        self.obstacles = Obstacles(machine)
        self.layer_no = 0
        self.memo = {}

    def emit(self, p, a, c, e=None, f=None):
        line = "G1 X%.3f Y%.3f Z%.3f A%.3f C%.3f" % (p[0], p[1], p[2], a, c)
        if e is not None:
            line += " E%.5f" % e
        if f is not None:
            line += " F%d" % f
        self.out.append(line)
        self.p, self.a, self.c = np.asarray(p, float), a, c

    def solve(self, tools, prev):
        out = []
        for t in tools:
            s = self.m.angles(t, prev)
            if s is None:
                return None
            out.append(s)
            prev = s
        return out

    def collides(self, pts, angles, h, every=None):
        head = self.m.head
        if head is None:
            return None
        obstacles = self.obstacles.at(h)
        every = self.check_every if every is None else every
        angles = np.asarray(angles, float)
        if every:
            dist = np.concatenate(([0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))))
            at = np.unique(np.concatenate((np.arange(0, dist[-1], every), [dist[-1]])))
            pts = np.column_stack([np.interp(at, dist, pts[:, k]) for k in range(3)])
            angles = np.column_stack([np.interp(at, dist, angles[:, k]) for k in range(2)])
        for p, (a, c) in zip(pts, angles):
            hit = head.collides(p, a, c, obstacles)
            if hit:
                return hit
        return None

    def orient(self, path, d, lean):
        lam = math.radians(max(lean, 1.0))
        if path.kind == "wall":
            options = [(path.pts, path.lean_dirs), (path.pts[::-1], path.lean_dirs[::-1])]
        else:
            options = [(path.pts, path.lean_dirs), (path.pts, -path.lean_dirs)]
        best = None
        for pts, dirs in options:
            angles = self.solve(math.cos(lam) * d + math.sin(lam) * dirs, (self.a, self.c))
            if angles is None:
                continue
            cost = abs(angles[-1][1]) if path.kind == "wall" else abs(angles[0][1] - self.c)
            if best is None or cost < best[0]:
                best = (cost, pts, dirs)
        if best is not None:
            path.pts, path.lean_dirs = best[1], best[2]

    def fit(self, path, chunk, h_local):
        lean = self.lean * min(1.0, h_local / self.ramp) if self.ramp else self.lean
        d = chunk.d
        self.orient(path, d, lean)
        key = (id(chunk), path.layer, path.kind)
        scales = [s for s in (1.0, 0.66, 0.33, 0.0) if s <= self.memo.get(key, 1.0)]
        if not path.lean_dirs.any():
            scales = [0.0]
        back = self.c + (180.0 if self.c < 0 else -180.0)
        for flip, prev in enumerate(((self.a, self.c), (-self.a, back))):
            for scale in scales:
                lam = math.radians(lean * scale)
                tools = math.cos(lam) * d + math.sin(lam) * path.lean_dirs
                angles = self.solve(tools, prev)
                if angles is None:
                    continue
                if self.collides(path.pts, angles, h_local) is None:
                    self.memo[key] = scale
                    self.reduced += scale < 1.0
                    self.flipped += flip
                    return tools, angles
        tools = np.tile(d, (len(path.pts), 1))
        angles = self.solve(tools, (self.a, self.c)) or self.solve(tools, (-self.a, back))
        if path.kind == "support":
            self.skipped += 1
            return None, None
        self.collisions.append((path.kind, path.layer, self.collides(path.pts, angles, h_local)))
        return tools, angles

    def e_for(self, dist, path):
        return dist * path.width * path.height / self.area

    def unretract(self):
        if self.retracted:
            self.out.append("G1 E%.3f F2100" % self.retract)
            self.retracted = False

    def do_retract(self):
        if not self.retracted:
            self.out.append("G1 E-%.3f F2100" % self.retract)
            self.retracted = True

    def hop_to(self, p1, a1, c1, t1, h_local):
        t0 = self.m.tool_axis(self.a, self.c)
        dist = np.linalg.norm(p1 - self.p)
        if dist < 2 * 0.45 and abs(a1 - self.a) < 1 and abs(c1 - self.c) < 1:
            self.emit(p1, a1, c1, f=self.travel_f)
            return
        self.do_retract()
        up0, up1 = self.p + t0 * self.hop, p1 + t1 * self.hop
        self.emit(up0, self.a, self.c, f=self.travel_f)
        if self.m.head is not None and (abs(a1 - self.a) > 2 or abs(c1 - self.c) > 2 or dist > 20):
            steps = np.linspace(0, 1, 6)
            pts = up0 + np.outer(steps, up1 - up0)
            angles = list(zip(self.a + steps * (a1 - self.a), self.c + steps * (c1 - self.c)))
            hit = self.collides(pts, angles, h_local, every=self.check_every)
            if hit:
                self.safe_move(p1, a1, c1)
                return
        self.emit(up1, a1, c1)
        self.emit(p1, a1, c1)

    def safe_z(self, x, y, angles):
        top = 0.0
        pts = list(self.obstacles.done)
        if self.m.head is not None:
            pts.append(self.m.head.platter)
        pts = np.vstack(pts) if pts else np.zeros((1, 3))
        cradle = self.m.head.cradle if self.m.head is not None else None
        for a, c in angles:
            mpts = self.m.to_machine(pts, a, c)
            near = mpts[(np.abs(mpts[:, 1] - y) < 100) | (np.hypot(mpts[:, 0] - x, mpts[:, 1] - y) < 100)]
            if len(near):
                top = max(top, near[:, 2].max())
            if cradle is not None:
                cm = self.m.from_a_frame(cradle, a)
                cm = cm[np.abs(cm[:, 1] - y) < 100]
                if len(cm):
                    top = max(top, cm[:, 2].max())
        return min(self.m.z_max - 1.0, top + 8.0)

    def safe_move(self, p1, a1, c1):
        m1 = self.m.to_machine(p1, a1, c1)[0]
        if self.p is None:
            z = max(self.safe_z(m1[0], m1[1], [(a1, c1)]), m1[2] + 10)
            self.out.append("G1 Z%.3f F600" % z)
            self.emit(self.m.to_part((m1[0], m1[1], z), a1, c1)[0], a1, c1, f=self.travel_f)
            self.emit(self.m.to_part((m1[0], m1[1], m1[2] + 2.0), a1, c1)[0], a1, c1)
            self.emit(p1, a1, c1)
            return
        self.do_retract()
        m0 = self.m.to_machine(self.p, self.a, self.c)[0]
        n = max(2, int(math.ceil(max(abs(a1 - self.a), abs(c1 - self.c)) / 5.0)) + 1)
        steps = np.linspace(0, 1, n)
        angles = list(zip(self.a + steps * (a1 - self.a), self.c + steps * (c1 - self.c)))
        z = max(self.safe_z(m0[0], m0[1], angles), self.safe_z(m1[0], m1[1], angles[-1:]), m0[2], m1[2] + 2)
        a0, c0 = self.a, self.c
        up = np.array([m0[0], m0[1], z])
        self.emit(self.m.to_part(up, a0, c0)[0], a0, c0, f=self.travel_f)
        for a, c in angles[1:]:
            self.emit(self.m.to_part(up, a, c)[0], a, c)
        over = np.array([m1[0], m1[1], z])
        self.emit(self.m.to_part(over, a1, c1)[0], a1, c1)
        near = np.array([m1[0], m1[1], m1[2] + 2.0])
        self.emit(self.m.to_part(near, a1, c1)[0], a1, c1)
        self.emit(p1, a1, c1)

    def run_path(self, path, chunk, h_local, first):
        tools, angles = self.fit(path, chunk, h_local)
        if tools is None:
            return first
        p0 = path.pts[0]
        if first:
            self.safe_move(p0, *angles[0])
        else:
            self.hop_to(p0, angles[0][0], angles[0][1], tools[0], h_local)
        self.unretract()
        f = path.speed * 60
        for i in range(1, len(path.pts)):
            dist = np.linalg.norm(path.pts[i] - path.pts[i - 1])
            self.emit(path.pts[i], *angles[i], e=self.e_for(dist, path), f=f if i == 1 else None)
        return False

    def chunk(self, chunk, layers, rot, first_chunk):
        self.obstacles.start(chunk, layers, rot)
        if not first_chunk:
            self.out.append("BED_MESH_CLEAR")
        first = True
        for lay in layers:
            if not lay.paths:
                continue
            self.layer_no += 1
            self.out.append(";LAYER_CHANGE\n;Z:%.3f\nSET_PRINT_STATS_INFO CURRENT_LAYER=%d"
                            % (lay.h, self.layer_no))
            for path in lay.paths:
                first = self.run_path(path, chunk, lay.h, first)
        self.obstacles.finish(chunk.mesh)
        self.obstacles.current = None

    def finish(self):
        self.do_retract()
        c = 360.0 * round(self.c / 360.0)
        if self.p is not None:
            m0 = self.m.to_machine(self.p, self.a, self.c)[0]
            n = max(2, int(math.ceil(max(abs(self.a), abs(c - self.c)) / 5.0)) + 1)
            steps = np.linspace(0, 1, n)
            angles = list(zip(self.a + steps * (0 - self.a), self.c + steps * (c - self.c)))
            z = max(self.safe_z(m0[0], m0[1], angles), m0[2] + 10)
            up = np.array([m0[0], m0[1], z])
            self.emit(self.m.to_part(up, self.a, self.c)[0], self.a, self.c, f=self.travel_f)
            for a, cc in angles[1:]:
                self.emit(self.m.to_part(up, a, cc)[0], a, cc)
