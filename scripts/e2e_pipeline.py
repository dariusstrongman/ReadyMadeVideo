#!/usr/bin/env python3
"""End-to-end proof of the real pipeline (spec: Definition of done).

Runs the ENTIRE flow against the live Stromation Supabase + a locally running
render backend, using a real MP4 (assets/media/project-zero.mp4, 17 s):

  create users -> sign in -> create project -> upload real footage ->
  refetch (persistence) -> save timeline -> queue render job ->
  POST /render -> poll status -> signed-URL download -> ffprobe the output ->
  full cross-user authorization suite (user B + anon must be locked out).

Admin ops (user creation) use the service key from the local secrets file.
Everything user-facing runs with USER JWTs + the publishable key only,
exactly like the browser app does.

Usage:  python scripts/e2e_pipeline.py [--keep]
        --keep  leave user A's account/project/export in place for manual
                UI inspection (default cleans everything up)
"""
import argparse
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENV_FILE = r"C:\Users\Darius\.stromation-secrets\.env"
ANON = "sb_publishable_8qa-nssfdtEkCz-42wOSWQ_2P7S4Zj7"
RENDER_API = os.environ.get("RENDER_API", "http://localhost:8787")
VIDEO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "assets", "media", "project-zero.mp4")
USER_A = ("e2e-usera@stromation.com", "E2e-test-passw0rd-A!")
USER_B = ("e2e-userb@stromation.com", "E2e-test-passw0rd-B!")

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        print("ABORTING on failure.")
        summary()
        sys.exit(1)


def summary():
    p = sum(1 for _, ok, _ in results if ok)
    print(f"\n=== {p}/{len(results)} checks passed ===")


def env():
    vals = {}
    for line in open(ENV_FILE, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"')
    return vals


def http(method, url, headers=None, body=None, raw=None, timeout=120):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = r.read()
            try:
                return r.status, json.loads(content) if content else None
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                return r.status, content
    except urllib.error.HTTPError as e:
        content = e.read()
        try:
            return e.code, json.loads(content)
        except json.JSONDecodeError:
            return e.code, content.decode(errors="replace")[:300]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    e = env()
    SB = e["SUPABASE_MAIN_URL"].rstrip("/")
    SERVICE = e["SUPABASE_MAIN_SERVICE_KEY"]
    admin_h = {"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}",
               "Content-Type": "application/json"}

    def user_headers(token, extra=None):
        return {"apikey": ANON, "Authorization": f"Bearer {token}",
                "Content-Type": "application/json", **(extra or {})}

    # ---------- 0. backend up ----------
    st, out = http("GET", f"{RENDER_API}/healthz")
    check("render backend healthy", st == 200 and out.get("ok") is True)

    # ---------- 1. create accounts (admin, auto-confirmed) ----------
    uids = {}
    for email, pw in (USER_A, USER_B):
        st, out = http("GET", f"{SB}/auth/v1/admin/users?page=1&per_page=100", headers=admin_h)
        existing = next((u for u in (out.get("users") or []) if u["email"] == email), None)
        if existing:
            uids[email] = existing["id"]
        else:
            st, out = http("POST", f"{SB}/auth/v1/admin/users", headers=admin_h,
                           body={"email": email, "password": pw, "email_confirm": True})
            check(f"create account {email}", st == 200 and "id" in out, f"HTTP {st}")
            uids[email] = out["id"]
    check("both test accounts exist", len(uids) == 2)

    # ---------- 2. sign in ----------
    tokens = {}
    for email, pw in (USER_A, USER_B):
        st, out = http("POST", f"{SB}/auth/v1/token?grant_type=password",
                       headers={"apikey": ANON, "Content-Type": "application/json"},
                       body={"email": email, "password": pw})
        check(f"sign in {email}", st == 200 and "access_token" in out, f"HTTP {st}")
        tokens[email] = out["access_token"]
    tA, tB = tokens[USER_A[0]], tokens[USER_B[0]]
    uidA = uids[USER_A[0]]

    # profile auto-created by trigger?
    st, out = http("GET", f"{SB}/rest/v1/profiles?select=display_name",
                   headers=user_headers(tA))
    check("profile auto-created on signup (trigger)", st == 200 and len(out) == 1,
          f"rows={len(out) if isinstance(out, list) else out}")

    # ---------- 3. create project ----------
    st, out = http("POST", f"{SB}/rest/v1/projects",
                   headers=user_headers(tA, {"Prefer": "return=representation"}),
                   body={"name": "E2E Pipeline Proof", "user_id": uidA})
    check("create project", st == 201, f"HTTP {st}: {out}")
    project = out[0]

    # ---------- 4. upload REAL footage ----------
    asset_id = str(uuid.uuid4())
    fname = os.path.basename(VIDEO)
    path = f"users/{uidA}/projects/{project['id']}/raw/{asset_id}/{fname}"
    raw = open(VIDEO, "rb").read()
    st, out = http("POST", f"{SB}/storage/v1/object/raw-footage/{path}",
                   headers={"apikey": ANON, "Authorization": f"Bearer {tA}",
                            "Content-Type": mimetypes.guess_type(fname)[0] or "video/mp4"},
                   raw=raw)
    check(f"upload real footage ({len(raw)} bytes)", st == 200, f"HTTP {st}: {out}")

    st, out = http("POST", f"{SB}/rest/v1/media_assets",
                   headers=user_headers(tA, {"Prefer": "return=representation"}),
                   body={"id": asset_id, "project_id": project["id"], "user_id": uidA,
                         "filename": fname, "storage_path": path,
                         "mime_type": "video/mp4", "size_bytes": len(raw),
                         "duration_seconds": 17.0})
    check("media_assets record created", st == 201, f"HTTP {st}: {out}")

    # ---------- 5. persistence: refetch like a fresh page load ----------
    st, out = http("GET",
                   f"{SB}/rest/v1/media_assets?project_id=eq.{project['id']}&select=*",
                   headers=user_headers(tA))
    check("asset visible after refetch (persistence)", st == 200 and len(out) == 1)

    # ---------- 6. save timeline ----------
    timeline_json = {
        "version": 1, "width": 1280, "height": 720, "fps": 30, "duration": 8,
        "tracks": [
            {"id": "video-1", "type": "video", "clips": [
                {"id": "clip-1", "assetId": asset_id, "sourceStart": 1.0,
                 "sourceEnd": 7.0, "timelineStart": 2, "timelineEnd": 8, "volume": 1}]},
            {"id": "text-1", "type": "text", "clips": [
                {"id": "title-1", "text": "E2E REAL PIPELINE", "timelineStart": 0,
                 "timelineEnd": 2, "fontSize": 72, "position": "center"}]},
        ],
    }
    st, out = http("POST", f"{SB}/rest/v1/timelines",
                   headers=user_headers(tA, {"Prefer": "return=representation"}),
                   body={"project_id": project["id"], "user_id": uidA,
                         "version": 1, "timeline_json": timeline_json})
    check("timeline saved to database", st == 201, f"HTTP {st}: {out}")
    timeline = out[0]

    # ---------- 7. queue + trigger render ----------
    st, out = http("POST", f"{SB}/rest/v1/render_jobs",
                   headers=user_headers(tA, {"Prefer": "return=representation"}),
                   body={"project_id": project["id"], "timeline_id": timeline["id"],
                         "user_id": uidA, "status": "queued"})
    check("render job queued", st == 201, f"HTTP {st}: {out}")
    job = out[0]

    st, out = http("POST", f"{RENDER_API}/render",
                   headers={"Authorization": f"Bearer {tA}",
                            "Content-Type": "application/json"},
                   body={"job_id": job["id"]})
    check("render accepted by backend", st == 200, f"HTTP {st}: {out}")

    # ---------- 8. poll real status ----------
    final = None
    for _ in range(60):
        st, out = http("GET", f"{SB}/rest/v1/render_jobs?id=eq.{job['id']}&select=*",
                       headers=user_headers(tA))
        j = out[0]
        if j["status"] in ("completed", "failed"):
            final = j
            break
        time.sleep(2)
    check("render completed", final is not None and final["status"] == "completed",
          f"status={final and final['status']}, err={final and final.get('error_message')}")
    check("output metadata recorded",
          bool(final["output_storage_path"]) and final["output_size_bytes"] > 0
          and final["output_width"] == 1280,
          f"{final['output_width']}x{final['output_height']}, "
          f"{final['output_size_bytes']}B, {final['output_duration_seconds']:.1f}s")

    # ---------- 9. signed download + verify the MP4 is real ----------
    st, out = http("POST", f"{SB}/storage/v1/object/sign/exports/{final['output_storage_path']}",
                   headers=user_headers(tA), body={"expiresIn": 600})
    check("signed URL created by owner", st == 200 and "signedURL" in out, f"HTTP {st}: {out}")
    st, blob = http("GET", f"{SB}/storage/v1{out['signedURL']}")
    check("download via signed URL", st == 200 and isinstance(blob, bytes) and len(blob) > 1000,
          f"{len(blob) if isinstance(blob, bytes) else blob} bytes")
    tmp_out = os.path.join(os.environ.get("TEMP", "/tmp"), "e2e_render_output.mp4")
    open(tmp_out, "wb").write(blob)
    probe = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_format", "-show_streams", tmp_out],
                           capture_output=True, timeout=60)
    meta = json.loads(probe.stdout)
    dur = float(meta["format"]["duration"])
    vstream = next(s for s in meta["streams"] if s["codec_type"] == "video")
    check("output is a valid H.264 MP4 (title 2s + trim 6s ≈ 8s)",
          probe.returncode == 0 and vstream["codec_name"] == "h264" and 7.5 <= dur <= 8.6,
          f"codec={vstream['codec_name']}, duration={dur:.2f}s, "
          f"{vstream['width']}x{vstream['height']}")
    check("output has AAC audio stream",
          any(s["codec_type"] == "audio" and s["codec_name"] == "aac" for s in meta["streams"]))

    # ---------- 10. AUTHORIZATION SUITE ----------
    print("\n--- authorization: user B + signed-out must be locked out ---")
    anon_h = {"apikey": ANON, "Authorization": f"Bearer {ANON}"}

    st, out = http("GET", f"{SB}/rest/v1/projects?select=*", headers=anon_h)
    check("signed-out cannot list projects", st in (200, 401) and (not isinstance(out, list) or len(out) == 0))

    st, out = http("GET", f"{SB}/rest/v1/projects?id=eq.{project['id']}&select=*",
                   headers=user_headers(tB))
    check("user B cannot read A's project", st == 200 and len(out) == 0)

    st, out = http("GET", f"{SB}/rest/v1/media_assets?id=eq.{asset_id}&select=*",
                   headers=user_headers(tB))
    check("user B cannot read A's media asset", st == 200 and len(out) == 0)

    st, out = http("GET", f"{SB}/rest/v1/timelines?id=eq.{timeline['id']}&select=*",
                   headers=user_headers(tB))
    check("user B cannot read A's timeline", st == 200 and len(out) == 0)

    st, out = http("POST", f"{RENDER_API}/render",
                   headers={"Authorization": f"Bearer {tB}", "Content-Type": "application/json"},
                   body={"job_id": job["id"]})
    check("user B cannot trigger a render of A's job", st in (403, 404), f"HTTP {st}")

    st, out = http("POST", f"{RENDER_API}/render",
                   headers={"Content-Type": "application/json"}, body={"job_id": job["id"]})
    check("signed-out cannot trigger a render", st == 401, f"HTTP {st}")

    st, out = http("POST", f"{SB}/storage/v1/object/sign/exports/{final['output_storage_path']}",
                   headers=user_headers(tB), body={"expiresIn": 600})
    check("user B cannot sign A's export", st in (400, 403, 404), f"HTTP {st}: {out}")

    st, out = http("GET", f"{SB}/storage/v1/object/raw-footage/{path}",
                   headers=user_headers(tB))
    check("user B cannot download A's raw footage", st in (400, 403, 404), f"HTTP {st}")

    st, out = http("GET", f"{SB}/storage/v1/object/exports/{final['output_storage_path']}",
                   headers={"apikey": ANON})
    check("no public/anon access to exports", st in (400, 401, 403, 404), f"HTTP {st}")

    # ---------- 11. cleanup ----------
    if not args.keep:
        # sweep everything under this user's prefix in both buckets (covers
        # leftovers from any earlier aborted runs), then delete accounts
        for bucket in ("raw-footage", "exports"):
            st, out = http("POST", f"{SB}/storage/v1/object/list/{bucket}",
                           headers={**admin_h},
                           body={"prefix": f"users/{uidA}", "limit": 1000})
            names = []
            def walk(prefix):
                st, entries = http("POST", f"{SB}/storage/v1/object/list/{bucket}",
                                   headers=admin_h, body={"prefix": prefix, "limit": 1000})
                for ent in entries or []:
                    full = f"{prefix}/{ent['name']}"
                    if ent.get("id"):
                        names.append(full)
                    else:
                        walk(full)
            walk(f"users/{uidA}")
            if names:
                http("DELETE", f"{SB}/storage/v1/object/{bucket}",
                     headers={**admin_h, "Content-Type": "application/json"},
                     body={"prefixes": names})
        for email in uids:  # cascades all DB rows via FK
            http("DELETE", f"{SB}/auth/v1/admin/users/{uids[email]}", headers=admin_h)
        os.remove(tmp_out)
        print("\ncleanup: storage objects and both test users removed (DB rows cascade)")
    else:
        # user B has no data; still remove the account? keep both for UI testing
        print(f"\n--keep: user A account, project and export left in place for UI review")
        print(f"  local copy of rendered output: {tmp_out}")

    summary()


if __name__ == "__main__":
    main()
