import React from 'react'

/**
 * Catches render-time errors so one broken component cannot take the whole app
 * down to a blank page.
 *
 * This exists because it already happened: a Rules-of-Hooks violation in the
 * project view threw mid-render, React unmounted the entire tree, and the app
 * rendered as an empty black rectangle — no nav, no message, no way back, and
 * nothing to go on but a screenshot. A boundary turns that into a readable
 * failure the user can recover from and report.
 *
 * Deliberately shows the error text. This is an internal alpha; a real message
 * ("Rendered more hooks than during the previous render") is worth far more
 * than a polished apology that hides it.
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // Keep the component stack in the console for anyone with devtools open.
    console.error('[ReadyMadeVideo] render error', error, info?.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="boundary">
        <div className="boundary-card" role="alert">
          <div className="boundary-icon" aria-hidden="true">!</div>
          <h1 className="boundary-title">This screen hit an error.</h1>
          <p className="boundary-sub">
            Your videos and footage are safe — this is a display problem, not a
            data one. Reloading usually clears it.
          </p>
          <pre className="boundary-detail">{String(error?.message || error)}</pre>
          <div className="boundary-actions">
            <button className="btn btn-primary" onClick={() => window.location.reload()}>
              Reload the page
            </button>
            <a className="btn btn-ghost" href="/">← Back to Your Studio</a>
          </div>
        </div>
      </div>
    )
  }
}
