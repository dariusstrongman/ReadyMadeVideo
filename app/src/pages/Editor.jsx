import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../App'
import {
  createEditorState, editorApi, editorReducer, makeOperation,
  reorderArguments, track, trimArguments,
} from '../lib/editor'
import iconUrl from '../assets/icon.svg'

const COLORS = { picture: '#00d4ff', captions: '#f59e0b', music: '#7c3aed',
  sfx: '#22c55e', graphics: '#ec4899' }
const AUTOSAVE_MS = 800

// The visible customer editor speaks ONLY the immutable Product Editor API:
//   GET  /editor/{doc}                 -> load/rehydrate a versioned document
//   POST /editor/{doc}/operations      -> OperationBatch, returns next version
//   POST /candidates/{id}/preview-url  -> short-lived signed preview
//   POST /editor/render                -> enqueue export (Slice 4 owns download)
// No legacy candidate/render tables, no client-side timeline writes, no direct render.
export default function Editor() {
  const session = useAuth()
  const { id: projectId, documentId } = useParams()
  const navigate = useNavigate()

  const [state, dispatch] = useReducer(editorReducer, createEditorState())
  const [projectName, setProjectName] = useState('')
  const [previewUrl, setPreviewUrl] = useState(undefined)  // undefined=loading, null=none
  const [selected, setSelected] = useState(null)
  const [zoom, setZoom] = useState(80)
  const [snap, setSnap] = useState(true)
  const [playhead, setPlayhead] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [saveStatus, setSaveStatus] = useState('Loading…')
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [aiInput, setAiInput] = useState('')
  const [aiHistory, setAiHistory] = useState([])
  const videoRef = useRef(null)
  const savingRef = useRef(false)
  const stateRef = useRef(state)
  stateRef.current = state

  const doc = state.document
  // Each save produces a NEW immutable editor_documents row. docIdRef is updated
  // SYNCHRONOUSLY (not via render) so an export right after a save always targets
  // the confirmed latest revision — never a stale render snapshot.
  const docIdRef = useRef(documentId)
  const savingPromiseRef = useRef(null)   // the in-flight save, so export can await it
  const retryRef = useRef(null)

  const loadDoc = useCallback(async (targetId) => {
    setError('')
    try {
      const row = await editorApi(`/projects/${projectId}/editor/${targetId}`, session)
      docIdRef.current = row.id
      dispatch({ type: 'load', document: row.document, version: row.version })
      setSaveStatus('Saved')
      setProjectName(row.document?.projectName || 'Studio')
      if (row.candidate_run_id) {
        editorApi(`/projects/${projectId}/candidates/${row.candidate_run_id}/preview-url`,
          session, { method: 'POST', body: JSON.stringify({}) })
          .then(({ url }) => setPreviewUrl(url))
          .catch(() => setPreviewUrl(null))
      } else {
        setPreviewUrl(null)
      }
    } catch (e) {
      setError(e.message || 'Could not load this edit.')
      setSaveStatus('Error')
    }
  }, [projectId, session])

  useEffect(() => { loadDoc(documentId) }, [documentId, loadDoc])

  // Autosave: flush pending operations as one OperationBatch, debounced. On a
  // version conflict we REBASE pending onto the fresh server document and retry —
  // pending ops and undo/redo history are never discarded. Returns the confirmed
  // latest document id, or throws so callers (export) can abort.
  const save = useCallback(async () => {
    if (savingPromiseRef.current) return savingPromiseRef.current
    const current = stateRef.current
    if (!current.document || !current.pending.length) return docIdRef.current
    savingRef.current = true
    const run = (async () => {
      const batch = current.pending
      const operationIds = batch.map((op) => op.operationId)
      setSaveStatus('Saving…')
      try {
        const saved = await editorApi(
          `/projects/${projectId}/editor/${docIdRef.current}/operations`, session,
          { method: 'POST',
            body: JSON.stringify({ expectedVersion: current.version, operations: batch }) })
        docIdRef.current = saved.id                       // advance synchronously
        dispatch({ type: 'save_succeeded', document: saved.document,
          version: saved.version, operationIds })
        setSaveStatus('Saved')
        return saved.id
      } catch (e) {
        if (e.status === 409) {
          // Rebase pending onto the server's latest revision (no edits lost).
          const latestId = e.payload?.detail?.latestDocumentId || docIdRef.current
          const fresh = await editorApi(
            `/projects/${projectId}/editor/${latestId}`, session)
          docIdRef.current = fresh.id
          dispatch({ type: 'rebase', document: fresh.document, version: fresh.version })
          setSaveStatus('Reconciling…')
          throw e   // still unsaved; caller must not treat this as saved
        }
        setSaveStatus('Save failed — will retry')
        throw e
      }
    })()
    savingPromiseRef.current = run
    try {
      return await run
    } finally {
      savingRef.current = false
      savingPromiseRef.current = null
    }
  }, [projectId, session])

  useEffect(() => {
    if (!state.pending.length) return undefined
    const t = setTimeout(() => {
      // Failed autosave auto-reschedules so a transient error never strands edits.
      save().catch(() => {
        if (retryRef.current) clearTimeout(retryRef.current)
        retryRef.current = setTimeout(() => {
          if (stateRef.current.pending.length) save().catch(() => {})
        }, 3000)
      })
    }, AUTOSAVE_MS)
    return () => clearTimeout(t)
  }, [state.pending, save])

  useEffect(() => () => { if (retryRef.current) clearTimeout(retryRef.current) }, [])

  // Sync playhead <-> preview video.
  useEffect(() => {
    const vid = videoRef.current
    if (!vid) return undefined
    const onTime = () => setPlayhead(vid.currentTime)
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    vid.addEventListener('timeupdate', onTime)
    vid.addEventListener('play', onPlay)
    vid.addEventListener('pause', onPause)
    return () => {
      vid.removeEventListener('timeupdate', onTime)
      vid.removeEventListener('play', onPlay)
      vid.removeEventListener('pause', onPause)
    }
  }, [previewUrl])

  function apply(type, targetId, args) {
    if (!state.document) return
    const op = makeOperation(type, targetId, state.version, args)
    dispatch({ type: 'apply', operation: op })   // optimistic; reconciled on save
  }

  async function startExport() {
    setError('')
    // Export must use the CONFIRMED saved immutable revision. Wait for any in-flight
    // save, flush remaining pending, and abort if anything is still unsaved.
    try {
      if (savingPromiseRef.current) await savingPromiseRef.current
      if (stateRef.current.pending.length) await save()
    } catch {
      setError('Could not save your latest changes — export cancelled. Please retry.')
      return
    }
    if (stateRef.current.pending.length) {
      setError('You have unsaved changes — export cancelled. Please retry.')
      return
    }
    setExporting(true)
    try {
      await editorApi(`/projects/${projectId}/editor/render`, session,
        { method: 'POST', body: JSON.stringify({ documentId: docIdRef.current }) })
      navigate(`/project/${projectId}`)   // export progress + download live on the project
    } catch (e) {
      setError(e.message || 'Could not start the export.')
    } finally { setExporting(false) }
  }

  function sendAiMessage() {
    if (!aiInput.trim()) return
    const msg = aiInput.trim()
    setAiInput('')
    setAiHistory((h) => [...h, { role: 'user', text: msg },
      { role: 'ai', text: `[PROTOTYPE] Noted: "${msg}". Conversational edits arrive later — use the timeline controls for now.` }])
  }

  if (!doc) {
    return <div className="center"><p className="sub">{error || 'Loading editor…'}</p></div>
  }

  const canUndo = state.past.length > 0
  const canRedo = state.future.length > 0

  return (
    <div className="editor-page">
      <div className="editor-topbar">
        <a href="https://www.stromation.com" className="nav-logo" style={{ flexShrink: 0 }}>
          <img src={iconUrl} alt="" aria-hidden="true" />
          STROMATION
        </a>
        <span style={{ color: 'var(--border-2)' }}>/</span>
        <Link to={`/project/${projectId}`} style={{ color: 'var(--text-3)', fontSize: '0.78rem' }}>Project</Link>
        <span style={{ color: 'var(--border-2)' }}>/</span>
        <span className="project-name">Studio</span>
        <span className="spacer" />
        <span className="save-status">{saveStatus} · v{state.version}</span>
        <button className="btn btn-primary btn-sm" onClick={startExport} disabled={exporting}>
          {exporting ? 'Starting…' : 'Export video'}
        </button>
      </div>

      <div className="edit-shell">
        <div className="edit-commandbar">
          <div>
            <strong>Edit</strong>
            <span className="edit-history">
              <button onClick={() => dispatch({ type: 'undo' })} disabled={!canUndo} title="Undo">↩</button>
              <button onClick={() => dispatch({ type: 'redo' })} disabled={!canRedo} title="Redo">↪</button>
            </span>
          </div>
          <div>{error && <span className="edit-alert">{error}</span>}</div>
        </div>

        <div className="edit-stage">
          <aside className="edit-bin">
            <span className="edit-eyebrow">Footage</span>
            {track(doc, 'picture').items.map((item, i) => (
              <div key={item.id} className={`clip-bin-item ${selected?.id === item.id ? 'active' : ''}`}
                onClick={() => setSelected({ id: item.id, lane: 'picture' })}>
                <div className="clip-bin-thumb" style={{ display: 'flex', alignItems: 'center',
                  justifyContent: 'center', fontSize: '0.5rem', color: 'var(--text-3)',
                  fontFamily: 'var(--mono)' }}>{i + 1}</div>
                <div className="clip-bin-info">
                  <p className="clip-bin-name">{item.id}</p>
                  <p className="clip-bin-meta">{((item.sourceEnd - item.sourceStart) || 0).toFixed(1)}s</p>
                </div>
              </div>
            ))}
            {track(doc, 'picture').items.length === 0 && (
              <p style={{ fontSize: '0.75rem', color: 'var(--text-3)' }}>No clips.</p>
            )}
          </aside>

          <div className="edit-monitor">
            <div className="monitor-frame">
              {previewUrl
                ? <video ref={videoRef} src={previewUrl} />
                : <div className="monitor-empty">
                    {previewUrl === undefined ? 'Loading preview…' : 'Preview not available'}
                  </div>}
            </div>
            <div className="monitor-controls">
              <button className="monitor-btn" onClick={() => { const v = videoRef.current; if (v) v.currentTime = Math.max(0, v.currentTime - 5) }}>◀◀</button>
              <button className="monitor-btn" onClick={() => { const v = videoRef.current; if (v) (playing ? v.pause() : v.play()) }}>{playing ? '⏸' : '▶'}</button>
              <button className="monitor-btn" onClick={() => { const v = videoRef.current; if (v) v.currentTime = Math.min(doc.duration, v.currentTime + 5) }}>▶▶</button>
            </div>
            <div className="time-readout">
              <code>{formatTime(playhead)}</code>/ {formatTime(doc.duration)}
            </div>
          </div>

          <div className="ai-studio-panel">
            <div className="ai-studio-header">
              <span className="ai-studio-label">AI Studio</span>
              <span className="ai-prototype-badge">PROTOTYPE</span>
            </div>
            <div className="ai-history">
              {aiHistory.length === 0 && (
                <p style={{ fontSize: '0.75rem', color: 'var(--text-3)', padding: '8px 0' }}>
                  Describe a change. <span style={{ color: 'var(--violet)', opacity: 0.7 }}>Simulated in this prototype.</span>
                </p>
              )}
              {aiHistory.map((m, i) => (
                <div key={i} className={`ai-msg ${m.role === 'user' ? 'ai-msg-user' : 'ai-msg-ai'}`}>{m.text}</div>
              ))}
            </div>
            <div className="ai-input-row">
              <textarea className="ai-input" rows={2} value={aiInput}
                onChange={(e) => setAiInput(e.target.value)}
                placeholder="Describe a change…"
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAiMessage() } }} />
              <button className="ai-send-btn" onClick={sendAiMessage} disabled={!aiInput.trim()}>→</button>
            </div>
          </div>
        </div>

        <div className="edit-timeline-section">
          <div className="timeline-tools">
            <label>Zoom
              <input type="range" min="20" max="200" value={zoom} onChange={(e) => setZoom(Number(e.target.value))} />
            </label>
            <label>
              <input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} />
              Snap to 0.5s
            </label>
            <span style={{ flex: 1 }} />
            <span>{formatTime(playhead)} / {formatTime(doc.duration)}</span>
          </div>
          <Timeline document={doc} zoom={zoom} playhead={playhead} snap={snap}
            selected={selected} setSelected={setSelected} apply={apply}
            setPlayhead={(p) => { setPlayhead(p); if (videoRef.current) videoRef.current.currentTime = p }} />
        </div>

        <Inspector document={doc} selected={selected} apply={apply} />
      </div>
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
        onClick={(e) => {
          const box = e.currentTarget.getBoundingClientRect()
          let v = (e.clientX - box.left) / zoom
          if (snap) v = Math.round(v * 2) / 2
          setPlayhead(Math.max(0, Math.min(duration, v)))
        }}>
        <div className="time-ruler">
          {ruler.map((tick) => <span key={tick} style={{ left: tick * zoom }}>{tick}s</span>)}
        </div>
        <div className="playhead" style={{ left: playhead * zoom }} />
        {document.tracks?.map((lane) => (
          <div className="timeline-lane" key={lane.type}>
            <b>{lane.type}</b>
            <div className="lane-items">
              {lane.items?.map((item) => {
                const start = item.timelineStart ?? item.startSeconds ?? 0
                const end = item.timelineEnd ?? item.endSeconds ?? duration
                const itemWidth = Math.max(28, (end - start) * zoom)
                return (
                  <button key={item.id}
                    className={selected?.id === item.id ? 'clip selected' : 'clip'}
                    style={{ left: start * zoom, width: itemWidth, '--clip-color': COLORS[lane.type] || '#888' }}
                    onClick={(e) => { e.stopPropagation(); setSelected({ id: item.id, lane: lane.type }) }}>
                    <span>{item.text || item.displayText || item.id}</span>
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
              const clip = track(document, 'picture').items.find((i) => i.id === selected.id)
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
  if (!selected) {
    return (
      <aside className="edit-inspector">
        <span className="edit-eyebrow">Inspector</span>
        <p>Select a clip to inspect.</p>
      </aside>
    )
  }
  const current = track(document, selected.lane)?.items?.find((i) => i.id === selected.id)
  if (!current) {
    return (
      <aside className="edit-inspector">
        <span className="edit-eyebrow">Inspector</span>
        <p>Clip no longer exists.</p>
      </aside>
    )
  }
  return (
    <aside className="edit-inspector">
      <span className="edit-eyebrow">Inspector · {selected.lane}</span>
      <h2>{selected.id}</h2>
      {selected.lane === 'picture' && (
        <>
          <label>In point
            <input key={`in-${current.sourceStart}`} type="number" step=".1" defaultValue={current.sourceStart}
              onBlur={(e) => apply('trim_clip', selected.id, trimArguments(document, selected.id, 'start', Number(e.target.value)))} />
          </label>
          <label>Out point
            <input key={`out-${current.sourceEnd}`} type="number" step=".1" defaultValue={current.sourceEnd}
              onBlur={(e) => apply('trim_clip', selected.id, trimArguments(document, selected.id, 'end', Number(e.target.value)))} />
          </label>
        </>
      )}
      {selected.lane === 'captions' && (
        <label>Caption
          <textarea defaultValue={current.text || current.displayText}
            onBlur={(e) => apply('update_caption', selected.id, { text: e.target.value })} />
        </label>
      )}
      {selected.lane === 'music' && (
        <label>Music gain (dB)
          <input type="range" min="-60" max="6" defaultValue={current.gainDb}
            onChange={(e) => apply('set_music_gain', selected.id, { gainDb: Number(e.target.value) })} />
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

function formatTime(seconds) {
  const safe = Number.isFinite(seconds) ? seconds : 0
  return `${String(Math.floor(safe / 60)).padStart(2, '0')}:${(safe % 60).toFixed(1).padStart(4, '0')}`
}
