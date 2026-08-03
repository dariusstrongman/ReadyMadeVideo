import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import { supabase } from '../lib/supabase'

const STATUS_LABEL = {
  draft:           'Waiting for footage',
  uploading:       'Uploading footage',
  ready:           'Ready',
  analyzing:       'Analyzing…',
  analysis_failed: 'Analysis failed',
  draft_ready:     'Review your edit',
  rendering:       'Rendering…',
  render_failed:   'Export failed',
  completed:       'Export ready',
  complete:        'Export ready',
}
const STATUS_ACTION = {
  draft:       { label: 'Upload footage',    primary: true },
  uploading:   { label: 'View project',      primary: false },
  ready:       { label: 'View project',      primary: false },
  analyzing:   { label: 'View progress',     primary: false },
  draft_ready: { label: 'Review your edit',  primary: true },
  rendering:   { label: 'View progress',     primary: false },
  completed:   { label: 'Download export',   primary: true },
  complete:    { label: 'Download export',   primary: true },
}

// Film reel SVG icon
function FilmIcon() {
  return (
    <svg className="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/>
      <line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/>
      <line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/>
      <line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/>
      <line x1="17" y1="7" x2="22" y2="7"/>
    </svg>
  )
}

export default function Dashboard() {
  const session = useAuth()
  const navigate = useNavigate()
  const [projects, setProjects] = useState(null)
  const [error, setError] = useState('')

  async function load() {
    const { data, error } = await supabase.from('projects')
      .select('id,name,status,created_at,media_assets(count)')
      .order('created_at', { ascending: false })
    if (error) setError(error.message)
    else setProjects(data)
  }
  useEffect(() => { load() }, [])

  async function deleteProject(e, p) {
    e.stopPropagation()
    if (!confirm(`Delete "${p.name}"? This cannot be undone.`)) return
    setError('')
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
    const { error } = await supabase.from('projects').delete().eq('id', p.id)
    if (error) setError(error.message)
    await load()
  }

  if (projects === null) return <div className="center"><p className="sub">Loading…</p></div>

  if (projects.length === 0) {
    return (
      <div className="dashboard-empty">
        <FilmIcon />
        <h1 className="empty-title">Your studio is ready.</h1>
        <p className="empty-sub">Upload raw footage and Stromation builds your first edit.</p>
        <div className="empty-actions">
          <Link to="/project/new" className="btn btn-primary btn-lg">Start your first video</Link>
          <a href="https://www.stromation.com/showcase.html" className="sub" style={{ fontSize: '0.82rem' }}>
            See Project Zero example →
          </a>
        </div>
      </div>
    )
  }

  const hero = projects[0]
  const rest = projects.slice(1)
  const heroAction = STATUS_ACTION[hero.status] || { label: 'Open project', primary: false }
  const heroLabel = STATUS_LABEL[hero.status] || hero.status.replaceAll('_', ' ')

  return (
    <div className="wrap dashboard-page">
      {error && <div className="err" role="alert">{error}</div>}

      {/* Hero card — most recent project */}
      <div className="dashboard-hero-card" onClick={() => navigate(`/project/${hero.id}`)} style={{ cursor: 'pointer' }}>
        <div className="dhc-thumb">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="rgba(0,212,255,0.3)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" y1="2" x2="7" y2="22"/>
            <line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/>
          </svg>
          <span className="dhc-thumb-name">{hero.name}</span>
        </div>
        <div className="dhc-body">
          <span className="section-label">Continue</span>
          <h2 className="dhc-name">{hero.name}</h2>
          <p className="dhc-state">{heroLabel}</p>
          <p className="dhc-meta">{new Date(hero.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</p>
          <div className="dhc-actions">
            <button
              className={`btn ${heroAction.primary ? 'btn-primary' : 'btn-ghost'}`}
              onClick={e => { e.stopPropagation(); navigate(`/project/${hero.id}`) }}>
              {heroAction.label}
            </button>
            <button className="btn btn-danger btn-sm" onClick={e => deleteProject(e, hero)}>Delete</button>
          </div>
        </div>
      </div>

      {/* Other projects grid */}
      {rest.length > 0 && (
        <>
          <span className="section-label">All videos</span>
          <div className="project-grid">
            {rest.map(p => {
              const action = STATUS_ACTION[p.status] || { label: 'Open', primary: false }
              const label = STATUS_LABEL[p.status] || p.status.replaceAll('_', ' ')
              return (
                <div key={p.id} className="project-card" onClick={() => navigate(`/project/${p.id}`)}>
                  <div className="pc-thumb">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="rgba(0,212,255,0.25)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" y1="2" x2="7" y2="22"/>
                      <line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/>
                    </svg>
                  </div>
                  <div className="pc-body">
                    <p className="pc-name">{p.name}</p>
                    <p className="pc-meta">{new Date(p.created_at).toLocaleDateString()}</p>
                    <div className="pc-footer">
                      <span className={`badge ${p.status}`}>{label}</span>
                      <button className="btn btn-danger btn-sm" onClick={e => deleteProject(e, p)}>Delete</button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
