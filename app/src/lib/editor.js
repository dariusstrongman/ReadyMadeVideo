import { RENDER_API } from './config'

export async function editorApi(path, session, options = {}) {
  const response = await fetch(`${RENDER_API}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${session.access_token}`,
      ...(options.headers || {}),
    },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = typeof body.detail === 'string' ? body.detail
      : body.detail?.message || body.message || `Request failed (${response.status})`
    const error = new Error(detail)
    error.status = response.status
    error.payload = body
    throw error
  }
  return body
}

export function track(document, type) {
  return document.tracks.find((item) => item.type === type)
}

export function applyLocal(document, operation) {
  const next = structuredClone(document)
  const items = track(next, operation.type === 'update_caption' ? 'captions'
    : operation.type === 'set_music_gain' ? 'music'
      : operation.type === 'toggle_graphic' ? 'graphics' : 'picture').items
  const index = items.findIndex((item) => item.id === operation.targetId)
  if (index < 0) throw new Error('Timeline item no longer exists.')
  if (operation.type === 'reorder_clip') {
    const [clip] = items.splice(index, 1)
    items.splice(Math.min(operation.toIndex, items.length), 0, clip)
  } else if (operation.type === 'trim_clip') {
    items[index].sourceStart = operation.sourceStart
    items[index].sourceEnd = operation.sourceEnd
  } else if (operation.type === 'split_clip') {
    const left = items[index]
    const right = { ...left, id: `${left.id}-split-${operation.operationId.slice(0, 8)}`,
      sourceStart: operation.sourceTime }
    left.sourceEnd = operation.sourceTime
    items.splice(index + 1, 0, right)
  } else if (operation.type === 'delete_clip') {
    items.splice(index, 1)
  } else if (operation.type === 'update_caption') {
    items[index].text = operation.text
  } else if (operation.type === 'set_music_gain') {
    items[index].gainDb = operation.gainDb
  } else if (operation.type === 'toggle_graphic') {
    items[index].enabled = operation.enabled
  }
  let cursor = 0
  track(next, 'picture').items.forEach((clip) => {
    clip.timelineStart = Number(cursor.toFixed(3))
    cursor += (clip.sourceEnd - clip.sourceStart) / (clip.speed || 1)
    clip.timelineEnd = Number(cursor.toFixed(3))
  })
  next.duration = Number(cursor.toFixed(3))
  return next
}

export function editorReducer(state, action) {
  if (action.type === 'load') return { document: action.document, past: [], future: [], pending: [] }
  if (action.type === 'apply') return {
    document: applyLocal(state.document, action.operation),
    past: [...state.past, { document: state.document, pending: state.pending }], future: [],
    pending: [...state.pending, action.operation],
  }
  if (action.type === 'undo' && state.past.length) return {
    document: state.past.at(-1).document, past: state.past.slice(0, -1),
    future: [{ document: state.document, pending: state.pending }, ...state.future],
    pending: state.past.at(-1).pending,
  }
  if (action.type === 'redo' && state.future.length) return {
    document: state.future[0].document,
    past: [...state.past, { document: state.document, pending: state.pending }],
    future: state.future.slice(1), pending: state.future[0].pending,
  }
  return state
}

export function makeOperation(type, targetId, baseVersion, args = {}, actor = 'user') {
  return { operationId: crypto.randomUUID(), type, actor, targetId, baseVersion,
    timestamp: new Date().toISOString(), ...args }
}
