import { createContext, useContext, useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { supabase } from './lib/supabase'
import { clearResumeRecords } from './lib/s3upload'
import Login from './pages/Login'
import ResetPassword from './pages/ResetPassword'
import Dashboard from './pages/Dashboard'
import Project from './pages/Project'
import Operator from './pages/Operator'
import Editor from './pages/Editor'

const AuthCtx = createContext(null)
export const useAuth = () => useContext(AuthCtx)

export default function App() {
  const [session, setSession] = useState(undefined) // undefined = restoring

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session ?? null))
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => setSession(s ?? null))
    return () => sub.subscription.unsubscribe()
  }, [])

  if (session === undefined)
    return <div className="center"><p className="sub">Restoring session…</p></div>

  return (
    <AuthCtx.Provider value={session}>
      <Routes>
        <Route path="/login" element={session ? <Navigate to="/" replace /> : <Login />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/" element={<Protected><Dashboard /></Protected>} />
        <Route path="/project/:id" element={<Protected><Project /></Protected>} />
        <Route path="/project/:id/editor/:documentId" element={<Protected><Editor /></Protected>} />
        <Route path="/operator" element={<Protected><Operator /></Protected>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthCtx.Provider>
  )
}

function Protected({ children }) {
  const session = useAuth()
  if (!session) return <Navigate to="/login" replace />
  return (
    <>
      <TopNav />
      {children}
    </>
  )
}

function TopNav() {
  const session = useAuth()
  const nav = useNavigate()
  return (
    <nav className="nav">
      <a href="https://www.stromation.com" className="logo">STROMATION<span style={{ color: 'var(--cyan)' }}>.</span></a>
      <span className="small mono">app</span>
      <Link to="/" className="nav-link">Projects</Link>
      <span className="spacer" />
      <a href="https://www.stromation.com" className="btn btn-ghost">← Back to website</a>
      <span className="who">{session?.user?.email}</span>
      <button className="btn btn-ghost" onClick={async () => {
        clearResumeRecords(session?.user?.id)
        await supabase.auth.signOut(); nav('/login')
      }}>
        Sign out
      </button>
    </nav>
  )
}
