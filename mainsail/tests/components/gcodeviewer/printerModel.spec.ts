import { describe, expect, it } from 'vitest'
import { NullEngine } from '@babylonjs/core/Engines/nullEngine'
import { Scene } from '@babylonjs/core/scene'
import { Vector3 } from '@babylonjs/core/Maths/math.vector'
import { ModelConfig, PrinterModel, readSource } from '@/components/gcodeviewer/printerModel'

const printer = {
    motion_report: {
        live_position: [10, 20, 30, 4],
        live_pose: [10, 20, 30, 15, 0, 90],
        live_machine_position: [11, 21, 31],
    },
    toolhead: { position: [1, 2, 3, 0], extra_axes: { A: 5, C: 6 } },
    'manual_stepper tilt': { position: 12.5 },
}

describe('readSource', () => {
    it('reads live axes, machine position and rotary axes', () => {
        expect(readSource('x', printer)).toBe(10)
        expect(readSource('z', printer)).toBe(30)
        expect(readSource('e', printer)).toBe(4)
        expect(readSource('machine_y', printer)).toBe(21)
        expect(readSource('a', printer)).toBe(15)
        expect(readSource('c', printer)).toBe(90)
    })

    it('falls back to toolhead values', () => {
        const p = { toolhead: printer.toolhead }
        expect(readSource('y', p)).toBe(2)
        expect(readSource('machine_x', p)).toBe(1)
        expect(readSource('a', p)).toBe(5)
    })

    it('uses the simulated position for x y z when given', () => {
        expect(readSource('x', printer, [100, 200, 300])).toBe(100)
        expect(readSource('machine_z', printer, [100, 200, 300])).toBe(300)
    })

    it('reads any printer object field', () => {
        expect(readSource('manual_stepper tilt:position', printer)).toBe(12.5)
        expect(readSource('motion_report:live_pose.3', printer)).toBe(15)
        expect(readSource('missing:thing', printer)).toBeNull()
    })
})

function model(parts: ModelConfig['parts']) {
    const scene = new Scene(new NullEngine())
    const m = new PrinterModel(scene, 'http://test')
    m.config = { up_axis: 'z', unit_scale: 1, offset: [0, 0, 0], root: { name: 'root', file: null }, parts }
    return m
}

// the machine-frame position of a part-local point after update()
function place(m: PrinterModel, name: string, p: number[]) {
    const part = m.parts.find((x) => x.cfg.name === name)!
    return Vector3.TransformCoordinates(new Vector3(p[0], p[1], p[2]), part.world)
        .asArray()
        .map((v) => +v.toFixed(3) + 0)
}

describe('joints', () => {
    const parts: ModelConfig['parts'] = [
        {
            name: 'gantry',
            file: null,
            parent: 'root',
            joint: 'linear',
            axis: [0, 0, 1],
            source: 'z',
            home: 3,
            scale: 1,
        },
        {
            name: 'beam',
            file: null,
            parent: 'gantry',
            joint: 'linear',
            axis: [0, 1, 0],
            source: 'y',
            home: 100,
            scale: 1,
        },
        {
            name: 'tilt',
            file: null,
            parent: 'root',
            joint: 'rotary',
            axis: [1, 0, 0],
            origin: [0, 175, 5],
            source: 'a',
            home: 0,
            scale: 1,
            maximum: 60,
        },
    ]

    async function loaded() {
        const m = model(parts)
        await m.load(m.config!)
        return m
    }

    it('chains linear joints through parents', async () => {
        const m = await loaded()
        m.update({ z: 13, y: 150 })
        expect(place(m, 'gantry', [0, 0, 0])).toEqual([0, 0, 10])
        expect(place(m, 'beam', [0, 0, 0])).toEqual([0, 50, 10])
    })

    it('rotates right-handed about an offset axis', async () => {
        const m = await loaded()
        m.update({ a: 30 })
        // a point 10 mm along +Y from the pivot swings up toward +Z
        expect(place(m, 'tilt', [0, 185, 5])).toEqual([0, +(175 + 10 * Math.cos(Math.PI / 6)).toFixed(3), 10])
    })

    it('clamps to the joint limit', async () => {
        const m = await loaded()
        m.update({ a: 60 })
        const atLimit = place(m, 'tilt', [0, 185, 5])
        m.update({ a: 120 })
        expect(place(m, 'tilt', [0, 185, 5])).toEqual(atLimit)
    })

    it('leaves parts in place when a source is missing', async () => {
        const m = await loaded()
        m.update({})
        expect(place(m, 'beam', [1, 2, 3])).toEqual([1, 2, 3])
    })
})
