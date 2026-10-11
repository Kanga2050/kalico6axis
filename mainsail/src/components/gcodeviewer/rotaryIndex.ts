// A, B and C through a G-code file, so the G-code viewer can pose the
// rotary axes of the printer model at any file position.

export interface RotaryStep {
    pos: number // file offset of the line that set the values
    abc: number[] // A, B, C after that line
}

const MOVE = /^[ \t]*G(0?[01]|9[012])(?![\d.])([^\n;]*)/gim
const WORD = /([ABC])\s*([-+]?(?:\d+\.?\d*|\.\d+))/gi

export function indexRotary(text: string): RotaryStep[] {
    const steps: RotaryStep[] = []
    const abc = [0, 0, 0]
    let absolute = true
    MOVE.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = MOVE.exec(text))) {
        const code = parseInt(m[1], 10)
        if (code === 90 || code === 91) {
            absolute = code === 90
            continue
        }
        if (!/[ABC]/i.test(m[2])) continue
        let changed = false
        for (const w of m[2].matchAll(WORD)) {
            const i = 'ABC'.indexOf(w[1].toUpperCase())
            const v = parseFloat(w[2])
            abc[i] = code === 92 || absolute ? v : abc[i] + v
            changed = true
        }
        if (changed) steps.push({ pos: m.index, abc: [...abc] })
    }
    return steps
}

// A, B, C after every line that starts before `pos`
export function rotaryAt(steps: RotaryStep[], pos: number): number[] {
    let lo = 0
    let hi = steps.length
    while (lo < hi) {
        const mid = (lo + hi) >> 1
        if (steps[mid].pos < pos) lo = mid + 1
        else hi = mid
    }
    return lo ? steps[lo - 1].abc : [0, 0, 0]
}
