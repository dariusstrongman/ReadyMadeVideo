import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { Link, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { supabase } from './lib/supabase'
import ErrorBoundary from './ErrorBoundary'
import { clearResumeRecords } from './lib/s3upload'
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
    return (
      <div className="center" role="status" aria-live="polite">
        <p className="sub">Restoring session…</p>
      </div>
    )
  return (
    <AuthCtx.Provider value={session}>
      <ErrorBoundary>
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
      </ErrorBoundary>
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
  const triggerRef = useRef(null)
  const itemRefs = useRef([])
  const initial = session?.user?.email?.[0]?.toUpperCase() || '?'

  useEffect(() => {
    function handleClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  // Focus first item when menu opens
  useEffect(() => {
    if (menuOpen) {
      // Wait one frame for DOM to render
      requestAnimationFrame(() => {
        itemRefs.current[0]?.focus()
      })
    }
  }, [menuOpen])

  function handleTriggerKeyDown(e) {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
      e.preventDefault()
      setMenuOpen(true)
    }
    if (e.key === 'Escape') {
      setMenuOpen(false)
    }
  }

  function handleMenuKeyDown(e) {
    const items = itemRefs.current.filter(Boolean)
    const idx = items.indexOf(document.activeElement)
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      items[(idx + 1) % items.length]?.focus()
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      items[(idx - 1 + items.length) % items.length]?.focus()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setMenuOpen(false)
      triggerRef.current?.focus()
    } else if (e.key === 'Tab') {
      // Close on Tab — user is leaving the menu
      setMenuOpen(false)
    }
  }

  function closeMenu() {
    setMenuOpen(false)
    triggerRef.current?.focus()
  }

  return (
    <nav className="nav" role="navigation" aria-label="Main navigation">
      <a href="https://www.readymadevideo.com" className="nav-logo">
        <img src={iconUrl} alt="" aria-hidden="true" />
        READYMADEVIDEO
      </a>
      <span className="spacer" />
      <Link to="/project/new" className="btn btn-primary btn-sm">+ New video</Link>
      <div className="avatar-wrap" ref={menuRef}>
        <button
          ref={triggerRef}
          className="avatar-btn"
          onClick={() => setMenuOpen(v => !v)}
          onKeyDown={handleTriggerKeyDown}
          aria-label="Account menu"
          aria-expanded={menuOpen}
          aria-haspopup="menu"
        >
          {initial}
        </button>
        {menuOpen && (
          <div className="avatar-menu" role="menu" aria-label="Account options" onKeyDown={handleMenuKeyDown}>
            <div className="avatar-email">{session?.user?.email}</div>
            <div className="avatar-menu-sep" />
            <a
              href="https://www.readymadevideo.com"
              className="avatar-menu-item"
              role="menuitem"
              tabIndex={0}
              ref={el => { itemRefs.current[0] = el }}
              onClick={closeMenu}
            >
              ← Back to readymadevideo.com
            </a>
            <button
              className="avatar-menu-item"
              role="menuitem"
              tabIndex={0}
              ref={el => { itemRefs.current[1] = el }}
              onClick={async () => { setMenuOpen(false); clearResumeRecords(session?.user?.id); await supabase.auth.signOut(); nav('/login') }}
            >
              Sign out
            </button>
          </div>
        )}
      </div>
    </nav>
  )
}
