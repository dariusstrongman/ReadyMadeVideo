#!/usr/bin/env python3
"""Live operator-flow E2E (P3/P4): proves the job system + operator API end to
end against the real backend + Supabase + worker.

Flow: customer uploads real footage -> operator triggers ANALYSIS job (worker
runs the full pipeline) -> coverage report -> GENERATE-DRAFT job (autoedit +
critic) -> evaluation row exists -> operator records manual metrics ->
FINAL RENDER job -> project 'completed' + private export -> authorization
negatives (customer cannot use operator endpoints).

Env (required): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Optional: RENDER_API (default http://localhost:8787), E2E_VIDEO, --keep
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SB = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
ANON = os.environ.get("SUPABASE_ANON_KEY",
                      "sb_publishable_8qa-nssfdtEkCz-42wOSWQ_2P7S4Zj7")
API = os.environ.get("RENDER_API", "http://localhost:8787")
if not SB or not SERVICE:
    sys.exit("set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")

H = {"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}",
     "Content-Type": "application/json"}
results = []


def http(method, url, body=None, headers=None, raw=None, timeout=120):
    data = raw if raw is not None else (
        json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or H)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            c = r.read()
            try:
                return r.status, json.loads(c) if c else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                return r.status, c
    except urllib.error.HTTPError as e:
        c = e.read()
        try:
            return e.code, json.loads(c)
        except json.JSONDecodeError:
            return e.code, c.decode(errors="replace")[:300]


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        print(f"\n=== ABORT: {sum(results)}/{len(results)} passed ===")
        sys.exit(1)


def wait_job(job_id, token, timeout_s=600):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st, j = http("GET", f"{API}/jobs/{job_id}",
                     headers={"Authorization": f"Bearer {token}",
                              "Content-Type": "application/json"})
        if st == 200 and j["status"] in ("completed", "failed", "cancelled"):
            return j
        time.sleep(4)
    return {"status": "timeout"}


def main():
    keep = "--keep" in sys.argv
    sfx = uuid.uuid4().hex[:8]

    # backend up?
    st, out = http("GET", f"{API}/healthz")
    check("backend healthy", st == 200)

    # users: customer + operator (operator row = server-side grant)
    ids, tokens = {}, {}
    for tag in ("customer", "operator"):
        email = f"opflow-{tag}-{sfx}@stromation.com"
        pw = uuid.uuid4().hex + "Aa1!"
        st, u = http("POST", f"{SB}/auth/v1/admin/users",
                     body={"email": email, "password": pw, "email_confirm": True})
        ids[tag] = u["id"]
        st, t = http("POST", f"{SB}/auth/v1/token?grant_type=password",
                     headers={"apikey": ANON, "Content-Type": "application/json"},
                     body={"email": email, "password": pw})
        tokens[tag] = t["access_token"]
        if keep:
            print(f"  ({tag}: {email} / {pw})")
    st, out = http("POST", f"{SB}/rest/v1/operators",
                   body={"user_id": ids["operator"]},
                   headers={**H, "Prefer": "return=minimal"})
    check("operator role granted (DB row)", st == 201)

    def cust_h():
        return {"apikey": ANON, "Authorization": f"Bearer {tokens['customer']}",
                "Content-Type": "application/json"}

    def op_h():
        return {"Authorization": f"Bearer {tokens['operator']}",
                "Content-Type": "application/json"}

    # customer creates a project + uploads real multi-scene footage
    st, p = http("POST", f"{SB}/rest/v1/projects",
                 body={"user_id": ids["customer"], "name": "Operator Flow E2E"},
                 headers={**cust_h(), "Prefer": "return=representation"})
    project = p[0]

    video = os.environ.get("E2E_VIDEO")
    if not video:
        video = os.path.join(os.environ.get("TEMP", "/tmp"), f"opflow-{sfx}.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-filter_complex",
                        "testsrc=size=1280x720:rate=30:duration=5[a];"
                        "testsrc2=size=1280x720:rate=30:duration=6[b];"
                        "gradients=size=1280x720:rate=30:duration=4[c];"
                        "[a][b][c]concat=n=3:v=1:a=0[v];"
                        "sine=frequency=440:duration=15[aud]",
                        "-map", "[v]", "-map", "[aud]", "-c:v", "libx264",
                        "-preset", "veryfast", "-c:a", "aac", video],
                       check=True, timeout=120)
    asset_id = str(uuid.uuid4())
    path = f"users/{ids['customer']}/projects/{project['id']}/raw/{asset_id}/clip.mp4"
    st, out = http("POST", f"{SB}/storage/v1/object/raw-footage/{path}",
                   headers={"apikey": ANON,
                            "Authorization": f"Bearer {tokens['customer']}",
                            "Content-Type": "video/mp4"},
                   raw=open(video, "rb").read())
    check("customer uploaded real footage", st == 200)
    st, out = http("POST", f"{SB}/rest/v1/media_assets",
                   body={"id": asset_id, "project_id": project["id"],
                         "user_id": ids["customer"], "filename": "clip.mp4",
                         "storage_path": path, "mime_type": "video/mp4",
                         "size_bytes": os.path.getsize(video),
                         "duration_seconds": 15.0},
                   headers={**cust_h(), "Prefer": "return=minimal"})
    check("asset recorded", st == 201)

    # authorization negatives FIRST
    st, out = http("POST", f"{API}/projects/{project['id']}/analyze",
                   body={"params": {}},
                   headers={"Authorization": f"Bearer {tokens['customer']}",
                            "Content-Type": "application/json"})
    check("customer cannot use operator endpoint", st == 403, f"HTTP {st}")
    st, out = http("POST", f"{API}/projects/{project['id']}/sign",
                   body={"bucket": "raw-footage", "path": path},
                   headers={"Authorization": f"Bearer {tokens['customer']}",
                            "Content-Type": "application/json"})
    check("customer cannot sign via operator endpoint", st == 403)

    # ANALYSIS job through the worker
    st, job = http("POST", f"{API}/projects/{project['id']}/analyze",
                   body={"params": {"context": "operator flow test"}},
                   headers=op_h())
    check("analysis job enqueued", st == 200 and job.get("id"), f"HTTP {st}")
    st, dup = http("POST", f"{API}/projects/{project['id']}/analyze",
                   body={"params": {}}, headers=op_h())
    check("duplicate analyze is idempotent (same job)",
          st == 200 and dup.get("id") == job["id"])
    j = wait_job(job["id"], tokens["operator"])
    check("analysis job completed by worker", j["status"] == "completed",
          f"status={j['status']} err={j.get('error_message')}")
    st, segs = http("GET", f"{SB}/rest/v1/segments?project_id=eq.{project['id']}"
                           f"&select=id", headers=H)
    check("segment catalog produced", st == 200 and len(segs) >= 2,
          f"{len(segs)} segments")

    # coverage report
    st, cov = http("GET", f"{API}/projects/{project['id']}/coverage",
                   headers=op_h())
    check("coverage report generated", st == 200 and cov.get("missingRequired") is not None,
          f"missing: {len(cov.get('missingRequired', []))} categories")

    # DRAFT job (autoedit + critic)
    st, job2 = http("POST", f"{API}/projects/{project['id']}/generate-draft",
                    body={"params": {"brief": "energetic test edit",
                                     "target_duration": 12,
                                     "title": "OPERATOR FLOW"}},
                    headers=op_h())
    check("draft job enqueued", st == 200)
    j2 = wait_job(job2["id"], tokens["operator"])
    check("draft job completed", j2["status"] == "completed",
          f"status={j2['status']} err={j2.get('error_message')}")
    check("draft previews stored privately",
          bool((j2.get("artifacts") or {}).get("previews")))
    st, ev = http("GET", f"{SB}/rest/v1/draft_evaluations"
                         f"?project_id=eq.{project['id']}&select=*", headers=H)
    check("evaluation row auto-created", st == 200 and len(ev) >= 1)
    st, pr = http("GET", f"{SB}/rest/v1/projects?id=eq.{project['id']}"
                         f"&select=status", headers=H)
    check("project status draft_ready", pr[0]["status"] == "draft_ready",
          pr[0]["status"])

    # operator records manual metrics
    st, out = http("POST", f"{API}/projects/{project['id']}/evaluation",
                   body={"fields": {"human_correction_minutes": 7.5,
                                    "first_draft_rating": 6}},
                   headers=op_h())
    check("operator recorded evaluation metrics", st == 200)

    # FINAL RENDER job
    st, job3 = http("POST", f"{API}/projects/{project['id']}/render-final",
                    body={"params": {}}, headers=op_h())
    check("final render enqueued", st == 200)
    j3 = wait_job(job3["id"], tokens["operator"])
    check("final render completed", j3["status"] == "completed",
          f"status={j3['status']} err={j3.get('error_message')}")
    st, pr = http("GET", f"{SB}/rest/v1/projects?id=eq.{project['id']}"
                         f"&select=status,status_reason", headers=H)
    check("project completed with reason", pr[0]["status"] == "completed",
          str(pr[0]))
    st, out = http("POST", f"{API}/projects/{project['id']}/sign",
                   body={"bucket": "exports",
                         "path": (j3.get("artifacts") or {}).get("output", "")},
                   headers=op_h())
    check("operator can sign the final export", st == 200 and "url" in out)

    # audit trail exists
    st, audit = http("GET", f"{SB}/rest/v1/operator_audit"
                            f"?project_id=eq.{project['id']}&select=action",
                     headers=H)
    check("operator actions audited", st == 200 and len(audit) >= 5,
          f"{len(audit)} audit rows: {sorted({a['action'] for a in audit})}")

    # telemetry recorded
    st, met = http("GET", f"{SB}/rest/v1/stage_metrics"
                          f"?project_id=eq.{project['id']}&select=stage,estimated_cost_usd",
                   headers=H)
    check("stage metrics + cost estimates recorded", st == 200 and len(met) >= 2,
          f"{[m['stage'] for m in met]}")

    if keep:
        print(f"\n--keep: project {project['id']} + users left for console review")
    else:
        for uid in ids.values():
            http("DELETE", f"{SB}/auth/v1/admin/users/{uid}")
        print("\ncleanup: users removed (rows cascade); storage objects remain "
              "under deleted-user prefixes and are swept by admin tooling")
    print(f"\n=== {sum(results)}/{len(results)} operator-flow checks passed ===")


if __name__ == "__main__":
    main()
