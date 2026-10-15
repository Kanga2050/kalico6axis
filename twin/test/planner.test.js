import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { Planner, SEG_FIELDS, segPose } from '../src/planner.js'
import { runGcode } from '../src/gcode.js'

const machine = JSON.parse(readFileSync(new URL('../public/machine.json', import.meta.url)))

test('single straight move: v/a trapezoid', () => {
  const p = new Planner(machine)
  p.move([100, 100, 10, 0, 0, 0], [200, 100, 10, 0, 0, 0], 100)
  const s = p.finish()
  assert.equal(s.length, SEG_FIELDS)
  assert.ok(Math.abs(p.printTime - (100 / 100 + 100 / 10000)) < 1e-9)
  const out = new Float64Array(7)
  segPose(s, 0, p.printTime, out)
  assert.ok(Math.abs(out[0] - 200) < 1e-9)
})

test('rotary-only move is limited by the rotary axis', () => {
  const p = new Planner(machine)
  p.move([175, 175, 10, 0, 0, 0], [175, 175, 10, 0, 0, 90], 1000)
  p.finish()
  const v = machine.rotary_c.max_velocity, a = machine.rotary_c.max_accel
  assert.ok(p.printTime >= 90 / v)
  assert.ok(Math.abs(p.printTime - (90 / v + v / a)) < 0.05)
})

test('gcode with A/C words and arcs plans and ends in place', () => {
  const g = ['G28', 'G90', 'M83', 'G1 X175 Y175 Z5 F6000', 'G1 A30 C45 F3000', 'G1 X185 E1 F1200',
    'G2 X195 Y175 I5 J0 E0.5', 'G1 A0 C0', 'G91', 'G1 Z10', 'G90'].join('\n')
  const r = runGcode(g, machine)
  assert.ok(r.segs.length / SEG_FIELDS > 5)
  assert.ok(r.ext.length / 6 >= 2)
  const n = r.segs.length / SEG_FIELDS, o = (n - 1) * SEG_FIELDS, out = new Float64Array(7)
  segPose(r.segs, o, r.duration, out)
  assert.ok(Math.abs(out[2] - 15) < 1e-6)
  assert.ok(Math.abs(out[3]) < 1e-9 && Math.abs(out[5]) < 1e-9)
})
