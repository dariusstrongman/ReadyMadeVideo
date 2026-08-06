import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../App'
import { supabase } from '../lib/supabase'
import { RENDER_API } from '../lib/config'
import { editorApi } from '../lib/editor'
import { UPLOAD_LIMIT_TEXT } from '../lib/config'
import { MultipartUpload, UploadError, createRealTransport, interruptedUploads, validateFile } from '../lib/s3upload'

// A customer export is a Product Editor render: pipeline_jobs.kind === 'final_render'
// carrying an editor_document_id. Analysis/autoedit jobs are never exports.
export const isExportJob = (j) => j.kind === 'final_render' && !!(j.params?.editor_document_id)

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

// Output shapes offered for a re-cut. Same vocabulary as the New Project wizard.
const RESHAPE = [
  { id: '9:16', label: 'Vertical' },
  { id: '16:9', label: 'Widescreen' },
  { id: '1:1',  label: 'Square' },
]

// Name a cut by the frame it was actually rendered at, read from its own
// manifest — so two versions of the same video are told apart by what they ARE,
// not by an anonymous dot.
const shapeOf = (candidate) => {
  const tl = candidate?.manifest?.pictureTimeline
  const w = tl?.width, h = tl?.height
  if (!w || !h) return { label: 'Cut', ratio: '' }
  if (h > w) return { label: 'Vertical', ratio: '9:16' }
  if (h === w) return { label: 'Square', ratio: '1:1' }
  return { label: 'Widescreen', ratio: '16:9' }
}

const agoShort = (ts) => {
  const m = Math.floor((Date.now() - new Date(ts).getTime()) / 60000)
  if (!Number.isFinite(m)) return ''
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  return h < 24 ? `${h}h ago` : `${Math.floor(h / 24)}d ago`
}

// Duration as m:ss, read off the rendered video itself.
const fmtClock = (s) => {
  if (!Number.isFinite(s)) return ''
  const m = Math.floor(s / 60), sec = Math.round(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

// Upload progress formatting (S3 multipart transport)
const fmtBytes = (n) => n >= 1073741824
  ? `${(n / 1073741824).toFixed(2)} GB` : `${(n / 1048576).toFixed(1)} MB`
const fmtSpeed = (bps) => bps ? `${fmtBytes(bps)}/s` : ''
const fmtEta = (s) => (s == null || !isFinite(s)) ? ''
  : s >= 60 ? `${Math.round(s / 60)}m ${Math.round(s % 60)}s left` : `${Math.round(s)}s left`

export default function Project() {
  const session = useAuth()
  const { id: projectId } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState(null)
  const [assets, setAssets] = useState([])
  const [candidates, setCandidates] = useState([])
  const [candidateIdx, setCandidateIdx] = useState(0)
  const [previewUrls, setPreviewUrls] = useState({})   // candidateId -> signed URL
  const [opening, setOpening] = useState(false)
  const [jobs, setJobs] = useState([])
  const [analysis, setAnalysis] = useState([])
  const [networkError, setNetworkError] = useState(null)
  const [wsAttempted, setWsAttempted] = useState(false)
  const [documents, setDocuments] = useState([])
  const [pendingShape, setPendingShape] = useState(null)
  const [previewMeta, setPreviewMeta] = useState(null)  // {seconds,w,h} read off the <video>
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadPct, setUploadPct] = useState(0)
  const [uploadInfo, setUploadInfo] = useState(null)  // {bytesUploaded,totalBytes,speedBps,etaSeconds,state}
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState('')
  const [targetLen, setTargetLen] = useState('')   // '' = let the AI decide
  const [recutting, setRecutting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [recutError, setRecutError] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [pendingFiles, setPendingFiles] = useState([])
  const fileInputRef = useRef(null)
  const pollRef = useRef(null)
  const videoRef = useRef(null)

  const load = useCallback(async () => {
    try {
    const [{ data: proj }, { data: ast }, { data: js }, { data: analysisRows }] = await Promise.all([
      supabase.from('projects').select('*').eq('id', projectId).single(),
      supabase.from('media_assets').select('*').eq('project_id', projectId).order('created_at'),
      supabase.from('pipeline_jobs').select('*').eq('project_id', projectId).order('created_at', { ascending: false }),
      supabase.from('asset_analysis').select('kind,status,asset_id,created_at').eq('project_id', projectId).order('created_at', { ascending: false }),
    ])
    if (proj) setProject(proj)
    if (ast) setAssets(ast)
    if (js) setJobs(js)
    if (analysisRows) setAnalysis(analysisRows)
    // Candidates come from the immutable Product Editor workspace API (candidate_runs).
    // One contract for bridged / initial / revised candidates.
    try {
      const ws = await editorApi(`/projects/${projectId}/workspace`, session)
      setCandidates(ws.candidates || [])
      setDocuments(ws.documents || [])
    } catch {
      // Candidate query failed — leave candidates empty; the project view stays on
      // its processing/empty state and the poller retries. Never crashes the page.
    }
      setNetworkError(null)
    } catch (err) {
      setNetworkError(err?.message || 'Network error — check your connection.')
    } finally {
      // Marks that a load has been ATTEMPTED, so an empty candidates list can be
      // told apart from one still in flight. This MUST live on the outer finally:
      // if the project/asset queries above throw, the inner block never runs, and
      // a draft_ready project would hold on "loading" forever.
      setWsAttempted(true)
    }
  }, [projectId, session])

  useEffect(() => { load() }, [load])

  // Closing the tab kills the browser-side uploader (direct-to-S3 by design:
  // the bytes never transit our servers, so the page IS the upload worker).
  // Warn before the user does it accidentally.
  useEffect(() => {
    if (!uploading) return undefined
    const warn = (e) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [uploading])

  // Poll only while an analysis OR export job is active; stop on terminal states.
  const anyActiveJob = jobs.some(
    (j) => ['queued', 'processing', 'cancel_requested'].includes(j.status))
  useEffect(() => {
    if (!project) return undefined
    const active = ['analyzing', 'uploading', 'ready', 'analysis_failed', 'rendering']
      .includes(project.status) || anyActiveJob
    if (active) {
      pollRef.current = setInterval(load, 3000)
    } else {
      clearInterval(pollRef.current)
    }
    return () => clearInterval(pollRef.current)   // clears on unmount + status change
  }, [project?.status, anyActiveJob, load])

  // Autoplay candidate video
  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.load()
      videoRef.current.play().catch(() => {})
    }
  }, [candidateIdx])

  // Fetch a short-lived signed preview URL for the visible candidate through the
  // authorized backend endpoint. Never uses the raw storage path as a URL.
  useEffect(() => {
    const c = candidates[candidateIdx]
    if (!c || previewUrls[c.id] !== undefined) return
    let cancelled = false
    editorApi(`/projects/${projectId}/candidates/${c.id}/preview-url`, session,
      { method: 'POST', body: JSON.stringify({}) })
      .then(({ url }) => { if (!cancelled) setPreviewUrls(p => ({ ...p, [c.id]: url })) })
      .catch(() => { if (!cancelled) setPreviewUrls(p => ({ ...p, [c.id]: null })) })
    return () => { cancelled = true }
  }, [candidateIdx, candidates, projectId, session, previewUrls])

  // Open a candidate through the Product Editor start endpoint, then navigate to
  // the returned editor_document route. Same path for bridged / initial / revised.
  // Export the finished cut as a real deliverable: a Product Editor
  // final_render at FINAL quality. The preview proxy is never the export.
  async function exportFinal(c) {
    if (exporting) return
    setExporting(true); setError('')
    try {
      const doc = await editorApi(`/projects/${projectId}/editor/start`, session,
        { method: 'POST', body: JSON.stringify({ candidateRunId: c.id }) })
      await editorApi(`/projects/${projectId}/editor/render`, session,
        { method: 'POST', body: JSON.stringify({ documentId: doc.id }) })
      // Reload: latestExport now exists, so the page switches to ExportView,
      // which polls the final_render job and signs the real MP4 download.
      await load()
    } catch (e) {
      setError(e.message || 'Could not start the export.')
    } finally { setExporting(false) }
  }

  async function openCandidate(c) {
    setOpening(true); setError('')
    try {
      const doc = await editorApi(`/projects/${projectId}/editor/start`, session,
        { method: 'POST', body: JSON.stringify({ candidateRunId: c.id }) })
      navigate(`/project/${projectId}/editor/${doc.id}`)
    } catch (e) {
      setError(e.message || 'Could not open the editor.')
    } finally { setOpening(false) }
  }

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
    setUploading(true); setError(''); setUploadPct(0); setUploadInfo(null)
    const total = pendingFiles.length
    const grandTotalBytes = pendingFiles.reduce((s, { file }) => s + file.size, 0)
    let done = 0
    let bytesBefore = 0   // bytes from already-finalized files
    // Direct browser -> S3 multipart per file. The backend finalize step is the
    // sole creator of the media_asset (service-role only; clients can no longer
    // insert), so there is no client-side storage upload or DB insert here.
    for (const { file } of pendingFiles) {
      // Reject oversize / wrong type BEFORE any network call.
      try {
        validateFile(file)
      } catch (err) {
        setError(err.message); setUploading(false); setUploadInfo(null); return
      }
      const upload = new MultipartUpload({
        file, projectId, userId: session.user.id,
        transport: createRealTransport({ accessToken: session.access_token }),
        onProgress: (p) => {
          const fileFraction = (p?.percent ?? 0) / 100
          setUploadPct(Math.round(((done + fileFraction) / total) * 100))
          setUploadInfo({
            bytesUploaded: bytesBefore + (p?.bytesUploaded ?? 0),
            totalBytes: grandTotalBytes,
            speedBps: p?.speedBps ?? 0,
            etaSeconds: p?.etaSeconds ?? null,
            state: p?.state ?? 'uploading',
          })
        },
      })
      try {
        // initiate -> sign-parts -> PUT parts -> complete -> finalize.
        // finalize validates the object (size/MIME/ffprobe), creates the
        // media_asset, and flips the project to `ready`.
        await upload.start()
      } catch (err) {
        if (err instanceof UploadError && err.code === 'cancelled') {
          setUploading(false); setUploadInfo(null); return
        }
        setError(err.message || String(err)); setUploading(false); setUploadInfo(null); return
      }
      done++
      bytesBefore += file.size
      setUploadPct(Math.round((done / total) * 100))
    }
    // Upload complete — deliberately NO auto-start (user decision 2026-08-06):
    // editing begins on the explicit "Start editing" button, so more clips can
    // be added first and the edit never kicks off without consent. The
    // reloaded page shows the "N clips ready" start bar.
    setPendingFiles([])
    setUploading(false)
    setUploadInfo(null)
    await load()
  }

  // Footage present, nothing queued, not mid-upload, and no edit already made.
  // The last two conditions matter: offering "Start editing" on a project that
  // already has a cut invites the user to re-run analysis over finished work.
  const canStartEdit = (
    assets.length > 0 && !uploading && pendingFiles.length === 0 &&
    !jobs.some(j => ['queued', 'processing'].includes(j.status)) &&
    candidates.length === 0 &&
    !['draft_ready', 'rendering', 'completed', 'complete'].includes(project?.status)
  )

  async function startEditing() {
    if (starting) return
    setStarting(true); setStartError('')
    try {
      // Persist the requested length FIRST — the planning chain derives its
      // binding duration constraints from the project row. Tolerant of the
      // column not existing yet (migration 0026 not applied): length is a
      // preference, never a reason to block starting.
      const want = Number(targetLen) || Number(project?.target_duration_seconds) || null
      if (Number(targetLen)) {
        const { error: te } = await supabase.from('projects')
          .update({ target_duration_seconds: Number(targetLen) })
          .eq('id', projectId)
        if (te && !/column|target_duration/i.test(te.message || '')) {
          throw new Error(te.message)
        }
      }
      void want
      // Idempotent server-side: returns the existing active job if one exists.
      const r = await fetch(`${RENDER_API}/projects/${projectId}/request-analysis`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}` },
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `Request failed (${r.status})`)
      }
      await load()
    } catch (e) {
      setStartError(e.message || 'Could not start the edit. Please try again.')
    } finally {
      setStarting(false)
    }
  }

  // Re-cut this project at a different frame shape. The backend reuses the
  // existing analysis and re-runs only the autoedit stage.
  async function recut(aspectRatio) {
    if (recutting) return
    setRecutting(true); setRecutError('')
    try {
      await editorApi(`/projects/${projectId}/recut`, session, {
        method: 'POST', body: JSON.stringify({ aspectRatio }),
      })
      await load()          // status flips to analyzing; the workspace takes over
    } catch (e) {
      setRecutError(e.message || 'Could not start the re-cut.')
    } finally {
      setRecutting(false)
    }
  }

  if (!project) return <div className="center"><p className="sub">Loading…</p></div>

  const status = project.status
  const step = status === 'draft' || status === 'uploading' ? 1
    : ['ready', 'analyzing', 'analysis_failed'].includes(status) ? 2
    : status === 'draft_ready' ? 3
    : ['rendering', 'render_failed'].includes(status) ? 4
    : (status === 'completed' || status === 'complete') ? 5 : 1

  // ── Export (Product Editor render — pipeline_jobs kind=final_render only) ──
  const exportJobs = jobs.filter(isExportJob)   // never analysis/autoedit
  const latestExport = exportJobs[0] ?? null
  if (status === 'completed' || status === 'complete') {
    const done = exportJobs.find(j => j.status === 'completed') ?? latestExport
    return (
      <>
        <Breadcrumb projectName={project.name} projectId={projectId} />
        <ExportView job={done} project={project} projectId={projectId}
          session={session} onRetry={load} />
      </>
    )
  }
  if (latestExport && ['queued', 'processing', 'failed'].includes(latestExport.status)) {
    return (
      <>
        <Breadcrumb projectName={project.name} projectId={projectId} />
        <ExportView job={latestExport} project={project} projectId={projectId}
          session={session} onRetry={load} />
      </>
    )
  }

  // ── Edit exists, but we couldn't load it ────────────────────────────
  // draft_ready means the cut is finished. If the workspace call failed, the
  // old code fell through to the upload screen and asked for footage on a
  // video that is already edited — a dead end that also hides the finished
  // work. Say what happened and offer a retry instead.
  // `draft_ready` is handled entirely by the three branches below and must NEVER
  // fall through to the upload view: that screen invites you to add footage and
  // offers "Start editing", which would re-run analysis over a finished edit.
  // While the workspace call is still in flight, hold the step — don't guess.
  if (status === 'draft_ready' && !wsAttempted) {
    return (
      <>
        <Breadcrumb projectName={project.name} projectId={projectId} />
        <StepIndicator step={3} />
        <div className="reveal">
          <div className="reveal-stage reveal-loading">
            <div className="proc-log-spinner" aria-hidden="true" />
            <p>Loading your edit…</p>
          </div>
        </div>
      </>
    )
  }

  if (status === 'draft_ready' && candidates.length === 0) {
    return (
      <>
        <Breadcrumb projectName={project.name} projectId={projectId} />
        <div className="wrap" style={{ paddingTop: 48, paddingBottom: 80 }}>
          <div className="proc-error-card">
            <div className="proc-error-icon">!</div>
            <h2 className="proc-error-title">Your edit is ready, but it didn't load.</h2>
            <p className="proc-error-sub">
              The edit is finished and safe — this page just couldn't fetch it.
              {networkError ? ` (${networkError})` : ''}
            </p>
            <div className="proc-error-actions">
              <button className="btn btn-primary" onClick={load}>Try again</button>
              <Link to="/" className="btn btn-ghost">← Back to Your Studio</Link>
            </div>
          </div>
        </div>
      </>
    )
  }

  // ── Candidate reveal ────────────────────────────────────────────────
  if (status === 'draft_ready' && candidates.length > 0) {
    const c = candidates[candidateIdx]
    const preview = previewUrls[c.id]            // undefined = loading, null = unavailable
    const report = c.publishability
    const score = report?.overall_publishability_score
    // Real numbers from the manifest — never invent them.
    const tl = c.manifest?.pictureTimeline
    const clipsUsed = c.manifest?.sourceAssetIds?.length
    const dl = preview
      ? `${preview}${preview.includes('?') ? '&' : '?'}download=${encodeURIComponent(`${project.name || 'edit'}-preview.mp4`)}`
      : null
    return (
      <>
        <Breadcrumb projectName={project.name} projectId={projectId} />
        <StepIndicator step={3} />
        <div className="reveal">
          <header className="reveal-head">
            <span className="reveal-eyebrow">
              {report?.publishable ? 'Publish-ready' : 'First cut'}
              {Number.isFinite(score) ? ` · scores ${Math.round(score)}` : ''}
            </span>
            <h1 className="reveal-title">Your edit is ready.</h1>
            <p className="reveal-sub">
              Watch it below. Open the editor to change the cut, or download it as it is.
            </p>
          </header>

          <div className="reveal-stage">
            {preview
              ? <video
                  ref={videoRef}
                  className="reveal-video"
                  src={preview}
                  controls autoPlay muted loop playsInline
                  onLoadedMetadata={(e) => setPreviewMeta({
                    seconds: e.currentTarget.duration,
                    w: e.currentTarget.videoWidth,
                    h: e.currentTarget.videoHeight,
                  })}
                />
              : <div className="reveal-placeholder">
                  {preview === undefined ? 'Loading your edit…' : 'This preview could not be loaded.'}
                </div>
            }
          </div>

          {/* Facts about the cut, read off the render itself and the manifest. */}
          <dl className="reveal-facts">
            {previewMeta?.seconds > 0 && (
              <div><dt>Length</dt><dd>{fmtClock(previewMeta.seconds)}</dd></div>
            )}
            {clipsUsed > 0 && (
              <div><dt>Clips used</dt><dd>{clipsUsed}{assets.length ? ` of ${assets.length}` : ''}</dd></div>
            )}
            {previewMeta?.w > 0 && (
              <div><dt>Frame</dt><dd>{previewMeta.w}×{previewMeta.h}</dd></div>
            )}
            {tl?.fps && <div><dt>Rate</dt><dd>{tl.fps} fps</dd></div>}
          </dl>

          {error && <div className="err" role="alert">{error}</div>}

          <div className="reveal-actions">
            <button className="btn btn-primary btn-lg" disabled={exporting}
              aria-busy={exporting} onClick={() => exportFinal(c)}>
              {exporting ? 'Starting export…' : 'Export final MP4'}
            </button>
            <button className="btn btn-ghost btn-lg" disabled={opening}
              onClick={() => openCandidate(c)}>
              {opening ? 'Opening…' : 'Change this edit'}
            </button>
          </div>
          {/* The watchable video above is a low-bitrate preview proxy — never
              presented as the deliverable. The real MP4 comes from the
              final_render export. */}
          {dl && (
            <p className="reveal-preview-note">
              <a href={dl} download>Download preview</a>
              {previewMeta?.w > 0 ? ` (${previewMeta.w}×${previewMeta.h} proxy` : ' (low-quality proxy'}
              , not the final export)
            </p>
          )}

          {/* Re-cut at a different shape. Only the autoedit stage re-runs —
              the analysis catalog is reused — so this is quick compared with
              starting the video over. */}
          <div className="reshape">
            {!pendingShape ? (
              <>
                <p className="reshape-label">Need a different shape?</p>
                <div className="reshape-row">
                  {RESHAPE.map(a => {
                    const current = (project.aspect_ratio || '16:9') === a.id
                    return (
                      <button key={a.id}
                        className={`np-aspect-opt ${current ? 'selected' : ''}`}
                        disabled={recutting || current}
                        onClick={() => setPendingShape(a.id)}>
                        <span className={`np-aspect-box ar-${a.id.replace(':', '-')}`} aria-hidden="true" />
                        <span className="np-aspect-name">{a.label}</span>
                        <span className="np-aspect-note">{current ? 'Current' : `Re-cut ${a.id}`}</span>
                      </button>
                    )
                  })}
                </div>
              </>
            ) : (
              /* Re-cutting starts real work and changes what this page shows, so
                 confirm first. The copy states what actually happens: a re-cut
                 ADDS a cut, it does not overwrite one — nothing deletes prior
                 candidates, and they stay reachable through the version picker.
                 The only thing that does not carry over is editor work, which is
                 bound to the version it was made on, so that line appears only
                 when there is editor work to lose. */
              <div className="reshape-confirm" role="dialog" aria-live="polite"
                aria-label="Confirm re-cut">
                <p className="reshape-confirm-q">
                  Re-cut this video as{' '}
                  {RESHAPE.find(a => a.id === pendingShape)?.label.toLowerCase()} ({pendingShape})?
                </p>
                <ul className="reshape-confirm-list">
                  <li>Stromation builds a new cut from your footage. It takes a few minutes.</li>
                  <li>Your current cut is kept — switch back any time from the version picker.</li>
                  {documents.length > 0 && (
                    <li>Changes you made in the editor stay on the current version.
                        The new cut starts from your original footage.</li>
                  )}
                </ul>
                {recutError && <p className="start-bar-err" role="alert">{recutError}</p>}
                <div className="reshape-confirm-row">
                  <button className="btn btn-ghost" disabled={recutting}
                    onClick={() => { setPendingShape(null); setRecutError('') }}>
                    Cancel
                  </button>
                  <button className="btn btn-primary" disabled={recutting} aria-busy={recutting}
                    onClick={async () => { await recut(pendingShape); setPendingShape(null) }}>
                    {recutting ? 'Starting…' : `Re-cut as ${pendingShape}`}
                  </button>
                </div>
              </div>
            )}
          </div>

          {candidates.length > 1 && (
            <div className="reveal-alts">
              <span className="reveal-alts-label">
                {candidates.length} versions of this video
              </span>
              {/* Re-cutting keeps every previous cut, so the switcher has to say
                  which is which. Anonymous dots made "we kept your old version"
                  a promise the user could not actually act on. */}
              <div className="version-row">
                {candidates.map((cand, i) => {
                  const shape = shapeOf(cand)
                  return (
                    <button key={cand.id}
                      className={`version-chip ${i === candidateIdx ? 'active' : ''}`}
                      aria-current={i === candidateIdx}
                      onClick={() => { setCandidateIdx(i); setPreviewMeta(null) }}>
                      <span className={`np-aspect-box ar-${(shape.ratio || '16:9').replace(':', '-')}`}
                        aria-hidden="true" />
                      <span className="version-chip-text">
                        <span className="version-chip-name">
                          {shape.label}{shape.ratio ? ` · ${shape.ratio}` : ''}
                        </span>
                        <span className="version-chip-when">{agoShort(cand.created_at)}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
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
  // Reaching here is only correct for a project that genuinely wants footage.
  // Every other status is handled above, but each of those branches depends on
  // data that arrives asynchronously (export jobs, candidates), so a status
  // like `rendering` can land here for a beat before its job list loads — and
  // this screen would then ask for footage on a video that is already cut.
  // Hold instead of guessing; the poller re-renders once the data is in.
  if (!['draft', 'uploading'].includes(status)) {
    return (
      <>
        <Breadcrumb projectName={project.name} projectId={projectId} />
        <div className="reveal">
          <div className="reveal-stage reveal-loading">
            <div className="proc-log-spinner" aria-hidden="true" />
            <p>Loading this video…</p>
          </div>
        </div>
      </>
    )
  }

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
            <p className="upload-label">
              {uploadInfo && ['completing', 'finalizing'].includes(uploadInfo.state)
                ? 'Validating your footage… processing begins after validation.'
                : 'Uploading your footage securely…'}
            </p>
            <div className="progress" style={{ maxWidth: 400, margin: '0 auto' }}>
              <div style={{ width: `${uploadPct}%` }} />
            </div>
            {uploadInfo && (
              <p className="small" style={{ marginTop: 8, textAlign: 'center' }}>
                {fmtBytes(uploadInfo.bytesUploaded)} / {fmtBytes(uploadInfo.totalBytes)}
                {!['completing', 'finalizing'].includes(uploadInfo.state) && uploadInfo.speedBps
                  ? ` · ${fmtSpeed(uploadInfo.speedBps)}` : ''}
                {!['completing', 'finalizing'].includes(uploadInfo.state) && uploadInfo.etaSeconds
                  ? ` · ${fmtEta(uploadInfo.etaSeconds)}` : ''}
              </p>
            )}
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
            <p className="drop-zone-sub">{UPLOAD_LIMIT_TEXT} · stored privately under your account</p>
            <button className="btn btn-ghost" onClick={e => { e.stopPropagation(); fileInputRef.current?.click() }}>
              Browse files
            </button>
            <input ref={fileInputRef} type="file" accept="video/*" multiple style={{ display: 'none' }}
              onChange={e => handleFiles(e.target.files)} />
          </div>
        )}

        {/* Footage is in but no job is queued — this project is one click from
            starting, so lead with that rather than the drop zone. Reachable at
            status `draft` too, which ProcessingWorkspace never renders for: a
            project whose upload landed but whose status never advanced would
            otherwise sit here with footage and no way forward. */}
        {/* A tab close killed an in-flight upload: say so, and how to resume —
            the browser cannot reopen the file itself, but re-selecting it
            continues from the parts already in S3. */}
        {!uploading && interruptedUploads(session?.user?.id, projectId).length > 0
          && assets.length === 0 && (
          <div className="resume-note" role="status">
            <strong>An upload was interrupted.</strong>{' '}
            Re-select {interruptedUploads(session?.user?.id, projectId)
              .map(u => u.fileName).join(', ')}{' '}
            and it resumes from the parts already uploaded.
          </div>
        )}

        {canStartEdit && (
          <div className="start-bar" style={{ marginTop: 32 }}>
            <div className="start-bar-text">
              <p className="start-bar-title">
                {assets.length} {assets.length === 1 ? 'clip' : 'clips'} ready
              </p>
              <p className="start-bar-sub">
                Start the edit and Stromation builds your first cut. You can close this page.
              </p>
              <label className="start-bar-length">
                Target length{' '}
                <select value={targetLen || project?.target_duration_seconds || ''}
                  onChange={(e) => setTargetLen(e.target.value)} disabled={starting}>
                  <option value="">Let the AI decide</option>
                  <option value="30">~30 seconds</option>
                  <option value="60">~1 minute</option>
                  <option value="120">~2 minutes</option>
                  <option value="180">~3 minutes</option>
                  <option value="300">~5 minutes</option>
                </select>
              </label>
              {startError && (
                <p className="start-bar-err" role="alert" aria-live="assertive">{startError}</p>
              )}
            </div>
            <button className="btn btn-primary btn-lg" onClick={startEditing}
              disabled={starting} aria-busy={starting}>
              {starting ? 'Starting…' : 'Start editing'}
            </button>
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

        {/* Export history — only real export jobs (final_render), never analysis/autoedit */}
        {exportJobs.length > 0 && (
          <div style={{ marginTop: 32 }}>
            <span className="section-label">Export history</span>
            <div className="grid">
              {exportJobs.map(j => <JobRow key={j.id} job={j} session={session} projectId={projectId} onRetry={load} />)}
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

  // ── Hooks ──
  // Every hook must run on every render. These two previously sat below the
  // `if (networkError) return` bail-out, so the hook count changed whenever a
  // poll failed or recovered — React then tore down the whole tree and the app
  // rendered as a blank page.
  const [retrying, setRetrying] = React.useState(false)
  const [retryError, setRetryError] = React.useState('')

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

  // ── Footage in, nothing queued yet: hand over the control that starts it ──
  // The automatic POST /request-analysis fired after upload can fail quietly —
  // a dropped connection, or a 429 from the per-user concurrency cap. Showing a
  // progress spinner for work that was never enqueued is a lie the user can't
  // act on, and the stall card only appears after five minutes of that. Say
  // plainly that it hasn't started and offer the button. /request-analysis is
  // idempotent, so pressing it is safe even if a job did just start.
  if (state.kind === 'analysis_not_started' && canRetry) {
    return (
      <div className="wrap" style={{ paddingTop: 48, paddingBottom: 80 }}>
        <div className="proc-error-card proc-start-card">
          <div className="proc-start-icon" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
          </div>
          <h2 className="proc-error-title">Your footage is ready.</h2>
          <p className="proc-error-sub">
            {assets.length} {assets.length === 1 ? 'clip' : 'clips'} uploaded. Start the edit and
            Stromation builds your first cut. You can leave this page while it works.
          </p>
          {retryError && (
            <p className="proc-error-detail" role="alert" aria-live="assertive">{retryError}</p>
          )}
          <div className="proc-error-actions">
            <button
              className="btn btn-primary btn-lg"
              onClick={handleRetry}
              disabled={retrying}
              aria-busy={retrying}
            >
              {retrying ? 'Starting…' : 'Start editing'}
            </button>
            <Link to="/" className="btn btn-ghost">← Back to Your Studio</Link>
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
    // Planning/edit failures retry from where they stopped (request-edit:
    // never re-analyses, never falls back to a different engine); an analysis
    // failure retries analysis itself.
    const failedKind = state.job?.kind
    const retryPath = failedKind === 'analysis' ? 'request-analysis' : 'request-edit'
    return (
      <div className="wrap" style={{ paddingTop: 48, paddingBottom: 80 }}>
        <div className="proc-error-card">
          <div className="proc-error-icon">✕</div>
          <h2 className="proc-error-title">We couldn't finish the edit.</h2>
          <p className="proc-error-sub">
            Your footage and analysis are safe — this step can be retried.
          </p>
          {state.job.error_message && (
            <p className="proc-error-detail">{state.job.error_message}</p>
          )}
          {retryError && (
            <p className="proc-error-detail" role="alert" aria-live="assertive">{retryError}</p>
          )}
          <div className="proc-error-actions">
            <button className="btn btn-primary" disabled={retrying} aria-busy={retrying}
              onClick={async () => {
                if (retrying) return
                setRetrying(true); setRetryError('')
                try {
                  const r = await fetch(`${RENDER_API}/projects/${projectId}/${retryPath}`, {
                    method: 'POST',
                    headers: { Authorization: `Bearer ${session.access_token}` },
                  })
                  if (!r.ok) {
                    const d = await r.json().catch(() => ({}))
                    throw new Error(d.detail || `Request failed (${r.status})`)
                  }
                  if (onRetry) await onRetry()
                } catch (err) {
                  setRetryError(err.message || 'Could not retry. Please try again.')
                } finally {
                  setRetrying(false)
                }
              }}>
              {retrying ? 'Retrying…' : 'Try again'}
            </button>
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

// Output metadata comes ONLY from real pipeline_jobs.artifacts.output values;
// anything missing is omitted (never fabricated).
export function exportMeta(art = {}) {
  return [
    (art.width && art.height) ? `${art.width}×${art.height}` : null,
    Number.isFinite(art.duration) ? `${art.duration.toFixed(1)}s` : null,
    Number.isFinite(art.size_bytes) ? `${(art.size_bytes / 1048576).toFixed(2)} MB` : null,
  ].filter(Boolean).join(' · ')
}

// Sign on demand through the authorized backend (fresh short-lived URL each click,
// so an expired URL is never reused) and trigger the download. Never exposes the
// raw storage path.
async function downloadExport(projectId, job, session, projectName) {
  const { url } = await editorApi(
    `/projects/${projectId}/editor/renders/${job.id}/sign`, session,
    { method: 'POST', body: JSON.stringify({}) })
  const a = window.document.createElement('a')
  a.href = url
  a.download = `stromation-${(projectName || 'edit').replace(/[^\w.-]+/g, '_')}.mp4`
  a.rel = 'noopener'
  window.document.body.appendChild(a); a.click(); a.remove()
}

function ExportView({ job, project, projectId, session, onRetry }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const status = job?.status
  const meta = exportMeta(job?.artifacts)

  async function onDownload() {
    setBusy(true); setError('')
    try { await downloadExport(projectId, job, session, project.name) }
    catch (e) { setError(e.message || 'Could not prepare the download.') }
    finally { setBusy(false) }
  }
  async function onRetryExport() {
    setBusy(true); setError('')
    try {
      await editorApi(`/projects/${projectId}/editor/renders/${job.id}/retry`, session,
        { method: 'POST', body: JSON.stringify({}) })
      onRetry?.()
    } catch (e) { setError(e.message || 'Could not retry the export.') }
    finally { setBusy(false) }
  }

  if (status === 'completed') {
    return (
      <div className="export-success">
        <svg className="export-check" viewBox="0 0 52 52" fill="none">
          <circle cx="26" cy="26" r="25" stroke="currentColor" strokeWidth="2" opacity="0.2" />
          <path className="export-check-path" d="M14 27l8 8 16-16" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <h1 className="export-title">Your video is ready.</h1>
        {meta && <p className="export-meta">{meta}</p>}
        <div className="export-actions">
          <button className="btn btn-primary btn-lg" onClick={onDownload} disabled={busy}>
            {busy ? 'Preparing…' : '↓ Download MP4'}
          </button>
          <Link to="/" className="btn btn-ghost">← Back to studio</Link>
        </div>
        {error && <div className="err" role="alert">{error}</div>}
        <p className="export-feedback">
          <a href="mailto:hello@stromation.com">Send feedback on this edit</a>
        </p>
      </div>
    )
  }
  if (status === 'failed') {
    return (
      <div className="export-success">
        <h1 className="export-title">Export didn't finish.</h1>
        {job.error_message && <p className="export-meta">{job.error_message}</p>}
        <div className="export-actions">
          <button className="btn btn-primary btn-lg" onClick={onRetryExport} disabled={busy}>
            {busy ? 'Retrying…' : 'Retry export'}
          </button>
          <Link to={`/project/${projectId}`} className="btn btn-ghost">Back to project</Link>
        </div>
        {error && <div className="err" role="alert">{error}</div>}
      </div>
    )
  }
  // queued / processing
  return (
    <div className="export-success">
      <h1 className="export-title">Rendering your video…</h1>
      <p className="export-meta">{status === 'queued' ? 'Queued' : 'Rendering'}{job?.progress ? ` · ${job.progress}%` : ''}</p>
      <div className="progress" style={{ maxWidth: 280, margin: '16px auto' }}>
        <div style={{ width: `${job?.progress || 0}%` }} />
      </div>
      <p className="small">This updates automatically.</p>
    </div>
  )
}

function JobRow({ job, session, projectId, onRetry }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const meta = exportMeta(job.artifacts)
  async function onDownload() {
    setBusy(true); setError('')
    try { await downloadExport(projectId, job, session, `${job.id.slice(0, 8)}`) }
    catch (e) { setError(e.message || 'Could not prepare the download.') }
    finally { setBusy(false) }
  }
  async function onRetryExport() {
    setBusy(true); setError('')
    try {
      await editorApi(`/projects/${projectId}/editor/renders/${job.id}/retry`, session,
        { method: 'POST', body: JSON.stringify({}) })
      onRetry?.()
    } catch (e) { setError(e.message || 'Could not retry the export.') }
    finally { setBusy(false) }
  }
  return (
    <div className="list-item" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
      <div className="row">
        <span className="mono small">{job.id.slice(0, 8)}</span>
        <span className={`badge ${job.status}`}>{job.status}</span>
        {['queued', 'processing'].includes(job.status) && (
          <div className="progress" style={{ flex: 1 }}><div style={{ width: `${job.progress || 0}%` }} /></div>
        )}
        <span className="spacer" />
        {job.status === 'completed' && (
          <button className="btn btn-primary btn-sm" onClick={onDownload} disabled={busy}>
            {busy ? 'Preparing…' : 'Download MP4'}
          </button>
        )}
        {job.status === 'failed' && (
          <button className="btn btn-ghost btn-sm" onClick={onRetryExport} disabled={busy}>Retry</button>
        )}
      </div>
      {job.status === 'completed' && meta && (
        <p className="small" style={{ marginTop: 6 }}>{meta}</p>
      )}
      {job.status === 'failed' && job.error_message && (
        <p className="small" style={{ color: '#fca5a5', marginTop: 8 }}>{job.error_message}</p>
      )}
      {error && <div className="err">{error}</div>}
    </div>
  )
}
