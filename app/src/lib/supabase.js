import { createClient } from '@supabase/supabase-js'
import { RENDER_API, SUPABASE_ANON_KEY, SUPABASE_URL } from './config'

// Browser-safe values. RLS is the security boundary — the publishable key can
// only touch the caller's own rows and own storage paths.
export { RENDER_API, SUPABASE_ANON_KEY, SUPABASE_URL }

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)

/** Real upload with real progress: XHR PUT to the Storage REST endpoint with
 * the USER's JWT — RLS storage policies enforce the users/{uid}/ path scope. */
export function uploadWithProgress({ bucket, path, file, accessToken, onProgress }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${SUPABASE_URL}/storage/v1/object/${bucket}/${path}`)
    xhr.setRequestHeader('Authorization', `Bearer ${accessToken}`)
    xhr.setRequestHeader('apikey', SUPABASE_ANON_KEY)
    xhr.setRequestHeader('x-upsert', 'false')
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream')
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else reject(new Error(`upload failed (${xhr.status}): ${xhr.responseText?.slice(0, 200)}`))
    }
    xhr.onerror = () => reject(new Error('network error during upload'))
    xhr.send(file)
  })
}
