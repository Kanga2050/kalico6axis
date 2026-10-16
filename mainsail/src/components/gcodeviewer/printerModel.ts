// Renders the machine described by Kalico's [model] / [model_part]
// sections inside a gcodeviewer scene and moves the parts from status
// values. All geometry work happens here in the browser.
import { Scene } from '@babylonjs/core/scene'
import { Matrix, Vector3 } from '@babylonjs/core/Maths/math.vector'
import { ArcRotateCamera } from '@babylonjs/core/Cameras/arcRotateCamera'
import { Color3 } from '@babylonjs/core/Maths/math.color'
import { SceneLoader } from '@babylonjs/core/Loading/sceneLoader'
import { AbstractMesh } from '@babylonjs/core/Meshes/abstractMesh'
import { Mesh } from '@babylonjs/core/Meshes/mesh'
import { PBRMaterial } from '@babylonjs/core/Materials/PBR/pbrMaterial'
import { StandardMaterial } from '@babylonjs/core/Materials/standardMaterial'
import '@babylonjs/loaders/glTF'
import '@babylonjs/loaders/STL'
import { STLFileLoader } from '@babylonjs/loaders/STL'

export interface ModelPartConfig {
    name: string
    file: string | null
    parent?: string
    joint?: 'fixed' | 'linear' | 'rotary'
    color?: string | null
    visible?: boolean
    axis?: number[]
    origin?: number[]
    source?: string
    scale?: number
    home?: number
    minimum?: number | null
    maximum?: number | null
}

export interface ModelConfig {
    up_axis: 'z' | 'y'
    unit_scale: number
    offset: number[]
    root: ModelPartConfig
    parts: ModelPartConfig[]
}

export type ModelValues = Record<string, number>

export type ToolPosition = { axes: string; position: number }[] | { x: number; y: number; z: number }

const AXES: Record<string, number> = { x: 0, y: 1, z: 2 }

// machine (Z up, right handed) -> gcodeviewer scene (x, z, y)
const Q = Matrix.FromValues(1, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1)

function translation(v: number[]) {
    return Matrix.Translation(v[0], v[1], v[2])
}

// right handed rotation about a unit axis, as a row-vector matrix
function rotation(axis: number[], degrees: number) {
    const t = (degrees * Math.PI) / 180
    const c = Math.cos(t)
    const s = Math.sin(t)
    const k = 1 - c
    const [x, y, z] = axis
    return Matrix.FromValues(
        c + x * x * k,
        y * x * k + z * s,
        z * x * k - y * s,
        0,
        x * y * k - z * s,
        c + y * y * k,
        z * y * k + x * s,
        0,
        x * z * k + y * s,
        y * z * k - x * s,
        c + z * z * k,
        0,
        0,
        0,
        0,
        1
    )
}

// walk printer status by keys; returns undefined when a step is missing
function dig(obj: unknown, ...keys: (string | number)[]): unknown {
    let v = obj
    for (const k of keys) {
        if (v === null || typeof v !== 'object') return undefined
        v = (v as Record<string | number, unknown>)[k]
    }
    return v
}

function num(v: unknown): number | null {
    return typeof v === 'number' && !isNaN(v) ? v : null
}

// value of a joint source from Klipper status, or from a simulated position
export function readSource(source: string, printer: unknown, sim?: number[] | null): number | null {
    if (source in AXES || source.startsWith('machine_')) {
        const i = AXES[source.replace('machine_', '')]
        if (sim) return sim[i] ?? 0
        const machine = source.startsWith('machine_')
            ? num(dig(printer, 'motion_report', 'live_machine_position', i))
            : null
        return (
            machine ??
            num(dig(printer, 'motion_report', 'live_position', i)) ??
            num(dig(printer, 'toolhead', 'position', i))
        )
    }
    if (source === 'e')
        return num(dig(printer, 'motion_report', 'live_position', 3)) ?? num(dig(printer, 'toolhead', 'position', 3))
    if (['a', 'b', 'c'].includes(source)) {
        const i = { a: 3, b: 4, c: 5 }[source] as number
        const pose = dig(printer, 'motion_report', 'live_pose')
        if (Array.isArray(pose) && pose.some((v) => v)) return num(pose[i])
        return num(dig(printer, 'toolhead', 'extra_axes', source.toUpperCase())) ?? 0
    }
    const [obj, path] = source.split(':')
    return num(dig(printer, obj, ...(path ?? '').split('.').map((k) => (/^\d+$/.test(k) ? Number(k) : k))))
}

interface Part {
    cfg: ModelPartConfig
    meshes: AbstractMesh[]
    world: Matrix
    parent: Part | null
}

export class PrinterModel {
    scene: Scene
    config: ModelConfig | null = null
    parts: Part[] = []
    baseUrl: string
    loading: Promise<void> | null = null
    lastValues: ModelValues = {}

    constructor(scene: Scene, baseUrl: string) {
        this.scene = scene
        this.baseUrl = baseUrl.replace(/\/$/, '')
    }

    // file coordinates -> machine coordinates
    private fileToMachine(ext: string) {
        const c = this.config!
        const s = c.unit_scale
        // undo the loader's handedness change so we start from raw file values
        const loader = ext === 'glb' || ext === 'gltf' ? Matrix.Scaling(-1, 1, 1) : Matrix.Identity()
        const up =
            c.up_axis === 'y' ? Matrix.FromValues(1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1) : Matrix.Identity()
        return loader
            .multiply(up)
            .multiply(Matrix.Scaling(s, s, s))
            .multiply(translation(c.offset))
    }

    async load(config: ModelConfig) {
        this.dispose()
        this.config = config
        STLFileLoader.DO_NOT_ALTER_FILE_COORDINATES = true
        const all = [{ ...config.root, name: 'root', joint: 'fixed' as const }, ...config.parts]
        const byName: Record<string, Part> = {}
        for (const cfg of all) {
            const part: Part = { cfg, meshes: [], world: Matrix.Identity(), parent: null }
            part.parent = cfg.name === 'root' ? null : (byName[cfg.parent ?? 'root'] ?? null)
            byName[cfg.name] = part
            this.parts.push(part)
        }
        this.loading = Promise.all(this.parts.map((p) => this.loadPart(p))).then(() => {
            this.update(this.lastValues)
            // the gcodeviewer renders on demand; draw again once the new shaders are compiled
            this.scene.executeWhenReady(() => {
                if (!this.scene.isDisposed && this.scene.activeCamera) this.scene.render()
            })
        })
        await this.loading
    }

    private async loadPart(part: Part) {
        const file = part.cfg.file
        if (!file || part.cfg.visible === false) return
        const ext = (file.split('.').pop() ?? '').toLowerCase()
        const slash = file.lastIndexOf('/')
        const root = `${this.baseUrl}/${file.slice(0, slash + 1)}`
        const name = file.slice(slash + 1)
        let result
        try {
            result = await SceneLoader.ImportMeshAsync('', root, name, this.scene, null, '.' + ext)
        } catch (e) {
            window.console.warn(`model: could not load ${file}`, e)
            return
        }
        const toScene = this.fileToMachine(ext).multiply(Q)
        // parents first, so every world matrix includes the loader's root and node transforms
        const nodes = [...result.meshes, ...result.transformNodes]
        const depth = (n: { parent: unknown }) => {
            let d = 0
            for (let p = n.parent as { parent: unknown } | null; p; p = p.parent as { parent: unknown } | null) d++
            return d
        }
        nodes.sort((a, b) => depth(a) - depth(b)).forEach((n) => n.computeWorldMatrix(true))
        // config colours are #rrggbb or #rrggbbaa
        const hex = part.cfg.color?.replace('#', '') ?? ''
        const color = hex.length >= 6 ? Color3.FromHexString('#' + hex.slice(0, 6)) : null
        const colorAlpha = hex.length === 8 ? parseInt(hex.slice(6, 8), 16) / 255 : null
        for (const m of result.meshes) {
            if (!(m instanceof Mesh) || !m.getTotalVertices()) continue
            m.computeWorldMatrix(true)
            const w = m.getWorldMatrix().clone()
            m.setParent(null)
            m.position.setAll(0)
            m.rotationQuaternion = null
            m.rotation.setAll(0)
            m.scaling.setAll(1)
            m.bakeTransformIntoVertices(w.multiply(toScene))
            m.refreshBoundingInfo()
            const src = m.material as Partial<PBRMaterial> | null
            const pbr = src && src.albedoColor ? src : null
            const base = color ?? pbr?.albedoColor ?? new Color3(0.6, 0.6, 0.6)
            // plain materials like the rest of the gcodeviewer scene, which has no environment map
            const mat = new StandardMaterial(`model-${part.cfg.name}`, this.scene)
            mat.diffuseColor = base
            mat.emissiveColor = base.scale(0.35)
            mat.specularColor = (pbr?.metallic ?? 0) > 0.5 ? new Color3(0.5, 0.5, 0.5) : new Color3(0.12, 0.12, 0.12)
            mat.alpha = colorAlpha ?? pbr?.alpha ?? 1
            if (mat.alpha < 1) mat.backFaceCulling = false
            if (m.material && m.material !== mat) m.material.dispose()
            m.material = mat
            m.isPickable = false
            m.alwaysSelectAsActiveMesh = true
            part.meshes.push(m)
        }
        for (const m of result.meshes) if (!part.meshes.includes(m)) m.dispose()
    }

    // joint transform of one part in machine coordinates
    private local(cfg: ModelPartConfig, values: ModelValues): Matrix {
        if (!cfg.joint || cfg.joint === 'fixed' || !cfg.source) return Matrix.Identity()
        const raw = values[cfg.source]
        if (raw === undefined || raw === null || isNaN(raw)) return Matrix.Identity()
        let d = (raw - (cfg.home ?? 0)) * (cfg.scale ?? 1)
        if (cfg.minimum !== null && cfg.minimum !== undefined) d = Math.max(d, cfg.minimum)
        if (cfg.maximum !== null && cfg.maximum !== undefined) d = Math.min(d, cfg.maximum)
        const axis = cfg.axis ?? [0, 0, 1]
        if (cfg.joint === 'linear') return translation(axis.map((a) => a * d))
        const o = cfg.origin ?? [0, 0, 0]
        return translation(o.map((v) => -v))
            .multiply(rotation(axis, d))
            .multiply(translation(o))
    }

    sources(): string[] {
        return [...new Set(this.parts.map((p) => p.cfg.source).filter((s): s is string => !!s))]
    }

    update(values: ModelValues) {
        this.lastValues = values
        const Qi = Q.clone().invert()
        for (const part of this.parts) {
            const local = this.local(part.cfg, values)
            part.world = part.parent ? local.multiply(part.parent.world) : local
            const sceneMatrix = Qi.multiply(part.world).multiply(Q)
            for (const m of part.meshes) m.freezeWorldMatrix(sceneMatrix)
        }
    }

    // point the viewer's orbit camera at the whole machine
    fitCamera() {
        const camera = this.scene.activeCamera
        const meshes = this.meshes()
        if (!(camera instanceof ArcRotateCamera) || !meshes.length) return
        const min = new Vector3(Infinity, Infinity, Infinity)
        const max = new Vector3(-Infinity, -Infinity, -Infinity)
        for (const m of meshes) {
            const b = m.getBoundingInfo().boundingBox
            min.minimizeInPlace(b.minimumWorld)
            max.maximizeInPlace(b.maximumWorld)
        }
        camera.setTarget(Vector3.Center(min, max))
        camera.radius = Vector3.Distance(min, max) * 1.1
        camera.alpha = -Math.PI / 4
        camera.beta = Math.PI / 3
    }

    // meshes the gcodeviewer must not clip or dispose
    meshes(): AbstractMesh[] {
        return this.parts.flatMap((p) => p.meshes)
    }

    dispose() {
        for (const p of this.parts) for (const m of p.meshes) m.dispose(false, true)
        this.parts = []
    }
}
