// Direct browser -> S3 multipart upload for customer raw footage.
//
// The file body streams to S3 through short-lived presigned PUT URLs; only small
// JSON control calls (initiate / sign-parts / complete / finalize / abort) go to
// the FastAPI backend. Parts are read via File.slice() so the whole file is never
// buffered in memory. Progress, speed, ETA, per-part retry, pause/cancel, and
// best-effort resume-after-interruption are all handled by MultipartUpload.
import { MAX_UPLOAD_BYTES, RENDER_API, UPLOAD_PART_SIZE } from './config'

export { MAX_UPLOAD_BYTES, UPLOAD_PART_SIZE }

const S3_MIN_PART_SIZE = 5 * 1024 * 1024
const S3_MAX_PARTS = 10000
const ACCEPTED_TYPES = ['video/mp4', 'video/quicktime']

export class UploadError extends Error {
  constructor(code, message) { super(message); this.name = 'UploadError'; this.code = code }
}

/** Reject bad files BEFORE any network call. */
export function validateFile(file, { maxBytes = MAX_UPLOAD_BYTES } = {}) {
  const typeOk = ACCEPTED_TYPES.includes(file.type) || /\.(mp4|mov)$/i.test(file.name)
  if (!typeOk) throw new UploadError('unsupported_type', 'Please choose an MP4 or MOV file.')
  if (!file.size || file.size < 1) throw new UploadError('empty', 'That file is empty.')
  if (file.size > maxBytes)
    throw new UploadError('too_large',
      `File is ${(file.size / 1073741824).toFixed(2)} GB — the limit is 2 GB.`)
}

/** Choose a part size satisfying S3 multipart constraints. */
export function planParts(size, partSize = UPLOAD_PART_SIZE) {
  let ps = Math.max(Number(partSize) || UPLOAD_PART_SIZE, S3_MIN_PART_SIZE)
  let count = Math.max(1, Math.ceil(size / ps))
  if (count > S3_MAX_PARTS) { ps = Math.ceil(size / S3_MAX_PARTS); count = Math.ceil(size / ps) }
  return { partSize: ps, partCount: count }
}

/** Real transport over the authenticated FastAPI endpoints + S3 presigned PUTs. */
export function createRealTransport({ renderApi = RENDER_API, accessToken } = {}) {
  const post = async (path, body) => {
    const r = await fetch(`${renderApi}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` },
      body: JSON.stringify(body || {}),
    })
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}))
      const msg = detail?.detail?.message || detail?.detail || `request failed (${r.status})`
      throw new UploadError('server', typeof msg === 'string' ? msg : `request failed (${r.status})`)
    }
    return r.json()
  }
  return {
    initiate: ({ projectId, filename, contentType, size }) =>
      post(`/projects/${projectId}/raw-uploads/initiate`, { filename, contentType, size }),
    signParts: ({ projectId, sessionId, partNumbers }) =>
      post(`/projects/${projectId}/raw-uploads/${sessionId}/sign-parts`, { partNumbers }),
    complete: ({ projectId, sessionId, parts }) =>
      post(`/projects/${projectId}/raw-uploads/${sessionId}/complete`, { parts }),
    finalize: ({ projectId, sessionId }) =>
      post(`/projects/${projectId}/raw-uploads/${sessionId}/finalize`, {}),
    abort: ({ projectId, sessionId }) =>
      post(`/projects/${projectId}/raw-uploads/${sessionId}/abort`, {}).catch(() => {}),
    // PUT one part directly to S3; resolve with the part's ETag.
    putPart: ({ url, blob, signal, onProgress }) => new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('PUT', url)
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded) }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          const etag = xhr.getResponseHeader('ETag') || xhr.getResponseHeader('Etag')
          if (!etag) return reject(new UploadError('no_etag', 'S3 did not return an ETag (check CORS ExposeHeaders).'))
          resolve(etag.replace(/"/g, ''))
        } else reject(new UploadError('part_failed', `part upload failed (${xhr.status})`))
      }
      xhr.onerror = () => reject(new UploadError('network', 'network error during part upload'))
      xhr.onabort = () => reject(new UploadError('aborted', 'upload aborted'))
      if (signal) signal.addEventListener('abort', () => xhr.abort(), { once: true })
      xhr.send(blob)
    }),
  }
}

const memoryStore = () => {
  const m = new Map()
  return { get: (k) => m.get(k) || null, set: (k, v) => m.set(k, v), remove: (k) => m.delete(k) }
}
function localStore() {
  try {
    if (typeof localStorage === 'undefined') return memoryStore()
    return {
      get: (k) => { const v = localStorage.getItem(k); return v ? JSON.parse(v) : null },
      set: (k, v) => localStorage.setItem(k, JSON.stringify(v)),
      remove: (k) => localStorage.removeItem(k),
    }
  } catch { return memoryStore() }
}

const fingerprint = (projectId, file) =>
  `stromation_upload_${projectId}_${file.name}_${file.size}_${file.lastModified || 0}`

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

export class MultipartUpload {
  constructor({ file, projectId, transport, onProgress, onState,
                partSize = UPLOAD_PART_SIZE, maxRetries = 4, retryBaseMs = 1000,
                resumeStore } = {}) {
    this.file = file
    this.projectId = projectId
    this.transport = transport
    this.onProgress = onProgress || (() => {})
    this.onState = onState || (() => {})
    this.partSize = partSize
    this.maxRetries = maxRetries
    this.retryBaseMs = retryBaseMs
    this.store = resumeStore || localStore()
    this.state = 'idle'
    this._paused = false
    this._cancelled = false
    this._controller = null
    this._uploaded = new Map()   // partNumber -> etag
    this._committed = 0          // bytes from fully-completed parts
    this._bytesDone = 0          // committed + in-flight part progress
    this._startedAt = 0
  }

  _setState(s) { this.state = s; this.onState(s) }

  _emit() {
    const total = this.file.size
    const elapsed = (Date.now() - this._startedAt) / 1000 || 0.001
    const speed = this._bytesDone / elapsed
    const remaining = Math.max(0, total - this._bytesDone)
    this.onProgress({
      bytesUploaded: this._bytesDone, totalBytes: total,
      percent: total ? Math.round((this._bytesDone / total) * 100) : 0,
      speedBps: speed, etaSeconds: speed > 0 ? remaining / speed : null,
      state: this.state,
    })
  }

  pause() { if (this.state === 'uploading') { this._paused = true; this._setState('paused') } }
  resume() { if (this.state === 'paused') { this._paused = false; this._setState('uploading') } }
  cancel() {
    this._cancelled = true
    if (this._controller) this._controller.abort()
  }

  async _waitIfPaused() {
    while (this._paused && !this._cancelled) await sleep(150)
    if (this._cancelled) throw new UploadError('cancelled', 'upload cancelled')
  }

  async start() {
    validateFile(this.file)
    this._startedAt = Date.now()
    this._setState('uploading')
    const key = fingerprint(this.projectId, this.file)
    let saved = this.store.get(key)

    let session
    if (saved?.sessionId) {
      session = saved                       // resume-after-interruption
      for (const p of saved.parts || []) { this._uploaded.set(p.partNumber, p.etag) }
    } else {
      const init = await this.transport.initiate({
        projectId: this.projectId, filename: this.file.name,
        contentType: this.file.type || 'video/mp4', size: this.file.size,
      })
      session = { sessionId: init.sessionId, assetId: init.assetId,
                  partSize: init.partSize || this.partSize, parts: [] }
      this.store.set(key, session)
    }

    const { partSize, partCount } = planParts(this.file.size, session.partSize)
    // committed bytes already done from resumed parts
    this._committed = [...this._uploaded.keys()]
      .reduce((sum, n) => sum + Math.min(partSize, this.file.size - (n - 1) * partSize), 0)
    this._bytesDone = this._committed
    this._emit()

    try {
      for (let n = 1; n <= partCount; n++) {
        if (this._cancelled) throw new UploadError('cancelled', 'upload cancelled')
        await this._waitIfPaused()
        if (this._uploaded.has(n)) continue        // already uploaded (resume)
        const start = (n - 1) * partSize
        const end = Math.min(start + partSize, this.file.size)
        const blob = this.file.slice(start, end)   // NOT read into memory here
        const etag = await this._putWithRetry(session.sessionId, n, blob, end - start)
        this._uploaded.set(n, etag)
        session.parts = [...this._uploaded.entries()].map(([partNumber, e]) => ({ partNumber, etag: e }))
        this.store.set(key, session)
        this._emit()
      }

      const parts = [...this._uploaded.entries()]
        .map(([partNumber, etag]) => ({ partNumber, etag }))
        .sort((a, b) => a.partNumber - b.partNumber)
      this._setState('completing')
      await this.transport.complete({ projectId: this.projectId, sessionId: session.sessionId, parts })
      this._setState('finalizing')
      const asset = await this.transport.finalize({ projectId: this.projectId, sessionId: session.sessionId })
      this.store.remove(key)
      this._setState('completed')
      return asset
    } catch (err) {
      if (this._cancelled || err.code === 'cancelled') {
        await this.transport.abort({ projectId: this.projectId, sessionId: session.sessionId })
        this.store.remove(key)
        this._setState('cancelled')
        throw new UploadError('cancelled', 'upload cancelled')
      }
      this._setState('failed')
      throw err
    }
  }

  async _putWithRetry(sessionId, partNumber, blob, byteLen) {
    let attempt = 0
    let lastErr
    while (attempt <= this.maxRetries) {
      if (this._cancelled) throw new UploadError('cancelled', 'upload cancelled')
      await this._waitIfPaused()
      try {
        const { parts } = await this.transport.signParts({
          projectId: this.projectId, sessionId, partNumbers: [partNumber],
        })
        const url = parts[0].url
        this._controller = new AbortController()
        const etag = await this.transport.putPart({
          url, blob, signal: this._controller.signal,
          onProgress: (loaded) => { this._bytesDone = this._committed + loaded; this._emit() },
        })
        this._committed += byteLen                 // part fully committed
        this._bytesDone = this._committed
        return etag
      } catch (err) {
        if (this._cancelled || err.code === 'cancelled' || err.code === 'aborted') throw err
        lastErr = err
        attempt += 1
        if (attempt > this.maxRetries) break
        await sleep(Math.min(this.retryBaseMs * 2 ** (attempt - 1), 8000))  // backoff
      }
    }
    throw lastErr || new UploadError('part_failed', `part ${partNumber} failed after retries`)
  }
}
