// Keyboard movement and fullscreen for the 3D views.
//
// W A S D move the view across the scene, Space up and Shift down, while the
// canvas has focus (click it first). Speed follows the zoom: a quarter of the
// camera's distance from the target per second, between 5 and 150 mm/s.
import { Scene } from '@babylonjs/core/scene'
import { ArcRotateCamera } from '@babylonjs/core/Cameras/arcRotateCamera'
import { Vector3 } from '@babylonjs/core/Maths/math.vector'

const KEYS: Record<string, string> = {
    KeyW: 'w',
    KeyA: 'a',
    KeyS: 's',
    KeyD: 'd',
    Space: 'up',
    ShiftLeft: 'down',
    ShiftRight: 'down',
}
const hooked = new WeakSet<HTMLCanvasElement>()

export function addKeyboardMove(scene: Scene) {
    const canvas = scene.getEngine().getRenderingCanvas()
    if (!canvas || hooked.has(canvas)) return
    hooked.add(canvas)
    canvas.tabIndex = 0
    canvas.style.outline = 'none'

    const held = new Set<string>()
    let frame = 0
    let last = 0

    const step = (time: number) => {
        const camera = scene.activeCamera
        if (!held.size || scene.isDisposed || !(camera instanceof ArcRotateCamera)) {
            frame = 0
            return
        }
        const dt = last ? Math.min((time - last) / 1000, 0.1) : 0
        last = time
        const forward = camera.target.subtract(camera.position)
        forward.y = 0
        forward.normalize()
        const right = new Vector3(forward.z, 0, -forward.x)
        const move = Vector3.Zero()
        if (held.has('w')) move.addInPlace(forward)
        if (held.has('s')) move.subtractInPlace(forward)
        if (held.has('d')) move.addInPlace(right)
        if (held.has('a')) move.subtractInPlace(right)
        if (held.has('up')) move.y += 1
        if (held.has('down')) move.y -= 1
        if (move.lengthSquared() > 0) {
            // keep the angles and distance, so the whole view moves
            const speed = Math.min(150, Math.max(5, camera.radius * 0.25))
            camera.setTarget(camera.target.add(move.normalize().scale(speed * dt)), false, false, true)
            scene.render()
        }
        frame = requestAnimationFrame(step)
    }

    canvas.addEventListener('pointerdown', () => canvas.focus())
    canvas.addEventListener('keydown', (e) => {
        const key = KEYS[e.code]
        if (!key) return
        e.preventDefault()
        held.add(key)
        if (!frame) {
            last = 0
            frame = requestAnimationFrame(step)
        }
    })
    canvas.addEventListener('keyup', (e) => held.delete(KEYS[e.code]))
    canvas.addEventListener('blur', () => held.clear())
}

// show a 3D view's container fullscreen; Esc leaves
export function fullscreen(el: HTMLElement) {
    el.requestFullscreen().catch(() => {})
}
