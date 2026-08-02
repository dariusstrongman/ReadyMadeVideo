import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../App'
import { supabase } from '../lib/supabase'

const STATUS_COPY = {
  draft: 'Waiting for footage', uploading: 'Footage uploading', ready: 'Ready for analysis',
  analyzing: 'Analyzing footage', analysis_failed: 'Analysis failed',
  draft_ready: 'Candidate ready', rendering: 'Rendering export',
  render_failed: 'Render failed', completed: 'Export ready', complete: 'Export ready',
}

export default function Dashboard() {
  const session = useAuth()
  const [projects, setProjects] = useState(null)
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function load() {
    const { data, error } = await supabase.from('projects')
      .select('id,name,status,created_at').order('created_at', { ascending: false })
    if (error) setError(error.message)
    else setProjects(data)
  }
  useEffect(() => { load() }, [])

  async function createProject(e) {
    e.preventDefault()
    if (!name.trim()) return
    setBusy(true); setError('')
    const { error } = await supabase.from('projects')
      .insert({ name: name.trim(), user_id: session.user.id })
    if (error) setError(error.message)
    else { setName(''); await load() }
    setBusy(false)
  }

  async function deleteProject(p) {
    if (!confirm(`Delete "${p.name}"? Its footage and exports will be removed. This cannot be undone.`)) return
    setError('')
    // Best-effort storage cleanup first (DB rows cascade via FK on delete).
    // Documented behavior: if a storage delete fails, orphaned objects can
    // remain and are cleaned up manually — the DB is the source of truth.
    try {
      const uid = session.user.id
      for (const bucket of ['raw-footage', 'exports']) {
        const prefixes = bucket === 'raw-footage'
          ? [`users/${uid}/projects/${p.id}/raw`] : [`users/${uid}/projects/${p.id}/exports`]
        for (const prefix of prefixes) {
          const { data: entries } = await supabase.storage.from(bucket)
            .list(prefix, { limit: 100 })
          for (const entry of entries || []) {
            const base = `${prefix}/${entry.name}`
            if (entry.id === null) { // folder (asset id level) — list one deeper
              const { data: files } = await supabase.storage.from(bucket).list(base, { limit: 100 })
              if (files?.length)
                await supabase.storage.from(bucket).remove(files.map(f => `${base}/${f.name}`))
            } else {
              await supabase.storage.from(bucket).remove([base])
            }
          }
        }
      }
    } catch { /* best effort */ }
    const { error } = await supabase.from('projects').delete().eq('id', p.id)
    if (error) setError(error.message)
    await load()
  }

  return (
    <div className="wrap">
      <h1>Projects</h1>
      <p className="sub">Upload footage, review finished candidates, edit, and export.</p>
      {error && <div className="err" role="alert">{error}</div>}

      <form className="row" style={{ margin: '20px 0' }} onSubmit={createProject}>
        <input style={{ maxWidth: 360 }} placeholder="New project name"
          value={name} onChange={(e) => setName(e.target.value)} aria-label="New project name" />
        <button className="btn btn-primary" disabled={busy || !name.trim()}>Create project</button>
      </form>

      <div className="grid">
        {projects === null && <p className="sub">Loading…</p>}
        {projects?.length === 0 && <p className="sub">No projects yet.</p>}
        {projects?.map((p) => (
          <div key={p.id} className="list-item">
            <div style={{ flex: 1 }}>
              <Link to={`/project/${p.id}`} style={{ fontWeight: 600 }}>{p.name}</Link>
              <div className="small">created {new Date(p.created_at).toLocaleString()}</div>
              <div className="small">{STATUS_COPY[p.status] || p.status.replaceAll('_', ' ')}</div>
            </div>
            <span className={`badge ${p.status}`}>{p.status}</span>
            <button className="btn btn-danger" onClick={() => deleteProject(p)}>Delete</button>
          </div>
        ))}
      </div>
    </div>
  )
}
