import { describe, expect, it } from 'vitest'
import { indexRotary, rotaryAt } from '@/components/gcodeviewer/rotaryIndex'
import { poseToMachine, readSource } from '@/components/gcodeviewer/printerModel'

// pivots of this printer's trunnion, as corexy_ac reports them
const printer = {
    toolhead: {
        rotary_axes: {
            a: { axis: 'x', pivot: [175, 5] },
            c: { axis: 'z', pivot: [175, 175] },
        },
    },
    motion_report: {
        live_position: [100, 100, 10, 0],
        live_machine_position: [120, 90, 30],
        live_rotary_position: { a: 20, c: 45 },
        live_pose: [100, 100, 10, 19, 0, 44],
        live_steppers: { stepper_z1: 4.5 },
    },
}

// Kalico's CoreXYACKinematics._to_machine, for comparison
function kalicoToMachine(pose: number[]) {
    const [cx, cy, ay, az] = [175, 175, 175, 5]
    const r = Math.PI / 180
    const [sa, ca, sc, cc] = [Math.sin(pose[3] * r), Math.cos(pose[3] * r), Math.sin(pose[5] * r), Math.cos(pose[5] * r)]
    const dx = pose[0] - cx
    const dy = pose[1] - cy
    const qy = cy + dx * sc + dy * cc - ay
    const qz = pose[2] - az
    return [cx + dx * cc - dy * sc, ay + qy * ca - qz * sa, az + qy * sa + qz * ca]
}

describe('poseToMachine', () => {
    it('matches the Kalico kinematics', () => {
        for (const pose of [
            [100, 120, 10, 30, 0, 0],
            [200, 150, 40, 0, 0, 90],
            [50, 300, 2, -45, 0, 130],
            [175, 175, 5, 60, 0, -20],
        ]) {
            const ours = poseToMachine(pose, printer)
            kalicoToMachine(pose).forEach((v, i) => expect(ours[i]).toBeCloseTo(v, 9))
        }
    })

    it('is the identity without rotary axes', () => {
        expect(poseToMachine([1, 2, 3, 40, 0, 50], {})).toEqual([1, 2, 3])
    })
})

describe('rotary sources', () => {
    it('reads motor positions live', () => {
        expect(readSource('machine_x', printer)).toBe(120)
        expect(readSource('x', printer)).toBe(100)
        expect(readSource('a', printer)).toBe(20)
        expect(readSource('c', printer)).toBe(45)
        expect(readSource('motion_report:live_steppers.stepper_z1', printer)).toBe(4.5)
    })

    it('turns a simulated G-code pose into machine values', () => {
        const pose = [100, 120, 10, 30, 0, 60]
        const m = kalicoToMachine(pose)
        expect(readSource('machine_y', printer, pose)).toBeCloseTo(m[1], 9)
        expect(readSource('y', printer, pose)).toBe(120)
        expect(readSource('a', printer, pose)).toBe(30)
        expect(readSource('c', printer, pose)).toBe(60)
    })
})

describe('indexRotary', () => {
    const text = [
        'G28',
        'G1 X10 Y10 A15 F600', // 0: a=15
        'G1 X20 ; A99 in a comment',
        'G1 C90',
        'G91',
        'G1 A-5 C10',
        'G90',
        'G92 C0',
        'G10',
        'G1 B.5',
    ].join('\n')
    const steps = indexRotary(text)
    const at = (line: number) => text.split('\n').slice(0, line).join('\n').length + (line ? 1 : 0)

    it('tracks absolute, relative and G92 values', () => {
        expect(steps.map((s) => s.abc)).toEqual([
            [15, 0, 0],
            [15, 0, 90],
            [10, 0, 100],
            [10, 0, 0],
            [10, 0.5, 0],
        ])
    })

    it('looks up the values before a line', () => {
        expect(rotaryAt(steps, at(1))).toEqual([0, 0, 0])
        expect(rotaryAt(steps, at(1) + 1)).toEqual([15, 0, 0])
        expect(rotaryAt(steps, at(5))).toEqual([15, 0, 90])
        expect(rotaryAt(steps, Infinity)).toEqual([10, 0.5, 0])
    })
})
