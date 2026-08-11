// Output Intelligence — the customer chooses WHAT gets made, before any edit
// is planned. Renders only when the backend flag is on (the parent gates on
// the packages probe + the explicit "choose your outputs" status the flagged
// backend sets); with the flag off this file is never mounted, so the classic
// journey is untouched.
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { editorApi } from '../lib/editor'
import {
  CHILD_STATUS_LABEL, buildCustomSelection, customizationBounds,
  describeDeliverable, fmtDuration, packageProgressLine, selectionForPackage,
} from '../lib/outputIntelligence'

export default function OutputChooser({ projectId, session, packages, onPackagesChange }) {
  const [rec, setRec] = useState(null)
  const [error, setError] = useState('')
  const [rejection, setRejection] = useState(null)   // 422 payload
  const [busy, setBusy] = useState(false)
  const [customizing, setCustomizing] = useState(false)
  const [wantLong, setWantLong] = useState(true)
  const [shortCount, setShortCount] = useState(0)
  const [longDurationS, setLongDurationS] = useState('')
  const pollRef = useRef(null)

  const activePackage = (packages || [])[0] || null

  const loadRecommendation = useCallback(async () => {
    try {
      const r = await editorApi(
        `/projects/${projectId}/output-recommendation`, session,
        { method: 'POST' })
      setRec(r)
      const b = customizationBounds(r)
      setShortCount(b.maxShorts > 0 ? Math.min(b.maxShorts, 3) : 0)
      setLongDurationS(b.longRange ? String(b.longRange.recommended) : '')
    } catch (e) {
      setError(e.message || 'Could not read what your footage supports.')
    }
  }, [projectId, session])

  useEffect(() => {
    if (!activePackage) loadRecommendation()
  }, [activePackage, loadRecommendation])

  // Poll package progress while anything is still moving.
  const refresh = useCallback(async () => {
    try {
      const out = await editorApi(`/projects/${projectId}/output-packages`, session)
      onPackagesChange(out.packages || [])
    } catch { /* transient poll errors surface on the next tick */ }
  }, [projectId, session, onPackagesChange])

  useEffect(() => {
    if (!activePackage) return undefined
    if (activePackage.packageStatus !== 'processing') return undefined
    pollRef.current = setInterval(refresh, 5000)
    return () => clearInterval(pollRef.current)
  }, [activePackage, refresh])

  async function create(selection) {
    if (busy) return
    setBusy(true); setError(''); setRejection(null)
    try {
      const out = await editorApi(`/projects/${projectId}/output-packages`,
        session, {
          method: 'POST',
          body: JSON.stringify({ recommendationId: rec.id, selection }),
        })
      onPackagesChange([{ package: out.package, deliverables: out.deliverables,
        packageStatus: out.packageStatus }])
    } catch (e) {
      const detail = (e.payload && typeof e.payload.detail === 'object'
        && e.payload.detail) || {}
      if (detail.error === 'selection_not_feasible') {
        setRejection(detail.results)
      } else if (detail.error === 'stale_recommendation') {
        setError('Your footage changed — refreshing the recommendation.')
        await loadRecommendation()
      } else {
        setError(e.message || 'Could not create the package.')
      }
    } finally {
      setBusy(false)
    }
  }

  async function retryChild(childId) {
    try {
      await editorApi(
        `/projects/${projectId}/output-deliverables/${childId}/retry`,
        session, { method: 'POST' })
      await refresh()
    } catch (e) {
      setError(e.message || 'Retry failed.')
    }
  }

  // ── package progress view ─────────────────────────────────────────────
  if (activePackage) {
    const children = activePackage.deliverables || []
    return (
      <section className="oi-panel" aria-label="Your videos">
        <h2 className="oi-h">Your videos</h2>
        <p className="oi-progress">{packageProgressLine(children)}</p>
        <ul className="oi-children">
          {children.map((c, i) => {
            const d = describeDeliverable({
              format: c.spec?.kind, feasibleDurationS:
                [c.spec?.durationMin || 0, c.spec?.durationMax || 0],
              recommendedDurationS: c.spec?.durationMax || 0,
              recommendedAspect: c.spec?.aspectRatio,
            })
            return (
              <li key={c.id} className={`oi-child oi-${c.status}`}>
                <span className="oi-child-n">{i + 1}</span>
                <span className="oi-child-what">
                  {d.title} · {c.spec?.aspectRatio || ''}
                  {c.spec?.reason ? <em> — {c.spec.reason}</em> : null}
                </span>
                <span className="oi-child-status">
                  {CHILD_STATUS_LABEL[c.status] || c.status}
                  {c.error_message && ['failed', 'budget_blocked'].includes(c.status)
                    ? <small className="oi-err"> {c.error_message}</small> : null}
                </span>
                {['failed', 'budget_blocked'].includes(c.status) && (
                  <button className="btn btn-ghost btn-sm"
                    onClick={() => retryChild(c.id)}>Retry this one</button>
                )}
              </li>
            )
          })}
        </ul>
        {error && <div className="err" role="alert">{error}</div>}
        <p className="oi-note">
          Finished videos appear in this project as their cuts complete — each
          one opens in the editor on its own.
        </p>
      </section>
    )
  }

  // ── chooser view ──────────────────────────────────────────────────────
  if (!rec) {
    return (
      <section className="oi-panel" aria-busy="true">
        <p className="sub">Reading what your footage supports…</p>
        {error && <div className="err" role="alert">{error}</div>}
      </section>
    )
  }

  const pkgs = rec.packages || []
  const recommended = pkgs.find(p => p.packageKey === rec.recommended_key) || pkgs[0]
  const others = pkgs.filter(p => p !== recommended)
  const bounds = customizationBounds(rec)
  const inv = rec.inventory || {}

  return (
    <section className="oi-panel" aria-label="Choose your outputs">
      <h2 className="oi-h">ReadyMadeVideo recommends</h2>
      {rec.stale && (
        <div className="err" role="alert">
          Your footage changed since this was computed.{' '}
          <button className="btn btn-ghost btn-sm" onClick={loadRecommendation}>
            Refresh
          </button>
        </div>
      )}

      {recommended ? (
        <div className="oi-reco">
          <p className="oi-reco-title">{recommended.title}</p>
          <ul className="oi-deliverables">
            {recommended.deliverables.map(d => {
              const line = describeDeliverable(d)
              return (
                <li key={d.opportunityId}>
                  <strong>{line.title}</strong> · {line.dur} · {line.shape}
                  {d.reason ? <em> — {d.reason}</em> : null}
                </li>
              )
            })}
          </ul>
          <p className="oi-why">
            Why: {recommended.reason}. Based on {fmtDuration(inv.usable_seconds)} of
            usable footage{inv.raw_seconds > inv.usable_seconds * 1.2
              ? ` (from ${fmtDuration(inv.raw_seconds)} uploaded)` : ''}.
          </p>
          <button className="btn btn-primary btn-lg" disabled={busy || rec.stale}
            onClick={() => create(selectionForPackage(recommended))}>
            {busy ? 'Creating…' : 'Create recommended package'}
          </button>
        </div>
      ) : (
        <p className="oi-why">
          This footage does not support a finished video yet — no standalone
          moment or coherent story was found in the usable material.
        </p>
      )}

      {others.length > 0 && (
        <div className="oi-others">
          <p className="oi-others-label">Other options</p>
          {others.map(p => (
            <button key={p.packageKey} className="btn btn-ghost" disabled={busy || rec.stale}
              onClick={() => create(selectionForPackage(p))}>
              {p.title}
            </button>
          ))}
          <button className="btn btn-ghost" disabled={busy}
            onClick={() => setCustomizing(v => !v)}>
            Customize
          </button>
        </div>
      )}

      {customizing && (
        <div className="oi-custom">
          {bounds.hasLongForm && (
            <label className="oi-custom-row">
              <input type="checkbox" checked={wantLong}
                onChange={e => setWantLong(e.target.checked)} />
              Full video
              {bounds.longRange && wantLong && (
                <span className="oi-custom-dur">
                  <input type="number" min={bounds.longRange.lo}
                    max={bounds.longRange.hi} value={longDurationS}
                    onChange={e => setLongDurationS(e.target.value)} /> s
                  <small> (honest range {bounds.longRange.lo}–{bounds.longRange.hi}s)</small>
                </span>
              )}
            </label>
          )}
          <label className="oi-custom-row">
            Shorts:
            <input type="number" min="0" max={bounds.maxShorts} value={shortCount}
              onChange={e => setShortCount(e.target.value)} />
            <small> (your footage holds {bounds.maxShorts})</small>
          </label>
          <button className="btn btn-primary" disabled={busy}
            onClick={() => create(buildCustomSelection({
              wantLong, longDurationS, shortCount: Number(shortCount), bounds }))}>
            Create these
          </button>
        </div>
      )}

      {rejection && (
        <div className="oi-rejection" role="alert">
          {rejection.map((r, i) => (
            <div key={i}>
              {(r.reasons || []).map(x => <p key={x.code}>{x.message}</p>)}
              {r.alternative && (
                <button className="btn btn-ghost btn-sm" disabled={busy}
                  onClick={() => create([r.alternative])}>
                  Create {r.alternative.quantity
                    ? `${r.alternative.quantity} instead`
                    : 'the closest option instead'}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {error && !rec.stale && <div className="err" role="alert">{error}</div>}
    </section>
  )
}
