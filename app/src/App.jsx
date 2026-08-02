import { createContext, useContext, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { supabase } from './lib/supabase'
import Login from './pages/Login'
import ResetPassword from './pages/ResetPassword'
import Dashboard from './pages/Dashboard'
import Project from './pages/Project'
import Operator from './pages/Operator'

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
      <a href="/" className="logo">STROMATION<span style={{ color: 'var(--cyan)' }}>.</span></a>
      <span className="small mono">app</span>
      <span className="spacer" />
      <span className="who">{session?.user?.email}</span>
      <button className="btn btn-ghost" onClick={async () => { await supabase.auth.signOut(); nav('/login') }}>
        Sign out
      </button>
    </nav>
  )
}
