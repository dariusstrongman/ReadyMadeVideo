import { useState } from 'react'
import { supabase } from '../lib/supabase'

export default function Login() {
  const [mode, setMode] = useState('signin') // signin | signup | forgot
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function submit(e) {
    e.preventDefault()
    setError(''); setNotice(''); setBusy(true)
    try {
      if (mode === 'signin') {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
      } else if (mode === 'signup') {
        if (password.length < 8) throw new Error('Password must be at least 8 characters.')
        const { data, error } = await supabase.auth.signUp({ email, password })
        if (error) throw error
        if (!data.session)
          setNotice('Account created. Check your email to confirm your address, then sign in.')
      } else {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: `${window.location.origin}/reset-password`,
        })
        if (error) throw error
        setNotice('If that address has an account, a reset link is on its way.')
      }
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="center">
      <div className="card auth-card">
        <h1 style={{ fontFamily: 'var(--mono)', letterSpacing: '0.12em', fontSize: '1rem' }}>
          STROMATION<span style={{ color: 'var(--cyan)' }}>.</span>
        </h1>
        <p className="sub" style={{ marginTop: 8 }}>
          {mode === 'signin' && 'Sign in to your workspace.'}
          {mode === 'signup' && 'Create your account.'}
          {mode === 'forgot' && 'Reset your password.'}
        </p>
        {error && <div className="err" role="alert">{error}</div>}
        {notice && <div className="ok" role="status">{notice}</div>}
        <form onSubmit={submit}>
          <label htmlFor="email">Email</label>
          <input id="email" type="email" value={email} required autoComplete="email"
            onChange={(e) => setEmail(e.target.value)} />
          {mode !== 'forgot' && (
            <>
              <label htmlFor="password">Password</label>
              <input id="password" type="password" value={password} required
                autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
                onChange={(e) => setPassword(e.target.value)} />
            </>
          )}
          <button className="btn btn-primary" style={{ width: '100%', marginTop: 20 }} disabled={busy}>
            {busy ? 'Working…' : mode === 'signin' ? 'Sign in' : mode === 'signup' ? 'Create account' : 'Send reset link'}
          </button>
        </form>
        <p className="small" style={{ marginTop: 16 }}>
          {mode !== 'signin' && <a href="#" onClick={(e) => { e.preventDefault(); setMode('signin'); setError('') }}>Sign in</a>}
          {mode === 'signin' && <a href="#" onClick={(e) => { e.preventDefault(); setMode('signup'); setError('') }}>Create an account</a>}
          {' · '}
          {mode !== 'forgot' && <a href="#" onClick={(e) => { e.preventDefault(); setMode('forgot'); setError('') }}>Forgot password</a>}
          {mode === 'forgot' && <a href="#" onClick={(e) => { e.preventDefault(); setMode('signup'); setError('') }}>Create an account</a>}
        </p>
      </div>
    </div>
  )
}
