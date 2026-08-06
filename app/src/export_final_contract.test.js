/**
 * Export contract: the preview proxy is never presented as the deliverable.
 *
 * The reveal screen once offered "Download this cut", which signed the
 * candidate's preview_storage_path — a 360×640 CRF-30 proxy — while reading
 * like the final output. The real deliverable is a Product Editor final_render
 * whose artifact is signed by /editor/renders/{id}/sign. Source-scan style,
 * same as no_legacy_paths.test.js, because these are contracts about what the
 * shipped source says and calls.
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const dir = path.dirname(fileURLToPath(import.meta.url))
const project = readFileSync(path.join(dir, 'pages/Project.jsx'), 'utf8')

describe('final export is a real final_render, not the preview proxy', () => {
  it('offers "Export final MP4" on the finished-edit screen', () => {
    expect(project).toMatch(/Export final MP4/)
  })

  it('export starts an editor document and a final render', () => {
    // the export action must go candidate -> editor/start -> editor/render
    expect(project).toMatch(/editor\/start/)
    expect(project).toMatch(/editor\/render`/)
  })

  it('the signed final download comes from the final_render artifact', () => {
    expect(project).toMatch(/editor\/renders\/\$\{job\.id\}\/sign/)
  })

  it('never labels the preview as the final cut', () => {
    expect(project).not.toMatch(/Download this cut/)
  })

  it('the preview link says it is a preview, with proxy caveat', () => {
    expect(project).toMatch(/Download preview/)
    expect(project).toMatch(/not the final export/)
  })

  it('the preview download filename is marked as a preview', () => {
    expect(project).toMatch(/-preview\.mp4/)
  })
})

describe('failed edits offer an explicit retry', () => {
  it('planner/edit failures retry via request-edit, analysis via request-analysis', () => {
    expect(project).toMatch(/request-edit/)
    expect(project).toMatch(/request-analysis/)
  })
})


describe('editing starts on consent, never on upload completion', () => {
  it('startUpload does not call request-analysis', () => {
    const startUpload = project.slice(project.indexOf('async function startUpload'),
                                      project.indexOf('async function startEditing'))
    expect(startUpload).not.toMatch(/request-analysis/)
  })

  it('the explicit Start editing action exists and calls request-analysis', () => {
    expect(project).toMatch(/Start editing/)
    const startEditing = project.slice(project.indexOf('async function startEditing'))
    expect(startEditing.slice(0, 2000)).toMatch(/request-analysis/)
  })
})
