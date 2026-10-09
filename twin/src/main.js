import { TwinScene, FEATURE_COLORS } from './scene.js'
import { MoonrakerLive } from './live.js'
import { SimPrinter } from './sim.js'
import { FEATURES } from './gcode.js'

const $ = (id) => document.getElementById(id)
const f2 = (v) => (v == null || isNaN(v) ? '–' : v.toFixed(2))
const log = (s) => { const l = $('log'); l.textContent = (new Date().toLocaleTimeString() + '  ' + s + '\n' + l.textContent).slice(0, 4000) }

let mode = 'live'
let machine, manifest, scene
const sim = new SimPrinter()
const live = new MoonrakerLive((st) => { lastStatus = st; liveStatusChanged(st) }, log)
let lastStatus = {}
let liveFile = null, liveRes = null
const pose = new Float64Array(7)

function hms(t) {
  t = Math.max(0, Math.round(t))
  const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0')
}

function plan(text, name) {
  return new Promise((resolve, reject) => {
    const w = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' })
    $('loading').hidden = false
    w.onmessage = (ev) => {
      const d = ev.data
      if (d.progress != null) $('loading').textContent = `Planning ${name}: ${Math.round(d.progress * 100)}%`
      else { $('loading').hidden = true; w.terminate(); d.error ? reject(new Error(d.error)) : resolve(d.result) }
    }
    w.postMessage({ text, machine, id: 1 })
  })
}

function applyPrinterConfig(settings) {
  if (!settings) return
  const th = settings.printer || {}
  const t = machine.toolhead
  for (const k of ['max_velocity', 'max_accel', 'max_z_velocity', 'max_z_accel', 'square_corner_velocity', 'minimum_cruise_ratio'])
    if (th[k] != null) t[k] = th[k]
  for (const a of ['rotary_a', 'rotary_c']) {
    const s = settings[a]
    if (!s) continue
    for (const k of ['pivot', 'max_velocity', 'max_accel', 'position_min', 'position_max', 'instantaneous_corner_velocity'])
      if (s[k] != null) machine[a][k] = s[k]
  }
  scene.setMachine(machine)
  log(`Pivots from printer: A ${machine.rotary_a.pivot}, C ${machine.rotary_c.pivot}`)
}

async function liveStatusChanged(st) {
  const fn = st.print_stats?.filename
  if ($('follow-file').checked && fn && fn !== liveFile) {
    liveFile = fn
    try {
      log('Loading ' + fn)
      const text = await live.fetchFile(fn)
      liveRes = await plan(text, fn)
      scene.setToolpath(liveRes)
      log(`${fn}: ${liveRes.segs.length / 20} moves, est. ${hms(liveRes.duration)}`)
    } catch (e) { log(String(e)) }
  }
}

function showStatus(st) {
  $('state').textContent = st.print_stats?.state ?? '–'
  $('fname').textContent = st.print_stats?.filename || '–'
  $('progress').style.width = ((st.virtual_sdcard?.progress || 0) * 100).toFixed(1) + '%'
  const he = st.extruder, bd = st.heater_bed
  $('hotend').textContent = he ? `${f2(he.temperature)} / ${f2(he.target)} °C` : '–'
  $('bed').textContent = bd ? `${f2(bd.temperature)} / ${f2(bd.target)} °C` : '–'
}

function showPose(p, m) {
  $('px').textContent = f2(p[0]); $('py').textContent = f2(p[1]); $('pz').textContent = f2(p[2])
  $('pa').textContent = f2(p[3]) + '°'; $('pc').textContent = f2(p[5]) + '°'; $('pe').textContent = f2(p[6])
  $('mx').textContent = f2(m[0]); $('my').textContent = f2(m[1]); $('mz').textContent = f2(m[2])
}

function setMode(m) {
  mode = m
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.mode === m))
  $('live-panel').hidden = m !== 'live'; $('sim-panel').hidden = m !== 'sim'
  scene.clearToolpath()
  if (m === 'sim' && sim.res) scene.setToolpath(sim.res)
  if (m === 'live' && liveRes) scene.setToolpath(liveRes)
}

let last = performance.now()
let uiTick = 0
function frame(now) {
  const dt = (now - last) / 1000; last = now
  if (mode === 'sim') {
    sim.step(dt)
    sim.pose(pose)
    const i = sim.seg()
    if ($('whole').checked) scene.showAllPath(); else scene.setProgress(i)
    if (now - uiTick > 100) {
      showStatus(sim.status(pose))
      $('sim-time').textContent = sim.res ? `${hms(sim.t)} / ${hms(sim.duration)}` : '–'
      if (sim.res && document.activeElement !== $('scrub')) $('scrub').value = Math.round(1000 * sim.t / Math.max(sim.duration, 1e-9))
      $('play').textContent = sim.playing ? 'Pause' : 'Play'
    }
  } else {
    live.pose(pose)
    if (liveRes) {
      if ($('whole').checked) scene.showAllPath()
      else {
        const fp = lastStatus.virtual_sdcard?.file_position || 0
        const fo = liveRes.fileOffsets
        let lo = 0, hi = fo.length
        while (lo < hi) { const mid = (lo + hi) >> 1; if (fo[mid] < fp) lo = mid + 1; else hi = mid }
        scene.setProgress(lo)
      }
    }
    if (now - uiTick > 100) showStatus(lastStatus)
  }
  scene.setPose(Array.from(pose.slice(0, 6)))
  if (now - uiTick > 100) { showPose(pose, scene.machinePos); uiTick = now }
  scene.render()
  requestAnimationFrame(frame)
}

async function init() {
  machine = await fetch('./machine.json').then(r => r.json())
  let hasModel = true
  manifest = await fetch('./model/manifest.json').then(r => { if (!r.ok) throw new Error(); return r.json() }).catch(() => {
    hasModel = false
    return {
      groups: {}, bed_top_center: [177, 217, 127], nozzle_tip_cad: [177, 181.1, 175],
      tilt_axis: { point: [177, 217, 132], dir: [1, 0, 0] }, rotary_axis: { point: [177, 217, 127], dir: [0, 0, 1] }
    }
  })
  scene = new TwinScene($('c'), manifest, machine)
  if (!hasModel) { scene.buildPlaceholder(); log('CAD model not found, showing a simple stand-in') }
  sim.manual = [machine.rotary_c.pivot[0], machine.rotary_c.pivot[1], 40, 0, 0, 0]
  $('legend').innerHTML = FEATURES.slice(0, 14).map((f, i) => `<span style="--c:#${FEATURE_COLORS[i].toString(16).padStart(6, '0')}">${f}</span>`).join('')
  await scene.load('./model/', (f, g) => { $('loading').textContent = `Loading printer model… ${Math.round(f * 100)}%` })
  $('loading').hidden = true
  requestAnimationFrame(frame)

  document.querySelectorAll('.tab').forEach(b => b.onclick = () => setMode(b.dataset.mode))
  $('panels').onchange = (e) => scene.showPanels(e.target.checked)
  const saved = localStorageGet('twin.host')
  const params = new URLSearchParams(location.search)
  $('host').value = params.get('printer') || saved || (location.port === '7125' ? location.host : '')
  $('connect').onclick = async () => {
    const h = $('host').value.trim()
    $('conn').textContent = 'Connecting…'; $('conn').classList.remove('ok')
    try {
      const ep = await live.connect(h)
      localStorageSet('twin.host', h)
      $('conn').textContent = 'Connected ' + ep.http.replace(/^https?:\/\//, ''); $('conn').classList.add('ok')
      try {
        const r = await live.call('printer.objects.query', { objects: { configfile: ['settings'] } })
        applyPrinterConfig(r.status.configfile.settings)
      } catch (e) { log('Config not read: ' + e.message) }
      liveFile = null; liveStatusChanged(lastStatus)
    } catch (e) { $('conn').textContent = 'Not connected'; log(e.message) }
  }
  if ($('host').value) $('connect').click()

  const simLoad = async (text, name) => {
    try {
      const res = await plan(text, name)
      sim.load(res, name)
      setMode('sim')
      $('warnings').innerHTML = res.warnings.map(w => `<li>${w}</li>`).join('')
      log(`${name}: ${res.segs.length / 20} moves, ${res.ext.length / 6} extrusions, ${hms(res.duration)}`)
    } catch (err) { log(String(err)) }
  }
  $('file').onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return
    simLoad(await f.text(), f.name)
  }
  if (params.get('gcode')) {
    const url = params.get('gcode')
    fetch(url).then(r => r.text()).then(t => simLoad(t, url.split('/').pop())).catch(e => log(e.message))
  }
  $('play').onclick = () => { if (sim.res) { if (sim.t >= sim.duration) sim.t = 0; sim.playing = !sim.playing } }
  $('end').onclick = () => { if (sim.res) { sim.t = sim.duration; sim.playing = false } }
  $('rate').onchange = (e) => sim.rate = parseFloat(e.target.value)
  sim.rate = parseFloat($('rate').value)
  $('scrub').oninput = (e) => { if (sim.res) sim.t = sim.duration * e.target.value / 1000 }
  const man = () => {
    sim.manual[3] = parseFloat($('man-a').value); sim.manual[5] = parseFloat($('man-c').value)
    $('man-a-v').textContent = sim.manual[3] + '°'; $('man-c-v').textContent = sim.manual[5] + '°'
  }
  $('man-a').oninput = man; $('man-c').oninput = man
  $('man-a').min = machine.rotary_a.position_min ?? -100; $('man-a').max = machine.rotary_a.position_max ?? 100
}

function localStorageGet(k) { try { return localStorage.getItem(k) } catch (e) { return null } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v) } catch (e) { } }

init().catch(e => { $('loading').textContent = 'Failed to load: ' + e.message; console.error(e) })
