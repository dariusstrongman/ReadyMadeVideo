import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const dir = path.dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(path.join(dir, rel), 'utf8')
const project = read('pages/Project.jsx')
const editor = read('pages/Editor.jsx')
const dashboard = read('pages/Dashboard.jsx')

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

  it('does not use legacy render_jobs output_* fields', () => {
    for (const field of ['output_storage_path', 'output_width', 'output_height',
      'output_duration_seconds', 'output_size_bytes']) {
      expect(project).not.toContain(field)
      expect(editor).not.toContain(field)
    }
  })

  it('never signs storage directly in the browser (uses the backend sign endpoint)', () => {
    expect(project).not.toMatch(/createSignedUrl/)
    expect(editor).not.toMatch(/createSignedUrl/)
    expect(project).toMatch(/editor\/renders\/[^/]*\/sign/)   // export download via backend
  })

  it('reads export metadata from pipeline_jobs.artifacts', () => {
    expect(project).toMatch(/artifacts/)
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

  it('deletes projects via the server endpoint, not client-side', () => {
    expect(dashboard).not.toMatch(/from\(['"]projects['"]\)\.delete/)
    expect(dashboard).not.toMatch(/storage\.from\([^)]*\)\.remove/)
    expect(dashboard).toMatch(/editorApi\(`\/projects\/\$\{p\.id\}`/)
    expect(dashboard).toMatch(/DELETE/)
  })
})
