import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const dir = path.dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(path.join(dir, rel), 'utf8')
const project = read('pages/Project.jsx')
const editor = read('pages/Editor.jsx')

describe('no legacy customer path remains (shipped app source)', () => {
  it('never queries edit_candidates', () => {
    expect(project).not.toMatch(/edit_candidates/)
    expect(editor).not.toMatch(/edit_candidates/)
  })

  it('never writes render_jobs', () => {
    expect(project).not.toMatch(/render_jobs/)
    expect(editor).not.toMatch(/render_jobs/)
  })

  it('never writes timeline_json directly from the client', () => {
    expect(project).not.toMatch(/timeline_json/)
    expect(editor).not.toMatch(/timeline_json/)
  })

  it('never calls the legacy ${RENDER_API}/render endpoint', () => {
    expect(project).not.toMatch(/\$\{RENDER_API\}\/render["'`]/)
    expect(editor).not.toMatch(/\$\{RENDER_API\}\/render["'`]/)
    expect(editor).not.toMatch(/RENDER_API/)   // editor talks only via editorApi
  })

  it('does not use a raw private storage path as a media URL', () => {
    expect(project).not.toMatch(/preview_storage_path/)
    expect(editor).not.toMatch(/preview_storage_path/)
    expect(project).not.toMatch(/src=\{[^}]*storage_path/)
    expect(editor).not.toMatch(/src=\{[^}]*storage_path/)
  })

  it('uses the immutable Product Editor endpoints', () => {
    expect(project).toMatch(/\/workspace/)
    expect(project).toMatch(/editor\/start/)
    expect(project).toMatch(/preview-url/)
    expect(editor).toMatch(/\/operations/)
    expect(editor).toMatch(/editor\/render/)
    expect(editor).toMatch(/preview-url/)
  })

  it('opens candidates by navigating to the returned editor_document id', () => {
    // openCandidate uses the doc returned by /editor/start, not the candidate id
    expect(project).toMatch(/editorApi\([^)]*editor\/start/)
    expect(project).toMatch(/editor\/\$\{doc\.id\}/)
  })
})
