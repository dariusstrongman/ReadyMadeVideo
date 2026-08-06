import { describe, expect, it } from 'vitest'
import { exportMeta, isExportJob } from '../pages/Project'

describe('isExportJob — only real export jobs count', () => {
  it('accepts a final_render editor render', () => {
    expect(isExportJob({ kind: 'final_render', params: { editor_document_id: 'd1' } })).toBe(true)
  })
  it('excludes analysis and autoedit jobs', () => {
    expect(isExportJob({ kind: 'analysis', params: {} })).toBe(false)
    expect(isExportJob({ kind: 'autoedit', params: {} })).toBe(false)
  })
  it('excludes a final_render with no editor document (non-customer)', () => {
    expect(isExportJob({ kind: 'final_render', params: {} })).toBe(false)
    expect(isExportJob({ kind: 'final_render' })).toBe(false)
  })
})

describe('exportMeta — real artifacts only, never fabricated', () => {
  it('formats resolution, duration, and size from artifacts', () => {
    expect(exportMeta({ width: 1080, height: 1920, duration: 8, size_bytes: 3 * 1048576 }))
      .toBe('1080×1920 · 8.0s · 3.00 MB')
  })
  it('omits missing metadata (no placeholders)', () => {
    expect(exportMeta({ width: 1080, height: 1920 })).toBe('1080×1920')
    expect(exportMeta({ duration: 12.4 })).toBe('12.4s')
    expect(exportMeta({})).toBe('')
    expect(exportMeta(undefined)).toBe('')
  })
  it('never invents a resolution when only one dimension is present', () => {
    expect(exportMeta({ width: 1080 })).toBe('')
  })
})


describe('exportMeta — queued jobs have null artifacts', () => {
  it('null artifacts render as empty meta, never a crash', () => {
    expect(exportMeta(null)).toBe('')
    expect(exportMeta(undefined)).toBe('')
    expect(exportMeta({})).toBe('')
  })
})
