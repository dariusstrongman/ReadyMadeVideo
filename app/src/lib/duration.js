/**
 * Footage-aware duration bands.
 *
 * An edit implies COMPRESSION: offering "20+ minutes" against 24 minutes of
 * raw clips is barely editing, and the planner would honestly refuse most of
 * it anyway. A band is offered only when its LOWER bound is at most half the
 * raw footage — i.e. the result is at least a 2:1 distillation. Short-form
 * and "let the AI decide" are always available.
 */
export const DURATION_BANDS = [
  { value: '10-60',     label: '10–60 seconds',  min: 10,   max: 60 },
  { value: '60-180',    label: '1–3 minutes',    min: 60,   max: 180 },
  { value: '180-300',   label: '3–5 minutes',    min: 180,  max: 300 },
  { value: '300-600',   label: '5–10 minutes',   min: 300,  max: 600 },
  { value: '600-1200',  label: '10–20 minutes',  min: 600,  max: 1200 },
  { value: '1200-3600', label: '20+ minutes',    min: 1200, max: 3600 },
]

const MIN_COMPRESSION = 2   // an edit is at least a 2:1 distillation

export function availableDurationBands(totalRawSeconds) {
  if (!Number.isFinite(totalRawSeconds) || totalRawSeconds <= 0) {
    return DURATION_BANDS            // durations unknown: offer everything
  }
  return DURATION_BANDS.filter(
    (b) => b.min <= totalRawSeconds / MIN_COMPRESSION)
}
