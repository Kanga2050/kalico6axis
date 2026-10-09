# Prints reference poses from the firmware's own corexy_ac math for the JS tests.
import ast, json, math, random, sys, pathlib

src = pathlib.Path(__file__).resolve().parents[2] / 'kalico' / 'klippy' / 'kinematics' / 'corexy_ac.py'
tree = ast.parse(src.read_text())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CoreXYACKinematics')
ns = {'math': math, 'DEG': math.pi / 180.0}
for fn in cls.body:
    if isinstance(fn, ast.FunctionDef) and fn.name in ('_to_machine', '_to_part', '_rotate'):
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(src), 'exec'), ns)


class K:
    cx, cy, ay, az = 175.0, 175.0, 175.0, 5.0


k = K()
random.seed(1)
cases = []
for _ in range(40):
    p = [random.uniform(0, 350), random.uniform(0, 350), random.uniform(0, 200), random.uniform(-100, 100), 0.0, random.uniform(-720, 720)]
    m = ns['_to_machine'](k, p)
    back = ns['_to_part'](k, m, p[3], p[5])
    cases.append({'pose': p, 'machine': list(m), 'part': list(back)})
json.dump({'pivots': [k.cx, k.cy, k.ay, k.az], 'cases': cases}, sys.stdout)
