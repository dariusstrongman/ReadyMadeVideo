// Client-side media metadata probing.
//
// probeDuration reads a file's duration via a hidden <video> element so it can be
// persisted into media_assets.duration_seconds at upload time (the Product Editor
// needs per-asset duration bounds). Dependencies are injectable so the resolve /
// error / invalid-duration branches are unit-testable without a real video decode.
export function probeDuration(file, {
  createVideo = () => document.createElement('video'),
  createURL = (f) => URL.createObjectURL(f),
  revokeURL = (u) => URL.revokeObjectURL(u),
  timeoutMs = 15000,
} = {}) {
  return new Promise((resolve) => {
    const v = createVideo()
    let settled = false
    let url = null
    let timer = null
    const done = (val) => {
      if (settled) return
      settled = true
      if (timer) clearTimeout(timer)
      if (url) { try { revokeURL(url) } catch { /* ignore */ } }
      resolve(val)
    }
    v.preload = 'metadata'
    v.onloadedmetadata = () => {
      const d = Number(v.duration)
      done(Number.isFinite(d) && d > 0 ? d : null)  // null when unknown/invalid
    }
    v.onerror = () => done(null)
    timer = setTimeout(() => done(null), timeoutMs)   // never hang the upload
    try {
      url = createURL(file)
      v.src = url
    } catch {
      done(null)
    }
  })
}
