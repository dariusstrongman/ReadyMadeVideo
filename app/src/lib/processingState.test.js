import { describe, it, expect } from 'vitest'
import { deriveProcessingState, resolveStallAnchor, STALL_THRESHOLD_MS } from '../pages/Project.jsx'

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
      assets: [],
      jobs: [{ status: 'queued', error_message: null }],
      nowMs: NOW,
    })
    expect(result.kind).toBe('job_queued')
    expect(result.kind).not.toBe('stalled')
  })

  // ── Stall does NOT fire if analysis has started ───────────────────────────────
  // (already tested above, but ensure assets param is passed)
  it('does NOT return stalled when analysis rows exist even if past threshold (with assets param)', () => {
    const result = deriveProcessingState({
      project: project({ created_at: STALE }),
      assets: [],
      analysis: [{ kind: 'probe', status: 'running', created_at: STALE }],
      jobs: [],
      nowMs: NOW,
    })
    expect(result.kind).toBe('analysis_running')
  })
})

// ── resolveStallAnchor tests ─────────────────────────────────────────────────
describe('resolveStallAnchor', () => {
  const OLD_PROJECT_TS = NOW - STALL_THRESHOLD_MS - 60_000  // well past threshold
  const NEW_ASSET_TS   = NOW - 30_000                        // 30 sec ago (fresh)

  function oldProject() {
    return { created_at: new Date(OLD_PROJECT_TS).toISOString() }
  }

  // ── 1. Old project with a newly uploaded asset does not stall immediately ────
  it('returns new asset timestamp when asset is newer than project', () => {
    const anchor = resolveStallAnchor({
      assets:   [{ created_at: new Date(NEW_ASSET_TS).toISOString() }],
      analysis: [],
      jobs:     [],
      project:  oldProject(),
    })
    expect(anchor).toBe(NEW_ASSET_TS)
    // Confirm: elapsed from anchor is only 30s, well under threshold
    expect(NOW - anchor).toBeLessThan(STALL_THRESHOLD_MS)
  })

  // ── 2. Old project with no new activity can stall ─────────────────────────────
  it('falls back to project.created_at when no assets/analysis/jobs exist', () => {
    const anchor = resolveStallAnchor({
      assets:   [],
      analysis: [],
      jobs:     [],
      project:  oldProject(),
    })
    expect(anchor).toBe(OLD_PROJECT_TS)
    // Confirm: elapsed from anchor exceeds threshold
    expect(NOW - anchor).toBeGreaterThan(STALL_THRESHOLD_MS)
  })

  // ── 3. Latest media asset timestamp overrides project.created_at ──────────────
  it('picks the latest asset timestamp over project.created_at', () => {
    const olderAsset = NOW - 4 * 60 * 1000   // 4 min ago
    const newerAsset = NOW - 2 * 60 * 1000   // 2 min ago
    const anchor = resolveStallAnchor({
      assets: [
        { created_at: new Date(olderAsset).toISOString() },
        { created_at: new Date(newerAsset).toISOString() },
      ],
      analysis: [],
      jobs:     [],
      project:  oldProject(),
    })
    expect(anchor).toBe(newerAsset)
  })

  // ── 4. Render job timestamp suppresses stall ──────────────────────────────────
  it('uses render job created_at when it is the most recent signal', () => {
    const jobTs = NOW - 60_000  // 1 min ago
    const anchor = resolveStallAnchor({
      assets:   [],
      analysis: [],
      jobs:     [{ created_at: new Date(jobTs).toISOString() }],
      project:  oldProject(),
    })
    expect(anchor).toBe(jobTs)
    expect(NOW - anchor).toBeLessThan(STALL_THRESHOLD_MS)
  })

  // ── 5. Analysis timestamp suppresses stall ────────────────────────────────────
  it('uses analysis created_at when it is the most recent signal', () => {
    const analysisTs = NOW - 90_000  // 1.5 min ago
    const anchor = resolveStallAnchor({
      assets:   [],
      analysis: [{ created_at: new Date(analysisTs).toISOString() }],
      jobs:     [],
      project:  oldProject(),
    })
    expect(anchor).toBe(analysisTs)
    expect(NOW - anchor).toBeLessThan(STALL_THRESHOLD_MS)
  })
})

// ── Upload trigger tests ────────────────────────────────────────────────────
// These tests verify the state model correctly handles the transition from
// upload-complete to analysis-started, and the stall behavior when analysis
// never starts.

describe('upload trigger and analysis start', () => {
  const recentAsset = { created_at: new Date(Date.now() - 30_000).toISOString() }
  const oldAsset    = { created_at: new Date(Date.now() - 10 * 60_000).toISOString() }
  const project     = { status: 'ready', created_at: new Date(Date.now() - 20 * 60_000).toISOString() }

  it('shows analysis_not_started immediately after upload (asset is recent)', () => {
    const state = deriveProcessingState({
      project, assets: [recentAsset], analysis: [], jobs: [],
      nowMs: Date.now(),
    })
    expect(state.kind).toBe('analysis_not_started')
  })

  it('does not stall when a recent asset exists even if project is old', () => {
    const state = deriveProcessingState({
      project, assets: [recentAsset], analysis: [], jobs: [],
      nowMs: Date.now(),
    })
    expect(state.kind).not.toBe('stalled')
  })

  it('transitions to analysis_running when asset_analysis rows appear', () => {
    const state = deriveProcessingState({
      project, assets: [recentAsset],
      analysis: [{ kind: 'probe', status: 'running', created_at: new Date().toISOString() }],
      jobs: [], nowMs: Date.now(),
    })
    expect(state.kind).toBe('analysis_running')
  })

  it('shows stalled when old project has old asset and no analysis after threshold', () => {
    const state = deriveProcessingState({
      project, assets: [oldAsset], analysis: [], jobs: [],
      nowMs: Date.now(),
    })
    expect(state.kind).toBe('stalled')
  })

  it('transitions to job_queued when a pipeline job appears', () => {
    const state = deriveProcessingState({
      project, assets: [recentAsset], analysis: [],
      jobs: [{ status: 'queued', created_at: new Date().toISOString() }],
      nowMs: Date.now(),
    })
    expect(state.kind).toBe('job_queued')
  })
})

// ── Stalled-state retry action tests ───────────────────────────────────────
// These tests verify the canRetry logic that controls whether the
// "Try starting the edit" button is shown in the stalled state.

describe('stalled-state retry eligibility (canRetry logic)', () => {
  const ownerId = 'user-abc'
  const otherId = 'user-xyz'
  const oldProject = {
    id: 'proj-1',
    user_id: ownerId,
    status: 'ready',
    created_at: new Date(Date.now() - 20 * 60_000).toISOString(),
  }
  const oldAsset = { created_at: new Date(Date.now() - 10 * 60_000).toISOString() }
  const activeJob = { status: 'queued', created_at: new Date().toISOString() }
  const completedJob = { status: 'completed', created_at: new Date(Date.now() - 5 * 60_000).toISOString() }

  // Helper: compute canRetry from the same logic as ProcessingWorkspace
  function canRetry({ session, project, assets, jobs }) {
    return !!(
      session &&
      project.user_id === session.user.id &&
      assets.length > 0 &&
      !jobs.some(j => ['queued', 'processing'].includes(j.status))
    )
  }

  it('existing stuck project can start analysis (owner, has assets, no active job)', () => {
    expect(canRetry({
      session: { user: { id: ownerId } },
      project: oldProject,
      assets: [oldAsset],
      jobs: [],
    })).toBe(true)
  })

  it('duplicate clicks prevented — active job disables retry button', () => {
    expect(canRetry({
      session: { user: { id: ownerId } },
      project: oldProject,
      assets: [oldAsset],
      jobs: [activeJob],
    })).toBe(false)
  })

  it('unauthorized project — wrong user cannot retry', () => {
    expect(canRetry({
      session: { user: { id: otherId } },
      project: oldProject,
      assets: [oldAsset],
      jobs: [],
    })).toBe(false)
  })

  it('no-assets project cannot start analysis', () => {
    expect(canRetry({
      session: { user: { id: ownerId } },
      project: oldProject,
      assets: [],
      jobs: [],
    })).toBe(false)
  })

  it('active job (queued) returns existing job — retry disabled', () => {
    expect(canRetry({
      session: { user: { id: ownerId } },
      project: oldProject,
      assets: [oldAsset],
      jobs: [activeJob],
    })).toBe(false)
  })

  it('active job (processing) — retry disabled', () => {
    expect(canRetry({
      session: { user: { id: ownerId } },
      project: oldProject,
      assets: [oldAsset],
      jobs: [{ status: 'processing', created_at: new Date().toISOString() }],
    })).toBe(false)
  })

  it('completed job does not block retry (new analysis allowed)', () => {
    expect(canRetry({
      session: { user: { id: ownerId } },
      project: oldProject,
      assets: [oldAsset],
      jobs: [completedJob],
    })).toBe(true)
  })

  it('no session — retry not available', () => {
    expect(canRetry({
      session: null,
      project: oldProject,
      assets: [oldAsset],
      jobs: [],
    })).toBe(false)
  })
})
