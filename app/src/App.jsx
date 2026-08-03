import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { Link, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { supabase } from './lib/supabase'
import Login from './pages/Login'
import ResetPassword from './pages/ResetPassword'
import Dashboard from './pages/Dashboard'
import NewProject from './pages/NewProject'
import Project from './pages/Project'
import Operator from './pages/Operator'
import Editor from './pages/Editor'
import iconUrl from './assets/icon.svg'

const AuthCtx = createContext(null)
export const useAuth = () => useContext(AuthCtx)

export default function App() {
  const [session, setSession] = useState(undefined)
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
        <Route path="/project/new" element={<Protected noNav><NewProject /></Protected>} />
        <Route path="/project/:id" element={<Protected><Project /></Protected>} />
        <Route path="/project/:id/editor/:documentId" element={<Protected noNav><Editor /></Protected>} />
        <Route path="/operator" element={<Protected><Operator /></Protected>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthCtx.Provider>
  )
}

function Protected({ children, noNav = false }) {
  const session = useAuth()
  if (!session) return <Navigate to="/login" replace />
  if (noNav) return <>{children}</>
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
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef(null)
  const initial = session?.user?.email?.[0]?.toUpperCase() || '?'

  useEffect(() => {
    function handleClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  return (
    <nav className="nav">
      <a href="https://www.stromation.com" className="nav-logo">
        <img src={iconUrl} alt="" aria-hidden="true" />
        STROMATION
      </a>
      <span className="spacer" />
      <Link to="/project/new" className="btn btn-primary btn-sm">+ New video</Link>
      <div className="avatar-wrap" ref={menuRef}>
        <button className="avatar-btn" onClick={() => setMenuOpen(v => !v)} aria-label="Account menu">
          {initial}
        </button>
        {menuOpen && (
          <div className="avatar-menu">
            <div className="avatar-email">{session?.user?.email}</div>
            <div className="avatar-menu-sep" />
            <a href="https://www.stromation.com" className="avatar-menu-item" onClick={() => setMenuOpen(false)}>
              ← Back to stromation.com
            </a>
            <button className="avatar-menu-item" onClick={async () => { setMenuOpen(false); await supabase.auth.signOut(); nav('/login') }}>
              Sign out
            </button>
          </div>
        )}
      </div>
    </nav>
  )
}
