import { runGcode } from './gcode.js'

self.onmessage = (ev) => {
  const { text, machine, id } = ev.data
  try {
    const r = runGcode(text, machine, (f) => self.postMessage({ id, progress: f }))
    self.postMessage({ id, result: r }, [r.segs.buffer, r.ext.buffer, r.extMeta.buffer, r.extSeg.buffer, r.fileOffsets.buffer])
  } catch (e) {
    self.postMessage({ id, error: String(e && e.stack || e) })
  }
}
