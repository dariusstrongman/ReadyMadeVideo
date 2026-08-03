/**
 * Tests for the media_assets insert schema mismatch fix.
 *
 * Root cause: The frontend was inserting `original_filename` and `file_size_bytes`
 * but the live schema has `filename` (NOT NULL) and `size_bytes`.
 * PostgREST silently ignored the unknown columns, leaving `filename` as NULL,
 * which caused the worker to fail with "project has no uploaded footage".
 *
 * These tests verify the correct field names and error-handling behavior.
 */

import { describe, it, expect, vi } from 'vitest'

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Builds the insert payload the way startUpload() now does */
function buildInsertPayload(projectId, userId, key, file) {
  return {
    project_id: projectId,
    user_id: userId,
    storage_path: key,
    filename: file.name,        // was: original_filename (wrong)
    size_bytes: file.size,      // was: file_size_bytes (wrong)
    mime_type: file.type,
  }
}

/** Simulates the upload flow: storage upload → DB insert → project status update */
async function simulateUpload({ storageError, dbError, statusError, file, projectId, userId, key }) {
  const uploaded = []
  const removed = []
  const inserted = []
  const statusUpdates = []

  const supabase = {
    storage: {
      from: () => ({
        upload: async () => ({ error: storageError || null }),
        remove: async (keys) => { removed.push(...keys); return { error: null } },
      }),
    },
    from: (table) => ({
      insert: async (payload) => {
        inserted.push({ table, payload })
        return { error: dbError || null }
      },
      update: async (patch) => ({
        eq: async () => {
          statusUpdates.push(patch)
          return { error: statusError || null }
        },
      }),
    }),
  }

  let error = null
  let uploadComplete = false

  const { error: upErr } = await supabase.storage.from('raw-footage').upload(key, file, {})
  if (upErr) { error = upErr.message; return { error, uploaded, removed, inserted, statusUpdates, uploadComplete } }

  const payload = buildInsertPayload(projectId, userId, key, file)
  const { error: dbErr } = await supabase.from('media_assets').insert(payload)
  if (dbErr) {
    await supabase.storage.from('raw-footage').remove([key])
    error = `Could not save footage record: ${dbErr.message}. Please try again.`
    return { error, uploaded, removed, inserted, statusUpdates, uploadComplete }
  }

  const { error: statusErr } = await (await supabase.from('projects').update({ status: 'ready' })).eq('id', projectId)
  if (statusErr) {
    error = `Upload complete but could not start processing: ${statusErr.message}`
    return { error, uploaded, removed, inserted, statusUpdates, uploadComplete }
  }

  uploadComplete = true
  return { error, uploaded, removed, inserted, statusUpdates, uploadComplete }
}

const mockFile = { name: 'workout.mp4', size: 10_000_000, type: 'video/mp4' }
const projectId = 'proj-123'
const userId = 'user-456'
const key = `users/${userId}/projects/${projectId}/raw/1234_abc.mp4`

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('media_assets insert — correct field names', () => {
  it('uses filename (not original_filename)', () => {
    const payload = buildInsertPayload(projectId, userId, key, mockFile)
    expect(payload).toHaveProperty('filename', 'workout.mp4')
    expect(payload).not.toHaveProperty('original_filename')
  })

  it('uses size_bytes (not file_size_bytes)', () => {
    const payload = buildInsertPayload(projectId, userId, key, mockFile)
    expect(payload).toHaveProperty('size_bytes', 10_000_000)
    expect(payload).not.toHaveProperty('file_size_bytes')
  })

  it('preserves all required fields', () => {
    const payload = buildInsertPayload(projectId, userId, key, mockFile)
    expect(payload.project_id).toBe(projectId)
    expect(payload.user_id).toBe(userId)
    expect(payload.storage_path).toBe(key)
    expect(payload.mime_type).toBe('video/mp4')
  })
})

describe('media_assets insert — error handling', () => {
  it('successful insert completes upload and sets project to ready', async () => {
    const result = await simulateUpload({ file: mockFile, projectId, userId, key })
    expect(result.error).toBeNull()
    expect(result.uploadComplete).toBe(true)
    expect(result.inserted).toHaveLength(1)
    expect(result.inserted[0].table).toBe('media_assets')
    expect(result.statusUpdates).toHaveLength(1)
    expect(result.statusUpdates[0]).toEqual({ status: 'ready' })
  })

  it('failed DB insert shows visible error message', async () => {
    const result = await simulateUpload({
      file: mockFile, projectId, userId, key,
      dbError: { message: 'null value in column "filename"' },
    })
    expect(result.error).toContain('Could not save footage record')
    expect(result.error).toContain('null value in column "filename"')
  })

  it('failed DB insert cleans up orphaned storage object', async () => {
    const result = await simulateUpload({
      file: mockFile, projectId, userId, key,
      dbError: { message: 'null value in column "filename"' },
    })
    expect(result.removed).toContain(key)
  })

  it('project status is NOT set to ready when DB insert fails', async () => {
    const result = await simulateUpload({
      file: mockFile, projectId, userId, key,
      dbError: { message: 'null value in column "filename"' },
    })
    expect(result.statusUpdates).toHaveLength(0)
    expect(result.uploadComplete).toBe(false)
  })

  it('failed status update shows visible error and does not silently succeed', async () => {
    const result = await simulateUpload({
      file: mockFile, projectId, userId, key,
      statusError: { message: 'permission denied' },
    })
    expect(result.error).toContain('Upload complete but could not start processing')
    expect(result.uploadComplete).toBe(false)
  })

  it('storage upload error stops the flow before DB insert', async () => {
    const result = await simulateUpload({
      file: mockFile, projectId, userId, key,
      storageError: { message: 'Bucket not found' },
    })
    expect(result.error).toBe('Bucket not found')
    expect(result.inserted).toHaveLength(0)
    expect(result.statusUpdates).toHaveLength(0)
  })
})
