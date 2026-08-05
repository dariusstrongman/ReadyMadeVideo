#!/usr/bin/env node
/**
 * Fails when a className used in JSX has no rule in styles.css.
 *
 * Three separate bugs this session were exactly this: markup renamed without the
 * stylesheet following, so elements mounted and rendered invisible or unstyled.
 * The production build cannot catch it (a missing CSS class is valid code) and
 * unit tests do not render these screens, so it reached the user every time.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const CSS = 'src/styles.css'
const ROOT = 'src'

// Prefixes of classes built by template interpolation (`st-i-${intent}`), where
// the static half is never a rule on its own.
const DYNAMIC_PREFIXES = ['st-i-', 'ar-']

function jsxFiles(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) return jsxFiles(p)
    return p.endsWith('.jsx') && !p.endsWith('.test.jsx') ? [p] : []
  })
}

const css = readFileSync(CSS, 'utf8')
const defined = new Set([...css.matchAll(/\.([a-zA-Z][\w-]*)/g)].map((m) => m[1]))

const problems = []
for (const file of jsxFiles(ROOT)) {
  const src = readFileSync(file, 'utf8')
  const used = new Set()
  for (const m of src.matchAll(/className="([^"{]+)"/g)) {
    m[1].split(/\s+/).forEach((n) => n && used.add(n))
  }
  for (const m of src.matchAll(/className=\{`([^`]*)`\}/g)) {
    m[1].replace(/\$\{[^}]*\}/g, ' ').split(/\s+/).forEach((n) => n && used.add(n))
  }
  for (const name of used) {
    if (defined.has(name)) continue
    if (DYNAMIC_PREFIXES.includes(name)) continue
    problems.push(`${file}: .${name}`)
  }
}

if (problems.length) {
  console.error('classname-guard: used in JSX but absent from styles.css\n')
  problems.forEach((p) => console.error(`  ${p}`))
  console.error('\nAdd the rule, or correct the class name.')
  process.exit(1)
}
console.log('classname-guard: every className has a matching CSS rule')
