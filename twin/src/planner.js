// Port of the Kalico toolhead lookahead (klippy/toolhead.py) and the
// corexy_ac move checks, so simulated timing matches the firmware.
// Commanded position vector: [x, y, z, e, a, c]
import { CoreXYAC } from './kinematics.js'

const DEG = Math.PI / 180
const LOOKAHEAD_FLUSH_TIME = 0.25
export const SEG_FIELDS = 20 // t0 accel_t cruise_t decel_t start_v cruise_v accel move_d sx sy sz sa sc rx ry rz ra rc e0 er

class Move {
  constructor(p, start, end, speed) {
    this.start = start.slice(); this.end = end.slice()
    this.accel = p.maxAccel
    this.jd = p.junctionDeviation
    let velocity = Math.min(speed, p.maxVelocity)
    this.kin = true
    const d = end.map((v, i) => v - start[i])
    let moveD = Math.hypot(d[0], d[1], d[2])
    let inv
    if (moveD < 1e-9) {
      this.end[0] = start[0]; this.end[1] = start[1]; this.end[2] = start[2]
      d[0] = d[1] = d[2] = 0
      const rotD = Math.max(Math.abs(d[4]), Math.abs(d[5]), 0)
      moveD = rotD || Math.max(...d.slice(3).map(Math.abs))
      inv = moveD ? 1 / moveD : 0
      if (!rotD) { this.accel = 99999999.9; velocity = speed; this.kin = false }
    } else inv = 1 / moveD
    this.d = d; this.moveD = moveD
    this.r = d.map(v => v * inv)
    this.minMoveT = moveD / velocity
    this.maxStartV2 = 0
    this.maxCruiseV2 = velocity * velocity
    this.deltaV2 = 2 * moveD * this.accel
    this.maxSmoothedV2 = 0
    this.smoothDeltaV2 = 2 * moveD * p.maxAccelToDecel
    this.nextJunctionV2 = 999999999.9
  }
  limitSpeed(speed, accel) {
    const s2 = speed * speed
    if (s2 < this.maxCruiseV2) { this.maxCruiseV2 = s2; this.minMoveT = this.moveD / speed }
    this.accel = Math.min(this.accel, accel)
    this.deltaV2 = 2 * this.moveD * this.accel
    this.smoothDeltaV2 = Math.min(this.smoothDeltaV2, this.deltaV2)
  }
  calcJunction(prev, p) {
    if (!this.kin || !prev.kin) return
    const ea = []
    for (const [i, icv] of [[3, p.extruderICV], [4, p.rotary_a.icv], [5, p.rotary_c.icv]]) {
      const dr = this.r[i] - prev.r[i]
      ea.push(dr ? (icv / Math.abs(dr)) ** 2 : this.maxCruiseV2)
    }
    let maxStartV2 = Math.min(this.maxCruiseV2, prev.maxCruiseV2, prev.nextJunctionV2, prev.maxStartV2 + prev.deltaV2, ...ea)
    const a = this.r, b = prev.r
    const cosT = -(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])
    const sinD2 = Math.sqrt(Math.max(0.5 * (1 - cosT), 0))
    const cosD2 = Math.sqrt(Math.max(0.5 * (1 + cosT), 0))
    const oms = 1 - sinD2
    if (oms > 0 && cosD2 > 0) {
      const R = sinD2 / oms
      const qt = 0.25 * sinD2 / cosD2
      maxStartV2 = Math.min(maxStartV2, R * this.jd * this.accel, R * prev.jd * prev.accel, this.deltaV2 * qt, prev.deltaV2 * qt)
    }
    this.maxStartV2 = maxStartV2
    this.maxSmoothedV2 = Math.min(maxStartV2, prev.maxSmoothedV2 + prev.smoothDeltaV2)
  }
  setJunction(s2, c2, e2) {
    const h = 0.5 / this.accel
    const accelD = (c2 - s2) * h, decelD = (c2 - e2) * h
    const cruiseD = this.moveD - accelD - decelD
    this.startV = Math.sqrt(s2); this.cruiseV = Math.sqrt(c2); this.endV = Math.sqrt(e2)
    this.accelT = accelD / ((this.startV + this.cruiseV) * 0.5)
    this.cruiseT = cruiseD / this.cruiseV
    this.decelT = decelD / ((this.endV + this.cruiseV) * 0.5)
  }
}

export class Planner {
  constructor(machine) {
    const t = machine.toolhead
    this.p = {
      maxVelocity: t.max_velocity, maxAccel: t.max_accel,
      maxZVelocity: t.max_z_velocity, maxZAccel: t.max_z_accel,
      scv: t.square_corner_velocity, minCruiseRatio: t.minimum_cruise_ratio ?? 0.5,
      extruderICV: t.extruder_instantaneous_corner_velocity ?? 1.0,
      rotary_a: { ...machine.rotary_a, icv: machine.rotary_a.instantaneous_corner_velocity ?? 1.0 },
      rotary_c: { ...machine.rotary_c, icv: machine.rotary_c.instantaneous_corner_velocity ?? 1.0 }
    }
    this.kin = new CoreXYAC(machine)
    this.limits = machine.limits
    this.update()
    this.queue = []
    this.flushTime = LOOKAHEAD_FLUSH_TIME
    this.printTime = 0
    this.out = []
    this.warnings = []
  }
  update() {
    const p = this.p
    p.junctionDeviation = p.scv * p.scv * (Math.SQRT2 - 1) / p.maxAccel
    p.maxAccelToDecel = p.maxAccel * (1 - p.minCruiseRatio)
  }
  setLimits({ velocity, accel, scv, mcr }) {
    this.flush()
    if (velocity != null) this.p.maxVelocity = velocity
    if (accel != null) this.p.maxAccel = accel
    if (scv != null) this.p.scv = scv
    if (mcr != null) this.p.minCruiseRatio = mcr
    this.update()
  }
  pose(pos) { return [pos[0], pos[1], pos[2], pos[4], 0, pos[5]] }
  checkMove(m) {
    const k = this.kin, p = this.p
    for (const [i, ra] of [[4, p.rotary_a], [5, p.rotary_c]]) {
      if (!m.d[i]) continue
      const ep = m.end[i]
      if ((ra.position_min != null && ep < ra.position_min) || (ra.position_max != null && ep > ra.position_max))
        this.warnings.push(`rotary ${i === 4 ? 'A' : 'C'} out of range: ${ep.toFixed(2)}`)
      const ratio = m.moveD / Math.abs(m.d[i])
      m.limitSpeed(ra.max_velocity * ratio, ra.max_accel * ratio)
    }
    if (!m.kin) return
    const start = this.pose(m.start), end = this.pose(m.end)
    let samples = [end]
    if (start[3] !== end[3] || start[5] !== end[5]) {
      samples = []
      for (let i = 1; i <= 8; i++) samples.push(start.map((s, j) => s + (end[j] - s) * i / 8))
    }
    const ms = k.toMachine(start)
    for (const s of samples) {
      const mp = k.toMachine(s)
      for (let i = 0; i < 3; i++) {
        const [lo, hi] = this.limits[i]
        if (mp[i] !== ms[i] && (mp[i] < lo - 1e-6 || mp[i] > hi + 1e-6)) {
          this.warnings.push(`machine ${'XYZ'[i]} out of range: ${mp[i].toFixed(2)}`)
          break
        }
      }
    }
    const rr = this.pose(m.r)
    const wa = Math.abs(rr[3]) * DEG, wc = Math.abs(rr[5]) * DEG
    const rc = Math.max(...[start, end].map(q => Math.hypot(q[0] - k.cx, q[1] - k.cy)))
    const dz = Math.max(...[start, end].map(q => Math.abs(q[2] - k.az)))
    const ra = Math.hypot(Math.abs(k.cy - k.ay) + rc, dz)
    const swing = wa * ra + wc * rc
    const zr = swing + Math.max(...[start, end].map(q => Math.abs(k.rotate(rr, q)[2])))
    if (swing) {
      const kk = Math.hypot(rr[0], rr[1], rr[2]) + swing
      m.limitSpeed(p.maxVelocity / kk, p.maxAccel / kk)
      const cent = wa * wa * ra + wc * wc * rc
      if (cent) m.limitSpeed(Math.sqrt(0.5 * p.maxAccel / cent), m.accel)
    }
    if (zr) m.limitSpeed(p.maxZVelocity / zr, p.maxZAccel / zr)
  }
  move(start, end, speed) {
    const m = new Move(this.p, start, end, speed)
    if (!m.moveD) return
    if (m.kin) this.checkMove(m)
    else this.checkMove(m)
    this.queue.push(m)
    if (this.queue.length === 1) return
    m.calcJunction(this.queue[this.queue.length - 2], this.p)
    this.flushTime -= m.minMoveT
    if (this.flushTime <= 0) this.flush(true)
  }
  dwell(t) { this.flush(); this.printTime += Math.max(0, t) }
  flush(lazy = false) {
    this.flushTime = LOOKAHEAD_FLUSH_TIME
    let update = lazy
    const q = this.queue
    let flushCount = q.length
    const delayed = []
    let nextEnd = 0, nextSm = 0, peak = 0
    for (let i = flushCount - 1; i >= 0; i--) {
      const m = q[i]
      const reachStart = nextEnd + m.deltaV2
      const startV2 = Math.min(m.maxStartV2, reachStart)
      const reachSm = nextSm + m.smoothDeltaV2
      const sm = Math.min(m.maxSmoothedV2, reachSm)
      if (sm < reachSm) {
        if (sm + m.smoothDeltaV2 > nextSm || delayed.length) {
          if (update && peak) { flushCount = i; update = false }
          peak = Math.min(m.maxCruiseV2, (sm + reachSm) * 0.5)
          if (delayed.length) {
            if (!update && i < flushCount) {
              let mc = peak
              for (let j = delayed.length - 1; j >= 0; j--) {
                const [dm, ms2, me2] = delayed[j]
                mc = Math.min(mc, ms2)
                dm.setJunction(Math.min(ms2, mc), mc, Math.min(me2, mc))
              }
            }
            delayed.length = 0
          }
        }
        if (!update && i < flushCount) {
          const c2 = Math.min((startV2 + reachStart) * 0.5, m.maxCruiseV2, peak)
          m.setJunction(Math.min(startV2, c2), c2, Math.min(nextEnd, c2))
        }
      } else delayed.push([m, startV2, nextEnd])
      nextEnd = startV2
      nextSm = sm
    }
    if (update || !flushCount) return
    for (const m of q.slice(0, flushCount)) this.emit(m)
    q.splice(0, flushCount)
  }
  emit(m) {
    const s = this.pose(m.start), r = this.pose(m.r)
    const accel = m.accel
    this.out.push(this.printTime, m.accelT, m.cruiseT, m.decelT, m.startV, m.cruiseV, accel, m.moveD,
      s[0], s[1], s[2], s[3], s[5], r[0], r[1], r[2], r[3], r[5], m.start[3], m.r[3])
    this.printTime += m.accelT + m.cruiseT + m.decelT
  }
  finish() { this.flush(); return Float64Array.from(this.out) }
}

// distance travelled along a segment at local time t
export function segDist(seg, o, t) {
  const at = seg[o + 1], ct = seg[o + 2], dt = seg[o + 3]
  const sv = seg[o + 4], cv = seg[o + 5], acc = seg[o + 6]
  if (t <= 0) return 0
  if (t < at) return (sv + 0.5 * acc * t) * t
  let d = (sv + cv) * 0.5 * at
  t -= at
  if (t < ct) return d + cv * t
  d += cv * ct; t -= ct
  t = Math.min(t, dt)
  return d + (cv - 0.5 * acc * t) * t
}

export function segPose(seg, o, t, out) {
  const d = segDist(seg, o, t - seg[o])
  out[0] = seg[o + 8] + seg[o + 13] * d
  out[1] = seg[o + 9] + seg[o + 14] * d
  out[2] = seg[o + 10] + seg[o + 15] * d
  out[3] = seg[o + 11] + seg[o + 16] * d
  out[4] = 0
  out[5] = seg[o + 12] + seg[o + 17] * d
  out[6] = seg[o + 18] + seg[o + 19] * d
  return out
}

export function segEnd(seg, o) { return seg[o] + seg[o + 1] + seg[o + 2] + seg[o + 3] }

// binary search for the segment active at time t
export function findSeg(seg, t, hint = 0) {
  const n = seg.length / SEG_FIELDS
  if (!n) return -1
  let i = Math.min(Math.max(hint, 0), n - 1)
  if (seg[i * SEG_FIELDS] <= t && t < segEnd(seg, i * SEG_FIELDS)) return i
  let lo = 0, hi = n - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (seg[mid * SEG_FIELDS] <= t) lo = mid; else hi = mid - 1
  }
  return lo
}
