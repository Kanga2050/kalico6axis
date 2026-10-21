"""Layers, walls and infill for a chunk. Layers are flat in the chunk's frame;
along walls the nozzle leans toward the wall's outside and across infill it
leans across the lines."""
import math

import numpy as np
import shapely
import shapely.affinity
from scipy.spatial import cKDTree
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
        self.support = Polygon()
        self.cells = []
        self.held = None
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


def centerline(poly, w):
    c = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    if np.linalg.norm(c[1] - c[0]) > np.linalg.norm(c[2] - c[1]):
        c = np.roll(c, 1, axis=0)
    line = LineString([(c[0] + c[1]) / 2, (c[2] + c[3]) / 2]).intersection(poly.buffer(1e-3))
    lines = [g for g in getattr(line, "geoms", [line]) if isinstance(g, LineString) and g.length > 0]
    if not lines:
        return None, 0.0
    line = max(lines, key=lambda g: g.length)
    return np.asarray(line.coords)[[0, -1]], min(w, max(0.2 * w, poly.area / line.length))


def thin_parts(region, w):
    opened = region.buffer(-w * 0.5, join_style="mitre").buffer(w * 0.5, join_style="mitre")
    out = []
    for poly in polygons(region.difference(opened.buffer(1e-3))):
        if poly.area > 0.05 * w * w:
            out.append(poly)
    return out


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


def section(local, h):
    s = local.section(plane_origin=[0, 0, h], plane_normal=[0, 0, 1])
    if s is None:
        return Polygon()
    p, _ = s.to_2D(to_2D=np.eye(4))
    return shapely.union_all([g.buffer(0) for g in p.polygons_full]) if p.polygons_full else Polygon()


def samples(layers, step=0.4):
    out = [np.zeros((0, 3))]
    for lay in layers:
        for p in lay.paths:
            seg = np.diff(p.pts, axis=0)
            n = np.maximum(1, np.ceil(np.linalg.norm(seg, axis=1) / step)).astype(int)
            out += [a + np.outer(np.arange(k + 1) / k, s) for a, s, k in zip(p.pts[:-1], seg, n)]
    return np.vstack(out)


class ChunkSlicer:
    def __init__(self, layer=0.3, width=0.45, walls=2, infill=0.2, shells=3,
                 wall_speed=30, infill_speed=45, blend=2.0, step=0.5, supports=True, overhang=50.0):
        self.supports = supports
        self.slide = layer * math.tan(math.radians(overhang)) + 0.05
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
            out.append(Layer(len(out), h, section(local, h - self.layer / 2)))
            h += self.layer
        return out

    def bed(self, chunk, rot, h, big=1000.0):
        g = rot[:2, 2]
        c = -(chunk.origin[2] + h * rot[2, 2])
        n = np.hypot(*g)
        if n < 1e-9:
            return shapely.box(-big, -big, big, big) if c > 0 else Polygon()
        g, c = g / n, c / n
        q, t = g * c, np.array([-g[1], g[0]])
        return Polygon([q + big * t, q - big * t, q - big * t - big * g, q + big * t - big * g])

    def support(self, chunk, layers, rot, printed, cell=2.0, gap=0.6, touch=0.2, reach=60.0):
        base = getattr(chunk, "base", None)
        q = np.zeros((0, 3)) if printed is None else (printed - chunk.origin) @ rot.T
        if base is not None and printed is not None:
            near = q[(q[:, 2] > -1.0) & (q[:, 2] < 0.05)]
            base = base.intersection(shapely.MultiPoint(near[:, :2]).buffer(self.width)) if len(near) else Polygon()
        if base is not None:
            base = base.buffer(-self.width / 2, join_style="mitre").buffer(self.width / 2, join_style="mitre")
        order = np.argsort(q[:, 2])
        q, zs = q[order], q[order, 2]
        scale = self.width * 0.75 / self.layer
        tree = cKDTree(q * (1, 1, scale)) if len(q) else None

        def solid(m):
            h = self.layer * (m + 0.5)
            g = self.bed(chunk, rot, h)
            near = q[np.searchsorted(zs, h - self.layer):np.searchsorted(zs, h + self.layer)]
            return g.union(shapely.MultiPoint(near[:, :2]).buffer(self.width * 0.75)) if len(near) else g

        def lands(m, pts):
            h = self.layer * (m + 0.5)
            hit = shapely.contains_xy(self.bed(chunk, rot, h), pts[:, 0], pts[:, 1])
            if tree is not None and len(pts):
                xyz = np.column_stack((pts, np.full(len(pts), h * scale)))
                hit |= tree.query(xyz, distance_upper_bound=self.width * 0.75)[0] < np.inf
            return hit

        need, dropped, extra = 0.0, 0.0, {}
        for k, lay in enumerate(layers):
            prev = layers[k - 1].region if k else base
            if prev is None:
                continue
            lay.held = prev.buffer(self.slide)
            free = lay.region.difference(lay.held)
            free = free.buffer(-0.05, join_style="mitre").buffer(0.05, join_style="mitre")
            if free.is_empty:
                continue
            if free.area < 0.1:
                lay.region = lay.region.intersection(lay.held)
                continue
            x0, y0, x1, y1 = free.bounds
            gx, gy = np.meshgrid(np.arange(x0 + cell / 2, x1, cell), np.arange(y0 + cell / 2, y1, cell))
            pts = np.column_stack((gx.ravel(), gy.ravel()))
            pts = pts[shapely.contains_xy(free.buffer(cell * 0.75), pts[:, 0], pts[:, 1])]
            land = np.full(len(pts), np.iinfo(int).min)
            todo = np.ones(len(pts), bool)
            m = k - 1
            while todo.any() and (k - m) * self.layer < reach:
                if 0 <= m < len(layers):
                    hit = todo & shapely.contains_xy(layers[m].region, pts[:, 0], pts[:, 1])
                    land[hit], todo[hit] = m + 2, False
                hit = todo & lands(m, pts)
                land[hit], todo[hit] = m + 1, False
                m -= 1
            ok = land > np.iinfo(int).min
            lay.held = shapely.union_all([lay.held] + [shapely.box(x - cell / 2, y - cell / 2, x + cell / 2, y + cell / 2)
                                                        for x, y in pts[ok]])
            dropped += lay.region.difference(lay.held).area
            lay.region = lay.region.intersection(lay.held)
            for p, start in zip(pts, land):
                for j in range(start, k - 1) if start > np.iinfo(int).min else ():
                    (layers[j].cells if j >= 0 else extra.setdefault(j, [])).append(p)
            need += free.area
        low = min(extra, default=0)
        layers[:0] = [Layer(0, self.layer * (j + 1), Polygon()) for j in range(low, 0)]
        for j, cells in extra.items():
            layers[j - low].cells = cells
        for i, lay in enumerate(layers):
            lay.index = i
            if lay.cells:
                squares = [shapely.box(x - cell * 0.45, y - cell * 0.45, x + cell * 0.45, y + cell * 0.45)
                           for x, y in lay.cells]
                lay.support = shapely.union_all(squares).difference(lay.region.buffer(gap)).difference(
                    solid(i + low).buffer(touch))
        chunk.dropped = dropped
        return need, layers

    def slice(self, chunk, printed=None):
        rot, local = self.frame(chunk)
        layers = self.sections(local)
        w = self.width
        chunk.supported = chunk.dropped = 0.0
        if self.supports:
            chunk.supported, layers = self.support(chunk, layers, rot, printed)
        for k, lay in enumerate(layers):
            if lay.support.is_empty:
                continue
            angle = math.pi / 2 * (k % 2)
            for seg in hatch(lay.support, angle, 2.0):
                lay.paths.append(Path("support", self.to_part(chunk, rot, seg, lay.h), np.zeros((2, 3)),
                                      k, self.layer, w, self.infill_speed))
        for k, lay in enumerate(layers):
            if lay.region.is_empty:
                continue
            thin = thin_parts(lay.region, w)
            if lay.held is not None:
                thin = [g for t in thin for g in polygons(t.intersection(lay.held)) if g.area > 0.05 * w * w]
            for poly in thin:
                seg, width = centerline(poly, w)
                if seg is None:
                    continue
                d2 = seg[1] - seg[0]
                if np.linalg.norm(d2) < w:
                    continue
                side = np.array([-d2[1], d2[0]]) / np.linalg.norm(d2)
                lay.paths.append(Path("gap", self.to_part(chunk, rot, seg, lay.h), np.tile(side @ rot[:2], (2, 1)),
                                      k, self.layer, width, self.wall_speed))
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
