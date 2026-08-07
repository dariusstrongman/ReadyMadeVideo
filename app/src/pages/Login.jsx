import { useState } from 'react'
import { supabase } from '../lib/supabase'
import iconUrl from '../assets/icon.svg'

export default function Login() {
  const [mode, setMode] = useState('signin') // signin | signup | forgot
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError(''); setOk('')
    try {
      if (mode === 'signin') {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
      } else if (mode === 'signup') {
        const { error } = await supabase.auth.signUp({ email, password })
        if (error) throw error
        setOk('Check your email to confirm your account.')
      } else {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: `${window.location.origin}/reset-password`,
        })
        if (error) throw error
        setOk('Password reset link sent — check your email.')
      }
    } catch (err) {
      setError(err.message || String(err))
    } finally { setBusy(false) }
  }

  const headings = {
    signin: 'Sign in to your workspace.',
    signup: 'Create your account. Early access is free.',
    forgot: "We'll send a reset link to your email.",
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <div className="login-brand-icon">
            <img src={iconUrl} alt="" aria-hidden="true" style={{ width: 26, height: 26 }} />
          </div>
          <span className="login-brand-name">READYMADEVIDEO</span>
          <span className="login-tagline">Raw footage in. Finished video out.</span>
        </div>
        <p className="login-title">{headings[mode]}</p>
        {error && <div className="err" role="alert" aria-live="assertive">{error}</div>}
        {ok    && <div className="ok"  role="status">{ok}</div>}
        <form onSubmit={submit}>
          <label>Email
            <input type="email" value={email} onChange={e => setEmail(e.target.value)}
              autoFocus autoComplete="email" required />
          </label>
          {mode !== 'forgot' && (
            <label>Password
              <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required />
            </label>
          )}
          <button className="btn btn-primary btn-lg" style={{ width: '100%', marginTop: 20 }}
            type="submit" disabled={busy}>
            {busy
              ? <><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ animation: 'spin 0.8s linear infinite', flexShrink: 0 }}><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>{mode === 'signin' ? ' Signing in…' : mode === 'signup' ? ' Creating account…' : ' Sending…'}</>
              : mode === 'signin' ? 'Sign in' : mode === 'signup' ? 'Create account' : 'Send reset link'
            }
          </button>
        </form>
        <div className="login-footer">
          {mode === 'signin' && <>
            <a href="#" onClick={e => { e.preventDefault(); setMode('signup'); setError(''); setOk('') }}>Create an account</a>
            {' · '}
            <a href="#" onClick={e => { e.preventDefault(); setMode('forgot'); setError(''); setOk('') }}>Forgot password</a>
          </>}
          {mode === 'signup' && <>
            Already have an account?{' '}
            <a href="#" onClick={e => { e.preventDefault(); setMode('signin'); setError(''); setOk('') }}>Sign in</a>
          </>}
          {mode === 'forgot' && <>
            <a href="#" onClick={e => { e.preventDefault(); setMode('signin'); setError(''); setOk('') }}>← Back to sign in</a>
          </>}
        </div>
      </div>
    </div>
  )
}
