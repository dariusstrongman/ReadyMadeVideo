// Build-time guard: fail if anything resembling a server-only secret is in the
// frontend source or the production bundle. Run in CI after `npm run build`.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const PATTERNS = [
  /service_role/i,               // supabase service key JWTs carry this claim
  /sb_secret_[A-Za-z0-9]/,       // new-style secret keys
  /SUPABASE_SERVICE_ROLE_KEY\s*[:=]\s*['"][^'"]+/,
  /eyJ[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{20,}/, // JWT-looking blobs
  /sk-[A-Za-z0-9]{40,}/,         // OpenAI
  /AIza[0-9A-Za-z_-]{30,}/,      // Google API keys
  /sbp_[a-z0-9]{30,}/,           // supabase PAT
]

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) yield* walk(p)
    else if (/\.(js|jsx|ts|tsx|html|css|map|json)$/.test(name)) yield p
  }
}

let failed = false
for (const root of ['src', 'dist']) {
  let files = []
  try { files = [...walk(root)] } catch { continue }
  for (const f of files) {
    const text = readFileSync(f, 'utf8')
    for (const re of PATTERNS) {
      const m = text.match(re)
      if (m) {
        console.error(`SECRET-GUARD FAIL: ${f} matches ${re} (${m[0].slice(0, 24)}…)`)
        failed = true
      }
    }
  }
}
if (failed) process.exit(1)
console.log('secret-guard: frontend source and bundle are clean')
