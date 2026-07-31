#!/usr/bin/env python3
"""Apply video-pipeline migrations to the Stromation Supabase project.

Reads configuration from the ENVIRONMENT only (never commit secrets):
  SUPABASE_STROMATION_PAT   -> Management API (DDL)         [required]
  SUPABASE_URL              -> project REST url             [required]
  SUPABASE_SERVICE_ROLE_KEY -> service role (buckets)       [required]
Optionally set SECRETS_ENV_FILE to a local .env file to source them from.

Safety: verifies the project name is "Stromation" before running anything.
Idempotent: migrations use IF NOT EXISTS / drop-policy-then-create.
"""
import json
import os
import sys
import urllib.request
import urllib.error

PROJECT_REF = "iadzcnzgbtuigyodeqas"
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "supabase", "migrations")

def env():
    vals = dict(os.environ)
    env_file = os.environ.get("SECRETS_ENV_FILE")
    if env_file and os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    missing = [k for k in ("SUPABASE_STROMATION_PAT",) if not vals.get(k)]
    if missing:
        sys.exit(f"missing required env: {', '.join(missing)} "
                 f"(set directly or via SECRETS_ENV_FILE)")
    return vals

def call(url, method="GET", headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": "Mozilla/5.0",
                                          "Content-Type": "application/json",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]

def main():
    e = env()
    pat = e["SUPABASE_STROMATION_PAT"]
    sb_url = (e.get("SUPABASE_URL") or e.get("SUPABASE_MAIN_URL", "")).rstrip("/")
    service = e.get("SUPABASE_SERVICE_ROLE_KEY") or e.get("SUPABASE_MAIN_SERVICE_KEY", "")
    if not sb_url or not service:
        sys.exit("missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY")
    mgmt = {"Authorization": f"Bearer {pat}"}

    # 1. identity guard
    st, proj = call(f"https://api.supabase.com/v1/projects/{PROJECT_REF}", headers=mgmt)
    assert st == 200 and proj["name"] == "Stromation", f"identity check failed: {st} {proj}"
    print(f"project verified: {proj['name']} ({PROJECT_REF})")

    # 2. run SQL migrations in order
    for fname in sorted(os.listdir(MIGRATIONS_DIR)):
        if not fname.endswith(".sql"):
            continue
        sql = open(os.path.join(MIGRATIONS_DIR, fname), encoding="utf-8").read()
        st, out = call(f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
                       method="POST", headers=mgmt, body={"query": sql})
        print(f"migration {fname}: HTTP {st}" + ("" if st == 201 or st == 200 else f" -> {out}"))
        if st not in (200, 201):
            sys.exit(1)

    # 3. create private buckets (idempotent — 409 means it already exists)
    # NOTE: per-file limit is capped by the Supabase plan (free tier = 50 MB global max).
    # Raising above the plan cap returns EntityTooLarge. Bump on Pro plan later.
    sh = {"apikey": service, "Authorization": f"Bearer {service}"}
    for bucket, limit_mb in (("raw-footage", 50), ("exports", 50)):
        st, out = call(f"{sb_url}/storage/v1/bucket", method="POST", headers=sh,
                       body={"id": bucket, "name": bucket, "public": False,
                             "file_size_limit": limit_mb * 1024 * 1024})
        state = "created" if st == 200 else ("already exists" if st in (400, 409) and "already" in str(out).lower() else f"HTTP {st}: {out}")
        print(f"bucket {bucket}: {state}")

    # 4. verify state
    st, out = call(f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
                   method="POST", headers=mgmt,
                   body={"query": "select table_name from information_schema.tables where table_schema='public' order by 1;"})
    print("tables now:", [r["table_name"] for r in out])
    st, out = call(f"{sb_url}/storage/v1/bucket", headers=sh)
    print("buckets now:", [(b["id"], "private" if not b["public"] else "PUBLIC!") for b in out])

if __name__ == "__main__":
    main()
