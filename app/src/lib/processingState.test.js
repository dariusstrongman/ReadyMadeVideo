import { describe, it, expect } from 'vitest'
import { deriveProcessingState, STALL_THRESHOLD_MS } from '../pages/Project.jsx'

// ── Fixtures ──────────────────────────────────────────────────────────────────
const NOW = 1_000_000_000_000  // fixed "now" for deterministic tests
const RECENT   = new Date(NOW - 60_000).toISOString()           // 1 min ago
const STALE    = new Date(NOW - STALL_THRESHOLD_MS - 1000).toISOString() // just past threshold
const FRESH    = new Date(NOW - 30_000).toISOString()            // 30 sec ago

function project(overrides = {}) {
  return { status: 'analyzing', created_at: RECENT, ...overrides }
}

// ── 1. No render job, no analysis, fresh project → analysis_not_started ──────
describe('deriveProcessingState', () => {
  it('returns analysis_not_started when no job and no analysis rows and project is fresh', () => {
    const result = deriveProcessingState({
      project: project({ created_at: FRESH }),
      analysis: [],
      jobs: [],
      nowMs: NOW,
    })
    expect(result.kind).toBe('analysis_not_started')
  })

  // ── 2. No render job, no analysis rows, project past stall threshold → stalled ──
  it('returns stalled when no job, no analysis, and project is past stall threshold', () => {
    const result = deriveProcessingState({
      project: project({ created_at: STALE }),
      analysis: [],
      jobs: [],
      nowMs: NOW,
    })
    expect(result.kind).toBe('stalled')
  })

  // ── 3. Render job queued ──────────────────────────────────────────────────────
  it('returns job_queued when latest render job status is queued', () => {
    const result = deriveProcessingState({
      project: project(),
      analysis: [],
      jobs: [{ status: 'queued', error_message: null }],
      nowMs: NOW,
    })
    expect(result.kind).toBe('job_queued')
    expect(result.job.status).toBe('queued')
  })

  // ── 4. Render job processing ──────────────────────────────────────────────────
  it('returns job_processing when latest render job status is processing', () => {
    const result = deriveProcessingState({
      project: project(),
      analysis: [],
      jobs: [{ status: 'processing', error_message: null }],
      nowMs: NOW,
    })
    expect(result.kind).toBe('job_processing')
    expect(result.job.status).toBe('processing')
  })

  // ── 5. Render job failed ──────────────────────────────────────────────────────
  it('returns job_failed with job reference when latest render job status is failed', () => {
    const failedJob = { status: 'failed', error_message: 'ffmpeg exited with code 1' }
    const result = deriveProcessingState({
      project: project(),
      analysis: [],
      jobs: [failedJob],
      nowMs: NOW,
    })
    expect(result.kind).toBe('job_failed')
    expect(result.job).toBe(failedJob)
    expect(result.job.error_message).toBe('ffmpeg exited with code 1')
  })

  // ── 6. No asset_analysis rows (same as analysis_not_started, fresh) ───────────
  it('returns analysis_not_started when analysis array is empty and project is recent', () => {
    const result = deriveProcessingState({
      project: project({ created_at: RECENT }),
      analysis: [],
      jobs: [],
      nowMs: NOW,
    })
    expect(result.kind).toBe('analysis_not_started')
  })

  // ── 7. Partial analysis completion ───────────────────────────────────────────
  it('returns analysis_running when some analysis rows exist (partial)', () => {
    const result = deriveProcessingState({
      project: project(),
      analysis: [
        { kind: 'probe',      status: 'completed' },
        { kind: 'mechanical', status: 'running'   },
      ],
      jobs: [],
      nowMs: NOW,
    })
    expect(result.kind).toBe('analysis_running')
  })

  // ── 8. Full analysis completion (all kinds done, no render job yet) ───────────
  it('returns analysis_running when all analysis rows are completed but no render job', () => {
    const result = deriveProcessingState({
      project: project(),
      analysis: [
        { kind: 'probe',      status: 'completed' },
        { kind: 'proxy',      status: 'completed' },
        { kind: 'mechanical', status: 'completed' },
        { kind: 'audio',      status: 'completed' },
        { kind: 'scenes',     status: 'completed' },
        { kind: 'semantic',   status: 'completed' },
        { kind: 'motion',     status: 'completed' },
        { kind: 'catalog',    status: 'completed' },
      ],
      jobs: [],
      nowMs: NOW,
    })
    // All analysis done but no render job = still analysis_running (waiting for job creation)
    expect(result.kind).toBe('analysis_running')
  })

  // ── 9. Candidate-ready transition (project.status === draft_ready) ────────────
  it('returns candidate_ready when project.status is draft_ready', () => {
    const result = deriveProcessingState({
      project: project({ status: 'draft_ready' }),
      analysis: [],
      jobs: [],
      nowMs: NOW,
    })
    expect(result.kind).toBe('candidate_ready')
  })

  // ── 9b. Candidate-ready via completed render job ──────────────────────────────
  it('returns candidate_ready when latest render job is completed', () => {
    const result = deriveProcessingState({
      project: project(),
      analysis: [],
      jobs: [{ status: 'completed', error_message: null }],
      nowMs: NOW,
    })
    expect(result.kind).toBe('candidate_ready')
  })

  // ── 10. Polling timeout (simulated via stall threshold) ───────────────────────
  it('returns stalled (not infinite spinner) when project is past threshold with no progress', () => {
    const result = deriveProcessingState({
      project: project({ created_at: new Date(NOW - STALL_THRESHOLD_MS - 5000).toISOString() }),
      analysis: [],
      jobs: [],
      nowMs: NOW,
    })
    expect(result.kind).toBe('stalled')
    // Confirm it is NOT analysis_not_started (which would spin forever)
    expect(result.kind).not.toBe('analysis_not_started')
  })

  // ── Stall threshold boundary: exactly at threshold → not yet stalled ──────────
  it('does NOT return stalled when elapsed time equals the threshold exactly', () => {
    const result = deriveProcessingState({
      project: project({ created_at: new Date(NOW - STALL_THRESHOLD_MS).toISOString() }),
      analysis: [],
      jobs: [],
      nowMs: NOW,
    })
    // elapsed === threshold, not > threshold, so still analysis_not_started
    expect(result.kind).toBe('analysis_not_started')
  })

  // ── Stall does NOT fire if analysis has started ───────────────────────────────
  it('does NOT return stalled when analysis rows exist even if past threshold', () => {
    const result = deriveProcessingState({
      project: project({ created_at: STALE }),
      analysis: [{ kind: 'probe', status: 'running' }],
      jobs: [],
      nowMs: NOW,
    })
    expect(result.kind).toBe('analysis_running')
    expect(result.kind).not.toBe('stalled')
  })

  // ── Stall does NOT fire if a render job exists ────────────────────────────────
  it('does NOT return stalled when a render job exists even if past threshold', () => {
    const result = deriveProcessingState({
      project: project({ created_at: STALE }),
      analysis: [],
      jobs: [{ status: 'queued', error_message: null }],
      nowMs: NOW,
    })
    expect(result.kind).toBe('job_queued')
    expect(result.kind).not.toBe('stalled')
  })
})
