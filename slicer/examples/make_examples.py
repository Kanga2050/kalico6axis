"""Write the test models: python slicer/examples/make_examples.py"""
import os

import manifold3d as mf
import numpy as np
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))


def box(sx, sy, sz, x=0, y=0, z=0):
    return mf.Manifold.cube((sx, sy, sz)).translate((x - sx / 2, y - sy / 2, z))


def cyl(r, h, n=48):
    return mf.Manifold.cylinder(h, r, r, n)


def rod(r, length, tilt, yaw, start):
    return cyl(r, length).rotate((0, tilt, 0)).rotate((0, 0, yaw)).translate(start)


MODELS = {
    "tbracket": lambda: box(20, 20, 40) + box(20, 40, 10, y=30, z=30),
    "ybranch": lambda: cyl(6, 26) + rod(5, 30, 60, 0, (0, 0, 22)) + rod(5, 30, 60, 180, (0, 0, 22)),
    "mushroom": lambda: cyl(6, 22) + cyl(18, 6).translate((0, 0, 20)),
    "tallmushroom": lambda: cyl(7, 62) + cyl(18, 6).translate((0, 0, 60)),
    "arch": lambda: box(12, 16, 30, x=-24) + box(12, 16, 30, x=24) + box(60, 16, 8, z=30),
    "lbracket": lambda: box(16, 16, 45) + box(16, 40, 12, y=-28, z=33),
}


def build(name):
    m = MODELS[name]().to_mesh()
    return trimesh.Trimesh(np.asarray(m.vert_properties[:, :3], float), np.asarray(m.tri_verts))


if __name__ == "__main__":
    for name in MODELS:
        mesh = build(name)
        mesh.export(os.path.join(HERE, name + ".stl"))
        print("%-9s %6d faces %8.0f mm3 %s" % (name, len(mesh.faces), mesh.volume,
                                              np.round(mesh.extents, 1)))
