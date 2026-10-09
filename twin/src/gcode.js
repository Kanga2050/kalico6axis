// G-code interpreter that mirrors Kalico's gcode_move for X Y Z E A C,
// feeding moves into the planner and recording extrusion for the toolpath.
import { Planner, SEG_FIELDS } from './planner.js'

export const FEATURES = ['Other', 'Outer wall', 'Inner wall', 'Overhang wall', 'Sparse infill', 'Internal solid infill',
  'Top surface', 'Bottom surface', 'Bridge', 'Gap infill', 'Skirt', 'Brim', 'Support', 'Support interface', 'Travel']

function featureIndex(name) {
  const n = name.trim().toLowerCase()
  const i = FEATURES.findIndex(f => f.toLowerCase() === n)
  if (i >= 0) return i
  if (n.includes('outer') || n.includes('perimeter') && n.includes('external')) return 1
  if (n.includes('perimeter') || n.includes('wall')) return 2
  if (n.includes('solid')) return 5
  if (n.includes('infill')) return 4
  if (n.includes('top')) return 6
  if (n.includes('bottom')) return 7
  if (n.includes('bridge')) return 8
  if (n.includes('support')) return 12
  if (n.includes('skirt')) return 10
  return 0
}

const WORD = /([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))/g

export function runGcode(text, machine, onProgress) {
  const planner = new Planner(machine)
  const home = machine.home || [0, 0, 0]
  let pos = [home[0], home[1], home[2], 0, 0, 0] // x y z e a c
  let base = [0, 0, 0, 0, 0, 0]
  let absolute = true, absE = true
  let speed = 25
  let feature = 0
  let layer = 0, layerZ = 0
  const ext = []          // part-frame extrusion lines: x0 y0 z0 x1 y1 z1
  const extMeta = []      // feature, layer
  const extSeg = []       // planner segment index that drew it
  const fileOffsets = []  // byte offset of each move (per planner segment index)
  const layers = []
  const lines = text.split('\n')
  let offset = 0
  const axisIdx = { X: 0, Y: 1, Z: 2, E: 3, A: 4, C: 5 }
  const total = text.length
  let nextReport = 0

  const segCount = () => planner.out.length / SEG_FIELDS + planner.queue.length

  function doMove(target, extruding) {
    const before = segCount()
    planner.move(pos, target, speed)
    const after = segCount()
    if (after > before) fileOffsets.push(offset)
    if (extruding && after > before) {
      ext.push(pos[0], pos[1], pos[2], target[0], target[1], target[2])
      extMeta.push(feature, layer)
      extSeg.push(after - 1)
    }
    pos = target
  }

  function arc(cmd, params) {
    const cw = cmd === 2
    const tx = params.X !== undefined ? (absolute ? params.X + base[0] : pos[0] + params.X) : pos[0]
    const ty = params.Y !== undefined ? (absolute ? params.Y + base[1] : pos[1] + params.Y) : pos[1]
    const tz = params.Z !== undefined ? (absolute ? params.Z + base[2] : pos[2] + params.Z) : pos[2]
    let cx, cy
    if (params.R !== undefined) {
      const dx = tx - pos[0], dy = ty - pos[1], d = Math.hypot(dx, dy) / 2
      const h = Math.sqrt(Math.max(params.R * params.R - d * d, 0)) * ((cw ? 1 : -1) * Math.sign(params.R))
      cx = pos[0] + dx / 2 - h * dy / (2 * d); cy = pos[1] + dy / 2 + h * dx / (2 * d)
    } else { cx = pos[0] + (params.I || 0); cy = pos[1] + (params.J || 0) }
    const r = Math.hypot(pos[0] - cx, pos[1] - cy)
    let a0 = Math.atan2(pos[1] - cy, pos[0] - cx), a1 = Math.atan2(ty - cy, tx - cx)
    let sweep = a1 - a0
    if (cw && sweep >= 0) sweep -= 2 * Math.PI
    if (!cw && sweep <= 0) sweep += 2 * Math.PI
    const res = machine.arc_resolution || 1.0
    const n = Math.max(1, Math.ceil(Math.abs(sweep) * r / res))
    let e1 = pos[3]
    if (params.E !== undefined) e1 = absE ? params.E + base[3] : pos[3] + params.E
    const p0 = pos.slice()
    for (let i = 1; i <= n; i++) {
      const f = i / n, a = a0 + sweep * f
      const t = p0.slice()
      t[0] = i === n ? tx : cx + r * Math.cos(a)
      t[1] = i === n ? ty : cy + r * Math.sin(a)
      t[2] = p0[2] + (tz - p0[2]) * f
      t[3] = p0[3] + (e1 - p0[3]) * f
      doMove(t, e1 > p0[3])
    }
  }

  for (const raw of lines) {
    const lineLen = raw.length + 1
    let line = raw
    const sc = line.indexOf(';')
    if (sc >= 0) {
      const c = line.slice(sc + 1).trim()
      if (c.startsWith('TYPE:')) feature = featureIndex(c.slice(5))
      else if (c.startsWith('FEATURE:')) feature = featureIndex(c.slice(8))
      else if (c === 'LAYER_CHANGE' || c.startsWith('LAYER:')) { layer++; layers.push({ layer, offset, seg: segCount() }) }
      else if (c.startsWith('Z:')) layerZ = parseFloat(c.slice(2))
      line = line.slice(0, sc)
    }
    line = line.trim().toUpperCase()
    if (!line) { offset += lineLen; continue }
    if (line.startsWith('SET_VELOCITY_LIMIT')) {
      const g = (k) => { const m = line.match(new RegExp(k + '=([\\d.]+)')); return m ? parseFloat(m[1]) : null }
      planner.setLimits({ velocity: g('VELOCITY'), accel: g('ACCEL'), scv: g('SQUARE_CORNER_VELOCITY'), mcr: g('MINIMUM_CRUISE_RATIO') })
      offset += lineLen; continue
    }
    const cmdM = line.match(/^([GM])(\d+)/)
    if (!cmdM) { offset += lineLen; continue }
    const params = {}
    WORD.lastIndex = 0
    let w
    const rest = line.slice(cmdM[0].length)
    while ((w = WORD.exec(rest))) params[w[1]] = parseFloat(w[2])
    const code = cmdM[1] + cmdM[2]
    if (code === 'G0' || code === 'G1') {
      if (params.F) speed = params.F / 60
      const t = pos.slice()
      for (const [k, i] of Object.entries(axisIdx)) {
        if (params[k] === undefined) continue
        const abs = k === 'E' ? absE : absolute
        t[i] = abs ? params[k] + base[i] : pos[i] + params[k]
      }
      doMove(t, t[3] > pos[3] + 1e-9 && (t[0] !== pos[0] || t[1] !== pos[1] || t[2] !== pos[2]))
    } else if (code === 'G2' || code === 'G3') {
      if (params.F) speed = params.F / 60
      arc(code === 'G2' ? 2 : 3, params)
    } else if (code === 'G4') {
      planner.dwell((params.P || 0) / 1000 + (params.S || 0))
    } else if (code === 'G28') {
      planner.flush()
      const all = !('X' in params || 'Y' in params || 'Z' in params)
      if (all || 'X' in params) pos[0] = home[0]
      if (all || 'Y' in params) pos[1] = home[1]
      if (all || 'Z' in params) pos[2] = home[2]
    } else if (code === 'G90') { absolute = true; absE = true }
    else if (code === 'G91') { absolute = false; absE = false }
    else if (code === 'M82') absE = true
    else if (code === 'M83') absE = false
    else if (code === 'G92') {
      for (const [k, i] of Object.entries(axisIdx)) if (params[k] !== undefined) base[i] = pos[i] - params[k]
    } else if (code === 'M204') {
      const a = params.S ?? Math.min(params.P ?? Infinity, params.T ?? Infinity)
      if (isFinite(a)) planner.setLimits({ accel: a })
    }
    offset += lineLen
    if (onProgress && offset > nextReport) { onProgress(offset / total); nextReport = offset + total / 50 }
  }
  const segs = planner.finish()
  return {
    segs,
    ext: Float32Array.from(ext),
    extMeta: Uint16Array.from(extMeta),
    extSeg: Uint32Array.from(extSeg),
    fileOffsets: Float64Array.from(fileOffsets),
    layers,
    warnings: [...new Set(planner.warnings)].slice(0, 50),
    duration: planner.printTime
  }
}
