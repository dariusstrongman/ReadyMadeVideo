// The selection contract: what the UI sends must be exactly what the server
// validates. These are the pure-translation guarantees.
import { describe, expect, it } from 'vitest'
import {
  buildCustomSelection, customizationBounds, packageProgressLine,
  selectionForPackage,
} from './outputIntelligence'

const LONG = {
  format: 'long_form', opportunityId: 'long-1',
  recommendedDurationS: 300, feasibleDurationS: [60, 600],
  recommendedAspect: '16:9',
}
const SHORT = (n) => ({
  format: 'short_form', opportunityId: `short-${n}`,
  recommendedDurationS: 45, feasibleDurationS: [8, 60],
  recommendedAspect: '9:16',
})

describe('selectionForPackage', () => {
  it('keeps each long-form as its own item and collapses shorts to a quantity', () => {
    const sel = selectionForPackage({
      deliverables: [LONG, SHORT(1), SHORT(2), SHORT(3)] })
    expect(sel).toEqual([
      { kind: 'long_form', opportunityId: 'long-1' },
      { kind: 'short_form', quantity: 3 },
    ])
  })

  it('multi-story packages carry every long id — independent stories stay separate', () => {
    const sel = selectionForPackage({
      deliverables: [LONG, { ...LONG, opportunityId: 'long-2' }] })
    expect(sel.map(i => i.opportunityId)).toEqual(['long-1', 'long-2'])
  })
})

describe('customizationBounds', () => {
  it('reads the honest limits off the recommendation, never inflates', () => {
    const b = customizationBounds({ packages: [
      { deliverables: [LONG, SHORT(1), SHORT(2)] },
      { deliverables: [SHORT(1), SHORT(2), SHORT(3)] },   // overlap dedupes by id
    ] })
    expect(b.maxShorts).toBe(3)
    expect(b.hasLongForm).toBe(true)
    expect(b.longRange).toEqual({ lo: 60, hi: 600, recommended: 300 })
  })

  it('no long-form opportunity => customization cannot request one', () => {
    const b = customizationBounds({ packages: [
      { deliverables: [SHORT(1)] }] })
    expect(b.hasLongForm).toBe(false)
    const sel = buildCustomSelection({
      wantLong: true, longDurationS: 300, shortCount: 1, bounds: b })
    expect(sel).toEqual([{ kind: 'short_form', quantity: 1 }])
  })
})

describe('packageProgressLine', () => {
  it('is honest about partial completion', () => {
    const line = packageProgressLine([
      { status: 'ready' }, { status: 'ready' }, { status: 'failed' }])
    expect(line).toContain('2 of 3')
    expect(line).toContain('attention')
  })
  it('claims completion only when every child is ready', () => {
    expect(packageProgressLine([{ status: 'ready' }, { status: 'ready' }]))
      .toBe('All 2 videos ready')
  })
})
