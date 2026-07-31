import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAuth } from '../App'
import { RENDER_API, supabase, uploadWithProgress } from '../lib/supabase'

const MAX_UPLOAD_BYTES = 50 * 1024 * 1024 // 50 MB — current Supabase plan cap
const ACCEPTED = ['video/mp4', 'video/quicktime']

export default function Project() {
  const { id } = useParams()
  const session = useAuth()
  const [project, setProject] = useState(undefined)
  const [assets, setAssets] = useState([])
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const { data: p, error: pe } = await supabase.from('projects')
      .select('*').eq('id', id).maybeSingle()
    if (pe) { setError(pe.message); return }
    setProject(p) // null => not found or not yours (RLS)
    if (!p) return
    const { data: a } = await supabase.from('media_assets')
      .select('*').eq('project_id', id).order('created_at')
    setAssets(a || [])
  }, [id])
  useEffect(() => { load() }, [load])

  if (project === undefined) return <div className="wrap"><p className="sub">Loading…</p></div>
  if (project === null)
    return (
      <div className="wrap">
        <div className="err">Project not found, or you do not have access to it.</div>
        <Link to="/">← Back to projects</Link>
      </div>
    )

  return (
    <div className="wrap">
      <p className="small"><Link to="/">← Projects</Link></p>
      <div className="row">
        <h1 style={{ flex: 1 }}>{project.name}</h1>
        <span className={`badge ${project.status}`}>{project.status}</span>
      </div>
      {error && <div className="err" role="alert">{error}</div>}
      <div className="grid" style={{ marginTop: 20 }}>
        <Uploader projectId={id} session={session} onDone={load} />
        <AssetList assets={assets} />
        {assets.length > 0 && <TimelinePanel projectId={id} session={session} assets={assets} />}
      </div>
    </div>
  )
}

/* ---------------- upload ---------------- */
function Uploader({ projectId, session, onDone }) {
  const [progress, setProgress] = useState(null)
  const [error, setError] = useState('')
  const inputRef = useRef()

  async function handleFile(file) {
    setError('')
    if (!file) return
    if (!ACCEPTED.includes(file.type) && !/\.(mp4|mov)$/i.test(file.name)) {
      setError('Please choose an MP4 or MOV file.'); return
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(`File is ${(file.size / 1048576).toFixed(1)} MB — the current limit is 50 MB per file (storage plan cap).`)
      return
    }
    const assetId = crypto.randomUUID()
    const uid = session.user.id
    const safeName = file.name.replace(/[^\w.\-]+/g, '_').slice(-120)
    const path = `users/${uid}/projects/${projectId}/raw/${assetId}/${safeName}`
    try {
      setProgress(0)
      await uploadWithProgress({
        bucket: 'raw-footage', path, file,
        accessToken: session.access_token,
        onProgress: setProgress,
      })
      const duration = await probeDuration(file)
      const { error: ie } = await supabase.from('media_assets').insert({
        id: assetId, project_id: projectId, user_id: uid,
        filename: file.name, storage_path: path,
        mime_type: file.type || 'video/mp4', size_bytes: file.size,
        duration_seconds: duration,
      })
      if (ie) throw new Error(`upload stored but record failed: ${ie.message}`)
      await supabase.from('projects').update({ status: 'ready' }).eq('id', projectId)
      onDone()
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setProgress(null)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <div className="card">
      <h2>Upload footage</h2>
      <p className="small">MP4 or MOV, up to 50 MB per file on the current storage plan. Files are stored privately and only visible to you.</p>
      {error && <div className="err" role="alert">{error}</div>}
      {progress === null ? (
        <input ref={inputRef} type="file" accept="video/mp4,video/quicktime,.mp4,.mov"
          style={{ marginTop: 12 }} aria-label="Choose video file"
          onChange={(e) => handleFile(e.target.files?.[0])} />
      ) : (
        <div style={{ marginTop: 14 }}>
          <div className="progress"><div style={{ width: `${progress}%` }} /></div>
          <p className="small" style={{ marginTop: 6 }}>Uploading… {progress}%</p>
        </div>
      )}
    </div>
  )
}

function probeDuration(file) {
  return new Promise((resolve) => {
    const v = document.createElement('video')
    v.preload = 'metadata'
    v.onloadedmetadata = () => { URL.revokeObjectURL(v.src); resolve(v.duration || null) }
    v.onerror = () => resolve(null)
    v.src = URL.createObjectURL(file)
  })
}

function AssetList({ assets }) {
  if (!assets.length) return <p className="sub">No footage uploaded yet.</p>
  return (
    <div className="card">
      <h2>Footage</h2>
      <div className="grid">
        {assets.map((a) => (
          <div key={a.id} className="list-item" style={{ background: 'var(--bg-3)' }}>
            <div style={{ flex: 1 }}>
              <span style={{ fontWeight: 600 }}>{a.filename}</span>
              <div className="small mono">
                {(a.size_bytes / 1048576).toFixed(1)} MB
                {a.duration_seconds ? ` · ${a.duration_seconds.toFixed(1)}s` : ''}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---------------- timeline + render ---------------- */
function TimelinePanel({ projectId, session, assets }) {
  const asset = assets[0]
  const maxEnd = asset.duration_seconds ? Math.floor(asset.duration_seconds * 10) / 10 : 30
  const [title, setTitle] = useState('MY FIRST STROMATION EDIT')
  const [sourceStart, setSourceStart] = useState(0)
  const [sourceEnd, setSourceEnd] = useState(Math.min(10, maxEnd))
  const [savedTimeline, setSavedTimeline] = useState(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const loadLatest = useCallback(async () => {
    const { data } = await supabase.from('timelines')
      .select('*').eq('project_id', projectId)
      .order('created_at', { ascending: false }).limit(1)
    const t = data?.[0]
    if (t) {
      setSavedTimeline(t)
      const tl = typeof t.timeline_json === 'string' ? JSON.parse(t.timeline_json) : t.timeline_json
      const v = tl.tracks?.find((x) => x.type === 'video')?.clips?.[0]
      const txt = tl.tracks?.find((x) => x.type === 'text')?.clips?.[0]
      if (v) { setSourceStart(v.sourceStart); setSourceEnd(v.sourceEnd) }
      if (txt) setTitle(txt.text)
    }
  }, [projectId])
  useEffect(() => { loadLatest() }, [loadLatest])

  function buildTimeline() {
    const titleDur = title.trim() ? 2 : 0
    const clipDur = sourceEnd - sourceStart
    return {
      version: 1, width: 1920, height: 1080, fps: 30,
      duration: titleDur + clipDur,
      tracks: [
        {
          id: 'video-1', type: 'video',
          clips: [{ id: 'clip-1', assetId: asset.id, sourceStart: Number(sourceStart),
            sourceEnd: Number(sourceEnd), timelineStart: titleDur,
            timelineEnd: titleDur + clipDur, volume: 1 }],
        },
        ...(title.trim() ? [{
          id: 'text-1', type: 'text',
          clips: [{ id: 'title-1', text: title.trim(), timelineStart: 0,
            timelineEnd: titleDur, fontSize: 72, position: 'center' }],
        }] : []),
      ],
    }
  }

  async function saveTimeline() {
    setError(''); setNotice(''); setBusy(true)
    try {
      const s = Number(sourceStart), e = Number(sourceEnd)
      if (Number.isNaN(s) || s < 0) throw new Error('Source start must be 0 or greater.')
      if (Number.isNaN(e) || e <= s) throw new Error('Source end must be greater than source start.')
      if (asset.duration_seconds && e > asset.duration_seconds + 0.05)
        throw new Error(`Source end exceeds the footage length (${asset.duration_seconds.toFixed(1)}s).`)
      const version = (savedTimeline?.version || 0) + 1
      const { data, error: se } = await supabase.from('timelines').insert({
        project_id: projectId, user_id: session.user.id,
        version, timeline_json: buildTimeline(),
      }).select().single()
      if (se) throw new Error(se.message)
      setSavedTimeline(data)
      setNotice(`Timeline v${version} saved.`)
    } catch (err) {
      setError(err.message || String(err))
    } finally { setBusy(false) }
  }

  return (
    <div className="card">
      <h2>Timeline</h2>
      <p className="small">
        v1 renderer: an optional 2-second title card, then your selected footage range,
        rendered to 1920×1080 H.264/AAC. Complex multi-clip timelines come later.
      </p>
      {error && <div className="err" role="alert">{error}</div>}
      {notice && <div className="ok" role="status">{notice}</div>}
      <label htmlFor="title">Title card text (blank = no title card)</label>
      <input id="title" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
      <div className="row">
        <div style={{ flex: 1 }}>
          <label htmlFor="ss">Source start (s)</label>
          <input id="ss" type="number" min="0" step="0.1" value={sourceStart}
            onChange={(e) => setSourceStart(e.target.value)} />
        </div>
        <div style={{ flex: 1 }}>
          <label htmlFor="se">Source end (s)</label>
          <input id="se" type="number" min="0.1" step="0.1" value={sourceEnd}
            onChange={(e) => setSourceEnd(e.target.value)} />
        </div>
      </div>
      <p className="small" style={{ marginTop: 8 }}>
        Output duration: {(title.trim() ? 2 : 0) + Math.max(0, Number(sourceEnd) - Number(sourceStart) || 0)}s
        {savedTimeline && <> · latest saved: v{savedTimeline.version}</>}
      </p>
      <div className="row" style={{ marginTop: 14 }}>
        <button className="btn btn-primary" onClick={saveTimeline} disabled={busy}>Save timeline</button>
        <button className="btn btn-ghost" onClick={loadLatest} disabled={busy}>Restore last saved</button>
      </div>
      {savedTimeline && <RenderPanel projectId={projectId} session={session} timeline={savedTimeline} />}
    </div>
  )
}

function RenderPanel({ projectId, session, timeline }) {
  const [jobs, setJobs] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const pollRef = useRef()

  const loadJobs = useCallback(async () => {
    const { data } = await supabase.from('render_jobs')
      .select('*').eq('project_id', projectId)
      .order('created_at', { ascending: false }).limit(5)
    setJobs(data || [])
    return data || []
  }, [projectId])

  useEffect(() => {
    loadJobs()
    pollRef.current = setInterval(async () => {
      const js = await loadJobs()
      if (!js.some((j) => ['queued', 'processing'].includes(j.status)))
        clearInterval(pollRef.current)
    }, 2500)
    return () => clearInterval(pollRef.current)
  }, [loadJobs])

  async function startRender(existingJobId = null) {
    setError(''); setBusy(true)
    try {
      let jobId = existingJobId
      if (!jobId) {
        const { data, error: je } = await supabase.from('render_jobs').insert({
          project_id: projectId, timeline_id: timeline.id,
          user_id: session.user.id, status: 'queued',
        }).select().single()
        if (je) throw new Error(je.message)
        jobId = data.id
      }
      const r = await fetch(`${RENDER_API}/render`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({ job_id: jobId }),
      })
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}))
        throw new Error(detail.detail || `render request failed (${r.status})`)
      }
      await supabase.from('projects').update({ status: 'rendering' }).eq('id', projectId)
      await loadJobs()
      clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        const js = await loadJobs()
        if (!js.some((j) => ['queued', 'processing'].includes(j.status))) {
          clearInterval(pollRef.current)
          await supabase.from('projects').update({ status: 'complete' }).eq('id', projectId)
        }
      }, 2500)
    } catch (err) {
      setError(err.message || String(err))
    } finally { setBusy(false) }
  }

  return (
    <div style={{ marginTop: 24, borderTop: '1px solid var(--border)', paddingTop: 18 }}>
      <div className="row">
        <h2 style={{ margin: 0, flex: 1 }}>Renders</h2>
        <button className="btn btn-primary" onClick={() => startRender()} disabled={busy}>
          {busy ? 'Starting…' : `Render timeline v${timeline.version}`}
        </button>
      </div>
      <p className="small">Rendering runs on the Stromation render service (FFmpeg). Status updates live below.</p>
      {error && <div className="err" role="alert">{error}</div>}
      <div className="grid" style={{ marginTop: 12 }}>
        {jobs.map((j) => <JobRow key={j.id} job={j} onRetry={() => startRender(j.id)} />)}
      </div>
    </div>
  )
}

function JobRow({ job, onRetry }) {
  const [signedUrl, setSignedUrl] = useState(null)
  const [urlError, setUrlError] = useState('')

  async function getUrl() {
    setUrlError('')
    const { data, error } = await supabase.storage.from('exports')
      .createSignedUrl(job.output_storage_path, 3600)
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
        <span className="spacer" style={{ flex: 1 }} />
        {job.status === 'failed' && <button className="btn btn-ghost" onClick={onRetry}>Retry</button>}
        {job.status === 'completed' && !signedUrl && (
          <button className="btn btn-primary" onClick={getUrl}>Preview + download</button>
        )}
      </div>
      {job.status === 'failed' && job.error_message && (
        <p className="small" style={{ color: '#fca5a5', marginTop: 8 }}>{job.error_message}</p>
      )}
      {job.status === 'completed' && (
        <p className="small" style={{ marginTop: 6 }}>
          {job.output_width}×{job.output_height} · {job.output_duration_seconds?.toFixed(1)}s ·{' '}
          {(job.output_size_bytes / 1048576).toFixed(2)} MB
        </p>
      )}
      {urlError && <div className="err">{urlError}</div>}
      {signedUrl && (
        <div style={{ marginTop: 12 }}>
          <video className="preview" src={signedUrl} controls />
          <p style={{ marginTop: 8 }}>
            <a className="btn btn-primary" href={signedUrl} download={`stromation-${job.id.slice(0, 8)}.mp4`}>
              Download MP4
            </a>
            <span className="small" style={{ marginLeft: 10 }}>link valid for 1 hour</span>
          </p>
        </div>
      )}
    </div>
  )
}
