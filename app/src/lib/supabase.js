import { createClient } from '@supabase/supabase-js'

// Browser-safe values. RLS is the security boundary — the publishable key can
// only touch the caller's own rows and own storage paths.
export const SUPABASE_URL =
  import.meta.env.VITE_SUPABASE_URL || 'https://iadzcnzgbtuigyodeqas.supabase.co'
export const SUPABASE_ANON_KEY =
  import.meta.env.VITE_SUPABASE_ANON_KEY ||
  'sb_publishable_8qa-nssfdtEkCz-42wOSWQ_2P7S4Zj7'
export const RENDER_API =
  import.meta.env.VITE_RENDER_API || 'http://localhost:8787'

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
