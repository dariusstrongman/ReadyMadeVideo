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
  const [artifactRefresh, setArtifactRefresh] = useState(0)

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
    try { await fn(); setMsg(`${label}: ok`); setArtifactRefresh((value) => value + 1); await refresh() }
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
        <button className="btn btn-secondary" onClick={() => act('preproduction',
          () => post(`/projects/${project.id}/preproduction`, {
            purpose: prompt('Purpose?') || 'cinematic fitness recap',
            targetPlatform: 'vertical',
          }))}>Build creative treatment</button>
        <button className="btn btn-secondary" onClick={() => act('picture edit',
          () => post(`/projects/${project.id}/picture-edit`, {}))}>
          Build picture candidates</button>
        <button className="btn btn-secondary" onClick={() => act('music and sound plan',
          () => post(`/projects/${project.id}/music-sound`, {}))}>
          Build music + sound plan</button>
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
            {j.status === 'cancel_requested' && (
              <span className="small" style={{ color: 'var(--amber)' }}>
                Cancelling… (stops at the next checkpoint; in-flight provider
                calls cannot be interrupted)</span>)}
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
      <PreproductionSection project={project} refreshKey={artifactRefresh} />
      <PictureEditSection project={project} refreshKey={artifactRefresh} />
      <MusicSoundSection project={project} refreshKey={artifactRefresh} />
      <AudioRenderSection project={project} session={session} act={act}
        refreshKey={artifactRefresh} />
      <VisualFinishingSection project={project} session={session} act={act}
        refreshKey={artifactRefresh} />
      <EditorialIntelligenceSection project={project} session={session} act={act}
        refreshKey={artifactRefresh} />
      <BlueprintSection project={project} />
      <TimelinesSection project={project} session={session} act={act} post={post} />
      <HumanCeilingSection project={project} session={session} />
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

function PreproductionSection({ project, refreshKey }) {
  const [run, setRun] = useState(null)
  useEffect(() => {
    supabase.from('preproduction_runs').select('*').eq('project_id', project.id)
      .order('version', { ascending: false }).limit(1)
      .then(({ data }) => setRun(data?.[0] || null))
  }, [project.id, refreshKey])
  if (!run) return (
    <Section title="Creative Treatment + capture ceiling + story variants">
      <p className="sub">No Milestone 1 preproduction run yet.</p>
    </Section>
  )
  return (
    <Section title={`Preproduction v${run.version} — ${run.status}`} defaultOpen>
      {(run.warnings || []).map((warning) => (
        <div className="err" key={warning}>{warning}</div>
      ))}
      <h3 className="small" style={{ fontWeight: 700 }}>Creative Treatment</h3>
      <Json data={run.creative_treatment} />
      <h3 className="small" style={{ fontWeight: 700 }}>Capture Quality Report</h3>
      <Json data={run.capture_quality_report} />
      <h3 className="small" style={{ fontWeight: 700 }}>Five story directions</h3>
      {(run.story_variants?.variants || []).map((variant) => (
        <div className="list-item" key={variant.variantId} style={{ marginBottom: 8 }}>
          <div style={{ flex: 1 }}>
            <b className="small">{variant.label}</b>
            <div className="small">{variant.editorialIntent}</div>
            {!variant.valid && <div className="small" style={{ color: '#fca5a5' }}>
              Rejected: {variant.rejectionReasons.join('; ')}</div>}
          </div>
          <span className={`badge ${variant.valid ? 'completed' : 'failed'}`}>
            {variant.valid ? 'supported' : 'unsupported'}
          </span>
        </div>
      ))}
    </Section>
  )
}

function PictureEditSection({ project, refreshKey }) {
  const [run, setRun] = useState(null)
  useEffect(() => {
    supabase.from('picture_edit_runs').select('*').eq('project_id', project.id)
      .order('version', { ascending: false }).limit(1)
      .then(({ data }) => setRun(data?.[0] || null))
  }, [project.id, refreshKey])
  if (!run) return (
    <Section title="Milestone 2 picture edit · visual rhythm + three candidates">
      <p className="sub">Build a Creative Treatment first, then generate picture-only candidates.</p>
    </Section>
  )
  return (
    <Section title={`Picture edit v${run.version} — ${run.status}`} defaultOpen>
      <p className="small">Picture-only timelines. No music, sound design, graphics, captions,
        color, or critic output has been added.</p>
      {(run.warnings || []).map((warning) => <div className="err" key={warning}>{warning}</div>)}
      <h3 className="small" style={{ fontWeight: 700 }}>Visual rhythm plans</h3>
      <Json data={run.visual_rhythm_plans} />
      <h3 className="small" style={{ fontWeight: 700 }}>Picture-edit candidates</h3>
      {(run.candidates || []).map((candidate) => (
        <div className="list-item" key={candidate.candidateId} style={{ marginBottom: 8 }}>
          <div style={{ flex: 1 }}>
            <b className="small">{candidate.label}</b>
            <div className="small mono">{candidate.storyVariantId} · {candidate.clipCount} clips ·
              {' '}{candidate.durationSeconds}s · score {candidate.editorialScore}</div>
            <div className="small">coverage {(candidate.coverageRatio * 100).toFixed(0)}%
              {candidate.rejectionReasons?.length
                ? ` · ${candidate.rejectionReasons.join('; ')}` : ''}</div>
          </div>
          {run.selected_candidate_id === candidate.candidateId &&
            <span className="badge processing">selected default</span>}
          <span className={`badge ${candidate.valid ? 'completed' : 'failed'}`}>
            {candidate.valid ? 'supported' : 'unsupported'}</span>
        </div>
      ))}
    </Section>
  )
}

function MusicSoundSection({ project, refreshKey }) {
  const [run, setRun] = useState(null)
  useEffect(() => {
    supabase.from('music_sound_runs').select('*').eq('project_id', project.id)
      .order('version', { ascending: false }).limit(1)
      .then(({ data }) => setRun(data?.[0] || null))
  }, [project.id, refreshKey])
  if (!run) return (
    <Section title="Milestone 3 music supervisor · music and sound plan">
      <p className="sub">Select a supported Milestone 2 picture candidate first.</p>
    </Section>
  )
  const plan = run.music_plan || {}
  const analysis = plan.beatPhraseAnalysis || {}
  const loudness = plan.loudnessTargets || {}
  const chatter = (plan.naturalAudioEvents || []).filter(
    (event) => event.classification === 'background_chatter')
  return (
    <Section title={`Music + sound v${run.version} — ${run.status}`} defaultOpen>
      <p className="small">Planning only. Selected picture timing remains immutable; no track
        file is selected or rendered.</p>
      <div className="grid-3">
        <div className="card small"><b>Tempo</b><br />{analysis.tempoBpm || '—'} BPM</div>
        <div className="card small"><b>Phrases</b><br />{analysis.phrases?.length || 0}</div>
        <div className="card small"><b>Loudness</b><br />
          {loudness.integratedLufs || '—'} LUFS</div>
      </div>
      <p className="small mono">candidate {plan.selectedCandidateId} ·
        {' '}{plan.pictureDurationSeconds}s · chatter windows {chatter.length} ·
        {' '}ducking moves {plan.musicDucking?.length || 0} ·
        {' '}impact accents {plan.impactEmphasis?.length || 0}</p>
      <h3 className="small" style={{ fontWeight: 700 }}>Beat + phrase analysis</h3>
      <Json data={analysis} />
      <h3 className="small" style={{ fontWeight: 700 }}>Natural audio + mix automation</h3>
      <Json data={{ events: plan.naturalAudioEvents,
        sourceInstructions: plan.sourceAudioInstructions,
        musicDucking: plan.musicDucking,
        impactEmphasis: plan.impactEmphasis }} />
      <h3 className="small" style={{ fontWeight: 700 }}>Ending + picture synchronization</h3>
      <Json data={{ fades: plan.fades, loudnessTargets: plan.loudnessTargets,
        musicalEnding: plan.musicalEnding, pictureMusicSync: plan.pictureMusicSync }} />
    </Section>
  )
}

function AudioRenderSection({ project, session, act, refreshKey }) {
  const [run, setRun] = useState(null)
  const [file, setFile] = useState(null)
  const [provider, setProvider] = useState('')
  const [licenseType, setLicenseType] = useState('commercial')
  const [licenseReference, setLicenseReference] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [previewUrl, setPreviewUrl] = useState(null)
  useEffect(() => {
    supabase.from('audio_mix_runs').select('*').eq('project_id', project.id)
      .order('version', { ascending: false }).limit(1)
      .then(({ data }) => setRun(data?.[0] || null))
  }, [project.id, refreshKey])
  useEffect(() => {
    if (!run?.preview_storage_path) { setPreviewUrl(null); return }
    api(session, 'POST', `/projects/${project.id}/sign`, {
      bucket: 'exports', path: run.preview_storage_path, expires_in: 3600,
    }).then(({ url }) => setPreviewUrl(url)).catch(() => setPreviewUrl(null))
  }, [project.id, run?.preview_storage_path, session])

  async function attachAndRender() {
    if (!file) throw new Error('choose a licensed music file')
    if (!provider.trim() || !licenseReference.trim() || !confirmed) {
      throw new Error('provider, license reference, and license confirmation are required')
    }
    const query = new URLSearchParams({ filename: file.name,
      content_type: file.type || 'application/octet-stream' })
    const uploadedResponse = await fetch(
      `${RENDER_API}/projects/${project.id}/licensed-music/upload?${query}`,
      { method: 'POST', headers: { Authorization: `Bearer ${session.access_token}` },
        body: file },
    )
    const uploaded = await uploadedResponse.json().catch(() => ({}))
    if (!uploadedResponse.ok) throw new Error(uploaded.detail || 'licensed track upload failed')
    return api(session, 'POST', `/projects/${project.id}/audio-render`, {
      ...uploaded,
      licenseMetadata: { provider: provider.trim(), licenseType,
        licenseReference: licenseReference.trim(), confirmedByOperator: true },
    })
  }

  const completed = run?.mix_instructions || {}
  const analysis = completed.analysis || {}
  const target = run?.target_vs_actual || {}
  const qc = run?.audio_qc || {}
  return (
    <Section title="Milestone 4 completed audio mix · licensed waveform + preview" defaultOpen>
      <div className="card" style={{ background: 'var(--bg-3)', marginBottom: 10 }}>
        <label className="small">Licensed track
          <input type="file" accept="audio/wav,audio/mpeg,audio/mp4,audio/aac,audio/flac,.m4a"
            onChange={(event) => setFile(event.target.files?.[0] || null)} />
        </label>
        <label className="small">License provider
          <input value={provider} onChange={(event) => setProvider(event.target.value)}
            placeholder="Artlist, Musicbed, direct composer…" /></label>
        <label className="small">License type
          <input value={licenseType} onChange={(event) => setLicenseType(event.target.value)} /></label>
        <label className="small">License/reference ID or receipt
          <input value={licenseReference}
            onChange={(event) => setLicenseReference(event.target.value)} /></label>
        <label className="small"><input type="checkbox" checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)} /> I confirm this project
          is licensed to use this track.</label>
        <button className="btn btn-secondary" onClick={() => act(
          run ? 'replace licensed track and rerender' : 'attach licensed track and render',
          attachAndRender)}>{run ? 'Replace track + render new version' : 'Attach + render preview'}</button>
      </div>
      {!run && <p className="sub">No completed Milestone 4 audio mix yet.</p>}
      {run && <>
        <p className="small mono">v{run.version} · {run.status} · picture timing unchanged ·
          {' '}target {target.targetBpm} BPM vs actual {target.actualBpm} BPM ·
          {' '}actual analysis: {analysis.analysisSource}</p>
        <div className="grid-3">
          <div className="card small"><b>Integrated</b><br />{qc.integratedLufs} LUFS</div>
          <div className="card small"><b>True peak</b><br />{qc.truePeakDbtp} dBTP</div>
          <div className="card small"><b>QC</b><br />{qc.passed ? 'passed' : 'review required'}</div>
        </div>
        <h3 className="small" style={{ fontWeight: 700 }}>Treatment target vs actual waveform</h3>
        <Json data={{ targetVsActual: target, actualWaveformAnalysis: analysis }} />
        <h3 className="small" style={{ fontWeight: 700 }}>Completed mix + audio QC</h3>
        <Json data={{ ducking: completed.mergedDuckingEnvelopes,
          sourceAudio: completed.sourceAudioInstructions, qc }} />
        {previewUrl && <video className="preview" src={previewUrl} controls
          style={{ maxWidth: 480, marginTop: 8 }} />}
      </>}
    </Section>
  )
}

function VisualFinishingSection({ project, session, act, refreshKey }) {
  const [graphics, setGraphics] = useState(null)
  const [captions, setCaptions] = useState(null)
  const [color, setColor] = useState(null)
  const [aspect, setAspect] = useState('9:16')
  const [lutPreset, setLutPreset] = useState('none')
  const [previewUrl, setPreviewUrl] = useState(null)
  useEffect(() => {
    Promise.all([
      supabase.from('graphics_runs').select('*').eq('project_id', project.id)
        .order('version', { ascending: false }).limit(1),
      supabase.from('caption_runs').select('*').eq('project_id', project.id)
        .order('version', { ascending: false }).limit(1),
      supabase.from('color_runs').select('*').eq('project_id', project.id)
        .order('version', { ascending: false }).limit(1),
    ]).then(([g, c, grade]) => {
      setGraphics(g.data?.[0] || null)
      setCaptions(c.data?.[0] || null)
      setColor(grade.data?.[0] || null)
    })
  }, [project.id, refreshKey])
  useEffect(() => {
    if (!color?.preview_storage_path) { setPreviewUrl(null); return }
    api(session, 'POST', `/projects/${project.id}/sign`, {
      bucket: 'exports', path: color.preview_storage_path, expires_in: 3600,
    }).then(({ url }) => setPreviewUrl(url)).catch(() => setPreviewUrl(null))
  }, [color?.preview_storage_path, project.id, session])

  const timeline = graphics?.graphics_timeline || {}
  const captionTimeline = captions?.caption_timeline || {}
  const grade = color?.color_instructions || {}
  const template = graphics?.brand_template || {}
  return (
    <Section title="Milestone 5 visual finishing · graphics + captions + color" defaultOpen>
      <div className="row" style={{ marginBottom: 10 }}>
        <label className="small">Platform <select value={aspect}
          onChange={(event) => setAspect(event.target.value)}>
          <option value="9:16">9:16 vertical</option><option value="1:1">1:1 square</option>
          <option value="16:9">16:9 landscape</option></select></label>
        <label className="small">Look <select value={lutPreset}
          onChange={(event) => setLutPreset(event.target.value)}>
          <option value="none">Neutral</option><option value="clean_warm">Clean warm</option>
          <option value="cool_contrast">Cool contrast</option>
          <option value="neutral_social">Neutral social</option></select></label>
        <button className="btn btn-secondary" onClick={() => act('build visual finishing',
          () => api(session, 'POST', `/projects/${project.id}/visual-finishing`, {
            aspect, lutPreset,
          }))}>Build finishing preview</button>
      </div>
      {!color && <p className="sub">A QC-passed Milestone 4 audio mix is required.</p>}
      {color && <>
        <p className="small mono">v{color.version} · {color.status} · immutable ·
          {' '}picture timing unchanged · audio unchanged</p>
        {previewUrl && <video className="preview" src={previewUrl} controls
          style={{ maxWidth: 480, marginTop: 8 }} />}
        <h3 className="small" style={{ fontWeight: 700 }}>Template preview</h3>
        <div className="row">
          {[template.primary, template.secondary, template.accent].filter(Boolean).map((value) =>
            <div key={value} className="card small" style={{ background: value,
              color: value === '#FFFFFF' ? '#101820' : '#fff', minWidth: 120 }}>{value}</div>)}
          <span className="small">{template.fontFamily} · {timeline.platform?.aspect}</span>
        </div>
        <h3 className="small" style={{ fontWeight: 700 }}>Graphics timeline</h3>
        <Json data={{ safeTitle: timeline.platform?.safeTitle,
          phraseBoundaries: timeline.phraseBoundaries, events: timeline.events }} />
        <h3 className="small" style={{ fontWeight: 700 }}>Caption preview + timing</h3>
        <Json data={{ timingProvenance: captions?.timing_provenance,
          evidenceDecisions: captionTimeline.evidenceDecisions,
          overlapsDetected: captionTimeline.overlapsDetected, groups: captionTimeline.groups }} />
        <h3 className="small" style={{ fontWeight: 700 }}>Color preview instructions</h3>
        <Json data={{ normalizationTarget: grade.normalizationTarget,
          lutPreset: grade.lutPreset, instructions: grade.instructions,
          renderQc: color.render_qc }} />
      </>}
    </Section>
  )
}

function EditorialIntelligenceSection({ project, session, act, refreshKey }) {
  const [tournament, setTournament] = useState(null)
  const [candidates, setCandidates] = useState([])
  const [critics, setCritics] = useState([])
  const [reports, setReports] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [previewUrl, setPreviewUrl] = useState(null)
  useEffect(() => {
    supabase.from('tournament_runs').select('*').eq('project_id', project.id)
      .order('version', { ascending: false }).limit(1).then(async ({ data }) => {
        const latest = data?.[0] || null
        setTournament(latest)
        if (!latest) { setCandidates([]); setCritics([]); setReports([]); return }
        const [candidateResult, criticResult, reportResult] = await Promise.all([
          supabase.from('candidate_runs').select('*').eq('batch_id', latest.batch_id)
            .order('candidate_index'),
          supabase.from('critic_runs').select('*').eq('batch_id', latest.batch_id)
            .order('critic_kind'),
          supabase.from('publishability_reports').select('*').eq('batch_id', latest.batch_id)
            .order('overall_publishability_score', { ascending: false }),
        ])
        setCandidates(candidateResult.data || [])
        setCritics(criticResult.data || [])
        setReports(reportResult.data || [])
        setSelectedId(latest.winner_candidate_run_id)
      })
  }, [project.id, refreshKey])
  const selected = candidates.find((item) => item.id === selectedId)
  useEffect(() => {
    if (!selected?.preview_storage_path) { setPreviewUrl(null); return }
    api(session, 'POST', `/projects/${project.id}/sign`, {
      bucket: 'exports', path: selected.preview_storage_path, expires_in: 3600,
    }).then(({ url }) => setPreviewUrl(url)).catch(() => setPreviewUrl(null))
  }, [selected?.preview_storage_path, project.id, session])
  const selectedCritics = critics.filter((item) => item.candidate_run_id === selectedId)
  const selectedReport = reports.find((item) => item.candidate_run_id === selectedId)
  return (
    <Section title="Milestone 6 editorial intelligence · critics + tournament + winner" defaultOpen>
      <p className="small">Creates immutable complete candidates, applies only evidence-requested bounded
        revisions, runs ten independent structured critics, compares every pair, and selects the highest
        publishability result. Existing source footage and Milestones 1–5 remain unchanged.</p>
      <button className="btn btn-primary" onClick={() => act('build editorial tournament',
        () => api(session, 'POST', `/projects/${project.id}/editorial-intelligence`, {
          includeBoundedRevision: true,
        }))}>Generate, critique + select winner</button>
      {!tournament && <p className="sub">A QC-passed Milestone 5 finishing run is required.</p>}
      {tournament && <>
        <p className="small mono">v{tournament.version} · immutable batch {tournament.batch_id.slice(0, 8)} ·
          {' '}{candidates.length} candidates · {tournament.pairwise_comparisons.length} pairwise comparisons</p>
        <h3 className="small" style={{ fontWeight: 700 }}>Candidate browser + winner selection</h3>
        <div className="row" style={{ flexWrap: 'wrap' }}>
          {candidates.map((item) => <button key={item.id}
            className={item.id === tournament.winner_candidate_run_id ? 'btn btn-primary' : 'btn btn-ghost'}
            onClick={() => setSelectedId(item.id)}>
            {item.candidate_key}{item.id === tournament.winner_candidate_run_id ? ' · winner' : ''}
          </button>)}
        </div>
        {selected && <>
          {previewUrl && <video className="preview" src={previewUrl} controls
            style={{ maxWidth: 480, marginTop: 10 }} />}
          <Json data={{ generationKind: selected.generation_kind,
            parentCandidateRunId: selected.parent_candidate_run_id,
            sourcePictureCandidateId: selected.source_picture_candidate_id,
            variant: selected.variant_config, renderQc: selected.render_qc,
            fabricatedFootage: selected.fabricated_footage }} />
          <h3 className="small" style={{ fontWeight: 700 }}>Structured critic reports</h3>
          <Json data={selectedCritics.map((item) => ({ critic: item.critic_kind,
            score: item.score, passed: item.passed, evidence: item.evidence,
            revisionRequests: item.revision_requests, consistencyHash: item.consistency_hash }))} />
          <h3 className="small" style={{ fontWeight: 700 }}>Publishability report</h3>
          <Json data={selectedReport} />
        </>}
        <h3 className="small" style={{ fontWeight: 700 }}>Tournament bracket + all pairwise evidence</h3>
        <Json data={{ bracket: tournament.bracket,
          pairwiseComparisons: tournament.pairwise_comparisons,
          winnerReasoning: tournament.winner_reasoning }} />
        <h3 className="small" style={{ fontWeight: 700 }}>Human-ceiling comparison viewer</h3>
        <Json data={tournament.human_ceiling_comparison} />
      </>}
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
    supabase.from('timelines').select('id,version,created_at,timeline_json,lineage,parent_timeline_id,is_immutable')
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
            v{t.version} · {t.lineage || 'legacy'}{t.is_immutable ? ' · locked' : ''}
            {compare.includes(t.id) ? ' ✓' : ''}
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
              {!t.is_immutable && <TrimForm project={project} timeline={t} act={act} post={post} />}
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

const SCORE_FIELDS = ['hook', 'story_clarity', 'shot_selection', 'shot_variety',
  'pacing', 'continuity', 'action_visibility', 'emotional_intensity',
  'natural_audio', 'audio_mix', 'captions_titles', 'color_consistency',
  'ending_payoff']

function HumanCeilingSection({ project, session: authSession }) {
  const [timelines, setTimelines] = useState([])
  const [humanSession, setHumanSession] = useState(null)
  const [abandonedSession, setAbandonedSession] = useState(null)
  const [initialId, setInitialId] = useState('')
  const [revisedId, setRevisedId] = useState('')
  const [operationText, setOperationText] = useState(
    JSON.stringify({ op: 'trim_clip', clipId: '', sourceStart: 0, sourceEnd: 1 }, null, 2))
  const [note, setNote] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [serverMeasured, setServerMeasured] = useState(0)
  const [timerRunning, setTimerRunning] = useState(false)
  const [report, setReport] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    const [{ data: tls }, { data: sessions }] = await Promise.all([
      supabase.from('timelines')
        .select('id,version,created_at,timeline_json,lineage,parent_timeline_id,is_immutable')
        .eq('project_id', project.id).order('version'),
      supabase.from('human_edit_sessions').select('*').eq('project_id', project.id)
        .order('created_at', { ascending: false }).limit(1),
    ])
    const rows = tls || []
    setTimelines(rows)
    const latest = sessions?.[0] || null
    const current = latest?.status === 'abandoned' ? null : latest
    setHumanSession(current)
    setAbandonedSession(latest?.status === 'abandoned' ? latest : null)
    const initial = rows.find((t) => t.lineage === 'autonomous_initial')
    const revised = rows.find((t) => t.lineage === 'autonomous_revised')
    if (initial) setInitialId(initial.id)
    if (revised) setRevisedId(revised.id)
    if (latest) {
      setInitialId(latest.autonomous_initial_timeline_id)
      setRevisedId(latest.autonomous_revised_timeline_id || '')
      setServerMeasured(Number(latest.server_measured_seconds || 0))
      setElapsed(Number(latest.client_reported_seconds || 0))
      setTimerRunning(latest.status === 'active' && latest.timing_state === 'running')
    }
  }, [project.id])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (!timerRunning) return undefined
    const timer = setInterval(() => setElapsed((value) => value + 1), 1000)
    return () => clearInterval(timer)
  }, [timerRunning])

  async function perform(label, fn) {
    setErr(''); setMsg('')
    try {
      const value = await fn()
      setMsg(`${label}: ok`)
      await load()
      return value
    } catch (e) {
      setErr(`${label}: ${e.message}`)
      return null
    }
  }

  const baselineOptions = timelines.filter((t) =>
    ['legacy', 'autonomous_initial', 'autonomous_revised'].includes(t.lineage || 'legacy'))
  const currentTimeline = timelines.find((t) => t.id === humanSession?.current_timeline_id)
  const approvedTimeline = timelines.find((t) => t.id === humanSession?.approved_timeline_id)
  const canStart = initialId && (!revisedId || initialId !== revisedId)

  async function start() {
    const data = await perform('start human ceiling', () => api(authSession, 'POST',
      `/projects/${project.id}/human-ceiling/start`, {
        autonomous_initial_timeline_id: initialId,
        ...(revisedId && { autonomous_revised_timeline_id: revisedId }),
      }))
    if (data) {
      setElapsed(0); setServerMeasured(0); setTimerRunning(true)
    }
  }

  async function applyManualOperation() {
    let operation
    try { operation = JSON.parse(operationText) }
    catch { setErr('manual operation: JSON is invalid'); return }
    const data = await perform('manual operation', () => api(authSession, 'POST',
      `/projects/${project.id}/timeline-ops`, {
        base_timeline_id: humanSession.current_timeline_id,
        human_edit_session_id: humanSession.id,
        operations: [operation], client_reported_seconds: elapsed, note,
      }))
    if (data) setServerMeasured(Number(data.timing?.server_measured_seconds || 0))
  }

  async function pause() {
    const data = await perform('pause human timing', () => api(authSession, 'POST',
      `/projects/${project.id}/human-ceiling/pause`, {
        session_id: humanSession.id, client_reported_seconds: elapsed,
      }))
    if (data) { setTimerRunning(false); setServerMeasured(data.server_measured_seconds) }
  }

  async function resume() {
    const data = await perform('resume human timing', () => api(authSession, 'POST',
      `/projects/${project.id}/human-ceiling/resume`, {
        session_id: humanSession.id, client_reported_seconds: elapsed,
      }))
    if (data) { setTimerRunning(true); setServerMeasured(data.server_measured_seconds) }
  }

  async function approve() {
    const data = await perform('approve human timeline', () => api(authSession, 'POST',
      `/projects/${project.id}/human-ceiling/approve`, {
        session_id: humanSession.id, client_reported_seconds: elapsed,
      }))
    if (data) {
      setTimerRunning(false)
      setServerMeasured(Number(data.timing?.server_measured_seconds || 0))
    }
  }

  async function abandon() {
    const reason = window.prompt('Reason for abandoning this human-ceiling session?')?.trim()
    if (!reason) { setErr('abandon: a reason is required'); return }
    if (!window.confirm('Abandon this session? The human draft will be frozen as non-approved evidence.')) return
    const data = await perform('abandon human session', () => api(authSession, 'POST',
      `/projects/${project.id}/human-ceiling/abandon`, {
        session_id: humanSession.id, reason, client_reported_seconds: elapsed,
      }))
    if (data) {
      setTimerRunning(false)
      setServerMeasured(Number(data.timing?.server_measured_seconds || 0))
    }
  }

  async function generateReport() {
    const data = await perform('generate comparison report', () => api(authSession, 'GET',
      `/projects/${project.id}/human-ceiling/report?session_id=${humanSession.id}`))
    if (data) setReport(data)
  }

  return (
    <Section title="Human-ceiling comparison: autonomous evidence vs human approved" defaultOpen>
      <p className="small">Starting a session freezes the available autonomous evidence and clones the
        revised timeline—or the initial timeline when no real revision exists—into a separate human lineage.
        Server timestamps are authoritative; the browser timer is only a diagnostic hint.</p>
      {err && <div className="err">{err}</div>}
      {msg && <div className="ok">{msg}</div>}
      {!humanSession && <div className="grid grid-2" style={{ marginTop: 10 }}>
        <label className="small">Autonomous initial
          <select value={initialId} onChange={(e) => setInitialId(e.target.value)}>
            <option value="">Select timeline</option>
            {baselineOptions.map((t) => <option key={t.id} value={t.id}>v{t.version} · {t.lineage}</option>)}
          </select>
        </label>
        <label className="small">Autonomous revised (optional)
          <select value={revisedId} onChange={(e) => setRevisedId(e.target.value)}>
            <option value="">No distinct revised timeline</option>
            {baselineOptions.map((t) => <option key={t.id} value={t.id}>v{t.version} · {t.lineage}</option>)}
          </select>
        </label>
        <button className="btn btn-primary" disabled={!canStart} onClick={start}>
          Freeze autonomous evidence + begin human lineage</button>
      </div>}
      {abandonedSession && !humanSession && <p className="small" style={{ color: 'var(--amber)' }}>
        Previous session abandoned: {abandonedSession.abandonment_reason}. Its draft and corrections remain
        frozen as non-approved evidence; a new session may be started.</p>}
      {humanSession && <div style={{ marginTop: 12 }}>
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <span className={`badge ${humanSession.status === 'active' ? 'processing' : 'completed'}`}>
            {humanSession.status}</span>
          <span className="small mono">human v{currentTimeline?.version || '?'} · server authoritative:
            {' '}{serverMeasured.toFixed(1)}s · client hint: {elapsed}s</span>
          {humanSession.status === 'active' && <>
            <button className="btn btn-ghost" onClick={timerRunning ? pause : resume}>
              {timerRunning ? 'Pause server timing' : 'Resume server timing'}</button>
            <label className="small">Client timer hint <input type="number" min="0" value={elapsed}
              onChange={(e) => setElapsed(Math.max(0, Number(e.target.value)))}
              style={{ width: 100, display: 'inline-block' }} /></label>
          </>}
        </div>
        {humanSession.status === 'active' && <div className="grid" style={{ marginTop: 10 }}>
          <label className="small">One constrained operation (replacement, trim, reorder, audio, title, etc.)
            <textarea rows="7" className="mono" value={operationText}
              onChange={(e) => setOperationText(e.target.value)} /></label>
          <input placeholder="Reason for this human decision" value={note}
            onChange={(e) => setNote(e.target.value)} />
          <div className="row">
            <button className="btn btn-secondary" onClick={applyManualOperation}>Apply + record operation</button>
            <button className="btn btn-primary" onClick={approve}>Approve human timeline</button>
            <button className="btn btn-danger" onClick={abandon}>Abandon session</button>
          </div>
        </div>}
        {humanSession.status === 'approved' && <>
          <p className="small">Approved human timeline: v{approvedTimeline?.version || '?'}.
            {' '}{humanSession.autonomous_revised_timeline_id ? 'All three versions' : 'Initial and human versions'}
            {' '}are immutable evidence. Correction time is server-measured.</p>
          <div className="grid grid-3">
            <ScorecardEditor label="Autonomous initial" timelineId={humanSession.autonomous_initial_timeline_id}
              project={project} session={humanSession} authSession={authSession} perform={perform} />
            {humanSession.autonomous_revised_timeline_id &&
              <ScorecardEditor label="Autonomous revised" timelineId={humanSession.autonomous_revised_timeline_id}
                project={project} session={humanSession} authSession={authSession} perform={perform} />}
            <ScorecardEditor label="Human approved" timelineId={humanSession.approved_timeline_id}
              project={project} session={humanSession} authSession={authSession} perform={perform} />
          </div>
          <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={generateReport}>
            Generate side-by-side report</button>
          {report && <pre style={{ whiteSpace: 'pre-wrap', marginTop: 10 }}>{report.markdown}</pre>}
        </>}
      </div>}
    </Section>
  )
}

function ScorecardEditor({ label, timelineId, project, session, authSession, perform }) {
  const [overall, setOverall] = useState('')
  const [publishable, setPublishable] = useState('')
  const [scores, setScores] = useState({})
  const [notes, setNotes] = useState('')
  return <div className="card" style={{ background: 'var(--bg-3)' }}>
    <b className="small">{label}</b>
    <label className="small">Overall (1-10)<input type="number" min="1" max="10" value={overall}
      onChange={(e) => setOverall(e.target.value)} /></label>
    <label className="small">Publishable<select value={publishable}
      onChange={(e) => setPublishable(e.target.value)}>
      <option value="">Not scored</option><option value="true">Yes</option><option value="false">No</option>
    </select></label>
    {SCORE_FIELDS.map((field) => <label key={field} className="small">{field}
      <input type="number" min="1" max="10" value={scores[field] || ''}
        onChange={(e) => setScores({ ...scores, [field]: e.target.value === '' ? null : Number(e.target.value) })} />
    </label>)}
    <textarea placeholder="Scorecard notes" rows="3" value={notes} onChange={(e) => setNotes(e.target.value)} />
    <button className="btn btn-secondary btn-sm" disabled={!overall} onClick={() => perform(
      `score ${label}`, () => api(authSession, 'POST', `/projects/${project.id}/human-ceiling/scorecard`, {
        session_id: session.id, timeline_id: timelineId, scores,
        overall_rating: Number(overall), publishable: publishable === '' ? null : publishable === 'true',
        evaluator_role: 'operator', notes,
      }))}>Save scorecard</button>
  </div>
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
      <p className="small" style={{ color: 'var(--amber)' }}>All cost figures are ESTIMATES (configurable pricing.json; see stage_metrics.units.pricing_version).</p>
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
