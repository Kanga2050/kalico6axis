// Virtual printer: plays planned G-code through the same motion model
// and exposes Moonraker-shaped status for the UI.
import { SEG_FIELDS, findSeg, segPose, segEnd } from './planner.js'

export class SimPrinter {
  constructor() {
    this.res = null; this.t = 0; this.rate = 1; this.playing = false
    this.hint = 0; this.name = ''
    this.manual = [175, 175, 50, 0, 0, 0]
  }
  load(res, name) { this.res = res; this.t = 0; this.hint = 0; this.name = name; this.playing = false }
  get duration() { return this.res ? this.res.duration : 0 }
  step(dt) { if (this.playing && this.res) { this.t = Math.min(this.t + dt * this.rate, this.duration); if (this.t >= this.duration) this.playing = false } }
  seg() {
    if (!this.res || !this.res.segs.length) return -1
    this.hint = findSeg(this.res.segs, this.t, this.hint)
    return this.hint
  }
  pose(out) {
    const i = this.seg()
    if (i < 0) { for (let k = 0; k < 6; k++) out[k] = this.manual[k]; out[6] = 0; return out }
    const o = i * SEG_FIELDS
    segPose(this.res.segs, o, Math.min(this.t, segEnd(this.res.segs, o)), out)
    return out
  }
  status(pose) {
    const i = Math.max(this.seg(), 0)
    const done = this.res ? this.t / Math.max(this.duration, 1e-9) : 0
    return {
      toolhead: { position: [pose[0], pose[1], pose[2], pose[6] || 0], extra_axes: { A: pose[3], C: pose[5] }, homed_axes: 'xyz' },
      print_stats: { state: this.res ? (this.playing ? 'printing' : (this.t >= this.duration ? 'complete' : 'paused')) : 'standby', filename: this.name, print_duration: this.t },
      virtual_sdcard: { progress: done, file_position: this.res && this.res.fileOffsets.length ? this.res.fileOffsets[Math.min(i, this.res.fileOffsets.length - 1)] : 0 }
    }
  }
}
