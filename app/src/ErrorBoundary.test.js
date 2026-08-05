/**
 * ErrorBoundary contract.
 *
 * There is no jsdom/testing-library in this project, so this covers the static
 * contract React relies on rather than a full mount: if getDerivedStateFromError
 * does not return state carrying the error, the boundary silently renders its
 * children again and the blank-page failure it exists to prevent comes straight
 * back. Full render coverage would need a DOM environment added.
 */
import { describe, it, expect, vi } from 'vitest'
import ErrorBoundary from './ErrorBoundary'

describe('ErrorBoundary — React contract', () => {
  it('is a class component, which is the only form that can catch render errors', () => {
    expect(typeof ErrorBoundary).toBe('function')
    expect(ErrorBoundary.prototype?.render).toBeTypeOf('function')
  })

  it('derives error state so the fallback is what renders next', () => {
    const err = new Error('Rendered more hooks than during the previous render.')
    expect(ErrorBoundary.getDerivedStateFromError(err)).toEqual({ error: err })
  })

  it('starts clean, so a healthy tree renders its children', () => {
    const b = new ErrorBoundary({ children: 'ok' })
    expect(b.state.error).toBeNull()
    expect(b.render()).toBe('ok')
  })

  it('renders the fallback instead of children once an error is held', () => {
    const b = new ErrorBoundary({ children: 'ok' })
    b.state = { error: new Error('boom') }
    const out = b.render()
    expect(out).not.toBe('ok')
    expect(out.props.className).toBe('boundary')
  })

  it('surfaces the message rather than hiding it behind an apology', () => {
    const b = new ErrorBoundary({ children: null })
    b.state = { error: new Error('useState is not a function') }
    // Internal alpha: the real error is worth more than a polished apology.
    expect(JSON.stringify(b.render())).toContain('useState is not a function')
  })

  it('logs the component stack for anyone with devtools open', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const b = new ErrorBoundary({ children: null })
    b.componentDidCatch(new Error('boom'), { componentStack: '  at Project' })
    expect(spy).toHaveBeenCalled()
    expect(spy.mock.calls[0].join(' ')).toContain('at Project')
    spy.mockRestore()
  })
})
