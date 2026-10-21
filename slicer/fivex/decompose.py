"""Split a mesh into chunks that each print without supports from their own
build direction. Each chunk is grown on a plane cut of the material printed
before it, with its layers parallel to that cut."""
import math

import manifold3d as mf
import numpy as np
import trimesh
from scipy.spatial import cKDTree

from .machine import sample


class Chunk:
    def __init__(self, mesh, d, origin, base=None):
        self.mesh = mesh
        self.d = np.asarray(d, float) / np.linalg.norm(d)
        self.origin = np.asarray(origin, float)
        self.base = base
        self.overhang = 0.0
        self.angles = None


def to_manifold(mesh):
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(mesh.vertices, np.float32),
                               tri_verts=np.asarray(mesh.faces, np.uint32)))


def to_trimesh(man):
    m = man.to_mesh()
    if not len(m.tri_verts):
        return None
    return trimesh.Trimesh(np.asarray(m.vert_properties[:, :3], float),
                           np.asarray(m.tri_verts, np.int64))


CACHE = {}


def key(mesh, *rest):
    return (mesh.identifier_hash,) + tuple(np.round(np.concatenate([np.ravel(r) for r in rest]), 4))


def cut(mesh, origin, n):
    k = ("cut",) + key(mesh, origin, n)
    if k not in CACHE:
        above, below = to_manifold(mesh).split_by_plane(tuple(n), float(np.dot(n, origin)))
        if below.is_empty() or above.is_empty():
            CACHE[k] = (None, [])
        else:
            CACHE[k] = (to_trimesh(below), [to_trimesh(above)])
    return CACHE[k]


def base_faces(mesh, d, origin, eps=1e-3):
    dots = mesh.face_normals @ d
    return (dots < -0.999) & (np.abs((mesh.triangles_center - origin) @ d) < eps)


def overhang_mask(mesh, d, origin, sin_a):
    return (mesh.face_normals @ d < -sin_a) & ~base_faces(mesh, d, origin)


def overhang_area(mesh, d, origin, sin_a):
    return float(mesh.area_faces[overhang_mask(mesh, d, origin, sin_a)].sum())


def clusters(mesh, mask, min_area):
    adj = mesh.face_adjacency
    adj = adj[mask[adj[:, 0]] & mask[adj[:, 1]]]
    groups = trimesh.graph.connected_components(adj, nodes=np.nonzero(mask)[0], min_len=1)
    return [g for g in groups if mesh.area_faces[g].sum() >= min_area]


def unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else None


def overhang_planes(mesh, d, origin, sin_a, min_area):
    mask = overhang_mask(mesh, d, origin, sin_a)
    support = mesh.area_faces[~mask] @ mesh.triangles_center[~mask] / mesh.area_faces[~mask].sum()
    out = []
    for g in clusters(mesh, mask, min_area):
        verts = mesh.vertices[np.unique(mesh.faces[g])]
        centre = mesh.area_faces[g] @ mesh.triangles_center[g] / mesh.area_faces[g].sum()
        dirs = []
        u = centre - support
        u = unit(u - (u @ d) * d)
        if u is not None:
            dirs.append(u)
        nb = np.unique(mesh.face_adjacency[np.isin(mesh.face_adjacency, g).any(1)])
        for w in mesh.face_normals[nb[~mask[nb]]]:
            w = unit(w - (w @ d) * d)
            if w is not None and all(w @ x < 0.97 for x in dirs):
                dirs.append(w)
        for u in dirs:
            for phi in (30, 45, 60, 75, 90):
                n = math.cos(math.radians(phi)) * d + math.sin(math.radians(phi)) * u
                out.append((verts[np.argmin(verts @ n)], n))
    return out


def convex_planes(mesh, threshold=0.05, max_parts=16):
    import coacd
    coacd.set_log_level("error")
    parts = coacd.run_coacd(coacd.Mesh(mesh.vertices, mesh.faces), threshold=threshold,
                            max_convex_hull=max_parts)
    hulls = [trimesh.Trimesh(v, f).convex_hull for v, f in parts]
    pts = sample(mesh, max(0.5, math.sqrt(mesh.area / 20000)))
    inside = []
    for h in hulls:
        off = (h.face_normals * h.triangles_center).sum(1)
        inside.append((h.face_normals @ pts.T - off[:, None]).max(0) < 0.5)
    out = []
    for i in range(len(hulls)):
        for j in range(i + 1, len(hulls)):
            contact = pts[inside[i] & inside[j]]
            if len(contact) < 6:
                continue
            o = contact.mean(0)
            n = np.linalg.svd(contact - o)[2][2]
            out.append((o, n if n @ (hulls[j].centroid - hulls[i].centroid) > 0 else -n, contact))
    return out


def frame(d):
    ref = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 else np.array([0, 1.0, 0])
    u = unit(ref - (ref @ d) * d)
    return np.vstack((u, np.cross(d, u), d))


def section(mesh, d, origin, h):
    import shapely
    rot = frame(d)
    local = mesh.copy()
    local.apply_translation(-origin)
    local.vertices = local.vertices @ rot.T
    s = local.section(plane_origin=[0, 0, h], plane_normal=[0, 0, 1])
    if s is None:
        return shapely.Polygon()
    p, _ = s.to_2D(to_2D=np.eye(4))
    return shapely.union_all([g.buffer(0) for g in p.polygons_full]) if p.polygons_full else shapely.Polygon()


def unsupported(mesh, d, origin, sin_a, base=None, step=1.0):
    k = ("support", base.wkb_hex if base is not None else "") + key(mesh, d, origin, [sin_a, step])
    if k not in CACHE:
        CACHE[k] = layer_support(mesh, d, origin, sin_a, base, step)
    return CACHE[k]


def layer_support(mesh, d, origin, sin_a, base, step):
    import shapely
    rot = frame(d)
    local = mesh.copy()
    local.apply_translation(-origin)
    local.vertices = local.vertices @ rot.T
    lo, hi = local.vertices[:, 2].min(), local.vertices[:, 2].max()
    slide = step * math.tan(math.asin(sin_a)) + 0.05
    prev, area, islands = None, 0.0, []
    if base is not None:
        prev = base
    h = max(lo, 0.0) + step / 2
    while h < hi:
        s = local.section(plane_origin=[0, 0, h], plane_normal=[0, 0, 1])
        region = None
        if s is not None:
            p, _ = s.to_2D(to_2D=np.eye(4))
            if p.polygons_full:
                region = shapely.union_all([g.buffer(0) for g in p.polygons_full])
        if region is not None and not region.is_empty:
            if prev is None:
                if h > max(lo, 0.0) + step:
                    islands.append((h, region))
                    area += region.area
            else:
                free = region.difference(prev.buffer(slide))
                for g in getattr(free, "geoms", [free]):
                    if g.area > 0.05:
                        area += g.area
                        if g.area > 1.0:
                            islands.append((h, g))
                region = region.intersection(prev.buffer(slide))
        prev = region
        h += step
    out = []
    for h, g in sorted(islands, key=lambda i: -i[1].area):
        ring = np.asarray(g.exterior.coords) if g.geom_type == "Polygon" else np.asarray(g.envelope.exterior.coords)
        pts = np.vstack(([g.centroid.x, g.centroid.y], ring[:, :2]))
        out.append(origin + np.column_stack((pts, np.full(len(pts), h))) @ rot)
    return area, out


class Planner:
    def __init__(self, machine, method="hybrid", overhang=50.0, max_tilt=90.0,
                 max_chunks=12, min_volume=20.0, check_collisions=True):
        self.machine = machine
        self.method = method
        self.sin_a = math.sin(math.radians(overhang))
        self.cos_tilt = math.cos(math.radians(max_tilt))
        self.max_chunks = max_chunks
        self.min_volume = min_volume
        self.check = check_collisions and machine.head is not None
        self.convex = []

    def plan(self, mesh):
        CACHE.clear()
        if self.method != "hybrid":
            return self.order(self.plan_one(mesh, self.method))
        plans = [self.plan_one(mesh, m, w) for m in ("overhang", "mixed") for w in (0.0, 1.0)]
        return self.order(min(plans, key=lambda p: (round(sum(c.overhang for c in p), 1), len(p))))

    def order(self, chunks, probes=24):
        n = len(chunks)
        deps = [set() for _ in chunks]
        rests = [set() for _ in chunks]
        for i, b in enumerate(chunks):
            cap = base_faces(b.mesh, b.d, b.origin)
            for need, mask in ((deps[i], cap), (rests[i], ~cap & (b.mesh.face_normals @ b.d < -0.1))):
                pts, normals = b.mesh.triangles_center[mask], b.mesh.face_normals[mask]
                for j, a in enumerate(chunks):
                    if j == i or not len(pts):
                        continue
                    _, dist, tri = trimesh.proximity.closest_point(a.mesh, pts)
                    if ((dist < 0.05) & ((a.mesh.face_normals[tri] * normals).sum(1) < -0.9)).any():
                        need.add(j)
        hit = np.zeros((n, n + 1, probes, 2), bool)
        if self.check:
            head = self.machine.head
            clouds = [sample(c.mesh, 2.0) for c in chunks]
            obstacles = [("part", s, cKDTree(s), None) for s in clouds]
            for i, c in enumerate(chunks):
                pts = clouds[i][np.argsort((clouds[i] - c.origin) @ c.d)]
                pts = pts[np.linspace(0, len(pts) - 1, probes).astype(int)] + 0.4 * c.d
                sols = self.solutions(c.d)
                hit[i, n, :, len(sols):] = True
                for s, (a, cc) in enumerate(sols):
                    for k, p in enumerate(pts):
                        hit[i, n, k, s] = head.collides(p, a, cc, []) is not None
                        for j in range(n):
                            hit[i, j, k, s] = j != i and head.collides(p, a, cc, [obstacles[j]]) is not None

        def blocked(i, done):
            return int(hit[i, done + [n]].any(0).all(-1).sum())

        done, left = [], list(range(n))
        while left:
            unmet = {i: (len(deps[i] - set(done)), len(rests[i] - set(done))) for i in left}
            ready = [i for i in left if unmet[i] == min(unmet.values())]
            pick = min(ready, key=lambda i: (blocked(i, done), sum(blocked(j, done + [i]) for j in left if j != i), i))
            done.append(pick)
            left.remove(pick)
        return [chunks[i] for i in done]

    def plan_one(self, mesh, method, small=0.0):
        self.use = method
        self.small = small
        self.volume = mesh.volume
        self.total = mesh.area
        self.min_area = max(4.0, 0.002 * mesh.area)
        self.chunk_cost = 0.5 * self.min_area
        if method in ("convex", "mixed") and not self.convex:
            self.convex = convex_planes(mesh)
        if self.check:
            self.platter = (self.machine.head.platter, cKDTree(self.machine.head.platter))
        root = Chunk(mesh, (0, 0, 1), (0, 0, 0))
        self.count = 1
        self.under = []
        chunks = self.split(root)
        for c in chunks:
            c.overhang = unsupported(c.mesh, c.d, c.origin, self.sin_a, c.base)[0]
        return chunks

    def candidates(self, chunk):
        out = []
        if self.use in ("overhang", "mixed"):
            out += overhang_planes(chunk.mesh, chunk.d, chunk.origin, self.sin_a, self.min_area)
        if self.use in ("convex", "mixed"):
            lo, hi = chunk.mesh.bounds
            d = chunk.d
            for o, n, contact in self.convex:
                if (o < lo - 1).any() or (o > hi + 1).any():
                    continue
                out.append((o, n))
                u = unit(n - (n @ d) * d)
                if u is None or n @ d < 0:
                    continue
                for phi in (30, 45, 60, 75):
                    m = math.cos(math.radians(phi)) * d + math.sin(math.radians(phi)) * u
                    out.append((contact[np.argmin(contact @ m)], m))
        return out

    def island_planes(self, chunk, islands):
        mesh, d = chunk.mesh, chunk.d
        centre = mesh.centroid
        rot = frame(d)
        sides = [math.cos(t) * rot[0] + math.sin(t) * rot[1] for t in np.arange(8) * math.pi / 4]
        out = []
        for pts in islands[:3]:
            c = pts[0]
            u = unit((c - centre) - ((c - centre) @ d) * d)
            if u is not None:
                for phi in (45, 60, 75, 90, 100):
                    n = math.cos(math.radians(phi)) * d + math.sin(math.radians(phi)) * u
                    for lift in (0.0, 3.0):
                        out.append((c + lift * d, n))
            for u in sides:
                for phi in (45, 60, 75):
                    n = math.cos(math.radians(phi)) * d + math.sin(math.radians(phi)) * u
                    out.append((pts[np.argmin(pts @ n)], n))
        return out

    def layer_planes(self, chunk, islands):
        o, d = chunk.origin, chunk.d
        heights = sorted({round(float((c[0] - o) @ d) - 1.0, 1) for c in islands})
        return [(o + h * d, d) for h in heights if h > 1.0][:3]

    def evaluate(self, mesh, d, o, po, n, here):
        if not (mesh.vertices @ n > po @ n + 0.5).any():
            return None
        below, aboves = cut(mesh, po, n)
        if below is None or below.volume < self.min_volume or any(a.volume < self.min_volume for a in aboves):
            return None
        quick = overhang_area(below, d, o, self.sin_a) + sum(overhang_area(a, n, po, self.sin_a) for a in aboves)
        return quick, below, aboves

    def feasible(self, child, n, below):
        if n[2] < self.cos_tilt:
            return False
        if not self.check:
            return True
        head = self.machine.head
        h = child.vertices @ n
        cap = child.vertices[h < h.min() + 0.05]
        top = child.vertices[np.concatenate((np.argsort(h)[-3:], np.argsort(child.vertices[:, 2])[:3]))]
        probe = np.vstack((cap[np.linspace(0, len(cap) - 1, min(len(cap), 6)).astype(int)], top))
        pts = np.vstack([sample(m, 2.0) for m in [below] + self.under])
        obstacles = [("part", pts, cKDTree(pts), None), ("platter",) + self.platter + (None,)]
        for sol in self.solutions(n):
            if all(head.collides(p + 0.4 * n, sol[0], sol[1], obstacles) is None for p in probe):
                return True
        return False

    def solutions(self, n):
        out = []
        for prev in ((0.0, 0.0), (0.0, 180.0)):
            s = self.machine.angles(n, prev)
            if s is not None and all(abs(s[0] - o[0]) > 1e-6 or abs(s[1] - o[1]) > 1e-6 for o in out):
                out.append(s)
        return out

    def split(self, chunk, depth=0):
        mesh, d, o = chunk.mesh, chunk.d, chunk.origin
        here, islands = unsupported(mesh, d, o, self.sin_a, chunk.base)
        if here <= self.min_area or depth >= self.max_chunks or self.count >= self.max_chunks:
            return [chunk]
        found, seen = [], set()
        for po, n in self.candidates(chunk) + self.island_planes(chunk, islands):
            sig = tuple(np.round(n, 2)) + (round(float(po @ n), 1),)
            if np.abs(n @ d) > 0.999 or sig in seen:
                continue
            seen.add(sig)
            r = self.evaluate(mesh, d, o, po, n, here)
            if r is not None:
                found.append((r[0], po, n, r[1], r[2]))
        found.sort(key=lambda f: f[0])
        best = None
        for _, po, n, below, aboves in found[:12]:
            base = section(below, n, po, -0.05)
            cost = unsupported(below, d, o, self.sin_a, chunk.base)[0] + self.chunk_cost * len(aboves)
            cost += self.small * here * sum(a.volume for a in aboves) / self.volume
            if best is not None and cost >= best[0]:
                continue
            cost += sum(unsupported(a, n, po, self.sin_a, base)[0] for a in aboves)
            if (best is None or cost < best[0]) and cost < here - self.min_area and all(
                    self.feasible(a, n, below) for a in aboves):
                best = (cost, below, aboves, po, n, base)
        if best is None:
            return self.look_ahead(chunk, islands, here, depth)
        _, below, aboves, po, n, base = best
        self.count += len(aboves)
        out = self.split(Chunk(below, d, o, chunk.base), depth + 1)
        self.under.append(below)
        for a in aboves:
            out += self.split(Chunk(a, n, po, base), depth + 1)
        self.under.pop()
        return out

    def look_ahead(self, chunk, islands, here, depth):
        mesh, d, o = chunk.mesh, chunk.d, chunk.origin
        count = self.count
        for po, n in self.layer_planes(chunk, islands):
            r = self.evaluate(mesh, d, o, po, n, here)
            if r is None:
                continue
            _, below, aboves = r
            base = section(below, n, po, -0.05)
            self.count = count + len(aboves)
            parts = []
            self.under.append(below)
            for a in aboves:
                parts += self.split(Chunk(a, d, po, base), depth + 1)
            self.under.pop()
            if len(parts) == len(aboves):
                continue
            rest = sum(unsupported(c.mesh, c.d, c.origin, self.sin_a, c.base)[0] for c in parts)
            below_left = unsupported(below, d, o, self.sin_a, chunk.base)[0]
            if below_left + rest < here - self.min_area:
                return self.split(Chunk(below, d, o, chunk.base), depth + 1) + parts
        self.count = count
        return [chunk]
