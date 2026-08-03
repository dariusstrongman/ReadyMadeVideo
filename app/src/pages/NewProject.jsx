import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import { supabase } from '../lib/supabase'

const PLATFORMS = [
  { id: 'youtube',   icon: '▶', label: 'YouTube' },
  { id: 'reels',     icon: '◉', label: 'Instagram Reels' },
  { id: 'tiktok',    icon: '♪', label: 'TikTok' },
  { id: 'archive',   icon: '◫', label: 'Personal archive' },
  { id: 'other',     icon: '○', label: 'Other' },
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
  const [vibe, setVibe] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function create() {
    if (!name.trim()) return
    setBusy(true); setError('')
    // platform and vibe are collected for future use but not yet persisted
    const { data, error } = await supabase.from('projects')
      .insert({ name: name.trim(), user_id: session.user.id })
      .select().single()
    if (error) { setError(error.message); setBusy(false); return }
    navigate(`/project/${data.id}`)
  }

  return (
    <div className="new-project-page">
      <div className="new-project-card">
        <Link to="/" className="np-back">← Back to projects</Link>

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
                  onClick={() => setPlatform(p.id)}>
                  <span className="np-option-icon">{p.icon}</span>
                  {p.label}
                </button>
              ))}
            </div>
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
