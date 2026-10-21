import math
import os
import subprocess
import sys

import numpy as np
import pytest
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
SLICER = os.path.dirname(HERE)
REPO = os.path.dirname(SLICER)
CONFIG = os.path.join(REPO, "config")
sys.path.insert(0, SLICER)

from fivex.check import check, support  # noqa: E402
from fivex.decompose import Planner, base_faces, unsupported  # noqa: E402
from fivex.machine import Machine  # noqa: E402
from fivex.toolpath import ChunkSlicer  # noqa: E402

sys.path.insert(0, os.path.join(SLICER, "examples"))
from make_examples import MODELS, build  # noqa: E402


@pytest.fixture(scope="session")
def machine():
    m = Machine.from_config(CONFIG)
    m.load_head(CONFIG)
    return m


@pytest.fixture(scope="session")
def models(tmp_path_factory):
    out = {}
    for name in MODELS:
        mesh = build(name)
        lo, hi = mesh.bounds
        mesh.apply_translation((175 - (lo[0] + hi[0]) / 2, 175 - (lo[1] + hi[1]) / 2, -lo[2]))
        out[name] = mesh
    return out


def kalico_kinematics():
    path = os.path.join(REPO, "kalico", "klippy", "kinematics", "corexy_ac.py")
    src = open(path).read()
    start = src.index("    def _to_machine")
    end = src.index("    def calc_position")
    ns = {"math": math, "DEG": math.pi / 180}
    exec("class K:\n" + src[start:end], ns)
    return ns["K"]


def test_transform_matches_kalico(machine):
    k = kalico_kinematics()()
    k.ay, k.az, k.cx, k.cy = machine.ay, machine.az, machine.cx, machine.cy
    rng = np.random.default_rng(1)
    for _ in range(200):
        p = rng.uniform((100, 100, 0), (250, 250, 120))
        a, c = rng.uniform(-100, 100), rng.uniform(-720, 720)
        ours = machine.to_machine(p, a, c)[0]
        assert np.allclose(ours, k._to_machine((p[0], p[1], p[2], a, 0, c)), atol=1e-9)
        assert np.allclose(machine.to_part(ours, a, c)[0], k._to_part(ours, a, c), atol=1e-9)
        assert np.allclose(machine.to_part(ours, a, c)[0], p, atol=1e-9)


def test_tool_axis_points_up_in_the_machine(machine):
    rng = np.random.default_rng(2)
    for _ in range(200):
        t = rng.normal(size=3)
        t[2] = abs(t[2]) + 0.2
        t /= np.linalg.norm(t)
        a, c = machine.angles(t, (rng.uniform(-30, 30), rng.uniform(-400, 400)))
        assert machine.a_min <= a <= machine.a_max
        assert np.allclose(machine.tool_axis(a, c), t, atol=1e-9)
        p = rng.uniform((150, 150, 0), (200, 200, 50))
        up = machine.to_machine(p + t, a, c)[0] - machine.to_machine(p, a, c)[0]
        assert np.allclose(up, (0, 0, 1), atol=1e-9)


def test_angles_stay_continuous(machine):
    prev = (10.0, 350.0)
    a, c = machine.angles(machine.tool_axis(10.0, -9.0), prev)
    assert abs(a - 10.0) < 1e-9 and abs(c - 351.0) < 1e-9


def test_head_clears_the_bed_upright_and_hits_it_tilted(machine):
    head = machine.head
    assert head.collides(np.array([175.0, 175.0, 0.3]), 0.0, 0.0, []) is None
    assert head.collides(np.array([175.0, 175.0, 0.3]), 30.0, 0.0, []) == "platter"
    assert head.collides(np.array([175.0, 175.0, 80.0]), 90.0, 0.0, []) is None


@pytest.mark.parametrize("name,chunks", [("tbracket", 2), ("lbracket", 2), ("ybranch", 3), ("tallmushroom", 3)])
@pytest.mark.parametrize("method", ["overhang", "hybrid"])
def test_decomposition_removes_overhangs(machine, models, name, chunks, method):
    mesh = models[name]
    plan = Planner(machine, method).plan(mesh)
    assert chunks <= len(plan) <= chunks * 3
    assert sum(c.mesh.volume for c in plan) == pytest.approx(mesh.volume, rel=1e-4)
    assert sum(c.overhang for c in plan) < 0.01 * mesh.area
    assert np.allclose(plan[0].d, (0, 0, 1))
    for c in plan:
        assert c.d[2] >= -1e-9
        assert c.mesh.is_winding_consistent


def test_tree_needs_cuts_in_several_directions(machine, models):
    mesh = models["tree"]
    plan = Planner(machine, "hybrid").plan(mesh)
    assert len(plan) >= 3
    assert len({tuple(np.round(c.d, 2)) for c in plan}) >= 3
    assert sum(c.mesh.volume for c in plan) == pytest.approx(mesh.volume, rel=1e-4)
    assert sum(c.overhang for c in plan) < 0.01 * mesh.area


def test_chunks_print_after_what_they_stand_on(machine, models):
    plan = Planner(machine, "hybrid").plan(models["tree"])
    for i, b in enumerate(plan):
        cap = base_faces(b.mesh, b.d, b.origin)
        for a in plan[i + 1:]:
            _, dist, tri = trimesh.proximity.closest_point(a.mesh, b.mesh.triangles_center[cap])
            assert not ((dist < 0.05) & ((a.mesh.face_normals[tri] * b.mesh.face_normals[cap]).sum(1) < -0.9)).any()


def test_unsolvable_parts_are_left_whole(machine, models):
    mesh = models["mushroom"]
    plan = Planner(machine, "hybrid").plan(mesh)
    assert sum(c.mesh.volume for c in plan) == pytest.approx(mesh.volume, rel=1e-4)
    before = unsupported(mesh, np.array([0, 0, 1.0]), np.zeros(3), math.sin(math.radians(50)))[0]
    assert sum(c.overhang for c in plan) <= before + 1e-6


def test_toolpaths_stay_inside_their_chunk(machine, models):
    plan = Planner(machine, "overhang").plan(models["lbracket"])
    for chunk in plan:
        rot, _, layers = ChunkSlicer(layer=0.6).slice(chunk)
        pts = np.vstack([p.pts for lay in layers for p in lay.paths if p.kind != "support"])
        assert trimesh.proximity.signed_distance(chunk.mesh, pts[:: max(1, len(pts) // 1500)]).min() > -0.3
        for lay in layers:
            for p in lay.paths:
                assert np.allclose(p.lean_dirs @ chunk.d, 0, atol=1e-9)


def test_gcode_stays_in_the_machine(tmp_path, machine, models):
    stl = tmp_path / "lbracket.stl"
    models["lbracket"].export(stl)
    out = tmp_path / "lbracket.gcode"
    res = subprocess.run([sys.executable, os.path.join(SLICER, "split5.py"), str(stl), str(out),
                          "--layer", "0.6", "--infill", "0.15"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "collisions left 0" in res.stdout
    text = out.read_text()
    assert "PRINT_START" in text and text.rstrip().endswith("PRINT_END")
    r = check(str(out), machine)
    assert r["outside limits"] == 0 and r["A outside limits"] == 0
    assert r["largest tilt while printing"] > 40
    assert r["filament mm"] > 0
    assert support(str(out))["midair"] == 0


@pytest.mark.parametrize("name", ["mushroom", "arch"])
def test_nothing_prints_in_midair(tmp_path, models, name):
    stl = tmp_path / (name + ".stl")
    models[name].export(stl)
    out = tmp_path / (name + ".gcode")
    res = subprocess.run([sys.executable, os.path.join(SLICER, "split5.py"), str(stl), str(out),
                          "--layer", "0.6", "--infill", "0.15"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert support(str(out))["midair"] == 0
    assert "collisions left 0" in res.stdout


@pytest.mark.skipif(not os.environ.get("SPLIT5_DICTS"), reason="set SPLIT5_DICTS=mcu=...,nhk=... to run Kalico")
def test_kalico_accepts_the_gcode(tmp_path, models):
    from fivex import firmware
    stl = tmp_path / "ybranch.stl"
    models["ybranch"].export(stl)
    out = tmp_path / "ybranch.gcode"
    subprocess.run([sys.executable, os.path.join(SLICER, "split5.py"), str(stl), str(out), "--layer", "0.6"],
                   check=True, capture_output=True)
    dicts = dict(kv.split("=", 1) for kv in os.environ["SPLIT5_DICTS"].split(","))
    rc, errors, _ = firmware.run(str(out), CONFIG, os.path.join(REPO, "kalico"), dicts)
    assert rc == 0, errors
