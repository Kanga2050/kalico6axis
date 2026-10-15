// Moonraker client. Subscribes to printer objects and, when the forked
// Moonraker/Kalico are installed, to the planned 6-axis motion queue.
import { SEG_FIELDS, findSeg, segPose, segEnd } from './planner.js'

const OBJECTS = {
  toolhead: ['position', 'extra_axes', 'homed_axes', 'estimated_print_time', 'axis_minimum', 'axis_maximum', 'machine_position'],
  motion_report: ['live_position', 'live_velocity', 'live_pose', 'live_machine_position'],
  gcode_move: ['gcode_position', 'extra_axes', 'speed_factor'],
  print_stats: ['state', 'filename', 'print_duration', 'total_duration', 'info'],
  virtual_sdcard: ['progress', 'file_position', 'is_active'],
  extruder: ['temperature', 'target'],
  heater_bed: ['temperature', 'target'],
  fan: ['speed'],
  webhooks: ['state', 'state_message']
}

export function endpoints(input) {
  let s = (input || '').trim()
  if (!s) s = location.host
  if (!/^https?:\/\//.test(s)) s = 'http://' + s
  const u = new URL(s)
  const hosts = []
  if (u.port) hosts.push(u.host)
  else { hosts.push(u.hostname + ':7125'); hosts.push(u.hostname) }
  return hosts.map(h => ({ http: `${u.protocol}//${h}`, ws: `${u.protocol === 'https:' ? 'wss' : 'ws'}://${h}/websocket` }))
}

export class MoonrakerLive {
  constructor(onStatus, onLog) {
    this.onStatus = onStatus; this.onLog = onLog
    this.status = {}
    this.id = 1; this.pending = new Map()
    this.trapq = []          // flat rows, SEG_FIELDS each, same layout as the planner
    this.offset = null       // print_time - wall time
    this.hasStream = false
  }

  async connect(input) {
    this.close()
    for (const ep of endpoints(input)) {
      try { await this.open(ep); this.ep = ep; return ep } catch (e) { this.onLog(`No Moonraker at ${ep.ws}`) }
    }
    throw new Error('Could not reach Moonraker')
  }

  open(ep) {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(ep.ws)
      const to = setTimeout(() => { ws.close(); reject(new Error('timeout')) }, 4000)
      ws.onopen = async () => {
        clearTimeout(to); this.ws = ws
        try {
          const info = await this.call('server.info')
          this.onLog(`Moonraker ${info.moonraker_version || ''}, Klippy ${info.klippy_state}`)
          const sub = await this.call('printer.objects.subscribe', { objects: OBJECTS })
          this.merge(sub.status, sub.eventtime)
          try {
            const r = await this.call('server.twin.subscribe')
            this.hasStream = !!r.streaming
            this.onLog('Motion stream on: full 6-axis trapq')
          } catch (e) {
            this.onLog('Twin component not found, using live_pose at 4 Hz')
          }
          resolve()
        } catch (e) { reject(e) }
      }
      ws.onerror = () => { clearTimeout(to); reject(new Error('ws error')) }
      ws.onclose = () => { if (this.ws === ws) { this.ws = null; this.onLog('Disconnected') } }
      ws.onmessage = (ev) => this.message(JSON.parse(ev.data))
    })
  }

  close() { if (this.ws) { const w = this.ws; this.ws = null; w.close() } this.trapq = []; this.status = {} }

  call(method, params) {
    return new Promise((resolve, reject) => {
      const id = this.id++
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ jsonrpc: '2.0', method, params, id }))
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); reject(new Error(method + ' timeout')) } }, 8000)
    })
  }

  message(msg) {
    if (msg.id && this.pending.has(msg.id)) {
      const p = this.pending.get(msg.id); this.pending.delete(msg.id)
      msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result)
      return
    }
    if (msg.method === 'notify_status_update') this.merge(msg.params[0], msg.params[1])
    else if (msg.method === 'notify_twin_trapq_batch') this.addTrapq(msg.params[0].data)
    else if (msg.method === 'notify_klippy_ready') this.connect(this.ep && this.ep.http)
  }

  merge(st, eventtime) {
    for (const [k, v] of Object.entries(st || {})) this.status[k] = { ...(this.status[k] || {}), ...v }
    const ept = st?.toolhead?.estimated_print_time
    if (ept != null) {
      const off = ept - performance.now() / 1000
      this.offset = this.offset == null ? off : Math.max(off, this.offset - 0.02)
    }
    this.onStatus(this.status)
  }

  addTrapq(rows) {
    for (const [t, dur, v0, acc, sp, dr] of rows) {
      // trapq rows are constant-accel pieces: fold into accel_t only
      this.trapq.push(t, dur, 0, 0, v0, v0 + acc * dur, acc, 0, sp[0], sp[1], sp[2], sp[3], sp[5], dr[0], dr[1], dr[2], dr[3], dr[5], 0, 0)
    }
    const now = this.printTime()
    if (now != null) {
      let cut = 0
      while (cut + SEG_FIELDS < this.trapq.length && segEnd(this.trapq, cut) < now - 5) cut += SEG_FIELDS
      if (cut) this.trapq.splice(0, cut)
    }
  }

  printTime() { return this.offset == null ? null : performance.now() / 1000 + this.offset }

  // best estimate of the part-frame pose now
  pose(out) {
    const t = this.printTime()
    if (this.hasStream && this.trapq.length && t != null) {
      const i = findSeg(this.trapq, t)
      if (i >= 0) { segPose(this.trapq, i * SEG_FIELDS, Math.min(t, segEnd(this.trapq, i * SEG_FIELDS)), out); return out }
    }
    const lp = this.status.motion_report?.live_pose
    if (lp && lp.some(v => v)) { for (let i = 0; i < 6; i++) out[i] = lp[i]; return out }
    const p = this.status.motion_report?.live_position || this.status.toolhead?.position || [0, 0, 0, 0]
    const ex = this.status.toolhead?.extra_axes || {}
    out[0] = p[0]; out[1] = p[1]; out[2] = p[2]; out[3] = ex.A ?? 0; out[4] = 0; out[5] = ex.C ?? 0
    return out
  }

  async fetchFile(name) {
    const r = await fetch(`${this.ep.http}/server/files/gcodes/${encodeURIComponent(name).replace(/%2F/g, '/')}`)
    if (!r.ok) throw new Error('download failed ' + r.status)
    return r.text()
  }
}
