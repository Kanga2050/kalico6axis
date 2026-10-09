import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { CoreXYAC } from './kinematics.js'

const FEATURE_COLORS = [0x9e9e9e, 0xff8c1a, 0xffd23f, 0x3a86ff, 0xb5179e, 0xd00000, 0xff006e, 0x8338ec,
  0x0096c7, 0x2ec4b6, 0x6c757d, 0x6c757d, 0x52b788, 0x40916c, 0x4cc9f0]

export class TwinScene {
  constructor(canvas, manifest, machine) {
    this.manifest = manifest
    this.setMachine(machine)
    const r = this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true })
    r.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.scene = new THREE.Scene()
    this.scene.background = new THREE.Color(0xeef1f4)
    const cam = this.camera = new THREE.PerspectiveCamera(35, 1, 5, 20000)
    cam.up.set(0, 0, 1)
    const c = manifest.bed_top_center
    cam.position.set(c[0] + 900, c[1] - 1100, c[2] + 700)
    this.controls = new OrbitControls(cam, canvas)
    this.controls.target.set(c[0], c[1], c[2] + 60)
    this.controls.update()
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.6))
    const d1 = new THREE.DirectionalLight(0xffffff, 1.6); d1.position.set(800, -600, 1400); this.scene.add(d1)
    const d2 = new THREE.DirectionalLight(0xffffff, 0.6); d2.position.set(-900, 700, 500); this.scene.add(d2)
    this.groups = {}
    for (const g of ['frame', 'panels', 'z', 'y', 'x', 'tilt', 'rotary']) {
      const o = new THREE.Group(); o.matrixAutoUpdate = false; o.name = g
      this.groups[g] = o; this.scene.add(o)
    }
    this.path = new THREE.Group(); this.path.matrixAutoUpdate = false; this.scene.add(this.path)
    this.tip = new THREE.Mesh(new THREE.SphereGeometry(1.6, 16, 12), new THREE.MeshBasicMaterial({ color: 0xff2d55 }))
    this.scene.add(this.tip)
    this.resize()
    window.addEventListener('resize', () => this.resize())
    this.pose = [0, 0, 0, 0, 0, 0]
  }

  setMachine(machine) {
    this.machine = machine
    this.kin = new CoreXYAC(machine)
    const m = this.manifest
    const bc = m.bed_top_center
    this.offset = [bc[0] - this.kin.cx, bc[1] - this.kin.cy, bc[2]]
    const n = m.nozzle_tip_cad
    this.nozzleMachine = [n[0] - this.offset[0], n[1] - this.offset[1], n[2] - this.offset[2]]
  }

  async load(base, onProgress) {
    const loader = new GLTFLoader()
    const entries = Object.entries(this.manifest.groups)
    let done = 0
    await Promise.all(entries.map(async ([g, info]) => {
      const gltf = await loader.loadAsync(base + info.file)
      gltf.scene.traverse((o) => {
        if (o.isMesh) {
          o.material.side = o.material.transparent ? THREE.DoubleSide : THREE.FrontSide
          if (o.material.transparent) { o.material.depthWrite = false; o.renderOrder = 2 }
        }
      })
      ;(this.groups[g] || this.groups.frame).add(gltf.scene)
      done++; onProgress && onProgress(done / entries.length, g)
    }))
    this.setPose(this.pose)
  }

  resize() {
    const c = this.renderer.domElement
    const w = c.clientWidth, h = c.clientHeight
    if (!w || !h) return
    this.renderer.setSize(w, h, false)
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
  }

  showPanels(v) { this.groups.panels.visible = v }

  // pose in part coordinates [x y z a b c]
  setPose(p) {
    this.pose = p
    const m = this.kin.toMachine(p)
    this.machinePos = m
    const n = this.nozzleMachine
    const dx = m[0] - n[0], dy = m[1] - n[1], dz = m[2] - n[2]
    this.groups.z.matrix.makeTranslation(0, 0, dz)
    this.groups.y.matrix.makeTranslation(0, dy, dz)
    this.groups.x.matrix.makeTranslation(dx, dy, dz)
    const ta = this.manifest.tilt_axis.point, ra = this.manifest.rotary_axis.point
    const tilt = new THREE.Matrix4().makeTranslation(ta[0], ta[1], ta[2])
      .multiply(new THREE.Matrix4().makeRotationX(p[3] * Math.PI / 180))
      .multiply(new THREE.Matrix4().makeTranslation(-ta[0], -ta[1], -ta[2]))
    const rot = tilt.clone()
      .multiply(new THREE.Matrix4().makeTranslation(ra[0], ra[1], ra[2]))
      .multiply(new THREE.Matrix4().makeRotationZ(p[5] * Math.PI / 180))
      .multiply(new THREE.Matrix4().makeTranslation(-ra[0], -ra[1], -ra[2]))
    this.groups.tilt.matrix.copy(tilt)
    this.groups.rotary.matrix.copy(rot)
    this.path.matrix.copy(rot).multiply(new THREE.Matrix4().makeTranslation(...this.offset))
    for (const g of Object.values(this.groups)) g.matrixWorldNeedsUpdate = true
    this.path.matrixWorldNeedsUpdate = true
    this.tip.position.set(m[0] + this.offset[0], m[1] + this.offset[1], m[2] + this.offset[2])
  }

  setToolpath(res) {
    this.clearToolpath()
    const n = res.ext.length / 6
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(res.ext, 3))
    const col = new Float32Array(n * 6)
    const tmp = new THREE.Color()
    for (let i = 0; i < n; i++) {
      tmp.setHex(FEATURE_COLORS[res.extMeta[i * 2]] ?? 0x999999)
      col.set([tmp.r, tmp.g, tmp.b, tmp.r, tmp.g, tmp.b], i * 6)
    }
    geo.setAttribute('color', new THREE.BufferAttribute(col, 3))
    geo.setDrawRange(0, 0)
    this.pathLines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ vertexColors: true }))
    this.pathLines.frustumCulled = false
    this.path.add(this.pathLines)
    this.extSeg = res.extSeg
  }

  clearToolpath() {
    if (this.pathLines) { this.path.remove(this.pathLines); this.pathLines.geometry.dispose(); this.pathLines = null }
  }

  // show extrusion drawn by all segments before segIndex
  setProgress(segIndex) {
    if (!this.pathLines) return
    const s = this.extSeg
    let lo = 0, hi = s.length
    while (lo < hi) { const mid = (lo + hi) >> 1; if (s[mid] < segIndex) lo = mid + 1; else hi = mid }
    this.pathLines.geometry.setDrawRange(0, lo * 2)
  }

  showAllPath() { if (this.pathLines) this.pathLines.geometry.setDrawRange(0, this.extSeg.length * 2) }

  render() { this.controls.update(); this.renderer.render(this.scene, this.camera) }
}

export { FEATURE_COLORS }
