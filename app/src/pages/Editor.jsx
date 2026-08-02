import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAuth } from '../App'
import { supabase } from '../lib/supabase'
import { editorApi, editorReducer, makeOperation, track } from '../lib/editor'

const initialState = { document: null, past: [], future: [], pending: [] }
const COLORS = { picture: '#00d4ff', captions: '#a78bfa', music: '#f59e0b',
  sfx: '#fb7185', graphics: '#34d399' }

export default function Editor() {
  const { id, documentId } = useParams()
  const session = useAuth()
  const [row, setRow] = useState(null)
  const [workspace, setWorkspace] = useState(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const [state, dispatch] = useReducer(editorReducer, initialState)
  const [selected, setSelected] = useState(null)
  const [zoom, setZoom] = useState(46)
  const [playhead, setPlayhead] = useState(0)
  const [snap, setSnap] = useState(true)
  const [status, setStatus] = useState('Loading saved revision…')
  const [error, setError] = useState('')
  const [prompt, setPrompt] = useState('')
  const [renderJob, setRenderJob] = useState(null)
  const saving = useRef(false)

  const load = useCallback(async (target = documentId) => {
    setError('')
    const [doc, ws] = await Promise.all([
      editorApi(`/projects/${id}/editor/${target}`, session),
      editorApi(`/projects/${id}/workspace`, session),
    ])
    setRow(doc); setWorkspace(ws); dispatch({ type: 'load', document: doc.document })
    setStatus(`Saved · revision ${doc.version}`)
    const candidate = ws.candidates.find((item) => item.id === doc.candidate_run_id)
    if (candidate?.preview_storage_path) {
      const { data } = await supabase.storage.from(candidate.preview_storage_bucket || 'exports')
        .createSignedUrl(candidate.preview_storage_path, 3600)
      setPreviewUrl(data?.signedUrl || '')
    }
  }, [documentId, id, session])

  useEffect(() => { load().catch((e) => setError(e.message)) }, [load])

  const save = useCallback(async () => {
    if (!row || !state.pending.length || saving.current) return
    saving.current = true; setStatus('Saving revision…'); setError('')
    try {
      const saved = await editorApi(`/projects/${id}/editor/${row.id}/operations`, session, {
        method: 'POST', body: JSON.stringify({ expectedVersion: row.version,
                                              operations: state.pending }),
      })
      setRow(saved); setWorkspace((current) => current ? {
        ...current, editorDocuments: [saved, ...current.editorDocuments],
      } : current)
      dispatch({ type: 'load', document: saved.document })
      setStatus(`Saved · revision ${saved.version}`)
      history.replaceState(null, '', `/project/${id}/editor/${saved.id}`)
    } catch (e) {
      setError(e.status === 409 ? 'This cut changed elsewhere. Reload the latest revision before saving.' : e.message)
      setStatus('Unsaved changes')
    } finally { saving.current = false }
  }, [id, row, session, state.pending])

  useEffect(() => {
    if (!state.pending.length) return undefined
    setStatus('Unsaved changes · autosaving')
    const timer = setTimeout(save, 900)
    return () => clearTimeout(timer)
  }, [save, state.pending])

  useEffect(() => {
    if (!renderJob || !['queued', 'processing'].includes(renderJob.status)) return undefined
    const timer = setInterval(async () => {
      const ws = await editorApi(`/projects/${id}/workspace`, session)
      const job = ws.renderJobs.find((item) => item.id === renderJob.id)
      if (job) setRenderJob(job)
    }, 2500)
    return () => clearInterval(timer)
  }, [id, renderJob, session])

  function apply(type, targetId, args = {}, actor = 'user') {
    if (!row) return
    dispatch({ type: 'apply', operation: makeOperation(type, targetId, row.version, args, actor) })
  }

  async function revise() {
    setError('')
    try {
      const proposal = await editorApi(`/projects/${id}/editor/revisions/propose`, session, {
        method: 'POST', body: JSON.stringify({ documentId: row.id,
          expectedVersion: row.version, prompt }),
      })
      proposal.operations.forEach((operation) => dispatch({ type: 'apply', operation }))
      setPrompt('')
    } catch (e) { setError(e.message) }
  }

  async function render() {
    await save()
    if (state.pending.length) return
    setError(''); setStatus('Queueing export…')
    try {
      const job = await editorApi(`/projects/${id}/editor/render`, session, {
        method: 'POST', body: JSON.stringify({ documentId: row.id }),
      })
      setRenderJob(job); setStatus(`Saved · revision ${row.version}`)
    } catch (e) { setError(e.message) }
  }

  async function retryRender() {
    try {
      const job = await editorApi(`/projects/${id}/editor/renders/${renderJob.id}/retry`,
        session, { method: 'POST' })
      setRenderJob(job)
    } catch (e) { setError(e.message) }
  }

  async function downloadRender() {
    try {
      const result = await editorApi(`/projects/${id}/editor/renders/${renderJob.id}/sign`,
        session, { method: 'POST' })
      window.location.assign(result.url)
    } catch (e) { setError(e.message) }
  }

  if (!state.document || !row) return <div className="wrap"><p className="sub">{error || status}</p></div>
  const candidate = workspace?.candidates.find((item) => item.id === row.candidate_run_id)

  return (
    <main className="edit-shell">
      <header className="edit-commandbar">
        <div><Link to={`/project/${id}`}>← Workspace</Link><strong>{workspace?.project.name}</strong></div>
        <div className="edit-history">
          <button onClick={() => dispatch({ type: 'undo' })} disabled={!state.past.length}>Undo</button>
          <button onClick={() => dispatch({ type: 'redo' })} disabled={!state.future.length}>Redo</button>
          <span>{status}</span>
          <button className="btn btn-primary" onClick={render} disabled={state.pending.length > 0}>Export revision {row.version}</button>
        </div>
      </header>
      {error && <div className="edit-alert" role="alert">{error}</div>}
      <section className="edit-stage">
        <aside className="edit-bin">
          <span className="edit-eyebrow">Candidate source</span>
          <h2>{candidate?.candidate_key || 'Editorial winner'}</h2>
          <p>{candidate?.publishability?.overall_publishability_score || '—'} publishability</p>
          <div className="candidate-mini">Immutable source<br/><b>{row.candidate_run_id.slice(0, 8)}</b></div>
          <hr />
          <span className="edit-eyebrow">Revision history</span>
          {workspace?.editorDocuments.filter((item) => item.candidate_run_id === row.candidate_run_id)
            .map((item) => <button key={item.id} className={item.id === row.id ? 'active' : ''}
              onClick={() => load(item.id).catch((e) => setError(e.message))}>
              Revision {item.version}<small>{new Date(item.created_at).toLocaleTimeString()}</small>
            </button>)}
        </aside>
        <div className="edit-monitor">
          <div className="monitor-frame">
            {previewUrl ? <video src={previewUrl} controls onTimeUpdate={(e) => setPlayhead(e.currentTarget.currentTime)} />
              : <div className="monitor-empty">Preview unavailable</div>}
          </div>
          <div className="time-readout"><code>{formatTime(playhead)}</code><span>/ {formatTime(state.document.duration)}</span></div>
        </div>
        <Inspector document={state.document} selected={selected} apply={apply} />
      </section>
      <section className="edit-timeline-section">
        <div className="timeline-tools">
          <button onClick={() => setPlayhead(0)}>⏮</button>
          <label>Zoom <input type="range" min="24" max="110" value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))} /></label>
          <label><input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} /> Snap</label>
          <span>Playhead {formatTime(playhead)}</span>
        </div>
        <Timeline document={state.document} zoom={zoom} playhead={playhead} setPlayhead={setPlayhead}
          snap={snap} selected={selected} setSelected={setSelected} apply={apply} />
      </section>
      <section className="revision-strip">
        <div><span className="edit-eyebrow">Conversational revision</span>
          <p>Phase 1 accepts bounded requests such as “remove first clip,” “caption: …,” or “mute music.”</p></div>
        <input value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Describe one bounded change" />
        <button className="btn btn-primary" onClick={revise} disabled={!prompt.trim()}>Propose change</button>
      </section>
      {renderJob && <RenderStatus job={renderJob} retry={retryRender} download={downloadRender} />}
    </main>
  )
}

function Timeline({ document, zoom, playhead, setPlayhead, snap, selected, setSelected, apply }) {
  const duration = document.duration
  const width = Math.max(760, duration * zoom)
  const ruler = Array.from({ length: Math.ceil(duration) + 1 }, (_, index) => index)
  return <div className="timeline-scroll"><div className="timeline-canvas" style={{ width }}
    onClick={(e) => { const box = e.currentTarget.getBoundingClientRect(); let value = (e.clientX - box.left) / zoom
      if (snap) value = Math.round(value * 2) / 2; setPlayhead(Math.max(0, Math.min(duration, value))) }}>
    <div className="time-ruler">{ruler.map((tick) => <span key={tick} style={{ left: tick * zoom }}>{tick}s</span>)}</div>
    <div className="playhead" style={{ left: playhead * zoom }} />
    {document.tracks.map((lane) => <div className="timeline-lane" key={lane.type}>
      <b>{lane.type}</b><div className="lane-items">
        {lane.items.map((item, index) => {
          const start = item.timelineStart ?? item.startSeconds ?? 0
          const end = item.timelineEnd ?? item.endSeconds ?? duration
          const left = lane.type === 'picture' ? start * zoom : start * zoom
          const itemWidth = Math.max(28, (end - start) * zoom)
          return <button key={item.id} className={selected?.id === item.id ? 'clip selected' : 'clip'}
            style={{ left, width: itemWidth, '--clip-color': COLORS[lane.type] }}
            onClick={(e) => { e.stopPropagation(); setSelected({ ...item, lane: lane.type, index }) }}>
            <span>{item.text || item.displayText || item.kind || item.id}</span>
            {lane.type === 'picture' && <i>{(end - start).toFixed(1)}s</i>}
          </button>
        })}
      </div></div>)}
    {selected?.lane === 'picture' && <div className="clip-actions">
      <button onClick={() => apply('reorder_clip', selected.id, { toIndex: Math.max(0, selected.index - 1) })}>Move left</button>
      <button onClick={() => apply('reorder_clip', selected.id, { toIndex: selected.index + 1 })}>Move right</button>
      <button onClick={() => apply('split_clip', selected.id, { sourceTime: selected.sourceStart + (selected.sourceEnd - selected.sourceStart) / 2 })}>Split</button>
      <button onClick={() => apply('delete_clip', selected.id)}>Delete</button>
    </div>}
  </div></div>
}

function Inspector({ document, selected, apply }) {
  if (!selected) return <aside className="edit-inspector"><span className="edit-eyebrow">Inspector</span><p>Select a timeline item.</p></aside>
  return <aside className="edit-inspector"><span className="edit-eyebrow">Inspector · {selected.lane}</span><h2>{selected.id}</h2>
    {selected.lane === 'picture' && <><label>In point<input type="number" step=".1" defaultValue={selected.sourceStart}
      onBlur={(e) => apply('trim_clip', selected.id, { sourceStart: Number(e.target.value), sourceEnd: selected.sourceEnd })} /></label>
      <label>Out point<input type="number" step=".1" defaultValue={selected.sourceEnd}
        onBlur={(e) => apply('trim_clip', selected.id, { sourceStart: selected.sourceStart, sourceEnd: Number(e.target.value) })} /></label></>}
    {selected.lane === 'captions' && <label>Caption<textarea defaultValue={selected.text || selected.displayText}
      onBlur={(e) => apply('update_caption', selected.id, { text: e.target.value })} /></label>}
    {selected.lane === 'music' && <label>Music gain (dB)<input type="range" min="-60" max="6" defaultValue={selected.gainDb}
      onChange={(e) => apply('set_music_gain', selected.id, { gainDb: Number(e.target.value) })} /></label>}
    {selected.lane === 'graphics' && <button className="btn btn-ghost"
      onClick={() => apply('toggle_graphic', selected.id, { enabled: !selected.enabled })}>
      {selected.enabled ? 'Hide graphic' : 'Show graphic'}</button>}
  </aside>
}

function RenderStatus({ job, retry, download }) {
  return <section className="render-toast"><span className={`badge ${job.status}`}>{job.status}</span>
    <b>Export {job.id.slice(0, 8)}</b><span>{job.current_stage || `${job.progress || 0}%`}</span>
    {job.status === 'failed' && <button className="btn btn-ghost" onClick={retry}>Retry</button>}
    {job.status === 'completed' && <button className="btn btn-primary" onClick={download}>Download MP4</button>}
    {job.error_message && <span className="err">{job.error_message}</span>}</section>
}

function formatTime(seconds) {
  const safe = Number.isFinite(seconds) ? seconds : 0
  return `${String(Math.floor(safe / 60)).padStart(2, '0')}:${(safe % 60).toFixed(1).padStart(4, '0')}`
}
