"""The A/C trunnion: Kalico corexy_ac's tool center point transform, A/C
for a tool direction, and collision checks against the printer's CAD."""
import configparser
import math
import os

import numpy as np
import trimesh
from scipy.spatial import cKDTree

DEG = math.pi / 180


def read_config(path):
    cp = configparser.ConfigParser(
        inline_comment_prefixes=("#", ";"), strict=False, interpolation=None
    )
    cp.optionxform = str
    cp.read(path)
    return cp


def floats(text):
    return [float(v) for v in text.replace(",", " ").split()]


class Machine:
    def __init__(self, a_pivot=(175.0, 5.0), c_pivot=(175.0, 175.0),
                 a_limits=(-100.0, 100.0), xy_max=350.0, z_limits=(-5.0, 330.0)):
        self.ay, self.az = a_pivot
        self.cx, self.cy = c_pivot
        self.a_min, self.a_max = a_limits
        self.xy_max = xy_max
        self.z_min, self.z_max = z_limits
        self.center = np.array([self.cx, self.cy, 0.0])
        self.head = None

    @classmethod
    def from_config(cls, cfg_dir):
        rot = read_config(os.path.join(cfg_dir, "printer", "rotary.cfg"))
        xy = read_config(os.path.join(cfg_dir, "printer", "xystepper.cfg"))
        z = read_config(os.path.join(cfg_dir, "printer", "zstepper.cfg"))
        ra, rc = rot["rotary_a"], rot["rotary_c"]
        return cls(
            floats(ra["pivot"]), floats(rc["pivot"]),
            (float(ra.get("position_min", -100)), float(ra.get("position_max", 100))),
            float(xy["stepper_x"].get("position_max", 350)),
            (float(z["stepper_z"].get("position_min", 0)),
             float(z["stepper_z"].get("position_max", 330))),
        )

    def to_machine(self, p, a, c):
        p = np.atleast_2d(p)
        a = np.broadcast_to(np.asarray(a, float) * DEG, len(p))
        c = np.broadcast_to(np.asarray(c, float) * DEG, len(p))
        sa, ca, sc, cc = np.sin(a), np.cos(a), np.sin(c), np.cos(c)
        dx, dy = p[:, 0] - self.cx, p[:, 1] - self.cy
        qy = self.cy + dx * sc + dy * cc - self.ay
        qz = p[:, 2] - self.az
        return np.column_stack((self.cx + dx * cc - dy * sc,
                                self.ay + qy * ca - qz * sa,
                                self.az + qy * sa + qz * ca))

    def to_part(self, m, a, c):
        m = np.atleast_2d(m)
        a = np.broadcast_to(np.asarray(a, float) * DEG, len(m))
        c = np.broadcast_to(np.asarray(c, float) * DEG, len(m))
        sa, ca, sc, cc = np.sin(a), np.cos(a), np.sin(c), np.cos(c)
        my, mz = m[:, 1] - self.ay, m[:, 2] - self.az
        dx, dy = m[:, 0] - self.cx, self.ay + my * ca + mz * sa - self.cy
        return np.column_stack((self.cx + dx * cc + dy * sc,
                                self.cy - dx * sc + dy * cc,
                                self.az - my * sa + mz * ca))

    def to_a_frame(self, m, a):
        m = np.atleast_2d(m)
        a = np.broadcast_to(np.asarray(a, float) * DEG, len(m))
        sa, ca = np.sin(a), np.cos(a)
        my, mz = m[:, 1] - self.ay, m[:, 2] - self.az
        return np.column_stack((m[:, 0], self.ay + my * ca + mz * sa,
                                self.az - my * sa + mz * ca))

    def from_a_frame(self, q, a):
        q = np.atleast_2d(q)
        a = np.broadcast_to(np.asarray(a, float) * DEG, len(q))
        sa, ca = np.sin(a), np.cos(a)
        qy, qz = q[:, 1] - self.ay, q[:, 2] - self.az
        return np.column_stack((q[:, 0], self.ay + qy * ca - qz * sa,
                                self.az + qy * sa + qz * ca))

    @staticmethod
    def tool_axis(a, c):
        a, c = np.asarray(a, float) * DEG, np.asarray(c, float) * DEG
        return np.stack((np.sin(a) * np.sin(c), np.sin(a) * np.cos(c), np.cos(a)), -1)

    def angles(self, t, prev):
        a_prev, c_prev = prev
        t = np.asarray(t, float) / np.linalg.norm(t)
        a = math.atan2(math.hypot(t[0], t[1]), t[2]) / DEG
        c = math.atan2(t[0], t[1]) / DEG if a > 1e-6 else c_prev
        best = None
        for aa, cc in ((a, c), (-a, c + 180.0)):
            if not self.a_min - 1e-9 <= aa <= self.a_max + 1e-9:
                continue
            cc += 360.0 * round((c_prev - cc) / 360.0)
            cost = abs(aa - a_prev) + abs(cc - c_prev) * (math.sin(abs(a) * DEG) + 0.05)
            if best is None or cost < best[0]:
                best = (cost, aa, cc)
        return None if best is None else best[1:]

    def in_limits(self, m, tol=1e-6):
        m = np.atleast_2d(m)
        return ((m[:, :2] >= -tol) & (m[:, :2] <= self.xy_max + tol)).all(1) & (
            m[:, 2] >= self.z_min - tol) & (m[:, 2] <= self.z_max + tol)

    def load_head(self, cfg_dir):
        self.head = Head(self, os.path.join(cfg_dir, "models"),
                         read_config(os.path.join(cfg_dir, "printer", "model.cfg")))
        return self.head


def sample(mesh, spacing, seed=1):
    count = max(200, int(mesh.area / spacing ** 2))
    return trimesh.sample.sample_surface(mesh, count, seed=seed)[0]


class Head:
    def __init__(self, machine, models_dir, model_cfg, spacing=1.4, reach=80.0, pitch=1.0):
        self.machine = machine
        self.reach = reach
        offset = np.array(floats(model_cfg["model"]["offset"]))
        home = {s.split()[-1]: float(model_cfg[s].get("home", 0))
                for s in model_cfg.sections() if s.startswith("model_part")}
        tip = np.array([home["carriage"], home["beam"], home["gantry"]])

        def load(name):
            return trimesh.load(os.path.join(models_dir, name + ".glb"),
                                force="mesh").apply_translation(offset)

        car = sample(load("carriage"), spacing) - tip
        car = car[(car[:, 2] > 1.2) & (np.abs(car[:, :2]) < reach).all(1) & (car[:, 2] < reach)]
        beam = sample(load("beam"), spacing)[:, 1:] - tip[1:]
        beam = beam[(np.abs(beam[:, 0]) < reach) & (beam[:, 1] < reach)]
        xs = np.arange(-reach, reach + pitch, pitch)
        ys = np.arange(min(car[:, 1].min(), beam[:, 0].min()) - pitch,
                       max(car[:, 1].max(), beam[:, 0].max()) + pitch, pitch)
        self.lo = np.full((len(xs), len(ys)), np.inf)
        self.hi = np.full((len(xs), len(ys)), -np.inf)
        ix = ((car[:, 0] - xs[0]) / pitch).astype(int)
        iy = ((car[:, 1] - ys[0]) / pitch).astype(int)
        np.minimum.at(self.lo, (ix, iy), car[:, 2])
        np.maximum.at(self.hi, (ix, iy), car[:, 2])
        by = ((beam[:, 0] - ys[0]) / pitch).astype(int)
        blo = np.full(len(ys), np.inf)
        bhi = np.full(len(ys), -np.inf)
        np.minimum.at(blo, by, beam[:, 1])
        np.maximum.at(bhi, by, beam[:, 1])
        self.lo = np.minimum(self.lo, blo[None, :])
        self.hi = np.maximum(self.hi, bhi[None, :])
        self.origin, self.pitch = np.array([xs[0], ys[0]]), pitch
        gx, gy = np.meshgrid(xs, ys, indexing="ij")
        cells = np.isfinite(self.lo) & (gx % 2 < pitch) & (gy % 2 < pitch)
        self.ends = np.vstack((np.column_stack((gx[cells], gy[cells], self.lo[cells])),
                               np.column_stack((gx[cells], gy[cells], self.hi[cells]))))
        rot = load("rotary")
        self.platter = sample(rot, 2 * spacing)
        self.platter_r = float(np.hypot(*(rot.vertices[:, :2] - (machine.cx, machine.cy)).T).max())
        self.platter_z = float(rot.vertices[:, 2].max())
        cradle = sample(load("tilt"), 2 * spacing)
        r = np.hypot(cradle[:, 0] - machine.cx, cradle[:, 1] - machine.cy)
        self.cradle = cradle[(cradle[:, 2] > self.platter_z) | (r > self.platter_r)]
        self.cradle_tree = cKDTree(self.cradle)

    def inside(self, rel, clearance):
        ij = np.floor((rel[:, :2] - self.origin) / self.pitch).astype(int)
        ok = (ij >= 0).all(1) & (ij[:, 0] < self.lo.shape[0]) & (ij[:, 1] < self.lo.shape[1])
        ij, z = ij[ok], rel[ok, 2]
        return ((z > self.lo[ij[:, 0], ij[:, 1]] - clearance) & (z < self.hi[ij[:, 0], ij[:, 1]] + clearance)).any()

    def collides(self, p, a, c, obstacles, clearance=1.0):
        m = self.machine
        tip = m.to_machine(p, a, c)[0]
        ends = m.to_part(tip + self.ends, a, c)
        if ((ends[:, 2] < self.platter_z + clearance) &
                (np.hypot(ends[:, 0] - m.cx, ends[:, 1] - m.cy) < self.platter_r)).any():
            return "platter"
        idx = self.cradle_tree.query_ball_point(m.to_a_frame(tip, a)[0], self.reach * 1.5)
        if idx and self.inside(m.from_a_frame(self.cradle[idx], a) - tip, clearance):
            return "cradle"
        for name, pts, tree, keep in obstacles:
            idx = np.asarray(tree.query_ball_point(p, self.reach * 1.5), int)
            if keep is not None and len(idx):
                idx = idx[keep(idx)]
            if len(idx) and self.inside(m.to_machine(pts[idx], a, c) - tip, clearance):
                return name
        return None
