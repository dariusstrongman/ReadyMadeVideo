#!/usr/bin/env python3
"""DB-boundary integrity tests (Priority 1).

Proves that cross-user and cross-project writes are rejected AT THE DATABASE
(triggers), even when attempted with the service role (which bypasses RLS) —
RLS alone is not the boundary anymore.

Reads configuration from the environment ONLY:
  SUPABASE_URL                (required)
  SUPABASE_SERVICE_ROLE_KEY   (required)
Creates two throwaway users, exercises the boundary, cleans everything up.
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SB = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
if not SB or not SERVICE:
    sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the environment")

H = {"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}",
     "Content-Type": "application/json"}
results = []


def http(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or H)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main():
    suffix = uuid.uuid4().hex[:8]
    users = {}
    for tag in ("a", "b"):
        st, out = http("POST", f"{SB}/auth/v1/admin/users",
                       body={"email": f"dbint-{tag}-{suffix}@stromation.com",
                             "password": uuid.uuid4().hex + "Aa1!",
                             "email_confirm": True})
        users[tag] = out["id"]

    def ins(table, body, prefer="return=representation"):
        return http("POST", f"{SB}/rest/v1/{table}", body=body,
                    headers={**H, "Prefer": prefer})

    # setup: project for each user
    st, pa = ins("projects", {"user_id": users["a"], "name": "A proj"})
    st, pb = ins("projects", {"user_id": users["b"], "name": "B proj"})
    pa, pb = pa[0], pb[0]

    # 1. media_asset with mismatched owner -> rejected by trigger
    st, out = ins("media_assets", {
        "project_id": pa["id"], "user_id": users["b"],
        "filename": "x.mp4", "storage_path": "users/x/x.mp4"})
    check("asset with user_id != project owner rejected", st >= 400
          and "cross-user" in str(out), f"HTTP {st}")

    # 2. valid asset for A
    st, asset = ins("media_assets", {
        "project_id": pa["id"], "user_id": users["a"],
        "filename": "a.mp4", "storage_path": "users/a/a.mp4"})
    check("valid asset accepted", st == 201)
    asset = asset[0]

    # 3. timeline pointing at A's project but owned by B -> rejected
    st, out = ins("timelines", {
        "project_id": pa["id"], "user_id": users["b"], "version": 1,
        "timeline_json": {"version": 1}})
    check("timeline cross-user rejected", st >= 400 and "cross-user" in str(out))

    st, tl = ins("timelines", {
        "project_id": pa["id"], "user_id": users["a"], "version": 1,
        "timeline_json": {"version": 1}})
    tl = tl[0]

    # 4. render job referencing A's timeline but B's project -> rejected
    st, out = ins("render_jobs", {
        "project_id": pb["id"], "user_id": users["b"], "timeline_id": tl["id"]})
    check("render job with foreign timeline rejected", st >= 400
          and ("cross-ref" in str(out) or "does not belong" in str(out)))

    # 5. render job cross-user -> rejected
    st, out = ins("render_jobs", {
        "project_id": pa["id"], "user_id": users["b"], "timeline_id": tl["id"]})
    check("render job cross-user rejected", st >= 400)

    # 6. segment referencing A's asset under B's project -> rejected
    st, out = ins("segments", {
        "segment_key": "s1", "asset_id": asset["id"], "project_id": pb["id"],
        "user_id": users["b"], "source_start": 0, "source_end": 1,
        "data": {}})
    check("segment with foreign asset rejected", st >= 400)

    # 7. asset_analysis cross-project -> rejected
    st, out = ins("asset_analysis", {
        "asset_id": asset["id"], "project_id": pb["id"], "user_id": users["b"],
        "kind": "probe", "data": {}})
    check("analysis record with foreign asset rejected", st >= 400)

    # 8. edit_run / correction cross-user -> rejected
    st, out = ins("edit_runs", {"project_id": pa["id"], "user_id": users["b"]})
    check("edit_run cross-user rejected", st >= 400)
    st, out = ins("user_corrections", {
        "project_id": pa["id"], "user_id": users["b"],
        "original_timeline_version": 1, "requested_change": "x",
        "accepted": True})
    check("correction cross-user rejected", st >= 400)

    # 9. pipeline_jobs idempotency: second active job of same kind rejected
    st, j1 = ins("pipeline_jobs", {
        "project_id": pa["id"], "user_id": users["a"], "kind": "analysis"})
    st2, out = ins("pipeline_jobs", {
        "project_id": pa["id"], "user_id": users["a"], "kind": "analysis"})
    check("duplicate active job rejected (idempotency index)",
          st == 201 and st2 == 409, f"first={st} second={st2}")

    # 10. status transition recorded with reason
    http("PATCH", f"{SB}/rest/v1/projects?id=eq.{pa['id']}",
         body={"status": "analyzing", "status_reason": "integrity test"},
         headers={**H, "Prefer": "return=minimal"})
    st, ev = http("GET", f"{SB}/rest/v1/project_status_events"
                         f"?project_id=eq.{pa['id']}&select=*")
    check("status transition event recorded with reason",
          st == 200 and len(ev) == 1 and ev[0]["reason"] == "integrity test"
          and ev[0]["to_status"] == "analyzing")

    # cleanup
    for uid in users.values():
        http("DELETE", f"{SB}/auth/v1/admin/users/{uid}")
    print(f"\n=== {sum(results)}/{len(results)} integrity checks passed ===")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
