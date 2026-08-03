import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../App'
import { supabase } from '../lib/supabase'
import { RENDER_API } from '../lib/config'

const STATUS_LABEL = {
  draft:           'Ready for footage',
  uploading:       'Uploading footage',
  ready:           'Ready',
  analyzing:       'Examining your clips',
  analysis_failed: 'Analysis failed',
  draft_ready:     'Your edit is ready.',
  rendering:       'Rendering…',
  render_failed:   'Export failed',
  completed:       'Export ready',
  complete:        'Export ready',
}

// Pipeline stages mapped to real asset_analysis.kind values
// Stages are shown in order; completed = green check, current = cyan pulse, pending = muted
const PIPELINE_STAGES = [
  {
    id: 'upload',
    label: 'Footage uploaded',
    description: 'Your clips are stored securely and ready for processing.',
    kinds: [],  // not an analysis kind — derived from project.status >= ready
  },
  {
    id: 'probe',
    label: 'Validating clips',
    description: 'Checking clip format, duration, resolution, and audio tracks.',
    kinds: ['probe'],
  },
  {
    id: 'examine',
    label: 'Examining footage',
    description: 'Checking clip duration, format, motion, and audio clarity.',
    kinds: ['proxy', 'mechanical', 'audio'],
  },
  {
    id: 'moments',
    label: 'Finding strong moments',
    description: 'Comparing clips and identifying the most useful sections.',
    kinds: ['scenes', 'semantic', 'motion'],
  },
  {
    id: 'build',
    label: 'Building the edit',
    description: 'Arranging selected footage into a structured sequence.',
    kinds: ['catalog'],
  },
  {
    id: 'preview',
    label: 'Preparing preview',
    description: 'Finalising the edit so you can watch it.',
    kinds: [],  // derived from project.status === draft_ready
  },
]
const ANALYSIS_STAGES = {
  analyzing:  { title: 'Examining your clips',      sub: 'Scoring shot quality, camera movement, and audio clarity.' },
  selecting:  { title: 'Finding the best moments',  sub: 'Ranking clips by visual quality and story potential.' },
  structuring:{ title: 'Building the story',        sub: 'Assembling an opening, middle, and close from your footage.' },
  editing:    { title: 'Building your edit',  sub: 'Placing clips on the timeline, syncing music, and setting pacing.' },
  finishing:  { title: 'Almost done',               sub: 'Adding final touches to your edit.' },
}

function StepIndicator({ step }) {
  const steps = [
    { n: 1, label: 'Add footage' },
    { n: 2, label: 'AI builds your edit' },
    { n: 3, label: 'Watch your edit' },
    { n: 4, label: 'Refine' },
    { n: 5, label: 'Export' },
  ]
  return (
    <div className="step-indicator">
      {steps.map((s, i) => (
        <>
          <div key={s.n} className={`step-item ${step === s.n ? 'active' : step > s.n ? 'done' : ''}`}>
            <span className="step-num">{step > s.n ? '✓' : s.n}</span>
            {s.label}
          </div>
          {i < steps.length - 1 && <div key={`sep-${s.n}`} className={`step-sep ${step > s.n ? 'done' : ''}`} />}
        </>
      ))}
    </div>
  )
}

function Breadcrumb({ projectName, projectId }) {
  return (
    <div className="breadcrumb">
      <Link to="/">Your studio</Link>
      <span className="bc-sep">/</span>
      <span className="bc-current">{projectName || '…'}</span>
    </div>
  )
}

export default function Project() {
  const session = useAuth()
  const { id: projectId } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState(null)
  const [assets, setAssets] = useState([])
  const [candidates, setCandidates] = useState([])
  const [candidateIdx, setCandidateIdx] = useState(0)
  const [jobs, setJobs] = useState([])
  const [analysis, setAnalysis] = useState([])
  const [networkError, setNetworkError] = useState(null)
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadPct, setUploadPct] = useState(0)
  const [dragOver, setDragOver] = useState(false)
  const [pendingFiles, setPendingFiles] = useState([])
  const fileInputRef = useRef(null)
  const pollRef = useRef(null)
  const videoRef = useRef(null)

  const load = useCallback(async () => {
    try {
    const [{ data: proj }, { data: ast }, { data: cands }, { data: js }, { data: analysisRows }] = await Promise.all([
      supabase.from('projects').select('*').eq('id', projectId).single(),
      supabase.from('media_assets').select('*').eq('project_id', projectId).order('created_at'),
      supabase.from('edit_candidates').select('*').eq('project_id', projectId).order('overall_score', { ascending: false }),
      supabase.from('pipeline_jobs').select('*').eq('project_id', projectId).order('created_at', { ascending: false }),
      supabase.from('asset_analysis').select('kind,status,asset_id,created_at').eq('project_id', projectId).order('created_at', { ascending: false }),
    ])
    if (proj) setProject(proj)
    if (ast) setAssets(ast)
    if (cands) setCandidates(cands)
    if (js) setJobs(js)
    if (analysisRows) setAnalysis(analysisRows)
      setNetworkError(null)
    } catch (err) {
      setNetworkError(err?.message || 'Network error — check your connection.')
    }
  }, [projectId])

  useEffect(() => { load() }, [load])

  // Poll while analyzing
  useEffect(() => {
    if (!project) return
    const active = ['analyzing', 'uploading', 'ready', 'analysis_failed'].includes(project.status)
    if (active) {
      pollRef.current = setInterval(load, 3000)
    } else {
      clearInterval(pollRef.current)
    }
    return () => clearInterval(pollRef.current)
  }, [project?.status, load])

  // Autoplay candidate video
  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.load()
      videoRef.current.play().catch(() => {})
    }
  }, [candidateIdx])

  async function handleFiles(files) {
    if (!files?.length) return
    const arr = Array.from(files)
    // Generate thumbnails for preview
    const previews = await Promise.all(arr.map(f => new Promise(resolve => {
      if (!f.type.startsWith('video/')) { resolve({ file: f, thumb: null }); return }
      const video = document.createElement('video')
      video.preload = 'metadata'
      video.src = URL.createObjectURL(f)
      video.onloadeddata = () => {
        video.currentTime = 0.5
        video.onseeked = () => {
          const canvas = document.createElement('canvas')
          canvas.width = 96; canvas.height = 54
          canvas.getContext('2d').drawImage(video, 0, 0, 96, 54)
          resolve({ file: f, thumb: canvas.toDataURL('image/jpeg', 0.7) })
          URL.revokeObjectURL(video.src)
        }
      }
      video.onerror = () => resolve({ file: f, thumb: null })
    })))
    setPendingFiles(previews)
  }

  async function startUpload() {
    if (!pendingFiles.length) return
    setUploading(true); setError(''); setUploadPct(0)
    const uid = session.user.id
    const total = pendingFiles.length
    let done = 0
    for (const { file } of pendingFiles) {
      const ext = file.name.split('.').pop()
      const key = `users/${uid}/projects/${projectId}/raw/${Date.now()}_${Math.random().toString(36).slice(2)}.${ext}`
      // Step 1: Upload to Supabase Storage (raw-footage bucket)
      const { error: upErr } = await supabase.storage.from('raw-footage').upload(key, file, {
        cacheControl: '3600', upsert: false,
        onUploadProgress: ({ loaded, total: t }) => {
          const fileProgress = loaded / t
          setUploadPct(Math.round(((done + fileProgress) / total) * 100))
        },
      })
      if (upErr) { setError(upErr.message); setUploading(false); return }
      // Step 2: Insert media_assets row using the correct schema column names:
      //   filename (NOT original_filename) — required NOT NULL
      //   size_bytes (NOT file_size_bytes)
      const { error: dbErr } = await supabase.from('media_assets').insert({
        project_id: projectId,
        user_id: uid,
        storage_path: key,
        filename: file.name,
        size_bytes: file.size,
        mime_type: file.type,
      })
      if (dbErr) {
        // DB insert failed — clean up the orphaned storage object to avoid
        // footage that the worker can never find
        await supabase.storage.from('raw-footage').remove([key])
        setError(`Could not save footage record: ${dbErr.message}. Please try again.`)
        setUploading(false)
        return
      }
      done++
      setUploadPct(Math.round((done / total) * 100))
    }
    // Only mark project ready if all inserts succeeded
    const { error: statusErr } = await supabase.from('projects').update({ status: 'ready' }).eq('id', projectId)
    if (statusErr) {
      setError(`Upload complete but could not start processing: ${statusErr.message}`)
      setUploading(false)
      return
    }
    // Trigger analysis — idempotent: backend returns existing job if one is already active
    try {
      await fetch(`${RENDER_API}/projects/${projectId}/request-analysis`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}` },
      })
    } catch (analyzeErr) {
      // Non-fatal: the stall-detection UI will surface this after 5 minutes
      console.warn('[Stromation] Could not trigger analysis:', analyzeErr)
    }
    setPendingFiles([])
    setUploading(false)
    await load()
  }

  if (!project) return <div className="center"><p className="sub">Loading…</p></div>

  const status = project.status
  const step = status === 'draft' || status === 'uploading' ? 1
    : ['ready', 'analyzing', 'analysis_failed'].includes(status) ? 2
    : status === 'draft_ready' ? 3
    : ['rendering', 'render_failed'].includes(status) ? 4
    : (status === 'completed' || status === 'complete') ? 5 : 1

  // ── Export success ──────────────────────────────────────────────────
  if (status === 'completed' || status === 'complete') {
    const latestJob = jobs.find(j => j.status === 'completed')
    return (
      <>
        <Breadcrumb projectName={project.name} projectId={projectId} />
        <ExportSuccess project={project} job={latestJob} session={session} />
      </>
    )
  }

  // ── Candidate reveal ────────────────────────────────────────────────
  if (status === 'draft_ready' && candidates.length > 0) {
    const c = candidates[candidateIdx]
    return (
      <>
        <Breadcrumb projectName={project.name} projectId={projectId} />
        <div className="candidate-reveal">
          <div className="candidate-reveal-header">
            <h2>Your edit is ready.</h2>
            {candidates.length > 1 && <span>{candidateIdx + 1} of {candidates.length} edits</span>}
          </div>
          <div className="candidate-video-wrap">
            {c.preview_url
              ? <video ref={videoRef} src={c.preview_url} autoPlay muted loop playsInline />
              : <div className="candidate-video-placeholder">Preview not available</div>
            }
          </div>
          <div className="candidate-info">
            <p className="candidate-title">{c.candidate_key || `Edit ${candidateIdx + 1}`}</p>
            <p className="candidate-highlights">
              {c.publishability_label || 'AI-assembled edit'}{c.overall_score ? ` · Score: ${Math.round(c.overall_score)}` : ''}
            </p>
            <div className="candidate-actions">
              <button className="btn btn-primary btn-lg"
                onClick={() => navigate(`/project/${projectId}/editor/${c.id}`)}>
                Watch this edit
              </button>
              <button className="btn btn-ghost"
                onClick={() => navigate(`/project/${projectId}/editor/${c.id}`)}>
                Open in editor
              </button>
            </div>
          </div>
          {candidates.length > 1 && (
            <div className="candidate-dots">
              {candidates.map((_, i) => (
                <button key={i} className={`candidate-dot ${i === candidateIdx ? 'active' : ''}`}
                  onClick={() => setCandidateIdx(i)} aria-label={`Edit ${i + 1}`} />
              ))}
            </div>
          )}
        </div>
      </>
    )
  }

  // ── Processing ──────────────────────────────────────────────────────
  if (['ready', 'analyzing', 'analysis_failed'].includes(status)) {
    return (
      <>
        <Breadcrumb projectName={project.name} projectId={projectId} />
        <StepIndicator step={2} />
        <ProcessingWorkspace
          project={project}
          assets={assets}
          analysis={analysis}
          jobs={jobs}
          networkError={networkError}
          session={session}
          onRetry={load}
          projectId={projectId}
        />
      </>
    )
  }

  // ── Upload ──────────────────────────────────────────────────────────
  const totalSize = pendingFiles.reduce((s, { file }) => s + file.size, 0)
  const fmtSize = b => b > 1e6 ? `${(b/1e6).toFixed(1)} MB` : `${(b/1e3).toFixed(0)} KB`

  return (
    <>
      <Breadcrumb projectName={project.name} projectId={projectId} />
      <StepIndicator step={step} />
      <div className="wrap" style={{ paddingTop: 40, paddingBottom: 80 }}>
        {error && <div className="err" role="alert">{error}</div>}

        {uploading ? (
          <div className="upload-progress-wrap">
            <div className="upload-pct">{uploadPct}%</div>
            <p className="upload-label">Uploading your footage securely…</p>
            <div className="progress" style={{ maxWidth: 400, margin: '0 auto' }}>
              <div style={{ width: `${uploadPct}%` }} />
            </div>
          </div>
        ) : pendingFiles.length > 0 ? (
          <div>
            <span className="section-label">Ready to upload</span>
            <div className="file-preview-list">
              {pendingFiles.map(({ file, thumb }, i) => (
                <div key={i} className="file-preview-item">
                  {thumb
                    ? <img src={thumb} className="file-preview-thumb" alt="" />
                    : <div className="file-preview-thumb" />
                  }
                  <span className="file-preview-name">{file.name}</span>
                  <span className="file-preview-meta">{fmtSize(file.size)}</span>
                </div>
              ))}
            </div>
            <p className="upload-total">{pendingFiles.length} file{pendingFiles.length > 1 ? 's' : ''} · {fmtSize(totalSize)}</p>
            <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
              <button className="btn btn-ghost" onClick={() => setPendingFiles([])}>Clear</button>
              <button className="btn btn-primary btn-lg" onClick={startUpload}>Start upload</button>
            </div>
          </div>
        ) : (
          <div
            className={`drop-zone ${dragOver ? 'drag-over' : ''}`}
            onClick={() => fileInputRef.current?.click()}
            onDragOver={e => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={e => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files) }}
          >
            <div className="drop-zone-icon">↑</div>
            <h2 className="drop-zone-title">Drop your footage here</h2>
            <p className="drop-zone-sub">MP4 or MOV · up to 50 MB per clip · stored privately under your account</p>
            <button className="btn btn-ghost" onClick={e => { e.stopPropagation(); fileInputRef.current?.click() }}>
              Browse files
            </button>
            <input ref={fileInputRef} type="file" accept="video/*" multiple style={{ display: 'none' }}
              onChange={e => handleFiles(e.target.files)} />
          </div>
        )}

        {/* Existing assets */}
        {assets.length > 0 && !uploading && pendingFiles.length === 0 && (
          <div style={{ marginTop: 32 }}>
            <span className="section-label">Your footage</span>
            <div className="grid">
              {assets.map(a => (
                <div key={a.id} className="list-item">
                  <span style={{ flex: 1, fontSize: '0.85rem' }}>{a.filename}</span>
                  <span className="small">{a.size_bytes ? fmtSize(a.size_bytes) : ''}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Render jobs */}
        {jobs.length > 0 && (
          <div style={{ marginTop: 32 }}>
            <span className="section-label">Export history</span>
            <div className="grid">
              {jobs.map(j => <JobRow key={j.id} job={j} session={session} projectId={projectId} onRetry={load} />)}
            </div>
          </div>
        )}
      </div>
    </>
  )
}


// ── Processing state derivation (pure function, testable) ──────────────────
// STALL_THRESHOLD_MS: 5 minutes of silence after the most recent relevant
// activity signal before we show the stall error state.
export const STALL_THRESHOLD_MS = 5 * 60 * 1000  // 5 minutes

// Resolve the most recent timestamp that signals real processing activity.
// Priority: assets.created_at > analysis.created_at > jobs.created_at >
//           project.created_at as final fallback.
export function resolveStallAnchor({ assets, analysis, jobs, project }) {
  const ts = (iso) => (iso ? new Date(iso).getTime() : 0)
  const candidates = [
    ...( assets   ?? []).map(a => ts(a.created_at)),
    ...( analysis ?? []).map(r => ts(r.created_at)),
    ...( jobs     ?? []).map(j => ts(j.created_at)),
    ts(project.created_at),
  ].filter(t => t > 0)
  return candidates.length > 0 ? Math.max(...candidates) : 0
}

export function deriveProcessingState({ project, assets, analysis, jobs, nowMs }) {
  const now = nowMs ?? Date.now()
  const latestJob = jobs[0] ?? null

  // Candidate ready — project transitioned out of processing
  if (project.status === 'draft_ready') return { kind: 'candidate_ready' }

  // Project-level failure — backend set analysis_failed on the project
  if (project.status === 'analysis_failed') {
    // Surface the most recent failed pipeline job for its error_message
    const failedJob = jobs.find(j => j.status === 'failed') ?? latestJob
    return { kind: 'job_failed', job: failedJob }
  }
  // Pipeline job states (pipeline_jobs table — kinds: analysis, autoedit, revision, final_render)
  if (latestJob) {
    if (latestJob.status === 'failed')     return { kind: 'job_failed',     job: latestJob }
    if (latestJob.status === 'processing') return { kind: 'job_processing', job: latestJob }
    if (latestJob.status === 'queued')     return { kind: 'job_queued',     job: latestJob }
    if (latestJob.status === 'completed' && project.status === 'draft_ready') return { kind: 'candidate_ready' }
    if (latestJob.status === 'cancelled')  return { kind: 'stalled' }
  }

  // No render job yet — check analysis
  const hasAnalysis = analysis.length > 0
  const anyRunning  = analysis.some(r => r.status === 'running')
  const anyDone     = analysis.some(r => r.status === 'completed')

  if (hasAnalysis && (anyRunning || anyDone)) {
    return { kind: 'analysis_running', analysis }
  }

  // Stall detection: no job, no analysis rows, and no recent activity
  const anchor  = resolveStallAnchor({ assets: assets ?? [], analysis, jobs, project })
  const elapsed = now - anchor
  if (!hasAnalysis && !latestJob && elapsed > STALL_THRESHOLD_MS) {
    return { kind: 'stalled' }
  }

  // Default: analysis not yet started (waiting for first pipeline row)
  return { kind: 'analysis_not_started' }
}

// ── ProcessingWorkspace ─────────────────────────────────────────────────────
function ProcessingWorkspace({ project, assets, analysis, jobs, networkError, session, onRetry, projectId }) {
  const fmtSize = b => !b ? '' : b > 1e6 ? `${(b/1e6).toFixed(1)} MB` : `${(b/1e3).toFixed(0)} KB`
  const fmtDur  = s => !s ? '' : s >= 60 ? `${Math.floor(s/60)}m ${Math.round(s%60)}s` : `${Math.round(s)}s`

  const state = deriveProcessingState({ project, assets, analysis, jobs })

  // ── Live elapsed timer ──
  const [elapsed, setElapsed] = React.useState(0)
  React.useEffect(() => {
    const job = jobs[0]
    const anchor = job?.started_at || project?.created_at
    if (!anchor) return
    const tick = () => setElapsed(Math.floor((Date.now() - new Date(anchor).getTime()) / 1000))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [jobs[0]?.started_at, project?.created_at])

  // ── Helpers ──
  const fmtElapsed = s => {
    if (s < 60) return `${s}s`
    const m = Math.floor(s / 60), sec = s % 60
    return `${m}m ${sec.toString().padStart(2,'0')}s`
  }
  const STAGE_LABELS = {
    probe: 'Validating clip format', proxy: 'Building proxy video',
    scenes: 'Detecting scene cuts', mechanical: 'Analyzing camera motion',
    audio: 'Measuring audio levels', transcript: 'Transcribing speech',
    semantic: 'AI scene analysis', motion: 'Scoring motion quality',
    catalog: 'Building segment catalog',
  }
  const assetName = id => assets.find(a => a.id === id)?.filename?.replace(/\.[^.]+$/, '') || 'clip'
  const assetIdx  = id => { const i = assets.findIndex(a => a.id === id); return i >= 0 ? i + 1 : 0 }

  // ── Network error ──
  if (networkError) {
    return (
      <div className="wrap" style={{ paddingTop: 48, paddingBottom: 80 }}>
        <div className="proc-error-card">
          <div className="proc-error-icon">✕</div>
          <h2 className="proc-error-title">Connection problem.</h2>
          <p className="proc-error-sub">{networkError}</p>
          <div className="proc-error-actions">
            <Link to="/" className="btn btn-ghost">← Back to Your Studio</Link>
          </div>
        </div>
      </div>
    )
  }

  // ── Stalled / no job created ──
  const [retrying, setRetrying] = React.useState(false)
  const [retryError, setRetryError] = React.useState('')

  const canRetry = (
    session &&
    project.user_id === session.user.id &&
    assets.length > 0 &&
    !jobs.some(j => ['queued', 'processing'].includes(j.status))
  )

  async function handleRetry() {
    if (retrying) return
    setRetrying(true)
    setRetryError('')
    try {
      const r = await fetch(`${RENDER_API}/projects/${projectId}/request-analysis`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}` },
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Request failed (${r.status})`)
      }
      // Success — reload state to move into queued/processing UI
      if (onRetry) await onRetry()
    } catch (err) {
      setRetryError(err.message || 'Could not start the edit. Please try again.')
    } finally {
      setRetrying(false)
    }
  }

  if (state.kind === 'stalled') {
    return (
      <div className="wrap" style={{ paddingTop: 48, paddingBottom: 80 }}>
        <div className="proc-error-card">
          <div className="proc-error-icon">✕</div>
          <h2 className="proc-error-title">We couldn't start the edit.</h2>
          <p className="proc-error-sub">
            Your footage is uploaded safely, but the editing job did not start.
          </p>
          {retryError && (
            <p className="proc-error-detail" role="alert" aria-live="assertive">
              {retryError}
            </p>
          )}
          <div className="proc-error-actions">
            {canRetry && (
              <button
                className="btn btn-primary"
                onClick={handleRetry}
                disabled={retrying}
                aria-busy={retrying}
              >
                {retrying ? 'Starting…' : 'Try starting the edit'}
              </button>
            )}
            <Link to="/" className="btn btn-ghost">← Back to Your Studio</Link>
            {assets.length > 0 && (
              <a href="#footage" className="btn btn-ghost">View uploaded footage</a>
            )}
          </div>
        </div>
        {assets.length > 0 && (
          <div id="footage">
            <FootageGrid assets={assets} fmtSize={fmtSize} fmtDur={fmtDur} />
          </div>
        )}
      </div>
    )
  }

  // ── Render job failed ──
  if (state.kind === 'job_failed') {
    return (
      <div className="wrap" style={{ paddingTop: 48, paddingBottom: 80 }}>
        <div className="proc-error-card">
          <div className="proc-error-icon">✕</div>
          <h2 className="proc-error-title">We couldn't start the edit.</h2>
          <p className="proc-error-sub">
            Your footage is uploaded safely, but the editing job did not start.
          </p>
          {state.job.error_message && (
            <p className="proc-error-detail">{state.job.error_message}</p>
          )}
          <div className="proc-error-actions">
            <Link to="/" className="btn btn-ghost">← Back to Your Studio</Link>
            {assets.length > 0 && (
              <a href="#footage" className="btn btn-ghost">View uploaded footage</a>
            )}
          </div>
        </div>
        {assets.length > 0 && (
          <div id="footage">
            <FootageGrid assets={assets} fmtSize={fmtSize} fmtDur={fmtDur} />
          </div>
        )}
      </div>
    )
  }

  // ── Derive stage states for active/not-started/queued/processing ──
  function stageState(stage) {
    if (stage.kinds.length === 0) {
      if (stage.id === 'upload') return 'done'
      if (stage.id === 'preview') return project.status === 'draft_ready' ? 'done' : 'pending'
      return 'pending'
    }
    const relevant = analysis.filter(r => stage.kinds.includes(r.kind))
    const anyRunning = relevant.some(r => r.status === 'running')
    const allDone = stage.kinds.every(k => relevant.some(r => r.kind === k && r.status === 'completed'))
    if (allDone) return 'done'
    if (anyRunning || relevant.some(r => r.status === 'completed')) return 'active'
    return 'pending'
  }
  const stageStates = PIPELINE_STAGES.map(s => ({ ...s, state: stageState(s) }))
  const currentStage = stageStates.find(s => s.state === 'active') || stageStates.find(s => s.state === 'pending') || stageStates[stageStates.length - 1]

  // Activity label varies by state
  const activityLabel = state.kind === 'job_queued'      ? 'Edit queued'
    : state.kind === 'job_processing'                    ? 'Building your edit'
    : state.kind === 'analysis_running'                  ? 'Creating your edit'
    : 'Starting up'

  return (
    <div className="proc-live-wrap">
      {/* ── Header row: title + elapsed timer ── */}
      <div className="proc-live-header">
        <div>
          <div className="proc-live-eyebrow">
            <span className="proc-live-dot" />
            {activityLabel}
          </div>
          <h2 className="proc-live-title">{currentStage.label}</h2>
          <p className="proc-live-sub">{currentStage.description}</p>
        </div>
        <div className="proc-live-timer" aria-label={`Elapsed: ${fmtElapsed(elapsed)}`}>
          <span className="proc-timer-num">{fmtElapsed(elapsed)}</span>
          <span className="proc-timer-label">elapsed</span>
        </div>
      </div>

      {/* ── Progress bar ── */}
      {jobs[0]?.progress > 0 && (
        <div className="proc-progress-track">
          <div className="proc-progress-fill" style={{ width: `${jobs[0].progress}%` }} />
          <span className="proc-progress-pct">{jobs[0].progress}%</span>
        </div>
      )}

      {/* ── Two-column: stages + live log ── */}
      <div className="proc-live-body">
        {/* Left: stage pipeline */}
        <div className="proc-live-stages">
          <div className="proc-live-section-label">Pipeline</div>
          <div className="proc-pipeline" role="list" aria-label="Processing stages">
            {stageStates.map((s) => (
              <div key={s.id} className={`proc-stage ${s.state}`} role="listitem">
                <div className="proc-stage-icon" aria-hidden="true">
                  {s.state === 'done'
                    ? <svg viewBox="0 0 16 16" fill="none" width="10" height="10"><path className="proc-stage-check" d="M3 8l3.5 3.5L13 5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                    : <div className="proc-stage-dot" />
                  }
                </div>
                <div className="proc-stage-text">
                  <span className="proc-stage-name">{s.label}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Right: live activity log */}
        <div className="proc-live-log">
          <div className="proc-live-section-label">
            Live activity
            <span className="proc-live-blink" aria-hidden="true" />
          </div>
          <div className="proc-log-scroll">
            {analysis.length === 0 ? (
              <div className="proc-log-empty">
                <div className="proc-log-spinner" />
                Waiting for first stage to start…
              </div>
            ) : (
              analysis.slice(0, 20).map((row, i) => {
                const clipNum = assetIdx(row.asset_id)
                const clipTotal = assets.length
                const label = STAGE_LABELS[row.kind] || row.kind
                const isDone = row.status === 'completed'
                const isFail = row.status === 'failed'
                return (
                  <div key={`${row.asset_id}-${row.kind}`} className={`proc-log-item ${isDone ? 'done' : isFail ? 'fail' : 'running'}`}
                    style={{ animationDelay: `${i * 30}ms` }}>
                    <span className="proc-log-icon">
                      {isDone ? '✓' : isFail ? '✕' : '·'}
                    </span>
                    <span className="proc-log-text">
                      {clipTotal > 1 && <span className="proc-log-clip">Clip {clipNum}/{clipTotal} — </span>}
                      {label}
                      {isFail && <span className="proc-log-fail"> (skipped)</span>}
                    </span>
                    <span className="proc-log-time">
                      {new Date(row.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </span>
                  </div>
                )
              })
            )}
            {/* Current job stage from pipeline_jobs.current_stage */}
            {jobs[0]?.current_stage && jobs[0].status === 'processing' && (
              <div className="proc-log-item running proc-log-current">
                <span className="proc-log-icon proc-log-pulse">●</span>
                <span className="proc-log-text">{jobs[0].current_stage}</span>
                <span className="proc-log-time">now</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Per-clip status grid ── */}
      {assets.length > 0 && (
        <div className="proc-clip-grid">
          <div className="proc-live-section-label">Your clips</div>
          <div className="proc-clip-status-row">
            {assets.map((a, i) => {
              const clipAnalysis = analysis.filter(r => r.asset_id === a.id)
              const doneCount = clipAnalysis.filter(r => r.status === 'completed').length
              const totalKinds = 9 // probe proxy scenes mechanical audio transcript semantic motion catalog
              const pct = Math.round((doneCount / totalKinds) * 100)
              const isActive = clipAnalysis.some(r => r.status === 'running') ||
                (jobs[0]?.current_stage || '').includes(`clip ${i+1}/`)
              const isDone = doneCount >= 7 // catalog+semantic may be skipped
              return (
                <div key={a.id} className={`proc-clip-status ${isDone ? 'done' : isActive ? 'active' : doneCount > 0 ? 'partial' : 'pending'}`}>
                  <div className="proc-clip-status-bar">
                    <div className="proc-clip-status-fill" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="proc-clip-status-name" title={a.filename}>
                    {a.filename?.replace(/\.[^.]+$/, '').slice(0, 18) || `Clip ${i+1}`}
                  </div>
                  <div className="proc-clip-status-pct">{isDone ? '✓' : pct > 0 ? `${pct}%` : '—'}</div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── Footer ── */}
      <div className="proc-live-footer">
        <span>Updates every 3 seconds · </span>
        <Link to="/" style={{ color: 'var(--cyan)' }}>Return to studio</Link>
        <span> while this runs</span>
      </div>
    </div>
  )
}

function FootageGrid({ assets, fmtSize, fmtDur }) {
  return (
    <div className="proc-footage" style={{ marginTop: 48 }}>
      <span className="section-label">Your footage</span>
      <div className="proc-clip-list" style={{ marginTop: 12 }}>
        {assets.map((a, i) => (
          <div
            key={a.id}
            className="proc-clip-card"
            style={{ animationDelay: `${i * 60}ms` }}
          >
            <div className="proc-clip-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="2" width="20" height="20" rx="3"/>
                <path d="M10 8l6 4-6 4V8z"/>
              </svg>
            </div>
            <div className="proc-clip-info">
              <span className="proc-clip-name">{a.filename}</span>
              <span className="proc-clip-meta">
                {[fmtSize(a.size_bytes), fmtDur(a.duration_seconds)].filter(Boolean).join(' · ')}
              </span>
            </div>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-label="Upload complete" style={{ color: 'var(--green)', flexShrink: 0 }}>
              <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" fill="rgba(34,197,94,0.08)"/>
              <path d="M5 8l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
        ))}
      </div>
    </div>
  )
}

function ExportSuccess({ project, job, session }) {
  const [signedUrl, setSignedUrl] = useState(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (job?.output_storage_path) {
      supabase.storage.from('exports').createSignedUrl(job.output_storage_path, 3600)
        .then(({ data }) => { if (data) setSignedUrl(data.signedUrl) })
    }
  }, [job])

  function copyShare() {
    navigator.clipboard.writeText(`Just finished my first edit with Stromation — raw footage in, finished video out. https://www.stromation.com`)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="export-success">
      <svg className="export-check" viewBox="0 0 52 52" fill="none">
        <circle cx="26" cy="26" r="25" stroke="currentColor" strokeWidth="2" opacity="0.2" />
        <path className="export-check-path" d="M14 27l8 8 16-16" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <h1 className="export-title">Your video is ready.</h1>
      <p className="export-meta">
        {job ? `${job.output_width || ''}×${job.output_height || ''} · ${job.output_duration_seconds?.toFixed(1) || '?'}s · ${job.output_size_bytes ? ((job.output_size_bytes/1048576).toFixed(2) + ' MB') : ''}` : project.name}
      </p>
      <div className="export-actions">
        {signedUrl
          ? <a className="btn btn-primary btn-lg" href={signedUrl} download={`stromation-${project.name}.mp4`}>↓ Download MP4</a>
          : <button className="btn btn-primary btn-lg" disabled>Preparing download…</button>
        }
        <Link to="/" className="btn btn-ghost">← Back to studio</Link>
      </div>
      <div className="export-share">
        <p className="export-share-label">Made with Stromation. Share it →</p>
        <div className="export-share-btns">
          <button className="btn btn-ghost btn-sm" onClick={copyShare}>
            {copied ? '✓ Copied!' : 'Copy tweet'}
          </button>
        </div>
      </div>
      <p className="export-feedback">
        <a href="mailto:hello@stromation.com">Send feedback on this edit</a>
      </p>
    </div>
  )
}

function JobRow({ job, session, projectId, onRetry }) {
  const [signedUrl, setSignedUrl] = useState(null)
  const [urlError, setUrlError] = useState('')
  async function getUrl() {
    setUrlError('')
    const { data, error } = await supabase.storage.from('exports').createSignedUrl(job.output_storage_path, 3600)
    if (error) setUrlError(error.message)
    else setSignedUrl(data.signedUrl)
  }
  return (
    <div className="list-item" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
      <div className="row">
        <span className="mono small">{job.id.slice(0, 8)}</span>
        <span className={`badge ${job.status}`}>{job.status}</span>
        {['queued', 'processing'].includes(job.status) && (
          <div className="progress" style={{ flex: 1 }}><div style={{ width: `${job.progress}%` }} /></div>
        )}
        <span className="spacer" />
        {job.status === 'completed' && !signedUrl && (
          <button className="btn btn-primary btn-sm" onClick={getUrl}>Download video</button>
        )}
      </div>
      {job.status === 'failed' && job.error_message && (
        <p className="small" style={{ color: '#fca5a5', marginTop: 8 }}>{job.error_message}</p>
      )}
      {urlError && <div className="err">{urlError}</div>}
      {signedUrl && (
        <div style={{ marginTop: 12 }}>
          <video className="preview" src={signedUrl} controls />
          <p style={{ marginTop: 8 }}>
            <a className="btn btn-primary btn-sm" href={signedUrl} download={`stromation-${job.id.slice(0, 8)}.mp4`}>
              Download MP4
            </a>
            <span className="small" style={{ marginLeft: 10 }}>link valid for 1 hour</span>
          </p>
        </div>
      )}
    </div>
  )
}
