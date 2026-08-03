import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import { supabase } from '../lib/supabase'

const STATUS_LABEL = {
  draft:           'Ready for footage',
  uploading:       'Uploading footage',
  ready:           'Footage uploaded',
  analyzing:       'Creating your edit…',
  analysis_failed: 'Analysis failed',
  draft_ready:     'Your edit is ready',
  rendering:       'Rendering…',
  render_failed:   'Export failed',
  completed:       'Export ready',
  complete:        'Export ready',
}

const STATUS_DOT = {
  draft:           'dot-idle',
  uploading:       'dot-active',
  ready:           'dot-active',
  analyzing:       'dot-active',
  analysis_failed: 'dot-error',
  draft_ready:     'dot-ready',
  rendering:       'dot-active',
  render_failed:   'dot-error',
  completed:       'dot-done',
  complete:        'dot-done',
}

const STATUS_ACTION = {
  draft:           { label: 'Add footage',         primary: true },
  uploading:       { label: 'View upload',      primary: false },
  ready:           { label: 'Open video',          primary: false },
  analyzing:       { label: 'Watch progress',   primary: false },
  analysis_failed: { label: 'See what happened',primary: false },
  draft_ready:     { label: 'Watch your edit',     primary: true },
  rendering:       { label: 'Watch progress',   primary: false },
  render_failed:   { label: 'See what happened',primary: false },
  completed:       { label: 'Download video',   primary: true },
  complete:        { label: 'Download video',   primary: true },
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

function StatCard({ label, value, sub, accent }) {
  return (
    <div className="stat-card" style={accent ? { borderColor: `${accent}40` } : {}}>
      <div className="stat-value" style={accent ? { color: accent } : {}}>{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
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

export default function Dashboard() {
  const session = useAuth()
  const navigate = useNavigate()
  const [projects, setProjects] = useState(null)
  const [jobs, setJobs] = useState([])
  const [error, setError] = useState('')

  async function load() {
    const [{ data: projs, error: pe }, { data: js }] = await Promise.all([
      supabase.from('projects')
        .select('id, name, status, created_at, updated_at')
        .order('updated_at', { ascending: false }),
      supabase.from('pipeline_jobs')
        .select('id, project_id, status, kind, created_at, artifacts')
        .in('kind', ['analysis', 'autoedit', 'final_render'])
        .eq('status', 'completed')
        .order('created_at', { ascending: false })
        .limit(10),
    ])
    if (pe) setError(pe.message)
    else setProjects(projs || [])
    if (js) setJobs(js)
  }

  useEffect(() => { load() }, [])

  if (projects === null) {
    return (
      <div className="dashboard-loading">
        <div className="dash-skeleton" />
        <div className="dash-skeleton" style={{ width: '60%' }} />
        <div className="dash-skeleton" style={{ width: '80%' }} />
      </div>
    )
  }

  // Stats
  const total = projects.length
  const active = projects.filter(p => ['uploading','ready','analyzing','rendering'].includes(p.status)).length
  const readyToReview = projects.filter(p => p.status === 'draft_ready').length
  const exported = projects.filter(p => ['completed','complete'].includes(p.status)).length

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
        <div className="empty-icon-wrap">
          <FilmIcon size={32} />
        </div>
        <h1 className="empty-title">Your studio is ready.</h1>
        <p className="empty-sub">
          Upload raw footage. Stromation builds your first edit.
        </p>
        <div className="empty-actions">
          <Link to="/project/new" className="btn btn-primary btn-lg">
            + Create your first video
          </Link>
          <a
            href="https://www.stromation.com/showcase.html"
            className="empty-example-link"
            target="_blank" rel="noopener noreferrer"
          >
            See Project Zero — a real example →
          </a>
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

  // ── Active projects (has projects) ───────────────────────────────────
  const inProgress = projects.filter(p => !['completed','complete'].includes(p.status))
  const done = projects.filter(p => ['completed','complete'].includes(p.status))

  return (
    <div className="dashboard-page wrap">
      {error && <div className="err" role="alert">{error}</div>}

      {/* Header row */}
      <div className="dash-header">
        <div>
          <h1 className="dash-title">Your studio</h1>
          <p className="dash-sub">
            {session?.user?.email}
          </p>
        </div>
        <Link to="/project/new" className="btn btn-primary">+ New video</Link>
      </div>

      {/* Stats row */}
      <div className="stats-row">
        <StatCard label="Total videos" value={total} />
        <StatCard label="Being edited" value={active} accent="var(--cyan)" sub={active > 0 ? 'AI is working' : null} />
        <StatCard label="Ready to watch" value={readyToReview} accent="#a78bfa" sub={readyToReview > 0 ? 'your edit is waiting' : null} />
        <StatCard label="Exported" value={exported} accent="#4ade80" />
      </div>

      {/* Ready-to-review callout */}
      {readyToReview > 0 && (
        <div className="review-callout">
          <span className="review-callout-dot" />
          <div>
            <strong>{readyToReview} {readyToReview > 1 ? 'edits' : 'edit'} ready to watch</strong>
            <p>Stromation finished {readyToReview > 1 ? 'these edits' : 'your edit'}. Watch it and download when you're happy.</p>
          </div>
          <button className="btn btn-primary btn-sm"
            onClick={() => {
              const p = projects.find(p => p.status === 'draft_ready')
              if (p) navigate(`/project/${p.id}`)
            }}>
            Watch it now →
          </button>
        </div>
      )}

      {/* In-progress projects */}
      {inProgress.length > 0 && (
        <section className="dash-section">
          <span className="section-label">Being edited</span>
          <div className="project-grid">
            {inProgress.map(p => <ProjectCard key={p.id} project={p} onDelete={load} />)}
            <Link to="/project/new" className="project-card-new">
              <span className="pcn-plus">+</span>
              <span>New video</span>
            </Link>
          </div>
        </section>
      )}

      {/* Completed exports */}
      {done.length > 0 && (
        <section className="dash-section">
          <span className="section-label">Finished videos</span>
          <div className="project-grid">
            {done.map(p => <ProjectCard key={p.id} project={p} onDelete={load} />)}
          </div>
        </section>
      )}

      {/* Recent export jobs */}
      {jobs.length > 0 && (
        <section className="dash-section">
          <span className="section-label">Recent downloads</span>
          <div className="export-list">
            {jobs.slice(0, 5).map(j => {
              const proj = projects.find(p => p.id === j.project_id)
              return (
                <div key={j.id} className="export-row" onClick={() => proj && navigate(`/project/${proj.id}`)}
                  style={{ cursor: proj ? 'pointer' : 'default' }}>
                  <div className="export-row-icon">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--cyan)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                    </svg>
                  </div>
                  <div className="export-row-info">
                    <p className="export-row-name">{proj?.name || 'Deleted video'}</p>
                    <p className="export-row-meta">
                      {j.kind ? `${j.kind.replace('_', ' ')} · ` : ''}
                      {timeAgo(j.created_at)}
                    </p>
                  </div>
                  <span className="badge completed">exported</span>
                </div>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}

function ProjectCard({ project: p, onDelete }) {
  const navigate = useNavigate()
  const session = useAuth()
  const label = STATUS_LABEL[p.status] || p.status.replaceAll('_', ' ')
  const dotClass = STATUS_DOT[p.status] || 'dot-idle'
  const action = STATUS_ACTION[p.status] || { label: 'Open', primary: false }
  const isActive = ['uploading','ready','analyzing','rendering'].includes(p.status)

  async function del(e) {
    e.stopPropagation()
    if (!confirm(`Delete "${p.name}"? This cannot be undone.`)) return
    try {
      const uid = session.user.id
      for (const bucket of ['raw-footage', 'exports']) {
        const prefix = bucket === 'raw-footage'
          ? `users/${uid}/projects/${p.id}/raw`
          : `users/${uid}/projects/${p.id}/exports`
        const { data: entries } = await supabase.storage.from(bucket).list(prefix, { limit: 100 })
        for (const entry of entries || []) {
          const base = `${prefix}/${entry.name}`
          if (entry.id === null) {
            const { data: files } = await supabase.storage.from(bucket).list(base, { limit: 100 })
            if (files?.length) await supabase.storage.from(bucket).remove(files.map(f => `${base}/${f.name}`))
          } else {
            await supabase.storage.from(bucket).remove([base])
          }
        }
      }
    } catch { /* best effort */ }
    await supabase.from('projects').delete().eq('id', p.id)
    onDelete()
  }

  return (
    <div className="project-card" onClick={() => navigate(`/project/${p.id}`)}>
      <div className="pc-thumb">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
          stroke="rgba(0,212,255,0.2)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="2" width="20" height="20" rx="2.18"/>
          <line x1="7" y1="2" x2="7" y2="22"/>
          <line x1="17" y1="2" x2="17" y2="22"/>
          <line x1="2" y1="12" x2="22" y2="12"/>
        </svg>
        {isActive && <span className="pc-thumb-pulse" />}
      </div>
      <div className="pc-body">
        <p className="pc-name">{p.name}</p>
        <div className="pc-status">
          <span className={`status-dot ${dotClass}`} />
          <span className="pc-status-label">{label}</span>
        </div>
        <p className="pc-meta">{timeAgo(p.updated_at || p.created_at)}</p>
      </div>
      <div className="pc-actions" onClick={e => e.stopPropagation()}>
        <button
          className={`btn btn-sm ${action.primary ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => navigate(`/project/${p.id}`)}>
          {action.label}
        </button>
        <button className="btn btn-sm btn-danger" onClick={del} aria-label={`Delete ${p.name}`}>✕</button>
      </div>
    </div>
  )
}
