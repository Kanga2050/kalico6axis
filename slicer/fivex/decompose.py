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
    def __init__(self, mesh, d, origin):
        self.mesh = mesh
        self.d = np.asarray(d, float) / np.linalg.norm(d)
        self.origin = np.asarray(origin, float)
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


def cut(mesh, origin, n):
    above, below = to_manifold(mesh).split_by_plane(tuple(n), float(np.dot(n, origin)))
    if below.is_empty() or above.is_empty():
        return None, []
    return to_trimesh(below), [to_trimesh(above)]


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
        if self.method != "hybrid":
            return self.plan_one(mesh, self.method)
        plans = [self.plan_one(mesh, m, w) for m in ("overhang", "convex", "mixed") for w in (0.0, 1.0)]
        return min(plans, key=lambda p: (round(sum(c.overhang for c in p), 1), len(p)))

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
        chunks = self.split(root)
        for c in chunks:
            c.overhang = overhang_area(c.mesh, c.d, c.origin, self.sin_a)
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

    def feasible(self, child, n, below):
        if n[2] < self.cos_tilt:
            return False
        if not self.check:
            return True
        head = self.machine.head
        h = child.vertices @ n
        cap = child.vertices[h < h.min() + 0.05]
        top = child.vertices[np.argsort(h)[-3:]]
        probe = np.vstack((cap[np.linspace(0, len(cap) - 1, min(len(cap), 6)).astype(int)], top))
        pts = sample(below, 2.0)
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
        here = overhang_area(mesh, d, o, self.sin_a)
        if here <= self.min_area or depth >= self.max_chunks:
            return [chunk]
        best = None
        for po, n in self.candidates(chunk):
            if np.abs(n @ d) > 0.999 or not (mesh.vertices @ n > po @ n).any():
                continue
            base = mesh.triangles_center[base_faces(mesh, d, o)]
            if len(base) and (base @ n > po @ n - 1e-6).any():
                continue
            below, aboves = cut(mesh, po, n)
            if below is None or below.volume < self.min_volume:
                continue
            if any(a.volume < self.min_volume for a in aboves):
                continue
            cost = overhang_area(below, d, o, self.sin_a) + self.chunk_cost * len(aboves)
            cost += self.small * here * sum(a.volume for a in aboves) / self.volume
            cost += sum(overhang_area(a, n, po, self.sin_a) for a in aboves)
            if best is not None and cost >= best[0]:
                continue
            if cost >= here - self.min_area or not all(self.feasible(a, n, below) for a in aboves):
                continue
            best = (cost, below, aboves, po, n)
        if best is None:
            return [chunk]
        _, below, aboves, po, n = best
        out = self.split(Chunk(below, d, o), depth + 1)
        for a in aboves:
            out += self.split(Chunk(a, n, po), depth + 1)
        return out
