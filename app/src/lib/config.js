export const SUPABASE_URL =
  import.meta.env.VITE_SUPABASE_URL || 'https://iadzcnzgbtuigyodeqas.supabase.co'
export const SUPABASE_ANON_KEY =
  import.meta.env.VITE_SUPABASE_ANON_KEY ||
  'sb_publishable_8qa-nssfdtEkCz-42wOSWQ_2P7S4Zj7'
export const RENDER_API =
  import.meta.env.VITE_RENDER_API ||
  (import.meta.env.DEV ? 'http://localhost:8787' : 'https://api.readymadevideo.com')

// One shared upload ceiling, mirrored by the backend (raw_uploads.MAX_UPLOAD_BYTES).
export const MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024 // 2 GiB (Internal Alpha)
// Default multipart part size; the server may return an adjusted size at initiate.
export const UPLOAD_PART_SIZE =
  Number(import.meta.env.VITE_UPLOAD_PART_SIZE) || 16 * 1024 * 1024 // 16 MiB
export const UPLOAD_LIMIT_TEXT = 'MP4, MOV • Up to 2 GB (Internal Alpha)'
