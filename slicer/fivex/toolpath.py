"""Layers, walls and infill for a chunk. Layers are flat in the chunk's frame;
along walls the nozzle leans toward the wall's outside and across infill it
leans across the lines."""
import math

import numpy as np
import shapely
import shapely.affinity
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.polygon import orient


def basis(d):
    ref = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 else np.array([0, 1.0, 0])
    u = ref - (ref @ d) * d
    u /= np.linalg.norm(u)
    return u, np.cross(d, u), d


class Path:
    def __init__(self, kind, pts, lean_dirs, layer, height, width, speed):
        self.kind = kind
        self.pts = pts
        self.lean_dirs = lean_dirs
        self.layer = layer
        self.height = height
        self.width = width
        self.speed = speed
        self.tools = None


class Layer:
    def __init__(self, index, h, region):
        self.index = index
        self.h = h
        self.region = region
        self.paths = []


def polygons(geom):
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [orient(geom, 1.0)]
    if isinstance(geom, MultiPolygon):
        return [orient(g, 1.0) for g in geom.geoms]
    return [orient(g, 1.0) for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def resample(ring, step):
    ring = np.asarray(ring)
    seg = np.diff(ring, axis=0)
    length = np.hypot(seg[:, 0], seg[:, 1])
    out = [ring[0]]
    for p, s, n in zip(ring[:-1], seg, length):
        k = max(1, int(math.ceil(n / step)))
        out += [p + s * (i / k) for i in range(1, k + 1)]
    return np.array(out)


def wall_normals(pts, blend):
    seg = np.diff(pts, axis=0)
    ang = np.unwrap(np.arctan2(-seg[:, 0], seg[:, 1]))
    ds = np.hypot(seg[:, 0], seg[:, 1])
    k = int(round(blend / max(ds.mean(), 1e-6)))
    if 0 < k and len(ang) > 2 * k:
        turn = 2 * math.pi * round((ang[-1] - ang[0]) / (2 * math.pi))
        ext = np.concatenate((ang[-k:] - turn, ang, ang[:k] + turn))
        ang = np.convolve(ext, np.ones(2 * k + 1) / (2 * k + 1), "valid")
    ang = np.concatenate((ang, ang[:1]))
    return np.column_stack((np.cos(ang), np.sin(ang)))


def simplify(pts, normals, tol_deg=1.0, tol_mm=0.02):
    keep = [0]
    for i in range(1, len(pts) - 1):
        j = keep[-1]
        da = math.degrees(math.acos(max(-1.0, min(1.0, normals[i] @ normals[j]))))
        if da > tol_deg:
            keep.append(i)
            continue
        a, b, p = pts[j], pts[i + 1], pts[i]
        ab = b - a
        n = np.hypot(*ab)
        if n > 1e-9 and abs(ab[0] * (p[1] - a[1]) - ab[1] * (p[0] - a[0])) / n > tol_mm:
            keep.append(i)
    keep.append(len(pts) - 1)
    return pts[keep], normals[keep]


def clean(geom, w):
    geom = geom.buffer(-0.05 * w, join_style="mitre").buffer(0.05 * w, join_style="mitre")
    return shapely.union_all([g for g in polygons(geom) if g.area > w * w / 4] or [Polygon()])


def hatch(region, angle, spacing, phase=0.0):
    if region.is_empty:
        return []
    guard = region.buffer(1e-3)
    ca, sa = math.cos(angle), math.sin(angle)
    rot = shapely.affinity.rotate(region, -angle, origin=(0, 0), use_radians=True)
    x0, y0, x1, y1 = rot.bounds
    out = []
    y = y0 + (phase % spacing) + spacing / 2
    flip = False
    while y < y1:
        cut = rot.intersection(LineString([(x0 - 1, y), (x1 + 1, y)]))
        segs = [g for g in getattr(cut, "geoms", [cut]) if isinstance(g, LineString) and g.length > 1e-3]
        segs.sort(key=lambda g: g.coords[0][0])
        if flip:
            segs = segs[::-1]
        for g in segs:
            (ax, ay), (bx, by) = g.coords[0], g.coords[-1]
            if flip:
                (ax, ay), (bx, by) = (bx, by), (ax, ay)
            seg = np.array([[ax * ca - ay * sa, ax * sa + ay * ca],
                            [bx * ca - by * sa, bx * sa + by * ca]])
            if guard.contains(LineString(seg)):
                out.append(seg)
        flip = not flip
        y += spacing
    return out


class ChunkSlicer:
    def __init__(self, layer=0.3, width=0.45, walls=2, infill=0.2, shells=3,
                 wall_speed=30, infill_speed=45, blend=2.0, step=0.5):
        self.layer = layer
        self.width = width
        self.walls = walls
        self.infill = infill
        self.shells = shells
        self.wall_speed = wall_speed
        self.infill_speed = infill_speed
        self.blend = blend
        self.step = step

    def frame(self, chunk):
        u, v, d = basis(chunk.d)
        rot = np.vstack((u, v, d))
        local = chunk.mesh.copy()
        local.apply_translation(-chunk.origin)
        local.vertices = local.vertices @ rot.T
        return rot, local

    def to_part(self, chunk, rot, xy, h):
        xy = np.atleast_2d(xy)
        return chunk.origin + np.column_stack((xy, np.full(len(xy), h))) @ rot

    def sections(self, local):
        top = local.vertices[:, 2].max()
        out = []
        h = self.layer
        while h <= top + 1e-6:
            s = local.section(plane_origin=[0, 0, h - self.layer / 2], plane_normal=[0, 0, 1])
            region = Polygon()
            if s is not None:
                p, _ = s.to_2D(to_2D=np.eye(4))
                region = shapely.union_all([g.buffer(0) for g in p.polygons_full]) if p.polygons_full else Polygon()
            out.append(Layer(len(out), h, region))
            h += self.layer
        return out

    def slice(self, chunk):
        rot, local = self.frame(chunk)
        layers = self.sections(local)
        w = self.width
        for k, lay in enumerate(layers):
            if lay.region.is_empty or lay.region.area < w * w:
                continue
            for i in reversed(range(self.walls)):
                loops = []
                for poly in polygons(lay.region.buffer(-w * (i + 0.5), join_style="mitre", mitre_limit=2.0)):
                    for ring in [poly.exterior] + list(poly.interiors):
                        if ring.length < 3 * w:
                            continue
                        pts = resample(np.asarray(ring.coords)[:, :2], self.step)
                        normals = wall_normals(pts, self.blend)
                        loops.append(simplify(pts, normals))
                for pts, normals in loops:
                    lean = normals @ rot[:2]
                    lay.paths.append(Path("wall", self.to_part(chunk, rot, pts, lay.h), lean,
                                          k, self.layer, w, self.wall_speed))
            inner = lay.region.buffer(-w * (self.walls - 0.15), join_style="mitre", mitre_limit=2.0)
            if inner.is_empty:
                continue
            near = [layers[j].region for j in range(k - self.shells, k + self.shells + 1)
                    if j != k and 0 <= j < len(layers)]
            covered = shapely.intersection_all(near) if len(near) == 2 * self.shells else Polygon()
            sparse = clean(inner.intersection(covered.buffer(-w)), w) if self.infill < 1 else Polygon()
            solid = clean(inner.difference(sparse), w) if not sparse.is_empty else inner
            angle = math.radians(45 + 90 * (k % 2))
            lean2 = np.array([-math.sin(angle), math.cos(angle)]) @ rot[:2]
            for region, spacing, kind in ((solid, w, "solid"), (sparse, w / max(self.infill, 1e-6), "infill")):
                for seg in hatch(region, angle, spacing):
                    lay.paths.append(Path(kind, self.to_part(chunk, rot, seg, lay.h),
                                          np.tile(lean2, (2, 1)), k, self.layer, w, self.infill_speed))
        return rot, local, layers
