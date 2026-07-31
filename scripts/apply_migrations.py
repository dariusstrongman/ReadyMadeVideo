#!/usr/bin/env python3
"""Apply video-pipeline migrations to the Stromation Supabase project.

Reads credentials from C:/Users/Darius/.stromation-secrets/.env (never committed):
  SUPABASE_STROMATION_PAT  -> Management API (DDL)
  SUPABASE_MAIN_URL        -> https://iadzcnzgbtuigyodeqas.supabase.co
  SUPABASE_MAIN_SERVICE_KEY-> service role (bucket creation)

Safety: verifies the project name is "Stromation" before running anything.
Idempotent: migrations use IF NOT EXISTS / drop-policy-then-create.
"""
import json
import os
import sys
import urllib.request
import urllib.error

ENV_FILE = r"C:\Users\Darius\.stromation-secrets\.env"
PROJECT_REF = "iadzcnzgbtuigyodeqas"
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "supabase", "migrations")

def env():
    vals = {}
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
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
    sb_url = e["SUPABASE_MAIN_URL"]
    service = e["SUPABASE_MAIN_SERVICE_KEY"]
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
