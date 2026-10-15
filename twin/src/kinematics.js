// Port of kalico/klippy/kinematics/corexy_ac.py (rotation-support branch).
// A pose is [x, y, z, a, b, c] in part coordinates (degrees for a, b, c).
// Machine coordinates are where the nozzle sits relative to the frame.

const DEG = Math.PI / 180

export class CoreXYAC {
  constructor(cfg) {
    this.cx = cfg.rotary_c.pivot[0]
    this.cy = cfg.rotary_c.pivot[1]
    this.ay = cfg.rotary_a.pivot[0]
    this.az = cfg.rotary_a.pivot[1]
  }

  toMachine(p) {
    const sa = Math.sin(p[3] * DEG), ca = Math.cos(p[3] * DEG)
    const sc = Math.sin(p[5] * DEG), cc = Math.cos(p[5] * DEG)
    const dx = p[0] - this.cx, dy = p[1] - this.cy
    const qy = this.cy + dx * sc + dy * cc - this.ay
    const qz = p[2] - this.az
    return [
      this.cx + dx * cc - dy * sc,
      this.ay + qy * ca - qz * sa,
      this.az + qy * sa + qz * ca
    ]
  }

  toPart(m, a, c) {
    const sa = Math.sin(a * DEG), ca = Math.cos(a * DEG)
    const sc = Math.sin(c * DEG), cc = Math.cos(c * DEG)
    const my = m[1] - this.ay, mz = m[2] - this.az
    const dx = m[0] - this.cx, dy = this.ay + my * ca + mz * sa - this.cy
    return [
      this.cx + dx * cc + dy * sc,
      this.cy - dx * sc + dy * cc,
      this.az - my * sa + mz * ca
    ]
  }

  rotate(v, p) {
    const sa = Math.sin(p[3] * DEG), ca = Math.cos(p[3] * DEG)
    const sc = Math.sin(p[5] * DEG), cc = Math.cos(p[5] * DEG)
    const vy = v[0] * sc + v[1] * cc
    return [v[0] * cc - v[1] * sc, vy * ca - v[2] * sa, vy * sa + v[2] * ca]
  }
}

export const poseOf = (pos) => [pos[0], pos[1], pos[2], pos[4] ?? 0, 0, pos[5] ?? 0]
