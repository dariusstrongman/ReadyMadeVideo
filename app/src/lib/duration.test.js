import { describe, expect, it } from 'vitest'
import { DURATION_BANDS, availableDurationBands } from './duration'

describe('footage-aware duration bands', () => {
  it('24 minutes of raw footage cannot request a 20+ minute "edit"', () => {
    const bands = availableDurationBands(24 * 60)
    expect(bands.map((b) => b.value)).not.toContain('1200-3600')
    expect(bands.map((b) => b.value)).toContain('600-1200')  // 10 min from 24: real edit
  })

  it('two minutes of clips offers only short-form', () => {
    const bands = availableDurationBands(120)
    expect(bands.map((b) => b.value)).toEqual(['10-60', '60-180'])
  })

  it('an hour of footage unlocks everything', () => {
    expect(availableDurationBands(3600)).toEqual(DURATION_BANDS)
  })

  it('unknown durations offer everything rather than guessing', () => {
    expect(availableDurationBands(0)).toEqual(DURATION_BANDS)
    expect(availableDurationBands(NaN)).toEqual(DURATION_BANDS)
  })
})
