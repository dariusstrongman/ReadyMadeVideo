#!/usr/bin/env python3
"""Project One readiness check — one command, clear pass/fail report.

Verifies the WHOLE production path with throwaway fixtures (never customer
footage): env, tools, database, migrations, storage, providers, worker claim +
real render job, signed download, cross-user isolation, audit, telemetry,
cancellation, temp cleanup.

Env (required): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Optional but checked: GEMINI_API_KEY, OPENAI_API_KEY, SUPABASE_ANON_KEY
Run:  python scripts/project_one_readiness.py
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "render-backend"))

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append((name, True, detail or ""))
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    except Exception as e:
        RESULTS.append((name, False, str(e)))
        print(f"  [FAIL] {name} — {type(e).__name__}: {str(e)[:160]}")


def main():
    print("=== Project One readiness ===\n")

    # 1. env
    def env_check():
        missing = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
                   if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"missing {missing}")
        soft = [k for k in ("GEMINI_API_KEY", "OPENAI_API_KEY")
                if not os.environ.get(k)]
        return f"required ok; optional missing: {soft or 'none'}"
    check("required environment variables", env_check)
    if not (os.environ.get("SUPABASE_URL")
            and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")):
        report()
        return

    # 2. tools
    check("ffmpeg installed", lambda: subprocess.run(
        ["ffmpeg", "-version"], capture_output=True, timeout=20,
        check=True) and "ok")
    check("ffprobe installed", lambda: subprocess.run(
        ["ffprobe", "-version"], capture_output=True, timeout=20,
        check=True) and "ok")

    from app import jobs, supa
    from app.pipeline import telemetry

    # 3. supabase + migrations
    check("supabase connection", lambda: (
        supa.db_select("projects", "limit=1"), "ok")[1])

    def migrations_check():
        for table in ("projects", "media_assets", "timelines", "render_jobs",
                      "asset_analysis", "segments", "edit_runs",
                      "user_corrections", "operators", "operator_audit",
                      "pipeline_jobs", "draft_evaluations", "stage_metrics",
                      "project_status_events"):
            supa.db_select(table, "limit=1")
        # 0006 columns present?
        supa.db_select("pipeline_jobs", "limit=1", "id,cancel_requested_by")
        return "all 14 tables + cancellation columns present"
    check("required migrations applied", migrations_check)

    def buckets_check():
        import httpx
        for b in ("raw-footage", "exports"):
            r = httpx.get(f"{supa.SUPABASE_URL}/storage/v1/bucket/{b}",
                          headers={"apikey": supa.SERVICE_KEY,
                                   "Authorization": f"Bearer {supa.SERVICE_KEY}"},
                          timeout=15)
            r.raise_for_status()
            if r.json().get("public"):
                raise RuntimeError(f"bucket {b} is PUBLIC")
        return "raw-footage + exports private"
    check("private buckets exist", buckets_check)

    check("operator account exists", lambda: (
        (_ for _ in ()).throw(RuntimeError("no operators registered"))
        if not supa.db_select("operators", "limit=1")
        else f"{len(supa.db_select('operators', 'limit=10'))} operator(s)"))

    # 4. providers — REAL small operations, not model-list pings
    from app.pipeline import telemetry as _tel

    def whisper_check():
        """Real transcription of a throwaway synthesized-speech fixture."""
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY not set")
        from app.pipeline.schemas import TranscriptArtifact
        from app.pipeline.transcribe import OpenAIWhisperProvider
        wav = os.path.join(tempfile.gettempdir(),
                           f"readiness-speech-{uuid.uuid4().hex[:6]}.wav")
        try:
            ps = ("Add-Type -AssemblyName System.Speech; "
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.SetOutputToWaveFile('{wav}'); "
                  "$s.Speak('Stromation readiness check complete'); $s.Dispose()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           check=True, capture_output=True, timeout=60)
            art = OpenAIWhisperProvider().transcribe(wav)
            assert isinstance(art, TranscriptArtifact)      # schema-valid
            if "readiness" not in art.text.lower() \
                    and "stromation" not in art.text.lower():
                raise RuntimeError(f"unexpected transcript: {art.text!r}")
            if not art.words:
                raise RuntimeError("no word timestamps returned")
            dur_min = 3 / 60
            _tel.record("readiness_whisper", None, None, 1.0,
                        units={"whisper_minutes": dur_min})
            return (f"transcribed {len(art.words)} words with timestamps "
                    f"(~${_tel.estimate_cost({'whisper_minutes': dur_min}):.5f})")
        finally:
            if os.path.exists(wav):
                os.remove(wav)
    check("REAL Whisper transcription works", whisper_check)

    def gemini_check():
        """Real media upload + schema-validated semantic analysis of a
        throwaway fixture (never personal footage)."""
        if not os.environ.get("GEMINI_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY not set")
        from app.pipeline.schemas import (ScenesArtifact, SceneRange,
                                          SemanticArtifact)
        from app.pipeline.semantic import GeminiVideoProvider
        vid = os.path.join(tempfile.gettempdir(),
                           f"readiness-video-{uuid.uuid4().hex[:6]}.mp4")
        try:
            from app.renderer import _default_font, _ff_escape
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                            "-f", "lavfi", "-i",
                            "testsrc2=size=640x360:rate=30:duration=3",
                            "-vf", f"drawtext=fontfile='{_ff_escape(_default_font())}'"
                                   ":text='STROMATION READINESS'"
                                   ":fontcolor=white:fontsize=40"
                                   ":x=(w-text_w)/2:y=(h-text_h)/2",
                            "-c:v", "libx264", "-preset", "veryfast", vid],
                           check=True, capture_output=True, timeout=120)
            scenes = ScenesArtifact(detector="fixture", threshold=0,
                                    scenes=[SceneRange(index=0, start=0, end=3)])
            # provider deletes the uploaded Gemini file in its finally block
            art = GeminiVideoProvider().analyze(vid, scenes,
                                                context="readiness fixture")
            assert isinstance(art, SemanticArtifact)         # schema-valid
            seg = art.segments[0]
            if not seg.search_description or not seg.shot_type:
                raise RuntimeError("required schema fields empty")
            units = {"gemini_requests": 1, "gemini_video_seconds": 3}
            _tel.record("readiness_gemini", None, None, 1.0, units=units)
            return (f"analyzed fixture: '{seg.search_description[:50]}…' "
                    f"(~${_tel.estimate_cost(units):.5f})")
        finally:
            if os.path.exists(vid):
                os.remove(vid)
    check("REAL Gemini media analysis works", gemini_check)

    # 5. fixture flow: user + project + clip + timeline + worker final render
    import httpx
    admin = {"apikey": supa.SERVICE_KEY,
             "Authorization": f"Bearer {supa.SERVICE_KEY}",
             "Content-Type": "application/json"}
    sfx = uuid.uuid4().hex[:8]
    users = {}
    for tag in ("a", "b"):
        r = httpx.post(f"{supa.SUPABASE_URL}/auth/v1/admin/users",
                       headers=admin,
                       json={"email": f"readiness-{tag}-{sfx}@stromation.com",
                             "password": uuid.uuid4().hex + "Aa1!",
                             "email_confirm": True}, timeout=30)
        r.raise_for_status()
        users[tag] = r.json()["id"]
    tmp = tempfile.mkdtemp(prefix="readiness-")
    try:
        clip = os.path.join(tmp, "fixture.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                        "-f", "lavfi", "-i",
                        "testsrc2=size=640x360:rate=30:duration=4",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                        "-shortest", clip], check=True, timeout=120)

        r = httpx.post(f"{supa.SUPABASE_URL}/rest/v1/projects",
                       headers={**admin, "Prefer": "return=representation"},
                       json={"user_id": users["a"], "name": "Readiness",
                             "status": "ready"}, timeout=30)
        project = r.json()[0]
        asset_id = str(uuid.uuid4())
        path = (f"users/{users['a']}/projects/{project['id']}"
                f"/raw/{asset_id}/fixture.mp4")
        supa.storage_upload("raw-footage", path, clip)
        httpx.post(f"{supa.SUPABASE_URL}/rest/v1/media_assets",
                   headers={**admin, "Prefer": "return=minimal"},
                   json={"id": asset_id, "project_id": project["id"],
                         "user_id": users["a"], "filename": "fixture.mp4",
                         "storage_path": path, "size_bytes": os.path.getsize(clip),
                         "duration_seconds": 4.0}, timeout=30).raise_for_status()
        tl = {"version": 1, "width": 1280, "height": 720, "fps": 30,
              "duration": 3,
              "tracks": [{"id": "video-1", "type": "video", "clips": [
                  {"id": "c1", "assetId": asset_id, "sourceStart": 0.5,
                   "sourceEnd": 3.5, "timelineStart": 0, "timelineEnd": 3,
                   "volume": 1, "speed": 1}]}]}
        httpx.post(f"{supa.SUPABASE_URL}/rest/v1/timelines",
                   headers={**admin, "Prefer": "return=minimal"},
                   json={"project_id": project["id"], "user_id": users["a"],
                         "version": 1, "timeline_json": tl},
                   timeout=30).raise_for_status()

        def preview_check():
            from app.renderer2 import render_timeline
            out = os.path.join(tmp, "preview.mp4")
            local = os.path.join(tmp, "local.mp4")
            shutil.copy(clip, local)
            r = render_timeline(tl, {asset_id: local}, out, profile="preview")
            return f"{r['width']}x{r['height']} {r['duration']:.1f}s"
        check("preview rendering works", preview_check)

        def worker_check():
            j = jobs.enqueue_job(project["id"], users["a"], "final_render")
            claimed = jobs._claim_next()
            if not claimed or claimed["id"] != j["id"]:
                raise RuntimeError("worker could not claim the fixture job")
            jobs._run_job(claimed)
            row = supa.db_select("pipeline_jobs", f"id=eq.{j['id']}")[0]
            if row["status"] != "completed":
                raise RuntimeError(f"{row['status']}: {row['error_message']}")
            main.export_path = row["artifacts"]["output"]
            return (f"final render completed "
                    f"({row['artifacts']['width']}x{row['artifacts']['height']})")
        check("worker claims + completes a fixture job (final render)",
              worker_check)

        def signed_check():
            r = httpx.post(f"{supa.SUPABASE_URL}/storage/v1/object/sign/exports/"
                           f"{main.export_path}",
                           headers=admin, json={"expiresIn": 120}, timeout=30)
            r.raise_for_status()
            url = f"{supa.SUPABASE_URL}/storage/v1{r.json()['signedURL']}"
            with urllib.request.urlopen(url, timeout=60) as resp:
                blob = resp.read()
            if len(blob) < 1000:
                raise RuntimeError("download too small")
            return f"{len(blob)} bytes"
        check("signed private download works", signed_check)

        def cross_user_check():
            """GENUINE denial suite: User B authenticates with the normal
            publishable key + their own JWT and attempts 11 unauthorized
            operations against User A's data. Any success = readiness FAIL."""
            anon = os.environ.get(
                "SUPABASE_ANON_KEY",
                "sb_publishable_8qa-nssfdtEkCz-42wOSWQ_2P7S4Zj7")
            # set known passwords + sign both users in for real JWTs
            pws = {}
            for tag in ("a", "b"):
                pws[tag] = uuid.uuid4().hex + "Aa1!"
                httpx.put(f"{supa.SUPABASE_URL}/auth/v1/admin/users/{users[tag]}",
                          headers=admin, json={"password": pws[tag]},
                          timeout=30).raise_for_status()
            toks = {}
            for tag in ("a", "b"):
                r = httpx.post(
                    f"{supa.SUPABASE_URL}/auth/v1/token?grant_type=password",
                    headers={"apikey": anon, "Content-Type": "application/json"},
                    json={"email": f"readiness-{tag}-{sfx}@stromation.com",
                          "password": pws[tag]}, timeout=30)
                r.raise_for_status()
                toks[tag] = r.json()["access_token"]

            def bh(extra=None):
                return {"apikey": anon, "Authorization": f"Bearer {toks['b']}",
                        **(extra or {})}

            export_path = getattr(main, "export_path", None)
            job_rows = supa.db_select("pipeline_jobs",
                                      f"project_id=eq.{project['id']}&limit=1")
            denials = []

            def deny(name, resp, allow_empty_list=False):
                ok = resp.status_code >= 400 or (
                    allow_empty_list and resp.status_code == 200
                    and resp.json() in ([], {}))
                denials.append((name, ok, resp.status_code))
                if not ok:
                    raise RuntimeError(
                        f"UNAUTHORIZED OPERATION SUCCEEDED: {name} "
                        f"(HTTP {resp.status_code})")

            base = supa.SUPABASE_URL
            deny("read A's project", httpx.get(
                f"{base}/rest/v1/projects?id=eq.{project['id']}&select=*",
                headers=bh(), timeout=20), allow_empty_list=True)
            deny("read A's media record", httpx.get(
                f"{base}/rest/v1/media_assets?id=eq.{asset_id}&select=*",
                headers=bh(), timeout=20), allow_empty_list=True)
            deny("read A's timeline", httpx.get(
                f"{base}/rest/v1/timelines?project_id=eq.{project['id']}&select=*",
                headers=bh(), timeout=20), allow_empty_list=True)
            if job_rows:
                deny("read A's job", httpx.get(
                    f"{base}/rest/v1/pipeline_jobs?id=eq.{job_rows[0]['id']}"
                    f"&select=*", headers=bh(), timeout=20),
                    allow_empty_list=True)
            deny("list A's storage objects", httpx.post(
                f"{base}/storage/v1/object/list/raw-footage",
                headers=bh({"Content-Type": "application/json"}),
                json={"prefix": f"users/{users['a']}", "limit": 10},
                timeout=20), allow_empty_list=True)
            deny("sign A's original footage", httpx.post(
                f"{base}/storage/v1/object/sign/raw-footage/{path}",
                headers=bh({"Content-Type": "application/json"}),
                json={"expiresIn": 60}, timeout=20))
            deny("download A's original footage", httpx.get(
                f"{base}/storage/v1/object/raw-footage/{path}",
                headers=bh(), timeout=20))
            if export_path:
                deny("sign A's export", httpx.post(
                    f"{base}/storage/v1/object/sign/exports/{export_path}",
                    headers=bh({"Content-Type": "application/json"}),
                    json={"expiresIn": 60}, timeout=20))
                deny("download A's export", httpx.get(
                    f"{base}/storage/v1/object/exports/{export_path}",
                    headers=bh(), timeout=20))
            deny("insert media into A's project", httpx.post(
                f"{base}/rest/v1/media_assets",
                headers=bh({"Content-Type": "application/json",
                            "Prefer": "return=minimal"}),
                json={"project_id": project["id"], "user_id": users["b"],
                      "filename": "evil.mp4", "storage_path": "users/x/x.mp4"},
                timeout=20))
            deny("trigger processing for A's project", httpx.post(
                f"{base}/rest/v1/pipeline_jobs",
                headers=bh({"Content-Type": "application/json",
                            "Prefer": "return=minimal"}),
                json={"project_id": project["id"], "user_id": users["b"],
                      "kind": "analysis"}, timeout=20))
            return f"all {len(denials)} unauthorized operations denied"
        check("GENUINE User B denial suite (JWT + publishable key)",
              cross_user_check)

        def audit_check():
            r = httpx.post(f"{supa.SUPABASE_URL}/rest/v1/operator_audit",
                           headers={**admin, "Prefer": "return=representation"},
                           json={"operator_user_id": users["a"],
                                 "action": "readiness_check",
                                 "project_id": project["id"],
                                 "details": {"probe": True}}, timeout=30)
            if r.status_code != 201:
                raise RuntimeError(f"HTTP {r.status_code}")
            return "confirmed insert"
        check("audit logging works", audit_check)

        check("telemetry logging works", lambda: (
            (_ for _ in ()).throw(RuntimeError("record returned False"))
            if not telemetry.record("readiness_check", project["id"], None, 0.1)
            else "recorded"))

        def cancel_check():
            j = jobs.enqueue_job(project["id"], users["a"], "analysis")
            jobs.request_cancel(j, requested_by=users["a"])
            claimed = jobs._claim_next()
            if claimed:                       # cancelled while queued -> no claim
                jobs._run_job(claimed)
            row = supa.db_select("pipeline_jobs", f"id=eq.{j['id']}")[0]
            if row["status"] != "cancelled":
                raise RuntimeError(f"status={row['status']}")
            return "queued job cancelled cleanly"
        check("cancellation works", cancel_check)

        def temp_check():
            leaks = glob.glob(os.path.join(tempfile.gettempdir(),
                                           "stromation-job-*"))
            if leaks:
                raise RuntimeError(f"{len(leaks)} leaked temp dirs")
            return "no leaked job temp dirs"
        check("temporary files are cleaned", temp_check)

    finally:
        for uid in users.values():
            httpx.delete(f"{supa.SUPABASE_URL}/auth/v1/admin/users/{uid}",
                         headers=admin, timeout=30)
        shutil.rmtree(tmp, ignore_errors=True)

    report()


def report():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n=== READINESS: {passed}/{len(RESULTS)} checks passed ===")
    if passed == len(RESULTS):
        print("READY for Project One (with real footage + operator review).")
        sys.exit(0)
    print("NOT READY — failures above must be resolved first.")
    sys.exit(1)


if __name__ == "__main__":
    main()
