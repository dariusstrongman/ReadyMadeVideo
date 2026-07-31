import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'

/** Landing page for the password-recovery email link. Supabase puts the user
 * into a temporary recovery session; updateUser sets the new password. */
export default function ResetPassword() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()

  async function submit(e) {
    e.preventDefault()
    setError(''); setBusy(true)
    try {
      if (password.length < 8) throw new Error('Password must be at least 8 characters.')
      const { error } = await supabase.auth.updateUser({ password })
      if (error) throw error
      nav('/', { replace: true })
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="center">
      <div className="card auth-card">
        <h1 style={{ fontSize: '1.1rem' }}>Choose a new password</h1>
        {error && <div className="err" role="alert">{error}</div>}
        <form onSubmit={submit}>
          <label htmlFor="pw">New password</label>
          <input id="pw" type="password" value={password} required autoComplete="new-password"
            onChange={(e) => setPassword(e.target.value)} />
          <button className="btn btn-primary" style={{ width: '100%', marginTop: 20 }} disabled={busy}>
            {busy ? 'Saving…' : 'Set password'}
          </button>
        </form>
      </div>
    </div>
  )
}
