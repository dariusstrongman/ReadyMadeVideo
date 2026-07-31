import { useCallback, useEffect, useState } from 'react'
import { RENDER_API, supabase } from '../lib/supabase'
import { useAuth } from '../App'

/* Operator console (P3). Access is enforced SERVER-SIDE:
   - reads come through operator RLS policies (is_operator())
   - actions call operator-only backend endpoints (service role stays server-side)
   - every action is audited in operator_audit
   This page merely renders what the operator's own JWT is allowed to see. */

async function api(session, method, path, body) {
  const r = await fetch(`${RENDER_API}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json',
      Authorization: `Bearer ${session.access_token}` },
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || `${method} ${path} -> ${r.status}`)
  return data
}

export default function Operator() {
  const session = useAuth()
  const [isOperator, setIsOperator] = useState(null)
  const [projects, setProjects] = useState([])
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    supabase.from('operators').select('user_id').maybeSingle()
      .then(({ data }) => setIsOperator(!!data))
  }, [])
  useEffect(() => {
    if (!isOperator) return
    supabase.from('projects')
      .select('id,name,status,status_reason,user_id,created_at')
      .order('created_at', { ascending: false }).limit(100)
      .then(({ data }) => setProjects(data || []))
  }, [isOperator])

  if (isOperator === null) return <div className="wrap"><p className="sub">Checking access…</p></div>
  if (!isOperator)
    return <div className="wrap"><div className="err">Operator access required. This role is granted server-side only.</div></div>

  return (
    <div className="wrap" style={{ maxWidth: 1300 }}>
      <h1>Operator console</h1>
      <p className="small">All actions are audited. Reads via operator RLS; actions via operator-only endpoints.</p>
      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 18, marginTop: 18 }}>
        <div className="grid" style={{ alignContent: 'start' }}>
          {projects.map((p) => (
            <button key={p.id} className="list-item" style={{ textAlign: 'left', cursor: 'pointer',
              borderColor: selected?.id === p.id ? 'var(--cyan)' : undefined }}
              onClick={() => setSelected(p)}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600 }}>{p.name}</div>
                <div className="small mono">{p.id.slice(0, 8)}</div>
              </div>
              <span className={`badge ${p.status}`}>{p.status}</span>
            </button>
          ))}
          {!projects.length && <p className="sub">No projects.</p>}
        </div>
        <div>{selected
          ? <ProjectDetail key={selected.id} project={selected} session={session} />
          : <p className="sub">Select a project.</p>}
        </div>
      </div>
    </div>
  )
}

function Section({ title, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <button onClick={() => setOpen(!open)} style={{ background: 'none', border: 'none',
        color: 'var(--text)', fontWeight: 700, fontSize: '0.95rem', cursor: 'pointer',
        width: '100%', textAlign: 'left' }}>
        {open ? '▾' : '▸'} {title}
      </button>
      {open && <div style={{ marginTop: 12 }}>{children}</div>}
    </div>
  )
}

function Json({ data }) {
  return <pre style={{ background: 'var(--bg-3)', borderRadius: 8, padding: 12,
    fontSize: '0.7rem', overflow: 'auto', maxHeight: 360 }}>{JSON.stringify(data, null, 2)}</pre>
}

function ProjectDetail({ project, session }) {
  const [owner, setOwner] = useState(null)
  const [assets, setAssets] = useState([])
  const [jobs, setJobs] = useState([])
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const refresh = useCallback(async () => {
    const [{ data: prof }, { data: a }, { data: j }] = await Promise.all([
      supabase.from('profiles').select('display_name').eq('user_id', project.user_id).maybeSingle(),
      supabase.from('media_assets').select('*').eq('project_id', project.id),
      supabase.from('pipeline_jobs').select('*').eq('project_id', project.id)
        .order('created_at', { ascending: false }).limit(10),
    ])
    setOwner(prof); setAssets(a || []); setJobs(j || [])
  }, [project.id, project.user_id])
  useEffect(() => { refresh(); const t = setInterval(refresh, 4000); return () => clearInterval(t) }, [refresh])

  async function act(label, fn) {
    setErr(''); setMsg('')
    try { await fn(); setMsg(`${label}: ok`); await refresh() }
    catch (e) { setErr(`${label}: ${e.message}`) }
  }
  const post = (path, body) => api(session, 'POST', path, body)

  return (
    <div>
      <div className="row">
        <h2 style={{ margin: 0, flex: 1 }}>{project.name}</h2>
        <span className={`badge ${project.status}`}>{project.status}</span>
      </div>
      <p className="small">owner: {owner?.display_name || project.user_id.slice(0, 8)} ·
        {' '}reason: {project.status_reason || '—'}</p>
      {err && <div className="err">{err}</div>}
      {msg && <div className="ok">{msg}</div>}

      <div className="row" style={{ margin: '12px 0' }}>
        <button className="btn btn-secondary" onClick={() => act('analyze',
          () => post(`/projects/${project.id}/analyze`, { params: {} }))}>Analyze</button>
        <button className="btn btn-secondary" onClick={() => act('generate draft',
          () => post(`/projects/${project.id}/generate-draft`,
            { params: { brief: prompt('Creative brief?') || project.name } }))}>Generate draft</button>
        <button className="btn btn-secondary" onClick={() => act('revise',
          () => post(`/projects/${project.id}/revise`, { params: {} }))}>Revise</button>
        <button className="btn btn-primary" onClick={() => act('final render',
          () => post(`/projects/${project.id}/render-final`, { params: {} }))}>Approve + render final</button>
      </div>

      <Section title={`Jobs (${jobs.length})`} defaultOpen>
        {jobs.map((j) => (
          <div key={j.id} className="list-item" style={{ marginBottom: 8 }}>
            <span className="mono small">{j.kind}</span>
            <span className={`badge ${j.status === 'processing' ? 'processing' : j.status}`}>{j.status}</span>
            <span className="small">{j.current_stage || ''} {j.progress}%</span>
            <span className="spacer" style={{ flex: 1 }} />
            {j.status === 'failed' && (
              <button className="btn btn-ghost" onClick={() => act('retry',
                () => post(`/jobs/${j.id}/retry`))}>Retry</button>)}
            {['queued', 'processing'].includes(j.status) && (
              <button className="btn btn-danger" onClick={() => act('cancel',
                () => post(`/jobs/${j.id}/cancel`))}>Cancel</button>)}
          </div>
        ))}
        {jobs.filter((j) => j.error_message).map((j) => (
          <p key={j.id} className="small" style={{ color: '#fca5a5' }}>
            {j.kind}: {j.error_message}</p>))}
      </Section>

      <Section title={`Assets (${assets.length})`}>
        {assets.map((a) => <AssetRow key={a.id} asset={a} project={project} session={session} />)}
      </Section>

      <ArtifactsSection project={project} assets={assets} />
      <SegmentsSection project={project} session={session} act={act} post={post} />
      <BlueprintSection project={project} />
      <TimelinesSection project={project} session={session} act={act} post={post} />
      <CoverageSection project={project} session={session} />
      <EvaluationSection project={project} session={session} act={act} post={post} />
      <AuditSection project={project} />
    </div>
  )
}

function AssetRow({ asset, project, session }) {
  const [url, setUrl] = useState(null)
  async function preview() {
    const { url } = await api(session, 'POST', `/projects/${project.id}/sign`,
      { bucket: 'raw-footage', path: asset.storage_path })
    setUrl(url)
  }
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="row">
        <span>{asset.filename}</span>
        <span className="small mono">{(asset.size_bytes / 1048576).toFixed(1)}MB
          {asset.duration_seconds ? ` · ${asset.duration_seconds.toFixed(1)}s` : ''}</span>
        <button className="btn btn-ghost" onClick={preview}>Private preview</button>
      </div>
      {url && <video className="preview" src={url} controls style={{ maxWidth: 480, marginTop: 8 }} />}
    </div>
  )
}

function ArtifactsSection({ project, assets }) {
  const [rows, setRows] = useState([])
  const [open, setOpen] = useState(null)
  useEffect(() => {
    if (!assets.length) return
    supabase.from('asset_analysis').select('id,asset_id,kind,status,error_message,data')
      .in('asset_id', assets.map((a) => a.id))
      .then(({ data }) => setRows(data || []))
  }, [assets])
  return (
    <Section title={`Analysis artifacts (${rows.length}) — probe · scenes · mechanical · transcript · semantic · motion`}>
      <div className="row" style={{ flexWrap: 'wrap' }}>
        {rows.map((r) => (
          <button key={r.id} className={`btn btn-ghost`} onClick={() => setOpen(open === r.id ? null : r.id)}>
            {r.kind}{r.status === 'failed' ? ' ⚠' : ''}
          </button>))}
      </div>
      {rows.filter((r) => r.id === open).map((r) => (
        <div key={r.id}>
          {r.error_message && <p className="small" style={{ color: '#fca5a5' }}>{r.error_message}</p>}
          <Json data={r.data} />
        </div>))}
    </Section>
  )
}

function SegmentsSection({ project, session, act, post }) {
  const [segs, setSegs] = useState([])
  const [q, setQ] = useState('')
  const load = useCallback(async () => {
    let query = supabase.from('segments')
      .select('id,segment_key,source_start,source_end,search_text,data')
      .eq('project_id', project.id).limit(100)
    if (q.trim()) query = query.ilike('search_text', `%${q.trim()}%`)
    const { data } = await query
    setSegs(data || [])
  }, [project.id, q])
  useEffect(() => { load() }, [load])
  return (
    <Section title={`Segment catalog (${segs.length})`}>
      <input placeholder="Search segments…" value={q} onChange={(e) => setQ(e.target.value)}
        style={{ maxWidth: 340, marginBottom: 10 }} />
      {segs.map((s) => (
        <div key={s.id} className="list-item" style={{ marginBottom: 6 }}>
          <span className="mono small">{s.segment_key}</span>
          <span className="small">{s.source_start.toFixed(1)}–{s.source_end.toFixed(1)}s ·
            motion {s.data.motionIntensity} · focus {s.data.focusScore}</span>
          <span className="small" style={{ flex: 1 }}>{(s.search_text || '').slice(0, 70)}</span>
          {s.data.problems?.length > 0 && <span className="badge failed">{s.data.problems.join(',')}</span>}
          <button className="btn btn-ghost" onClick={() => act('flag segment',
            () => post(`/segments/${s.id}/flag`,
              { unusable: !s.data.problems?.includes('operator_unusable'),
                reason: 'operator review' }).then(load))}>
            {s.data.problems?.includes('operator_unusable') ? 'Mark usable' : 'Mark unusable'}
          </button>
        </div>))}
    </Section>
  )
}

function BlueprintSection({ project }) {
  const [run, setRun] = useState(null)
  useEffect(() => {
    supabase.from('edit_runs').select('*').eq('project_id', project.id)
      .order('created_at', { ascending: false }).limit(1)
      .then(({ data }) => setRun(data?.[0] || null))
  }, [project.id])
  if (!run) return <Section title="Story blueprint + selection"><p className="sub">No edit run yet.</p></Section>
  return (
    <Section title="Story blueprint + candidate selection (why every clip was chosen)">
      <h3 className="small" style={{ fontWeight: 700 }}>Blueprint</h3>
      <Json data={run.blueprint} />
      <h3 className="small" style={{ fontWeight: 700 }}>Selection — per-beat ranked candidates + reasons</h3>
      {(run.selection?.beats || []).map((b) => (
        <div key={b.beatKey} style={{ marginBottom: 8 }}>
          <b className="small">{b.beatKey}</b>: <span className="small">
            {b.unfilled ? 'UNFILLED — ' + b.reason : `${b.chosen} — ${b.reason}`}</span>
        </div>))}
      <h3 className="small" style={{ fontWeight: 700 }}>Critic + revision</h3>
      <Json data={{ critic: run.critic_verdict, revision_ops: run.revision_ops,
        validator: run.validator_report }} />
    </Section>
  )
}

function TimelinesSection({ project, session, act, post }) {
  const [tls, setTls] = useState([])
  const [compare, setCompare] = useState([])
  useEffect(() => {
    supabase.from('timelines').select('id,version,created_at,timeline_json')
      .eq('project_id', project.id).order('version')
      .then(({ data }) => setTls(data || []))
  }, [project.id])
  const clipsOf = (tl) => (tl.timeline_json.tracks || [])
    .filter((t) => t.type === 'video').flatMap((t) => t.clips)
    .map((c) => `${c.id}: ${c.assetId.slice(0, 8)} ${c.sourceStart}-${c.sourceEnd}s x${c.speed || 1}`)
  return (
    <Section title={`Timelines (${tls.length} versions) — compare + constrained edits + approve`}>
      <div className="row" style={{ flexWrap: 'wrap' }}>
        {tls.map((t) => (
          <button key={t.id} className="btn btn-ghost"
            onClick={() => setCompare((c) => c.includes(t.id)
              ? c.filter((x) => x !== t.id) : [...c.slice(-1), t.id])}>
            v{t.version}{compare.includes(t.id) ? ' ✓' : ''}
          </button>))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 }}>
        {compare.map((id) => {
          const t = tls.find((x) => x.id === id)
          return t && (
            <div key={id} className="card" style={{ background: 'var(--bg-3)' }}>
              <div className="row">
                <b className="small">v{t.version}</b>
                <button className="btn btn-primary btn-sm" style={{ marginLeft: 'auto' }}
                  onClick={() => act(`final render v${t.version}`,
                    () => post(`/projects/${project.id}/render-final`,
                      { params: { timeline_id: t.id } }))}>Approve → final</button>
              </div>
              {clipsOf(t).map((c, i) => <div key={i} className="small mono">{c}</div>)}
              <TrimForm project={project} timeline={t} act={act} post={post} />
            </div>)
        })}
      </div>
    </Section>
  )
}

function TrimForm({ project, timeline, act, post }) {
  const [clipId, setClipId] = useState('')
  const [s, setS] = useState(''); const [e, setE] = useState('')
  return (
    <div className="row" style={{ marginTop: 8 }}>
      <input placeholder="clipId" value={clipId} onChange={(x) => setClipId(x.target.value)}
        style={{ width: 110 }} />
      <input placeholder="start" value={s} onChange={(x) => setS(x.target.value)} style={{ width: 64 }} />
      <input placeholder="end" value={e} onChange={(x) => setE(x.target.value)} style={{ width: 64 }} />
      <button className="btn btn-ghost" onClick={() => act('trim (constrained op)',
        () => post(`/projects/${project.id}/timeline-ops`, {
          base_timeline_id: timeline.id,
          operations: [{ op: 'trim_clip', clipId,
            ...(s !== '' && { sourceStart: Number(s) }),
            ...(e !== '' && { sourceEnd: Number(e) }) }],
        }))}>Trim</button>
    </div>
  )
}

function CoverageSection({ project, session }) {
  const [rep, setRep] = useState(null)
  const [err, setErr] = useState('')
  return (
    <Section title="Footage coverage (Project One capture checklist)">
      <button className="btn btn-secondary" onClick={async () => {
        try { setRep(await api(session, 'GET', `/projects/${project.id}/coverage`)) }
        catch (e) { setErr(e.message) }
      }}>Run coverage check</button>
      {err && <div className="err">{err}</div>}
      {rep && (
        <div style={{ marginTop: 10 }}>
          {rep.items.map((i) => (
            <div key={i.category} className="small">
              {i.present ? '✅' : (i.optional ? '◻️' : '❌')} {i.label}
              {i.present && <span className="mono"> ({i.matchingSegments.length})</span>}
            </div>))}
          {rep.warnings.map((w, i) => <p key={i} className="small" style={{ color: 'var(--amber)' }}>⚠ {w}</p>)}
        </div>)}
    </Section>
  )
}

function EvaluationSection({ project, session, act, post }) {
  const [ev, setEv] = useState(null)
  const [form, setForm] = useState({})
  useEffect(() => {
    supabase.from('draft_evaluations').select('*').eq('project_id', project.id)
      .order('created_at', { ascending: false }).limit(1)
      .then(({ data }) => setEv(data?.[0] || null))
  }, [project.id])
  const F = (k, type = 'number') => (
    <label className="small" style={{ display: 'inline-block', margin: '4px 10px 4px 0' }}>
      {k}: <input type={type} style={{ width: 70, display: 'inline-block' }}
        onChange={(e) => setForm({ ...form, [k]: type === 'number' ? Number(e.target.value) : e.target.value })} />
    </label>)
  return (
    <Section title="Evaluation (auto metrics + operator-recorded corrections/ratings)">
      {ev ? <Json data={ev} /> : <p className="sub">No evaluation yet (created by generate-draft).</p>}
      <div>
        {F('human_correction_minutes')}{F('clips_manually_replaced')}
        {F('clips_manually_trimmed')}{F('captions_manually_changed')}
        {F('music_adjustments')}{F('first_draft_rating')}{F('final_rating')}
      </div>
      <button className="btn btn-secondary" onClick={() => act('record evaluation',
        () => post(`/projects/${project.id}/evaluation`, { fields: form }))}>
        Record metrics</button>
    </Section>
  )
}

function AuditSection({ project }) {
  const [rows, setRows] = useState([])
  useEffect(() => {
    supabase.from('operator_audit').select('*').eq('project_id', project.id)
      .order('created_at', { ascending: false }).limit(20)
      .then(({ data }) => setRows(data || []))
  }, [project.id])
  return (
    <Section title={`Operator audit trail (${rows.length})`}>
      {rows.map((r) => (
        <div key={r.id} className="small mono">
          {new Date(r.created_at).toLocaleTimeString()} · {r.action} · {JSON.stringify(r.details).slice(0, 90)}
        </div>))}
    </Section>
  )
}
