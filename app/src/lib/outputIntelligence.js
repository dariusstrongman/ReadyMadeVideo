// Output Intelligence — pure helpers for the recommendation/package UI.
// Kept free of React and network so the selection contract is unit-testable:
// what the user clicks must translate into exactly the server's schema.

// Build the POST /output-packages selection for accepting a stored package.
// Long-form deliverables keep their opportunityId (independent stories are
// separate items); shorts collapse into one quantity item — the server maps
// quantity onto its own top-ranked moments, so ids are not repeated here.
export function selectionForPackage(pkg) {
  const items = []
  for (const d of pkg.deliverables || []) {
    if (d.format === 'long_form') {
      items.push({ kind: 'long_form', opportunityId: d.opportunityId })
    }
  }
  const shorts = (pkg.deliverables || []).filter(d => d.format === 'short_form')
  if (shorts.length > 0) {
    items.push({ kind: 'short_form', quantity: shorts.length })
  }
  return items
}

// Bounds a customized selection can honestly offer, read off the stored
// recommendation (server re-validates; these only shape the controls).
export function customizationBounds(recommendation) {
  const pkgs = recommendation?.packages || []
  const all = pkgs.flatMap(p => p.deliverables || [])
  const shorts = new Map()
  for (const d of all) {
    if (d.format === 'short_form') shorts.set(d.opportunityId, d)
  }
  const longs = new Map()
  for (const d of all) {
    if (d.format === 'long_form') longs.set(d.opportunityId, d)
  }
  const long = longs.values().next().value || null
  return {
    maxShorts: shorts.size,
    hasLongForm: longs.size > 0,
    longCount: longs.size,
    longRange: long ? {
      lo: Math.round(long.feasibleDurationS[0]),
      hi: Math.round(long.feasibleDurationS[1]),
      recommended: Math.round(long.recommendedDurationS),
    } : null,
  }
}

export function buildCustomSelection({ wantLong, longDurationS, shortCount, bounds }) {
  const items = []
  if (wantLong && bounds.hasLongForm) {
    const item = { kind: 'long_form' }
    if (longDurationS) item.durationTargetS = Number(longDurationS)
    items.push(item)
  }
  if (shortCount > 0) {
    items.push({ kind: 'short_form', quantity: Number(shortCount) })
  }
  return items
}

export function fmtDuration(s) {
  const n = Math.round(Number(s) || 0)
  if (n < 60) return `${n}s`
  const m = Math.floor(n / 60)
  const r = n % 60
  return r ? `${m}m ${r}s` : `${m} min`
}

// One line per deliverable card: honest, no model prose.
export function describeDeliverable(d) {
  const [lo, hi] = d.feasibleDurationS || [0, 0]
  const dur = d.format === 'short_form'
    ? `${Math.round(lo)}–${Math.round(hi)}s`
    : `${fmtDuration(d.recommendedDurationS)} (${fmtDuration(lo)}–${fmtDuration(hi)})`
  const shape = d.recommendedAspect === '9:16' ? 'vertical' : 'widescreen'
  return { title: d.format === 'long_form' ? 'Full video' : 'Short', dur, shape }
}

export const CHILD_STATUS_LABEL = {
  queued: 'Waiting',
  planning: 'Planning the edit',
  editing: 'Building the cut',
  ready: 'Ready',
  failed: 'Failed',
  cancelled: 'Cancelled',
  budget_blocked: 'Paused — budget limit',
}

// Derived summary line for a package (mirrors the server's derivation —
// display only; the server's packageStatus is authoritative).
export function packageProgressLine(children) {
  const total = children.length
  const ready = children.filter(c => c.status === 'ready').length
  const failed = children.filter(c =>
    ['failed', 'budget_blocked'].includes(c.status)).length
  if (ready === total) return `All ${total} videos ready`
  if (failed && ready + failed === total) {
    return `${ready} of ${total} ready — ${failed} need${failed === 1 ? 's' : ''} attention`
  }
  return `${ready} of ${total} ready`
}
