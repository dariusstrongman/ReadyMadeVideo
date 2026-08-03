import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../App'
import { supabase } from '../lib/supabase'
import { RENDER_API } from '../lib/config'
import { applyLocal, reorderArguments, trimArguments, track, makeOperation } from '../lib/editor'
import iconUrl from '../assets/icon.svg'

const COLORS = { picture: '#00d4ff', audio: '#22c55e', music: '#7c3aed', captions: '#f59e0b', graphics: '#ec4899' }

export default function Editor() {
  const session = useAuth()
  const { id: projectId, documentId } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState(null)
  const [document, setDocument] = useState(null)
  const [revisionNote, setRevisionNote] = useState('')
  const [selected, setSelected] = useState(null)
  const [zoom, setZoom] = useState(80)
  const [snap, setSnap] = useState(true)
  const [playhead, setPlayhead] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [aiInput, setAiInput] = useState('')
  const [aiHistory, setAiHistory] = useState([])
  const [aiLoading, setAiLoading] = useState(false)
  const [renderJob, setRenderJob] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saveStatus, setSaveStatus] = useState('Saved')
  const videoRef = useRef(null)
  const pollRef = useRef(null)

  const load = useCallback(async () => {
    const [{ data: proj }, { data: doc }] = await Promise.all([
      supabase.from('projects').select('id,name,status').eq('id', projectId).single(),
      supabase.from('edit_candidates').select('*').eq('id', documentId).single(),
    ])
    if (proj) setProject(proj)
    if (doc) setDocument(doc)
  }, [projectId, documentId])

  useEffect(() => { load() }, [load])

  // Sync playhead to video currentTime
  useEffect(() => {
    const vid = videoRef.current
    if (!vid) return
    function onTimeUpdate() { setPlayhead(vid.currentTime) }
    function onPlay() { setPlaying(true) }
    function onPause() { setPlaying(false) }
    vid.addEventListener('timeupdate', onTimeUpdate)
    vid.addEventListener('play', onPlay)
    vid.addEventListener('pause', onPause)
    return () => {
      vid.removeEventListener('timeupdate', onTimeUpdate)
      vid.removeEventListener('play', onPlay)
      vid.removeEventListener('pause', onPause)
    }
  }, [document])

  async function apply(op, itemId, args) {
    if (!document) return
    setSaveStatus('Saving…')
    const operation = makeOperation(op, itemId, 0, args)
    const next = applyLocal(document.timeline_json, operation)
    const { error } = await supabase.from('edit_candidates')
      .update({ timeline_json: next, revision_note: revisionNote || null })
      .eq('id', documentId)
    if (error) { setError(error.message); setSaveStatus('Error'); return }
    setDocument(d => ({ ...d, timeline_json: next }))
    setSaveStatus('Saved')
  }

  async function sendAiMessage() {
    if (!aiInput.trim() || aiLoading) return
    const msg = aiInput.trim()
    setAiInput('')
    setAiHistory(h => [...h, { role: 'user', text: msg }])
    setAiLoading(true)
    // Prototype: AI responses are simulated
    await new Promise(r => setTimeout(r, 1200))
    setAiHistory(h => [...h, {
      role: 'ai',
      text: `[PROTOTYPE] I've noted your request: "${msg}". AI-driven edits are not yet implemented. Use the timeline controls to make manual changes.`
    }])
    setAiLoading(false)
  }

  async function startRender() {
    setBusy(true); setError('')
    try {
      const { data: job, error: je } = await supabase.from('render_jobs').insert({
        project_id: projectId, timeline_id: documentId,
        user_id: session.user.id, status: 'queued',
      }).select().single()
      if (je) throw new Error(je.message)
      const r = await fetch(`${RENDER_API}/render`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session.access_token}` },
        body: JSON.stringify({ job_id: job.id }),
      })
      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || `render failed (${r.status})`) }
      setRenderJob(job)
      await supabase.from('projects').update({ status: 'rendering' }).eq('id', projectId)
      pollRef.current = setInterval(async () => {
        const { data: j } = await supabase.from('render_jobs').select('*').eq('id', job.id).single()
        if (j) setRenderJob(j)
        if (!['queued', 'processing'].includes(j?.status)) {
          clearInterval(pollRef.current)
          if (j?.status === 'completed') {
            await supabase.from('projects').update({ status: 'completed' }).eq('id', projectId)
            navigate(`/project/${projectId}`)
          }
        }
      }, 2500)
    } catch (err) {
      setError(err.message || String(err))
    } finally { setBusy(false) }
  }

  useEffect(() => () => clearInterval(pollRef.current), [])

  if (!document || !project) return <div className="center"><p className="sub">Loading editor…</p></div>

  const doc = document.timeline_json || { duration: 0, tracks: [] }

  return (
    <div className="editor-page">
      {/* Top bar */}
      <div className="editor-topbar">
        <a href="https://www.stromation.com" className="nav-logo" style={{ flexShrink: 0 }}>
          <img src={iconUrl} alt="" aria-hidden="true" />
          STROMATION
        </a>
        <span style={{ color: 'var(--border-2)' }}>/</span>
        <Link to={`/project/${projectId}`} style={{ color: 'var(--text-3)', fontSize: '0.78rem' }}>{project.name}</Link>
        <span style={{ color: 'var(--border-2)' }}>/</span>
        <span className="project-name">Studio</span>
        <span className="spacer" />
        <span className="save-status">{saveStatus}</span>
        <button className="btn btn-primary btn-sm" onClick={startRender} disabled={busy}>
          {busy ? 'Starting…' : 'Export video'}
        </button>
      </div>

      {/* Main editor shell */}
      <div className="edit-shell">
        {/* Command bar */}
        <div className="edit-commandbar">
          <div>
            <strong>{document.candidate_key || 'Edit'}</strong>
            <span className="edit-history">
              <button onClick={() => {}} disabled title="Undo">↩</button>
              <button onClick={() => {}} disabled title="Redo">↪</button>
            </span>
          </div>
          <div>
            {error && <span className="edit-alert">{error}</span>}
          </div>
        </div>

        {/* Three-panel stage */}
        <div className="edit-stage">
          {/* Clip bin */}
          <aside className="edit-bin">
            <span className="edit-eyebrow">Footage</span>
            {doc.tracks?.find(t => t.type === 'picture')?.items?.map((item, i) => {
              const dur = ((item.timelineEnd ?? item.endSeconds ?? 0) - (item.timelineStart ?? item.startSeconds ?? 0)).toFixed(1)
              const score = item.qualityScore || item.score || null
              const qualityClass = score >= 80 ? 'good' : score >= 50 ? 'ok' : score ? 'poor' : ''
              return (
                <div key={item.id} className={`clip-bin-item ${selected?.id === item.id ? 'active' : ''}`}
                  onClick={() => setSelected({ id: item.id, lane: 'picture' })}>
                  <div className="clip-bin-thumb" style={{ background: `linear-gradient(135deg, #1c1c28 0%, #252535 100%)`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.5rem', color: 'var(--text-3)', fontFamily: 'var(--mono)' }}>
                    {i + 1}
                  </div>
                  <div className="clip-bin-info">
                    <p className="clip-bin-name">{item.text || item.displayText || item.kind || item.id}</p>
                    <p className="clip-bin-meta">{dur}s</p>
                  </div>
                  {score && (
                    <div className="clip-quality">
                      <div className={`clip-quality-dot ${qualityClass}`} />
                      <div className={`clip-quality-dot ${score >= 70 ? qualityClass : ''}`} />
                      <div className={`clip-quality-dot ${score >= 85 ? qualityClass : ''}`} />
                    </div>
                  )}
                </div>
              )
            })}
            {doc.tracks?.find(t => t.type === 'picture')?.items?.length === 0 && (
              <p style={{ fontSize: '0.75rem', color: 'var(--text-3)' }}>No clips yet.</p>
            )}
            <hr />
            <span className="edit-eyebrow">AI Edit</span>
            {document && (
              <div className="candidate-mini">
                <b>{document.candidate_key}</b>
                <br />{document.publishability_label || ''}
                {document.overall_score ? ` · ${Math.round(document.overall_score)}` : ''}
              </div>
            )}
          </aside>

          {/* Monitor */}
          <div className="edit-monitor">
            <div className="monitor-frame">
              {document.preview_url
                ? <video ref={videoRef} src={document.preview_url} />
                : <div className="monitor-empty">No preview yet</div>
              }
            </div>
            <div className="monitor-controls">
              <button className="monitor-btn" onClick={() => { if (videoRef.current) { videoRef.current.currentTime = Math.max(0, videoRef.current.currentTime - 5) } }}>◀◀</button>
              <button className="monitor-btn" onClick={() => { const v = videoRef.current; if (v) playing ? v.pause() : v.play() }}>
                {playing ? '⏸' : '▶'}
              </button>
              <button className="monitor-btn" onClick={() => { if (videoRef.current) { videoRef.current.currentTime = Math.min(doc.duration, videoRef.current.currentTime + 5) } }}>▶▶</button>
            </div>
            <div className="time-readout">
              <code>{formatTime(playhead)}</code>/ {formatTime(doc.duration)}
            </div>
            <div style={{ width: '100%', maxWidth: 280 }}>
              <label style={{ margin: 0, fontSize: '0.7rem' }}>
                Revision note
                <input value={revisionNote} onChange={e => setRevisionNote(e.target.value)}
                  placeholder="Add a note about this version…" style={{ marginTop: 4, fontSize: '0.78rem', padding: '6px 10px' }} />
              </label>
            </div>
          </div>

          {/* AI Studio panel */}
          <div className="ai-studio-panel">
            <div className="ai-studio-header">
              <span className="ai-studio-label">AI Studio</span>
              <span className="ai-prototype-badge">PROTOTYPE</span>
            </div>
            <div className="ai-history">
              {aiHistory.length === 0 && (
                <p style={{ fontSize: '0.75rem', color: 'var(--text-3)', padding: '8px 0' }}>
                  Describe a change and the AI will suggest edits.<br />
                  <span style={{ color: 'var(--violet)', opacity: 0.7 }}>AI responses are simulated in this prototype.</span>
                </p>
              )}
              {aiHistory.map((m, i) => (
                <div key={i} className={`ai-msg ${m.role === 'user' ? 'ai-msg-user' : 'ai-msg-ai'}`}>
                  {m.text}
                </div>
              ))}
              {aiLoading && <div className="ai-msg ai-msg-ai" style={{ opacity: 0.6 }}>Thinking…</div>}
            </div>
            <div className="ai-input-row">
              <textarea
                className="ai-input"
                rows={2}
                value={aiInput}
                onChange={e => setAiInput(e.target.value)}
                placeholder="Describe a change — e.g. 'Make the opening faster'"
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAiMessage() } }}
              />
              <button className="ai-send-btn" onClick={sendAiMessage} disabled={aiLoading || !aiInput.trim()}>
                →
              </button>
            </div>
          </div>
        </div>

        {/* Timeline */}
        <div className="edit-timeline-section">
          <div className="timeline-tools">
            <label>Zoom
              <input type="range" min="20" max="200" value={zoom} onChange={e => setZoom(Number(e.target.value))} />
            </label>
            <label>
              <input type="checkbox" checked={snap} onChange={e => setSnap(e.target.checked)} />
              Snap to 0.5s
            </label>
            <span style={{ flex: 1 }} />
            <span>{formatTime(playhead)} / {formatTime(doc.duration)}</span>
          </div>
          <Timeline document={doc} zoom={zoom} playhead={playhead} setPlayhead={p => {
            setPlayhead(p)
            if (videoRef.current) videoRef.current.currentTime = p
          }} snap={snap} selected={selected} setSelected={setSelected} apply={apply} />
        </div>

        {/* Inspector */}
        <Inspector document={doc} selected={selected} apply={apply} />
      </div>

      {/* Render toast */}
      {renderJob && <RenderStatus job={renderJob} retry={startRender}
        download={async () => {
          const { data } = await supabase.storage.from('exports').createSignedUrl(renderJob.output_storage_path, 3600)
          if (data) window.open(data.signedUrl)
        }} />}
    </div>
  )
}

function Timeline({ document, zoom, playhead, setPlayhead, snap, selected, setSelected, apply }) {
  const duration = document.duration || 0
  const width = Math.max(760, duration * zoom)
  const ruler = Array.from({ length: Math.ceil(duration) + 1 }, (_, i) => i)
  return (
    <div className="timeline-scroll">
      <div className="timeline-canvas" style={{ width }}
        onClick={e => {
          const box = e.currentTarget.getBoundingClientRect()
          let v = (e.clientX - box.left) / zoom
          if (snap) v = Math.round(v * 2) / 2
          setPlayhead(Math.max(0, Math.min(duration, v)))
        }}>
        <div className="time-ruler">
          {ruler.map(tick => <span key={tick} style={{ left: tick * zoom }}>{tick}s</span>)}
        </div>
        <div className="playhead" style={{ left: playhead * zoom }} />
        {document.tracks?.map(lane => (
          <div className="timeline-lane" key={lane.type}>
            <b>{lane.type}</b>
            <div className="lane-items">
              {lane.items?.map(item => {
                const start = item.timelineStart ?? item.startSeconds ?? 0
                const end = item.timelineEnd ?? item.endSeconds ?? duration
                const itemWidth = Math.max(28, (end - start) * zoom)
                return (
                  <button key={item.id}
                    className={selected?.id === item.id ? 'clip selected' : 'clip'}
                    style={{ left: start * zoom, width: itemWidth, '--clip-color': COLORS[lane.type] || '#888' }}
                    onClick={e => { e.stopPropagation(); setSelected({ id: item.id, lane: lane.type }) }}>
                    <span>{item.text || item.displayText || item.kind || item.id}</span>
                    {lane.type === 'picture' && <i>{(end - start).toFixed(1)}s</i>}
                  </button>
                )
              })}
            </div>
          </div>
        ))}
        {selected?.lane === 'picture' && (
          <div className="clip-actions">
            <button onClick={() => apply('reorder_clip', selected.id, reorderArguments(document, selected.id, -1))}>← Move</button>
            <button onClick={() => apply('reorder_clip', selected.id, reorderArguments(document, selected.id, 1))}>Move →</button>
            <button onClick={() => {
              const clip = track(document, 'picture')?.items?.find(i => i.id === selected.id)
              if (clip) apply('split_clip', selected.id, { sourceTime: clip.sourceStart + (clip.sourceEnd - clip.sourceStart) / 2 })
            }}>Split</button>
            <button onClick={() => apply('delete_clip', selected.id)}>Delete</button>
          </div>
        )}
      </div>
    </div>
  )
}

function Inspector({ document, selected, apply }) {
  if (!selected) return (
    <aside className="edit-inspector">
      <span className="edit-eyebrow">Inspector</span>
      <p>Select a clip to inspect.</p>
    </aside>
  )
  const current = track(document, selected.lane)?.items?.find(i => i.id === selected.id)
  if (!current) return (
    <aside className="edit-inspector">
      <span className="edit-eyebrow">Inspector</span>
      <p>Clip no longer exists.</p>
    </aside>
  )
  return (
    <aside className="edit-inspector">
      <span className="edit-eyebrow">Inspector · {selected.lane}</span>
      <h2>{selected.id}</h2>
      {selected.lane === 'picture' && (
        <>
          <label>In point
            <input key={`in-${current.sourceStart}`} type="number" step=".1" defaultValue={current.sourceStart}
              onBlur={e => apply('trim_clip', selected.id, trimArguments(document, selected.id, 'start', Number(e.target.value)))} />
          </label>
          <label>Out point
            <input key={`out-${current.sourceEnd}`} type="number" step=".1" defaultValue={current.sourceEnd}
              onBlur={e => apply('trim_clip', selected.id, trimArguments(document, selected.id, 'end', Number(e.target.value)))} />
          </label>
        </>
      )}
      {selected.lane === 'captions' && (
        <label>Caption
          <textarea defaultValue={current.text || current.displayText}
            onBlur={e => apply('update_caption', selected.id, { text: e.target.value })} />
        </label>
      )}
      {selected.lane === 'music' && (
        <label>Music gain (dB)
          <input type="range" min="-60" max="6" defaultValue={current.gainDb}
            onChange={e => apply('set_music_gain', selected.id, { gainDb: Number(e.target.value) })} />
        </label>
      )}
      {selected.lane === 'graphics' && (
        <button className="btn btn-ghost" onClick={() => apply('toggle_graphic', selected.id, { enabled: !current.enabled })}>
          {current.enabled ? 'Hide graphic' : 'Show graphic'}
        </button>
      )}
    </aside>
  )
}

function RenderStatus({ job, retry, download }) {
  return (
    <div className="render-toast">
      <span className={`badge ${job.status}`}>{job.status}</span>
      <b>Export {job.id?.slice(0, 8)}</b>
      <span>{job.current_stage || `${job.progress || 0}%`}</span>
      {job.status === 'failed' && <button className="btn btn-ghost btn-sm" onClick={retry}>Retry</button>}
      {job.status === 'completed' && <button className="btn btn-primary btn-sm" onClick={download}>Download MP4</button>}
      {job.error_message && <span style={{ color: '#fca5a5', fontSize: '0.75rem' }}>{job.error_message}</span>}
    </div>
  )
}

function formatTime(seconds) {
  const safe = Number.isFinite(seconds) ? seconds : 0
  return `${String(Math.floor(safe / 60)).padStart(2, '0')}:${(safe % 60).toFixed(1).padStart(4, '0')}`
}
