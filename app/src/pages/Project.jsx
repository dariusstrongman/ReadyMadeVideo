import { useCallback, useEffect, useRef, useState } from 'react'
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
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadPct, setUploadPct] = useState(0)
  const [dragOver, setDragOver] = useState(false)
  const [pendingFiles, setPendingFiles] = useState([])
  const fileInputRef = useRef(null)
  const pollRef = useRef(null)
  const videoRef = useRef(null)

  const load = useCallback(async () => {
    const [{ data: proj }, { data: ast }, { data: cands }, { data: js }] = await Promise.all([
      supabase.from('projects').select('*').eq('id', projectId).single(),
      supabase.from('media_assets').select('*').eq('project_id', projectId).order('created_at'),
      supabase.from('edit_candidates').select('*').eq('project_id', projectId).order('overall_score', { ascending: false }),
      supabase.from('render_jobs').select('*').eq('project_id', projectId).order('created_at', { ascending: false }),
    ])
    if (proj) setProject(proj)
    if (ast) setAssets(ast)
    if (cands) setCandidates(cands)
    if (js) setJobs(js)
  }, [projectId])

  useEffect(() => { load() }, [load])

  // Poll while analyzing
  useEffect(() => {
    if (!project) return
    const active = ['analyzing', 'uploading', 'ready'].includes(project.status)
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
      const { error: upErr } = await supabase.storage.from('raw-footage').upload(key, file, {
        cacheControl: '3600', upsert: false,
        onUploadProgress: ({ loaded, total: t }) => {
          const fileProgress = loaded / t
          setUploadPct(Math.round(((done + fileProgress) / total) * 100))
        },
      })
      if (upErr) { setError(upErr.message); setUploading(false); return }
      await supabase.from('media_assets').insert({
        project_id: projectId, user_id: uid,
        storage_path: key, original_filename: file.name,
        file_size_bytes: file.size, mime_type: file.type,
      })
      done++
      setUploadPct(Math.round((done / total) * 100))
    }
    await supabase.from('projects').update({ status: 'ready' }).eq('id', projectId)
    setPendingFiles([])
    setUploading(false)
    await load()
  }

  if (!project) return <div className="center"><p className="sub">Loading…</p></div>

  const status = project.status
  const step = status === 'draft' || status === 'uploading' ? 1
    : ['ready', 'analyzing'].includes(status) ? 2
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
  if (['ready', 'analyzing'].includes(status)) {
    const stage = ANALYSIS_STAGES[status] || ANALYSIS_STAGES.analyzing
    return (
      <>
        <Breadcrumb projectName={project.name} projectId={projectId} />
        <StepIndicator step={2} />
        <div className="wrap" style={{ paddingTop: 48, paddingBottom: 80 }}>
          <div className="processing-card">
            <div className="processing-ring" />
            <h2 className="processing-title">{stage.title}</h2>
            <p className="processing-sub">{stage.sub}</p>
            <p className="processing-wait">
              This usually takes 3–5 minutes.<br />
              We'll show a notification when your edit is ready.
            </p>
          </div>
          {assets.length > 0 && (
            <div style={{ marginTop: 32 }}>
              <span className="section-label">Your footage</span>
              <div className="clip-peek">
                {assets.map((a, i) => (
                  <div key={a.id} className="clip-peek-item" style={{ animationDelay: `${i * 80}ms` }}>
                    {a.original_filename?.split('.')[0] || 'clip'}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
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
                  <span style={{ flex: 1, fontSize: '0.85rem' }}>{a.original_filename}</span>
                  <span className="small">{a.file_size_bytes ? fmtSize(a.file_size_bytes) : ''}</span>
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
