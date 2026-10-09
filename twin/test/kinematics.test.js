import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { CoreXYAC } from '../src/kinematics.js'

const ref = JSON.parse(readFileSync(new URL('./kin_reference.json', import.meta.url)))
const [cx, cy, ay, az] = ref.pivots
const k = new CoreXYAC({ rotary_c: { pivot: [cx, cy] }, rotary_a: { pivot: [ay, az] } })

test('toMachine matches corexy_ac._to_machine', () => {
  for (const c of ref.cases) {
    const m = k.toMachine(c.pose)
    for (let i = 0; i < 3; i++) assert.ok(Math.abs(m[i] - c.machine[i]) < 1e-9, `${i}: ${m[i]} vs ${c.machine[i]}`)
  }
})

test('toPart inverts toMachine like the firmware', () => {
  for (const c of ref.cases) {
    const p = k.toPart(c.machine, c.pose[3], c.pose[5])
    for (let i = 0; i < 3; i++) {
      assert.ok(Math.abs(p[i] - c.part[i]) < 1e-9)
      assert.ok(Math.abs(p[i] - c.pose[i]) < 1e-6)
    }
  }
})
