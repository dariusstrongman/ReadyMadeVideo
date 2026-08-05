import iconUrl from '../assets/icon.svg'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import { supabase } from '../lib/supabase'

// Where a video is going decides the shape it should be cut to. This answer
// used to be collected and discarded, so vertical footage bound for TikTok was
// still rendered 16:9 with black bars burned into the export.
const PLATFORMS = [
  { id: 'reels',   icon: '◉', label: 'Instagram Reels', aspect: '9:16' },
  { id: 'tiktok',  icon: '♪', label: 'TikTok',          aspect: '9:16' },
  { id: 'youtube', icon: '▶', label: 'YouTube',         aspect: '16:9' },
  { id: 'archive', icon: '◫', label: 'Personal archive', aspect: '16:9' },
  { id: 'other',   icon: '○', label: 'Other',           aspect: '16:9' },
]
const ASPECTS = [
  { id: '9:16', label: 'Vertical', note: 'Reels, TikTok, Shorts' },
  { id: '16:9', label: 'Widescreen', note: 'YouTube, desktop' },
  { id: '1:1',  label: 'Square', note: 'Feed posts' },
]
const VIBES = [
  { id: 'energetic',    icon: '⚡', label: 'Energetic' },
  { id: 'cinematic',    icon: '◈', label: 'Cinematic' },
  { id: 'documentary',  icon: '◉', label: 'Documentary' },
  { id: 'minimal',      icon: '○', label: 'Minimal' },
  { id: 'emotional',    icon: '◇', label: 'Emotional' },
  { id: 'other',        icon: '·', label: 'Other' },
]

export default function NewProject() {
  const session = useAuth()
  const navigate = useNavigate()
  const [step, setStep] = useState(1)
  const [name, setName] = useState('')
  const [platform, setPlatform] = useState('')
  const [aspect, setAspect] = useState('')
  const [vibe, setVibe] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function create() {
    if (!name.trim()) return
    setBusy(true); setError('')
    const chosen = aspect
      || PLATFORMS.find(p => p.id === platform)?.aspect
      || '16:9'
    const base = { name: name.trim(), user_id: session.user.id }
    let { data, error } = await supabase.from('projects')
      .insert({
        ...base,
        aspect_ratio: chosen,
        target_platform: platform || null,
        vibe: vibe || null,
      })
      .select().single()

    // This frontend can go live before migration 0025 has been applied. Rather
    // than leaving project creation broken for that window, fall back to the
    // columns that have always existed. The project is then rendered at the
    // 16:9 default until the migration lands.
    if (error && /column .* does not exist|aspect_ratio|target_platform|vibe/i.test(error.message || '')) {
      ({ data, error } = await supabase.from('projects')
        .insert(base).select().single())
    }

    if (error) { setError(error.message); setBusy(false); return }
    navigate(`/project/${data.id}`)
  }

  return (
    <div className="new-project-page">
      <div className="new-project-card">
        <a href="https://www.stromation.com" className="nav-logo" style={{ marginBottom: 24, justifyContent: 'center' }}>
          <img src={iconUrl} alt="" aria-hidden="true" />
          STROMATION
        </a>

        {step === 1 && (
          <>
            <p className="np-step">Step 1 of 3</p>
            <h1 className="np-heading">What are you making?</h1>
            <input
              className="np-name-input"
              placeholder="Morning run — August 3"
              value={name} onChange={e => setName(e.target.value)}
              autoFocus
              onKeyDown={e => e.key === 'Enter' && name.trim() && setStep(2)}
            />
            <button className="btn btn-primary btn-lg" style={{ width: '100%' }}
              disabled={!name.trim()} onClick={() => setStep(2)}>
              Continue →
            </button>
          </>
        )}

        {step === 2 && (
          <>
            <p className="np-step">Step 2 of 3</p>
            <h1 className="np-heading">Where will this live?</h1>
            <div className="np-option-grid">
              {PLATFORMS.map(p => (
                <button key={p.id} className={`np-option ${platform === p.id ? 'selected' : ''}`}
                  onClick={() => { setPlatform(p.id); setAspect(p.aspect) }}>
                  <span className="np-option-icon">{p.icon}</span>
                  {p.label}
                </button>
              ))}
            </div>
            {platform && (
              <div className="np-aspect">
                <p className="np-aspect-label">Shape of the finished video</p>
                <div className="np-aspect-row">
                  {ASPECTS.map(a => (
                    <button key={a.id}
                      className={`np-aspect-opt ${aspect === a.id ? 'selected' : ''}`}
                      onClick={() => setAspect(a.id)}>
                      <span className={`np-aspect-box ar-${a.id.replace(':', '-')}`} aria-hidden="true" />
                      <span className="np-aspect-name">{a.label}</span>
                      <span className="np-aspect-note">{a.note}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div style={{ display: 'flex', gap: 10 }}>
              <button className="btn btn-ghost" onClick={() => setStep(1)}>← Back</button>
              <button className="btn btn-primary btn-lg" style={{ flex: 1 }}
                onClick={() => setStep(3)}>
                Continue →
              </button>
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <p className="np-step">Step 3 of 3</p>
            <h1 className="np-heading">What's the vibe?</h1>
            <div className="np-option-grid">
              {VIBES.map(v => (
                <button key={v.id} className={`np-option ${vibe === v.id ? 'selected' : ''}`}
                  onClick={() => setVibe(v.id)}>
                  <span className="np-option-icon">{v.icon}</span>
                  {v.label}
                </button>
              ))}
            </div>
            {error && <div className="err" role="alert">{error}</div>}
            <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
              <button className="btn btn-ghost" onClick={() => setStep(2)}>← Back</button>
              <button className="btn btn-primary btn-lg" style={{ flex: 1 }}
                disabled={busy} onClick={create}>
                {busy ? 'Creating…' : 'Create video →'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
