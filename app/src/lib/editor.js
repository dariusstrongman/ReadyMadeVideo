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
  if (index < 0 && operation.type !== 'restore_clip') {
    throw new Error('Timeline item no longer exists.')
  }
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
  } else if (operation.type === 'restore_clip') {
    items.splice(Math.min(operation.toIndex, items.length), 0, structuredClone(operation.clip))
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

export const MAX_EDITOR_HISTORY = 100

export function createEditorState(document = null, version = null) {
  return { document, savedDocument: document, version, past: [], future: [], pending: [] }
}

function rebaseOperations(operations, version) {
  return operations.map((operation) => ({ ...operation, baseVersion: version }))
}

function replay(document, operations) {
  return operations.reduce((current, operation) => applyLocal(current, operation), document)
}

function reconcileHistory(entries, savedIds, version) {
  return entries.map((entry) => ({
    document: entry.document,
    pending: rebaseOperations(
      entry.pending.filter((operation) => !savedIds.has(operation.operationId)), version,
    ),
  }))
}

function pendingPrefix(prefix, operations) {
  return prefix.every((operation, index) =>
    operations[index]?.operationId === operation.operationId)
}

function operationsBetween(fromDocument, toDocument, version) {
  const operations = []
  let working = fromDocument
  const add = (type, targetId, args) => {
    const operation = makeOperation(type, targetId, version, args)
    operations.push(operation)
    working = applyLocal(working, operation)
  }
  const fromPicture = track(working, 'picture').items
  const toPicture = track(toDocument, 'picture').items
  fromPicture.filter((clip) => !toPicture.some((item) => item.id === clip.id))
    .forEach((clip) => add('delete_clip', clip.id))
  toPicture.filter((clip) => !track(working, 'picture').items
    .some((item) => item.id === clip.id))
    .forEach((clip) => add('restore_clip', clip.id, {
      clip, toIndex: toPicture.findIndex((item) => item.id === clip.id),
    }))
  toPicture.forEach((clip, toIndex) => {
    const current = track(working, 'picture').items.find((item) => item.id === clip.id)
    const currentIndex = track(working, 'picture').items.findIndex((item) => item.id === clip.id)
    if (currentIndex !== toIndex) add('reorder_clip', clip.id, { toIndex })
    if (current.sourceStart !== clip.sourceStart || current.sourceEnd !== clip.sourceEnd) {
      add('trim_clip', clip.id, { sourceStart: clip.sourceStart, sourceEnd: clip.sourceEnd })
    }
  })
  const scalarTracks = [
    ['captions', 'text', 'update_caption', 'text'],
    ['music', 'gainDb', 'set_music_gain', 'gainDb'],
    ['graphics', 'enabled', 'toggle_graphic', 'enabled'],
  ]
  scalarTracks.forEach(([lane, field, type, argument]) => {
    track(toDocument, lane).items.forEach((target) => {
      const current = track(working, lane).items.find((item) => item.id === target.id)
      if (current && current[field] !== target[field]) add(type, target.id, {
        [argument]: target[field],
      })
    })
  })
  return operations
}

export function editorReducer(state, action) {
  if (action.type === 'load') return createEditorState(action.document, action.version)
  if (action.type === 'apply') return {
    ...state,
    document: applyLocal(state.document, action.operation),
    past: [...state.past, { document: state.document, pending: state.pending }]
      .slice(-MAX_EDITOR_HISTORY), future: [],
    pending: [...state.pending, action.operation],
  }
  if (action.type === 'undo' && state.past.length) {
    const target = state.past.at(-1)
    const unsavedSnapshot = target.pending.length < state.pending.length
      && pendingPrefix(target.pending, state.pending)
    const delta = unsavedSnapshot ? []
      : operationsBetween(state.document, target.document, state.version)
    return {
      ...state,
      document: target.document, past: state.past.slice(0, -1),
      future: [{ document: state.document, pending: state.pending }, ...state.future]
        .slice(0, MAX_EDITOR_HISTORY),
      pending: unsavedSnapshot ? target.pending : [...state.pending, ...(delta || [])],
    }
  }
  if (action.type === 'redo' && state.future.length) {
    const target = state.future[0]
    const unsavedSnapshot = target.pending.length > state.pending.length
      && pendingPrefix(state.pending, target.pending)
    const delta = unsavedSnapshot ? []
      : operationsBetween(state.document, target.document, state.version)
    return {
      ...state,
      document: target.document,
      past: [...state.past, { document: state.document, pending: state.pending }]
        .slice(-MAX_EDITOR_HISTORY),
      future: state.future.slice(1),
      pending: unsavedSnapshot ? target.pending : [...state.pending, ...(delta || [])],
    }
  }
  if (action.type === 'save_succeeded') {
    const savedIds = new Set(action.operationIds)
    const pending = rebaseOperations(
      state.pending.filter((operation) => !savedIds.has(operation.operationId)),
      action.version,
    )
    return {
      ...state,
      savedDocument: action.document,
      version: action.version,
      document: replay(action.document, pending),
      pending,
      past: reconcileHistory(state.past, savedIds, action.version),
      future: reconcileHistory(state.future, savedIds, action.version),
    }
  }
  if (action.type === 'rebase') {
    // Version-conflict recovery: the server advanced WITHOUT our ops. Keep every
    // pending op (rebased to the latest version) and replay it onto the fresh server
    // document; undo/redo history is preserved. Nothing is discarded.
    const version = action.version
    const pending = rebaseOperations(state.pending, version)
    let document
    try {
      document = replay(action.document, pending)
    } catch {
      document = action.document   // diverged too far to replay; pending kept for retry
    }
    return {
      ...state,
      savedDocument: action.document,
      version,
      document,
      pending,
      past: reconcileHistory(state.past, new Set(), version),
      future: reconcileHistory(state.future, new Set(), version),
    }
  }
  return state
}

export function trimArguments(document, targetId, boundary, value) {
  const clip = track(document, 'picture').items.find((item) => item.id === targetId)
  if (!clip) throw new Error('Timeline item no longer exists.')
  return boundary === 'start'
    ? { sourceStart: value, sourceEnd: clip.sourceEnd }
    : { sourceStart: clip.sourceStart, sourceEnd: value }
}

export function reorderArguments(document, targetId, direction) {
  const items = track(document, 'picture').items
  const index = items.findIndex((item) => item.id === targetId)
  if (index < 0) throw new Error('Timeline item no longer exists.')
  return { toIndex: Math.max(0, Math.min(items.length - 1, index + direction)) }
}

export function makeOperation(type, targetId, baseVersion, args = {}, actor = 'user') {
  return { operationId: crypto.randomUUID(), type, actor, targetId, baseVersion,
    timestamp: new Date().toISOString(), ...args }
}
