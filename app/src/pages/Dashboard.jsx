import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import { supabase } from '../lib/supabase'
import { editorApi } from '../lib/editor'
import { isExportJob } from './Project'

/* Status vocabulary ─────────────────────────────────────────────────────
   Four intents, in the order footage actually travels through the studio:
     idle  → waiting on you for footage      (neutral)
     work  → Stromation is cutting           (cyan — the brand at work)
     ready → your eyes are needed            (violet)
     done  → exported                        (green)
     error → something stopped               (red)
   Colour is never decorative here: it always encodes where a video is. */
const STATUS = {
  draft:           { intent: 'idle',  label: 'Waiting for footage', action: 'Add footage',   lead: true },
  uploading:       { intent: 'work',  label: 'Uploading footage',   action: 'View upload' },
  ready:           { intent: 'work',  label: 'Footage received',    action: 'Open video' },
  analyzing:       { intent: 'work',  label: 'Building your edit',  action: 'Watch progress' },
  rendering:       { intent: 'work',  label: 'Rendering',           action: 'Watch progress' },
  draft_ready:     { intent: 'ready', label: 'Your edit is ready',  action: 'Watch your edit', lead: true },
  completed:       { intent: 'done',  label: 'Exported',            action: 'Download' },
  complete:        { intent: 'done',  label: 'Exported',            action: 'Download' },
  analysis_failed: { intent: 'error', label: 'Edit failed',         action: 'See what happened', lead: true },
  render_failed:   { intent: 'error', label: 'Export failed',       action: 'See what happened', lead: true },
}
const statusOf = (s, hasFootage = false) => {
  // `draft` means "no edit has been started", NOT "no footage". A project whose
  // upload landed but whose status never advanced is still draft, and calling
  // that "Waiting for footage" while its clips sit on the project page is a
  // straight contradiction. Footage present => it is waiting on a decision.
  if (s === 'draft' && hasFootage) {
    return { intent: 'ready', copy: 'start', label: 'Ready to edit', action: 'Start editing', lead: true }
  }
  return STATUS[s] || { intent: 'idle', label: String(s).replaceAll('_', ' '), action: 'Open' }
}

/* The hero answers one question: which video needs you right now?
   Lower number wins. Ordering is by how much it is blocked on the human. */
const HERO_RANK = { draft_ready: 0, analysis_failed: 1, render_failed: 1, draft: 2, analyzing: 3, rendering: 3, uploading: 4, ready: 4, completed: 5, complete: 5 }

const HERO_COPY = {
  ready: { eyebrow: 'Ready to watch', line: 'Your first cut is finished. Watch it, then download it or ask for changes.' },
  start: { eyebrow: 'Ready to edit',  line: 'Your footage is uploaded. Start the edit and Stromation builds your first cut.' },
  work:  { eyebrow: 'In the bay',     line: 'Stromation is cutting this now. You can leave — it keeps working.' },
  idle:  { eyebrow: 'Needs footage',  line: 'Drop in your raw clips and the edit starts on its own.' },
  error: { eyebrow: 'Stopped',        line: 'This one did not finish. Open it to see what happened.' },
  done:  { eyebrow: 'Exported',       line: 'This video is finished and downloaded.' },
}

function timeAgo(ts) {
  const diff = Date.now() - new Date(ts).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function FilmIcon({ size = 48 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="2" width="20" height="20" rx="2.18"/>
      <line x1="7" y1="2" x2="7" y2="22"/>
      <line x1="17" y1="2" x2="17" y2="22"/>
      <line x1="2" y1="12" x2="22" y2="12"/>
      <line x1="2" y1="7" x2="7" y2="7"/>
      <line x1="2" y1="17" x2="7" y2="17"/>
      <line x1="17" y1="17" x2="22" y2="17"/>
      <line x1="17" y1="7" x2="22" y2="7"/>
    </svg>
  )
}

/* An empty gate: what a camera sees before it rolls. Used wherever a video
   has no frame yet — far more honest than a grey placeholder icon. */
function Slate({ intent, label }) {
  return (
    <div className={`st-slate st-i-${intent}`} aria-hidden="true">
      <span className="st-slate-corner tl" /><span className="st-slate-corner tr" />
      <span className="st-slate-corner bl" /><span className="st-slate-corner br" />
      <span className="st-slate-label">{label}</span>
    </div>
  )
}

/* Real footage, not an icon. `#t=0.1` makes the browser paint an actual
   frame from the cut as the poster. Hover plays it silently — the medium
   is video, so the dashboard should move like video. */
function Frame({ src, intent, slateLabel, play = true }) {
  const ref = useRef(null)
  const [failed, setFailed] = useState(false)
  if (!src || failed) return <Slate intent={intent} label={slateLabel} />

  const motionOK = () => !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

  return (
    <video
      ref={ref}
      className="st-frame-video"
      src={`${src}#t=0.1`}
      preload="metadata"
      muted
      playsInline
      loop
      tabIndex={-1}
      onError={() => setFailed(true)}
      onMouseEnter={() => { if (play && motionOK()) ref.current?.play().catch(() => {}) }}
      onMouseLeave={() => { const v = ref.current; if (v) { v.pause(); v.currentTime = 0.1 } }}
    />
  )
}

export default function Dashboard() {
  const session = useAuth()
  const navigate = useNavigate()
  const [projects, setProjects] = useState(null)
  const [jobs, setJobs] = useState([])
  const [posters, setPosters] = useState({})   // projectId -> signed url | null
  const [footage, setFootage] = useState({})   // projectId -> clip count
  const [heroTools, setHeroTools] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const [{ data: projs, error: pe }, { data: js }, { data: ast }] = await Promise.all([
      supabase.from('projects')
        .select('id, name, status, created_at, updated_at')
        .is('deleted_at', null)          // hide soft-deleted projects
        .order('updated_at', { ascending: false }),
      // Only real exports belong here. `analysis` and `autoedit` are internal
      // pipeline stages, not something the customer downloaded — listing them
      // under "downloads" (badged "exported") was reporting machine steps as
      // user deliverables. Same rule Project.jsx already uses for isExportJob.
      supabase.from('pipeline_jobs')
        .select('id, project_id, status, kind, created_at, artifacts, params')
        .eq('kind', 'final_render')
        .eq('status', 'completed')
        .order('created_at', { ascending: false })
        .limit(10),
      supabase.from('media_assets').select('project_id'),
    ])
    if (pe) setError(pe.message)
    else setProjects(projs || [])
    // Reuse Project.jsx's tested rule rather than restating it here.
    if (js) setJobs(js.filter(isExportJob))
    if (ast) {
      const counts = {}
      for (const a of ast) counts[a.project_id] = (counts[a.project_id] || 0) + 1
      setFootage(counts)
    }
  }, [])

  useEffect(() => { load() }, [load])

  /* Posters load after the page paints, so nothing blocks first render.
     One query gets every candidate the user owns (RLS scopes it); the
     newest per project wins, and only that one gets a signed URL. */
  useEffect(() => {
    if (!projects?.length || !session) return undefined
    let cancelled = false
    ;(async () => {
      const { data: cands } = await supabase
        .from('candidate_runs')
        .select('id, project_id, created_at')
        .order('created_at', { ascending: false })
      if (cancelled || !cands?.length) return
      const newest = {}
      for (const c of cands) if (!newest[c.project_id]) newest[c.project_id] = c.id
      // Each signing call is audited server-side, so cap the fan-out: posters
      // are a nicety, not worth N audit writes on every dashboard visit.
      const order = projects.map(p => p.id).filter(id => newest[id]).slice(0, 12)
      for (const pid of order) {
        const cid = newest[pid]
        if (cancelled) return
        try {
          const { url } = await editorApi(
            `/projects/${pid}/candidates/${cid}/preview-url`, session,
            { method: 'POST', body: JSON.stringify({}) })
          if (!cancelled) setPosters(p => ({ ...p, [pid]: url }))
        } catch {
          if (!cancelled) setPosters(p => ({ ...p, [pid]: null }))
        }
      }
    })()
    return () => { cancelled = true }
  }, [projects, session])

  if (projects === null) {
    return (
      <div className="st-page">
        <div className="st-skel st-skel-title" />
        <div className="st-skel st-skel-hero" />
        <div className="st-grid">
          {[0, 1, 2, 3].map(i => <div key={i} className="st-skel st-skel-card" />)}
        </div>
      </div>
    )
  }

  const total = projects.length

  // ── Empty state ──────────────────────────────────────────────────────
  if (total === 0) {
    const steps = [
      { n: '01', title: 'Name your video', desc: 'Give your project a name.' },
      { n: '02', title: 'Drop in footage', desc: 'Raw clips from your phone, camera, or drone.' },
      { n: '03', title: 'AI builds the edit', desc: 'Stromation assembles a complete first cut.' },
      { n: '04', title: 'Watch and download', desc: 'Approve the edit, then export your MP4.' },
    ]
    return (
      <div className="dashboard-empty">
        <div className="empty-icon-wrap"><FilmIcon size={32} /></div>
        <h1 className="empty-title">Your studio is ready.</h1>
        <p className="empty-sub">Upload raw footage. Stromation builds your first edit.</p>
        <div className="empty-actions">
          <Link to="/project/new" className="btn btn-primary btn-lg">+ Create your first video</Link>
          <a href="https://www.stromation.com/showcase.html" className="empty-example-link"
            target="_blank" rel="noopener noreferrer">See Project Zero — a real example →</a>
        </div>
        <div className="empty-steps">
          {steps.map(s => (
            <div key={s.n} className="empty-step">
              <span className="empty-step-n">{s.n}</span>
              <p className="empty-step-title">{s.title}</p>
              <p className="empty-step-desc">{s.desc}</p>
            </div>
          ))}
        </div>
      </div>
    )
  }

  // ── The one that needs you ───────────────────────────────────────────
  const hero = [...projects].sort(
    (a, b) => (HERO_RANK[a.status] ?? 9) - (HERO_RANK[b.status] ?? 9)
  )[0]
  const rest = projects.filter(p => p.id !== hero.id)

  const heroStatus = statusOf(hero.status, (footage[hero.id] || 0) > 0)
  const heroCopy = HERO_COPY[heroStatus.copy || heroStatus.intent] || HERO_COPY.idle

  const waiting = projects.filter(p => statusOf(p.status, (footage[p.id] || 0) > 0).lead).length
  const working = projects.filter(p => statusOf(p.status, (footage[p.id] || 0) > 0).intent === 'work').length
  const exported = projects.filter(p => statusOf(p.status, (footage[p.id] || 0) > 0).intent === 'done').length

  return (
    <div className="st-page">
      {error && <div className="err" role="alert">{error}</div>}

      <header className="st-head">
        <div>
          <h1 className="st-title">Your studio</h1>
          {/* The old four stat tiles said 2 / 0 / 1 / 0 — maximum weight for
              minimum news. Same facts, one line, ninety percent less ink. */}
          <p className="st-tally">
            <span className="st-num">{total}</span> {total === 1 ? 'video' : 'videos'}
            {working > 0 && <> <i className="st-sep" /> <span className="st-num st-i-work">{working}</span> in the bay</>}
            {waiting > 0 && <> <i className="st-sep" /> <span className="st-num st-i-ready">{waiting}</span> waiting on you</>}
            {exported > 0 && <> <i className="st-sep" /> <span className="st-num st-i-done">{exported}</span> exported</>}
          </p>
        </div>
      </header>

      {/* ── Hero: the single next thing to do ── */}
      <section className={`st-hero st-i-${heroStatus.intent}`}
        role={heroTools ? undefined : 'link'}
        tabIndex={heroTools ? undefined : 0}
        aria-label={`${hero.name} — ${heroStatus.label}`}
        onClick={() => { if (!heroTools) navigate(`/project/${hero.id}`) }}
        onKeyDown={(e) => {
          if (heroTools) return
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/project/${hero.id}`) }
        }}>
        <div className="st-hero-frame st-toolhost">
          <Frame src={posters[hero.id]} intent={heroStatus.intent}
            slateLabel={heroStatus.label} play={!heroTools} />
          {/* The hero is a project like any other — it needs the same controls. */}
          <ProjectTools project={hero} onChanged={load} onDeleted={load}
            onOpenChange={setHeroTools} pinned />
          {heroStatus.intent === 'ready' && posters[hero.id] && (
            <span className="st-play" aria-hidden="true">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
            </span>
          )}
        </div>
        <div className="st-hero-meta">
          <span className="st-eyebrow"><i className={`st-dot st-i-${heroStatus.intent}`} />{heroCopy.eyebrow}</span>
          <h2 className="st-hero-title">{hero.name}</h2>
          <p className="st-hero-line">{heroCopy.line}</p>
          <div className="st-hero-foot">
            <button className="btn btn-primary"
              onClick={(e) => { e.stopPropagation(); navigate(`/project/${hero.id}`) }}>
              {heroStatus.action} →
            </button>
            <span className="st-stamp">{timeAgo(hero.updated_at || hero.created_at)}</span>
          </div>
        </div>
      </section>

      {/* ── The wall. Status lives on each card, so one grid holds them all. ── */}
      <section className="st-section">
        <div className="st-rule">
          <h2 className="st-rule-label">All videos</h2>
          <span className="st-rule-count">{total}</span>
        </div>
        <div className="st-grid">
          {rest.map(p => (
            <FrameCard key={p.id} project={p} poster={posters[p.id]}
              clips={footage[p.id] || 0} onDelete={load} onRefresh={load} />
          ))}
          <Link to="/project/new" className="st-new">
            <span className="st-new-plus">+</span>
            <span className="st-new-label">New video</span>
          </Link>
        </div>
      </section>

      {jobs.length > 0 && (
        <section className="st-section">
          <div className="st-rule">
            <h2 className="st-rule-label">Exports</h2>
            <span className="st-rule-count">{jobs.length}</span>
          </div>
          <div className="st-list">
            {jobs.slice(0, 5).map(j => {
              const proj = projects.find(p => p.id === j.project_id)
              return (
                <div key={j.id} className="st-row"
                  onClick={() => proj && navigate(`/project/${proj.id}`)}
                  style={{ cursor: proj ? 'pointer' : 'default' }}>
                  <span className="st-row-name">{proj?.name || 'Deleted video'}</span>
                  <span className="st-stamp">{timeAgo(j.created_at)}</span>
                </div>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}

/* Rename + delete for one project, rendered inside its frame.
   Shared by the hero and the grid cards: the hero IS a project, and while these
   lived only on the card the single project promoted to hero could not be
   renamed or deleted at all.

   Rename commits on an explicit Save rather than on blur. Blur-to-save meant
   Escape raced the commit — the input unmounted, blur fired, and the discarded
   text got written anyway. An explicit button removes the race instead of
   guarding it. */
function ProjectTools({ project: p, onChanged, onDeleted, onOpenChange, pinned = false }) {
  const session = useAuth()
  const [mode, setMode] = useState('idle')       // idle | rename | confirm
  const [draft, setDraft] = useState(p.name)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const inputRef = useRef(null)

  useEffect(() => { if (mode === 'rename') inputRef.current?.select() }, [mode])

  const stop = (e) => e.stopPropagation()
  function go(next) { setMode(next); setErr(''); onOpenChange?.(next !== 'idle') }

  async function saveName(e) {
    e?.preventDefault(); e?.stopPropagation()
    const name = draft.trim()
    if (!name || name === p.name) { setDraft(p.name); go('idle'); return }
    setBusy(true); setErr('')
    const { error } = await supabase.from('projects').update({ name }).eq('id', p.id)
    setBusy(false)
    if (error) { setErr(error.message); return }
    go('idle')
    onChanged?.()
  }

  async function confirmDelete(e) {
    stop(e)
    setBusy(true); setErr('')
    // Server-authorized, safe deletion: ownership-checked, marks the project
    // deleted and cleans storage server-side (preserving immutable edit
    // evidence). Idempotent.
    try {
      await editorApi(`/projects/${p.id}`, session, { method: 'DELETE' })
    } catch (e2) {
      setBusy(false)
      setErr(e2.message || 'Could not delete this video.')
      return
    }
    onDeleted?.()
  }

  return (
    <>
      {mode === 'idle' && (
        <div className={`st-tools${pinned ? ' st-tools-pinned' : ''}`} onClick={stop}>
          <button className="st-tool" title="Rename" aria-label={`Rename ${p.name}`}
            onClick={(e) => { stop(e); setDraft(p.name); go('rename') }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>
            </svg>
          </button>
          <button className="st-tool st-tool-kill" title="Delete" aria-label={`Delete ${p.name}`}
            onClick={(e) => { stop(e); go('confirm') }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2.4" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
      )}

      {mode === 'rename' && (
        <div className="st-veil" onClick={stop}>
          <form className="st-veil-form" onSubmit={saveName}>
            <label className="st-veil-q" htmlFor={`rn-${p.id}`}>Name this video</label>
            <input id={`rn-${p.id}`} ref={inputRef} className="st-rename" value={draft}
              maxLength={120} disabled={busy}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Escape') { setDraft(p.name); go('idle') }
              }} />
            {err && <p className="st-veil-err" role="alert">{err}</p>}
            <div className="st-veil-row">
              <button type="button" className="btn btn-sm btn-ghost" disabled={busy}
                onClick={(e) => { stop(e); setDraft(p.name); go('idle') }}>Cancel</button>
              <button type="submit" className="btn btn-sm btn-primary" disabled={busy}
                aria-busy={busy}>{busy ? 'Saving…' : 'Save'}</button>
            </div>
          </form>
        </div>
      )}

      {/* Deleting is destructive and irreversible — ask here, in our own voice,
          rather than handing off to a browser confirm() dialog. */}
      {mode === 'confirm' && (
        <div className="st-veil" onClick={stop}>
          <p className="st-veil-q">Delete this video?</p>
          <p className="st-veil-sub">The footage and the edit go with it.</p>
          {err && <p className="st-veil-err" role="alert">{err}</p>}
          <div className="st-veil-row">
            <button className="btn btn-sm btn-ghost" disabled={busy}
              onClick={(e) => { stop(e); go('idle') }}>Keep</button>
            <button className="btn btn-sm btn-danger" disabled={busy} aria-busy={busy}
              onClick={confirmDelete}>{busy ? 'Deleting…' : 'Delete'}</button>
          </div>
        </div>
      )}
    </>
  )
}

function FrameCard({ project: p, poster, clips = 0, onDelete, onRefresh }) {
  const navigate = useNavigate()
  const s = statusOf(p.status, clips > 0)
  const [toolsOpen, setToolsOpen] = useState(false)

  const open = () => { if (!toolsOpen) navigate(`/project/${p.id}`) }

  return (
    <article className={`st-card st-i-${s.intent}`}
      role={toolsOpen ? undefined : 'link'}
      tabIndex={toolsOpen ? undefined : 0}
      aria-label={`${p.name} — ${s.label}`}
      onClick={open}
      onKeyDown={(e) => {
        if (toolsOpen) return
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() }
      }}>
      <div className="st-card-frame st-toolhost">
        <Frame src={poster} intent={s.intent} slateLabel={s.label} play={!toolsOpen} />
        <ProjectTools project={p} onChanged={onRefresh} onDeleted={onDelete}
          onOpenChange={setToolsOpen} />
      </div>

      <div className="st-card-meta">
        <h3 className="st-card-name" title={p.name}>{p.name}</h3>
        <p className="st-card-status">
          <i className={`st-dot st-i-${s.intent}`} />{s.label}
          <span className="st-stamp">{timeAgo(p.updated_at || p.created_at)}</span>
        </p>
      </div>
    </article>
  )
}
