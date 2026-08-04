import { describe, expect, it } from 'vitest'
import { probeDuration } from './media'

const deps = (v) => ({ createVideo: () => v, createURL: () => 'blob:x', revokeURL: () => {} })

describe('probeDuration', () => {
  it('resolves the duration when metadata loads', async () => {
    const v = {}
    const p = probeDuration({}, deps(v))
    v.duration = 8.5
    v.onloadedmetadata()
    await expect(p).resolves.toBe(8.5)
  })

  it('resolves null on load error', async () => {
    const v = {}
    const p = probeDuration({}, deps(v))
    v.onerror()
    await expect(p).resolves.toBeNull()
  })

  it('resolves null for NaN duration', async () => {
    const v = {}
    const p = probeDuration({}, deps(v))
    v.duration = NaN
    v.onloadedmetadata()
    await expect(p).resolves.toBeNull()
  })

  it('resolves null for zero/negative duration', async () => {
    const v = {}
    const p = probeDuration({}, deps(v))
    v.duration = 0
    v.onloadedmetadata()
    await expect(p).resolves.toBeNull()
  })

  it('resolves null and never throws if object URL creation fails', async () => {
    const v = {}
    const p = probeDuration({}, { createVideo: () => v, createURL: () => { throw new Error('no URL') } })
    await expect(p).resolves.toBeNull()
  })
})
