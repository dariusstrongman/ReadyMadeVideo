import { describe, expect, it } from 'vitest'
import { applyLocal, editorReducer, makeOperation } from './editor'

const document = {
  duration: 4,
  tracks: [
    { type: 'picture', items: [
      { id: 'a', sourceStart: 0, sourceEnd: 2, timelineStart: 0, timelineEnd: 2 },
      { id: 'b', sourceStart: 2, sourceEnd: 4, timelineStart: 2, timelineEnd: 4 },
    ] },
    { type: 'captions', items: [{ id: 'c', text: 'Old' }] },
    { type: 'music', items: [{ id: 'music-main', gainDb: -12 }] },
    { type: 'sfx', items: [] }, { type: 'graphics', items: [{ id: 'g', enabled: true }] },
  ],
}

describe('editor operation reducer', () => {
  it('reorders and reflows without mutating the source', () => {
    const result = applyLocal(document, makeOperation('reorder_clip', 'b', 1, { toIndex: 0 }))
    expect(result.tracks[0].items.map((item) => item.id)).toEqual(['b', 'a'])
    expect(result.tracks[0].items[0].timelineStart).toBe(0)
    expect(document.tracks[0].items[0].id).toBe('a')
  })

  it('updates captions and supports undo', () => {
    const initial = { document, past: [], future: [], pending: [] }
    const changed = editorReducer(initial, { type: 'apply', operation:
      makeOperation('update_caption', 'c', 1, { text: 'New' }) })
    expect(changed.document.tracks[1].items[0].text).toBe('New')
    expect(editorReducer(changed, { type: 'undo' }).document).toEqual(document)
  })
})
