import { describe, expect, it } from 'vitest'
import { applyLocal, createEditorState, editorReducer, makeOperation, reorderArguments,
  trimArguments } from './editor'

const document = {
  duration: 6,
  tracks: [
    { type: 'picture', items: [
      { id: 'a', sourceStart: 0, sourceEnd: 2, timelineStart: 0, timelineEnd: 2 },
      { id: 'b', sourceStart: 2, sourceEnd: 4, timelineStart: 2, timelineEnd: 4 },
      { id: 'c', sourceStart: 4, sourceEnd: 6, timelineStart: 4, timelineEnd: 6 },
    ] },
    { type: 'captions', items: [{ id: 'caption', text: 'Old' }] },
    { type: 'music', items: [{ id: 'music-main', gainDb: -12 }] },
    { type: 'sfx', items: [] }, { type: 'graphics', items: [{ id: 'graphic', enabled: true }] },
  ],
}

function apply(state, operation) {
  return editorReducer(state, { type: 'apply', operation })
}

describe('editor operation reducer', () => {
  it('reorders and reflows without mutating the source', () => {
    const result = applyLocal(document, makeOperation('reorder_clip', 'b', 1, { toIndex: 0 }))
    expect(result.tracks[0].items.map((item) => item.id)).toEqual(['b', 'a', 'c'])
    expect(result.tracks[0].items[0].timelineStart).toBe(0)
    expect(document.tracks[0].items[0].id).toBe('a')
  })

  it('preserves an edit made while autosave is in flight and rebases its operation', () => {
    const first = makeOperation('update_caption', 'caption', 1, { text: 'First' })
    const second = makeOperation('set_music_gain', 'music-main', 1, { gainDb: -8 })
    const saving = apply(createEditorState(document, 1), first)
    const editedDuringSave = apply(saving, second)
    const serverDocument = applyLocal(document, first)
    const reconciled = editorReducer(editedDuringSave, {
      type: 'save_succeeded', document: serverDocument, version: 2,
      operationIds: [first.operationId],
    })

    expect(reconciled.document.tracks[1].items[0].text).toBe('First')
    expect(reconciled.document.tracks[2].items[0].gainDb).toBe(-8)
    expect(reconciled.pending).toHaveLength(1)
    expect(reconciled.pending[0].operationId).toBe(second.operationId)
    expect(reconciled.pending[0].baseVersion).toBe(2)
  })

  it('preserves multiple in-flight edits exactly once and in deterministic order', () => {
    const savedOp = makeOperation('update_caption', 'caption', 1, { text: 'Saved' })
    const later = [
      makeOperation('set_music_gain', 'music-main', 1, { gainDb: -10 }),
      makeOperation('toggle_graphic', 'graphic', 1, { enabled: false }),
    ]
    let state = apply(createEditorState(document, 1), savedOp)
    later.forEach((operation) => { state = apply(state, operation) })
    state = editorReducer(state, { type: 'save_succeeded',
      document: applyLocal(document, savedOp), version: 2,
      operationIds: [savedOp.operationId] })

    expect(state.pending.map((item) => item.operationId))
      .toEqual(later.map((item) => item.operationId))
    expect(new Set(state.pending.map((item) => item.operationId)).size).toBe(2)
    expect(state.document.tracks[2].items[0].gainDb).toBe(-10)
    expect(state.document.tracks[4].items[0].enabled).toBe(false)
  })

  it.each(['save_failed', 'save_conflict'])(
    'keeps every pending operation on %s so a retry loses nothing', (type) => {
      const operation = makeOperation('update_caption', 'caption', 1, { text: 'Retry me' })
      const state = apply(createEditorState(document, 1), operation)
      const unchanged = editorReducer(state, { type })
      expect(unchanged).toBe(state)
      expect(unchanged.pending[0].operationId).toBe(operation.operationId)
    },
  )

  it('reconciles a successful retry against the returned immutable revision', () => {
    const operation = makeOperation('update_caption', 'caption', 1, { text: 'Retried' })
    const pending = apply(createEditorState(document, 1), operation)
    const saved = applyLocal(document, operation)
    const reconciled = editorReducer(pending, { type: 'save_succeeded', document: saved,
      version: 2, operationIds: [operation.operationId] })
    expect(reconciled.document).toEqual(saved)
    expect(reconciled.savedDocument).toEqual(saved)
    expect(reconciled.pending).toEqual([])
    expect(reconciled.version).toBe(2)
  })

  it('keeps undo and redo history across successful autosave', () => {
    const operation = makeOperation('update_caption', 'caption', 1, { text: 'New' })
    const changed = apply(createEditorState(document, 1), operation)
    const saved = editorReducer(changed, { type: 'save_succeeded',
      document: applyLocal(document, operation), version: 2,
      operationIds: [operation.operationId] })
    expect(saved.past).toHaveLength(1)
    const undone = editorReducer(saved, { type: 'undo' })
    expect(undone.document).toEqual(document)
    expect(undone.version).toBe(2)
    expect(undone.pending).toHaveLength(1)
    expect(undone.pending[0]).toMatchObject({ baseVersion: 2, text: 'Old' })
    const redone = editorReducer(undone, { type: 'redo' })
    expect(redone.document.tracks[1].items[0].text).toBe('New')
    expect(redone.pending).toHaveLength(2)
    expect(redone.pending.every((item) => item.baseVersion === 2)).toBe(true)
  })

  it('preserves trim start when trim end is edited next', () => {
    let state = createEditorState(document, 1)
    state = apply(state, makeOperation('trim_clip', 'a', 1,
      trimArguments(state.document, 'a', 'start', 0.5)))
    state = apply(state, makeOperation('trim_clip', 'a', 1,
      trimArguments(state.document, 'a', 'end', 1.5)))
    expect(state.document.tracks[0].items[0]).toMatchObject({
      sourceStart: 0.5, sourceEnd: 1.5,
    })
  })

  it('resolves the current clip index for every repeated reorder', () => {
    let state = createEditorState(document, 1)
    state = apply(state, makeOperation('reorder_clip', 'a', 1,
      reorderArguments(state.document, 'a', 1)))
    state = apply(state, makeOperation('reorder_clip', 'a', 1,
      reorderArguments(state.document, 'a', 1)))
    expect(state.document.tracks[0].items.map((item) => item.id)).toEqual(['b', 'c', 'a'])
  })

  it('keeps autosave-safe undo history bounded', () => {
    let state = createEditorState(document, 1)
    for (let index = 0; index < 105; index += 1) {
      state = apply(state, makeOperation('update_caption', 'caption', 1, {
        text: `Revision ${index}`,
      }))
    }
    expect(state.past).toHaveLength(100)
  })

  it('persists undo of a clip deletion after autosave with a typed restore operation', () => {
    const deletion = makeOperation('delete_clip', 'b', 1)
    const changed = apply(createEditorState(document, 1), deletion)
    const savedDocument = applyLocal(document, deletion)
    const saved = editorReducer(changed, { type: 'save_succeeded', document: savedDocument,
      version: 2, operationIds: [deletion.operationId] })
    const undone = editorReducer(saved, { type: 'undo' })
    expect(undone.document.tracks[0].items.map((item) => item.id)).toEqual(['a', 'b', 'c'])
    expect(undone.pending).toHaveLength(1)
    expect(undone.pending[0]).toMatchObject({
      type: 'restore_clip', targetId: 'b', baseVersion: 2, toIndex: 1,
    })
    expect(undone.pending[0].clip.id).toBe('b')
  })
})
