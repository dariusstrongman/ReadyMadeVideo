import { describe, expect, it, vi } from 'vitest'
import { MAX_UPLOAD_BYTES, UPLOAD_LIMIT_TEXT } from './config'
import { MultipartUpload, UploadError, planParts, validateFile } from './s3upload'

const TWO_GB = 2 * 1024 * 1024 * 1024
const MB = 1024 * 1024

function fakeFile({ name = 'clip.mp4', type = 'video/mp4', size, lastModified = 1 }) {
  return { name, type, size, lastModified, slice: (s, e) => ({ size: e - s }) }
}

function makeTransport(overrides = {}) {
  const t = {
    _signCalls: 0, _putCalls: 0, _completeCalled: false, _finalizeCalled: false, _abortCalled: false,
    initiate: vi.fn(async () => ({ sessionId: 's1', assetId: 'a1', partSize: 5 * MB })),
    signParts: async ({ partNumbers }) => {
      t._signCalls += 1
      return { parts: partNumbers.map((n) => ({ partNumber: n, url: `https://s3.fake/${n}` })) }
    },
    putPart: async ({ blob, onProgress }) => {
      t._putCalls += 1
      if (onProgress) onProgress(blob.size)
      return `etag-${t._putCalls}`
    },
    complete: async () => { t._completeCalled = true; return {} },
    finalize: async () => { t._finalizeCalled = true; return { id: 'asset-1', storage_provider: 's3' } },
    abort: async () => { t._abortCalled = true; return {} },
  }
  return Object.assign(t, overrides)
}

describe('shared limit + copy', () => {
  it('MAX_UPLOAD_BYTES is exactly 2 GiB', () => {
    expect(MAX_UPLOAD_BYTES).toBe(TWO_GB)
    expect(MAX_UPLOAD_BYTES).toBe(2 * 1024 * 1024 * 1024)
  })
  it('the visible copy says 2 GB', () => {
    expect(UPLOAD_LIMIT_TEXT).toContain('2 GB')
    expect(UPLOAD_LIMIT_TEXT).toContain('MP4')
    expect(UPLOAD_LIMIT_TEXT).toContain('MOV')
  })
  it('no 50 MB limit survives in the config', () => {
    expect(UPLOAD_LIMIT_TEXT).not.toContain('50 MB')
    expect(MAX_UPLOAD_BYTES).not.toBe(50 * 1024 * 1024)
  })
})

describe('validateFile (before any network call)', () => {
  it('accepts just below 2 GB', () => {
    expect(() => validateFile(fakeFile({ size: TWO_GB - 1 }))).not.toThrow()
  })
  it('accepts exactly 2 GB', () => {
    expect(() => validateFile(fakeFile({ size: TWO_GB }))).not.toThrow()
  })
  it('rejects over 2 GB', () => {
    expect(() => validateFile(fakeFile({ size: TWO_GB + 1 }))).toThrow(UploadError)
    try { validateFile(fakeFile({ size: TWO_GB + 1 })) } catch (e) { expect(e.code).toBe('too_large') }
  })
  it('rejects a non-video type', () => {
    expect(() => validateFile(fakeFile({ name: 'x.txt', type: 'text/plain', size: 10 }))).toThrow(/MP4 or MOV/)
  })
  it('accepts .mov by extension', () => {
    expect(() => validateFile(fakeFile({ name: 'x.mov', type: 'video/quicktime', size: 10 }))).not.toThrow()
  })
})

describe('planParts', () => {
  it('splits into >=5 MiB parts', () => {
    expect(planParts(12 * MB, 5 * MB)).toEqual({ partSize: 5 * MB, partCount: 3 })
  })
  it('raises tiny part sizes to the 5 MiB S3 minimum', () => {
    expect(planParts(10 * MB, 1 * MB).partSize).toBe(5 * MB)
  })
})

describe('MultipartUpload', () => {
  it('uploads all parts then completes and finalizes', async () => {
    const t = makeTransport()
    const events = []
    const up = new MultipartUpload({
      file: fakeFile({ size: 12 * MB }), projectId: 'p', transport: t,
      retryBaseMs: 1, onProgress: (p) => events.push(p),
    })
    const asset = await up.start()
    expect(asset).toEqual({ id: 'asset-1', storage_provider: 's3' })
    expect(t._putCalls).toBe(3)                 // 12 MiB / 5 MiB -> 3 parts
    expect(t._completeCalled).toBe(true)
    expect(t._finalizeCalled).toBe(true)
    expect(events.at(-1).percent).toBe(100)
    expect(events.at(-1).totalBytes).toBe(12 * MB)
  })

  it('retries a transient part failure then succeeds', async () => {
    let attempts = 0
    const t = makeTransport({
      putPart: async ({ blob, onProgress }) => {
        attempts += 1
        if (attempts === 1) throw new UploadError('network', 'boom')
        if (onProgress) onProgress(blob.size)
        return `etag-${attempts}`
      },
    })
    const up = new MultipartUpload({
      file: fakeFile({ size: 4 * MB }), projectId: 'p', transport: t, retryBaseMs: 1,
    })
    const asset = await up.start()
    expect(asset.id).toBe('asset-1')
    expect(attempts).toBe(2)                     // single part: failed once, retried, succeeded
  })

  it('cancel aborts the multipart and rejects', async () => {
    let up
    const t = makeTransport({
      putPart: async () => { up.cancel(); throw new UploadError('aborted', 'stopped') },
    })
    up = new MultipartUpload({
      file: fakeFile({ size: 6 * MB }), projectId: 'p', transport: t, retryBaseMs: 1,
    })
    await expect(up.start()).rejects.toThrow(/cancelled/)
    expect(t._abortCalled).toBe(true)
  })

  it('resumes: already-uploaded parts are not re-sent', async () => {
    const store = (() => { const m = new Map(); return {
      get: (k) => m.get(k) || null, set: (k, v) => m.set(k, v), remove: (k) => m.delete(k) } })()
    const file = fakeFile({ size: 12 * MB })
    // seed a resume record as if parts 1 and 2 already uploaded
    const key = `stromation_upload_p_${file.name}_${file.size}_${file.lastModified}`
    store.set(key, { sessionId: 's1', assetId: 'a1', partSize: 5 * MB,
                     parts: [{ partNumber: 1, etag: 'e1' }, { partNumber: 2, etag: 'e2' }] })
    const t = makeTransport()
    const up = new MultipartUpload({
      file, projectId: 'p', transport: t, retryBaseMs: 1, resumeStore: store,
    })
    await up.start()
    expect(t.initiate).not.toHaveBeenCalled()    // resumed, no re-initiate
    expect(t._putCalls).toBe(1)                  // only the final missing part
  })
})
