import { describe, expect, it } from 'vitest'
import { createEditorState, editorReducer, makeOperation, track } from './editor'

const baseDoc = () => ({
  schemaVersion: 1, width: 1080, height: 1920, fps: 30, duration: 8,
  tracks: [
    { type: 'picture', items: [
      { id: 'a', assetId: 'x', sourceStart: 0, sourceEnd: 4, timelineStart: 0, timelineEnd: 4, speed: 1 },
      { id: 'b', assetId: 'x', sourceStart: 4, sourceEnd: 8, timelineStart: 4, timelineEnd: 8, speed: 1 }] },
    { type: 'captions', items: [] },
    { type: 'music', items: [] },
    { type: 'sfx', items: [] },
    { type: 'graphics', items: [] },
  ],
})

describe('OperationBatch contract (matches backend OperationBatch)', () => {
  it('makeOperation produces the exact typed operation shape', () => {
    const op = makeOperation('trim_clip', 'clip-1', 3, { sourceStart: 1, sourceEnd: 5 })
    expect(op).toMatchObject({
      type: 'trim_clip', actor: 'user', targetId: 'clip-1', baseVersion: 3,
      sourceStart: 1, sourceEnd: 5,
    })
    expect(typeof op.operationId).toBe('string')
    expect(op.operationId.length).toBeGreaterThan(10)
    expect(typeof op.timestamp).toBe('string')
  })

  it('is NOT the legacy flat payload from fix/edit-candidates-source', () => {
    const op = makeOperation('reorder_clip', 'a', 1, { toIndex: 1 })
    // discriminated `type` + operationId + baseVersion — never a flat {action,...}
    expect(op).not.toHaveProperty('action')
    expect(op).not.toHaveProperty('op')
    expect(op).not.toHaveProperty('documentId')   // batch carries version, op carries baseVersion
    expect(op).toHaveProperty('type')
    expect(op).toHaveProperty('baseVersion')
  })

  it('a batch is {expectedVersion, operations[]} with all ops sharing baseVersion', () => {
    const version = 4
    const ops = [
      makeOperation('reorder_clip', 'a', version, { toIndex: 1 }),
      makeOperation('trim_clip', 'b', version, { sourceStart: 4, sourceEnd: 7 }),
    ]
    const batch = { expectedVersion: version, operations: ops }
    expect(new Set(batch.operations.map((o) => o.baseVersion))).toEqual(new Set([version]))
    batch.operations.forEach((o) => {
      expect(o).toHaveProperty('operationId')
      expect(o).toHaveProperty('type')
      expect(o).toHaveProperty('targetId')
    })
  })
})

describe('optimistic apply + reconciliation', () => {
  it('applies optimistically and queues one pending op at the current version', () => {
    let s = createEditorState(baseDoc(), 3)
    const op = makeOperation('reorder_clip', 'a', s.version, { toIndex: 1 })
    s = editorReducer(s, { type: 'apply', operation: op })
    expect(s.pending).toHaveLength(1)
    expect(s.pending[0].baseVersion).toBe(3)
    expect(track(s.document, 'picture').items[0].id).toBe('b')  // optimistic reorder
  })

  it('duplicate operation IDs are idempotent on save_succeeded', () => {
    let s = createEditorState(baseDoc(), 3)
    const op = makeOperation('reorder_clip', 'a', s.version, { toIndex: 1 })
    s = editorReducer(s, { type: 'apply', operation: op })
    s = editorReducer(s, { type: 'save_succeeded', document: baseDoc(), version: 4,
      operationIds: [op.operationId, op.operationId] })   // duplicate ids
    expect(s.pending).toHaveLength(0)   // removed exactly once, no error
    expect(s.version).toBe(4)
  })
})
