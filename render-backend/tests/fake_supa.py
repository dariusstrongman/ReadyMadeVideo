"""In-memory Supabase-semantics fake for CI-independent tests.

Implements the REST subset the backend uses (PostgREST filters, conditional
PATCH, the pipeline_jobs partial-unique index, storage up/download) so worker
and API tests exercise REAL state transitions without the production project.
Live-DB behavior is separately covered by scripts/test_db_integrity.py and the
operator-flow E2E.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

ACTIVE = ("queued", "processing", "cancel_requested")


def _now():
    return datetime.now(timezone.utc).isoformat()


class FakeResponse(SimpleNamespace):
    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


def resp(status, body=None, text=""):
    return FakeResponse(status_code=status, _json=body, text=text or str(body))


class FakeSupabase:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {
            t: [] for t in ("projects", "media_assets", "timelines",
                            "render_jobs", "pipeline_jobs", "segments",
                            "asset_analysis", "edit_runs", "draft_evaluations",
                            "user_corrections", "operators", "operator_audit",
                            "stage_metrics", "project_status_events", "profiles",
                            "preproduction_runs",
                            "human_edit_sessions", "human_edit_timing_events",
                            "timeline_scorecards", "picture_edit_runs",
                            "music_sound_runs", "licensed_music_assets",
                            "audio_mix_runs", "graphics_runs", "caption_runs",
                            "color_runs", "candidate_runs", "critic_runs",
                            "publishability_reports", "tournament_runs",
                            "editor_documents", "editor_operations",
                            "editor_render_requests", "editor_revision_proposals",
                            "editor_audit_events", "pending_storage_cleanup",
                            "editorial_plans", "raw_upload_sessions",
                            "output_recommendations", "output_packages",
                            "output_deliverables")}
        self.storage: dict[str, bytes] = {}          # "bucket/path" -> data
        self.fail_tables: set[str] = set()           # simulate write failures
        self.conflict_once_tables: set[str] = set()  # simulate one unique collision
        self.users: dict[str, dict] = {}             # token -> user

    # ---------- filter parsing ----------
    @staticmethod
    def _match(row, cond):
        k, _, expr = cond.partition("=")
        if expr.startswith("eq."):
            return str(row.get(k)) == expr[3:]
        if expr.startswith("in.("):
            return str(row.get(k)) in expr[4:-1].split(",")
        if expr.startswith("ilike."):
            return expr[6:].strip("*%").lower() in str(row.get(k, "")).lower()
        return True

    def query(self, table, filters: str):
        rows = list(self.tables[table])
        order, limit = None, None
        for cond in (filters or "").split("&"):
            if not cond or cond.startswith("select="):
                continue
            if cond.startswith("order="):
                order = cond[6:]
                continue
            if cond.startswith("limit="):
                limit = int(cond[6:])
                continue
            rows = [r for r in rows if self._match(r, cond)]
        if order:
            col, _, direction = order.partition(".")
            rows.sort(key=lambda r: str(r.get(col) or ""),
                      reverse=(direction == "desc"))
        if limit is not None:
            rows = rows[:limit]
        return copy.deepcopy(rows)

    # ---------- REST verbs ----------
    def select(self, table, filters, sel="*"):
        return self.query(table, filters)

    def insert(self, table, body):
        if table in self.fail_tables:
            return resp(500, {"message": "simulated failure"})
        if table in self.conflict_once_tables:
            self.conflict_once_tables.discard(table)
            return resp(409, {"message": "simulated unique collision"})
        rows = body if isinstance(body, list) else [body]
        out = []
        for b in rows:
            b = dict(b)
            if table == "output_recommendations":
                # migration 0028: unique (project_id, catalog_hash, engine_version)
                dup = [r for r in self.tables[table]
                       if r["project_id"] == b["project_id"]
                       and r["catalog_hash"] == b["catalog_hash"]
                       and r["engine_version"] == b["engine_version"]]
                if dup:
                    return resp(409, {"message": "duplicate key value violates "
                                                 "unique constraint"})
            if table == "output_packages":
                # migration 0028: PARTIAL unique (project_id, request_key)
                # where status='active' — cancelled packages free their key
                dup = [r for r in self.tables[table]
                       if r["project_id"] == b["project_id"]
                       and r["request_key"] == b["request_key"]
                       and r.get("status", "active") == "active"]
                if dup:
                    return resp(409, {"message": "duplicate key value violates "
                                                 "unique constraint"})
            if table == "output_deliverables":
                # migration 0028: unique (package_id, position)
                dup = [r for r in self.tables[table]
                       if r["package_id"] == b["package_id"]
                       and r["position"] == b["position"]]
                if dup:
                    return resp(409, {"message": "duplicate key value violates "
                                                 "unique constraint"})
            if table == "pipeline_jobs":
                # Mirror migration 0022's pipeline_jobs_kind_check.
                if b.get("kind") not in ("analysis", "autoedit", "revision",
                                         "final_render", "editorial_plan"):
                    return resp(400, {"message": "new row for relation "
                                                 "\"pipeline_jobs\" violates check "
                                                 "constraint \"pipeline_jobs_kind_check\""})
                dup = [r for r in self.tables[table]
                       if r["project_id"] == b["project_id"]
                       and r["kind"] == b["kind"] and r["status"] in ACTIVE]
                if dup:
                    return resp(409, {"message": "duplicate key value violates "
                                                 "unique constraint"})
                b.setdefault("status", "queued")
                b.setdefault("progress", 0)
                b.setdefault("attempt_count", 0)
                b.setdefault("max_attempts", 3)
            if table == "timelines":
                b.setdefault("lineage", "legacy")
                b.setdefault("is_immutable", False)
            if table == "human_edit_sessions":
                duplicate = [r for r in self.tables[table]
                             if r["project_id"] == b["project_id"]
                             and r.get("status") == "active"]
                if duplicate and b.get("status", "active") == "active":
                    return resp(409, {"message": "duplicate active session"})
                b.setdefault("status", "active")
                b.setdefault("timing_state", "running")
                b.setdefault("server_measured_seconds", 0)
                b.setdefault("client_reported_seconds", None)
                b.setdefault("idle_gap_cap_seconds", 300)
                b.setdefault("human_correction_seconds", 0)
                b.setdefault("last_activity_at", _now())
            if table == "human_edit_timing_events":
                b.setdefault("occurred_at", _now())
            if table in ("preproduction_runs", "picture_edit_runs"):
                # Mirror the real (project_id, version) unique index AND the primary key:
                # the bridge writes deterministic ids, so a concurrent peer inserting the
                # same id must 409 (PK) and a version race must 409 (unique).
                if b.get("id") is not None and any(
                        r.get("id") == b["id"] for r in self.tables[table]):
                    return resp(409, {"message": f"duplicate {table} id"})
                duplicate = [r for r in self.tables[table]
                             if r["project_id"] == b["project_id"]
                             and r.get("version") == b.get("version")]
                if duplicate:
                    return resp(409, {"message": f"duplicate {table} version"})
            if table == "editorial_plans":
                duplicate = [r for r in self.tables[table]
                             if r["project_id"] == b["project_id"]
                             and r.get("version") == b.get("version")]
                if duplicate:
                    return resp(409, {"message": "duplicate editorial plan version"})
            if table == "pending_storage_cleanup":
                duplicate = [r for r in self.tables[table]
                             if r.get("bucket") == b.get("bucket")
                             and r.get("object_path") == b.get("object_path")]
                if duplicate:
                    return resp(409, {"message": "duplicate storage-cleanup entry"})
            if table == "music_sound_runs":
                duplicate = [r for r in self.tables[table]
                             if r["project_id"] == b["project_id"]
                             and r.get("version") == b.get("version")]
                if duplicate:
                    return resp(409, {"message": "duplicate music-sound version"})
            if table == "licensed_music_assets":
                duplicate = [r for r in self.tables[table]
                             if r["music_sound_run_id"] == b["music_sound_run_id"]
                             and r.get("version") == b.get("version")]
                if duplicate:
                    return resp(409, {"message": "duplicate licensed-music version"})
            if table == "audio_mix_runs":
                duplicate = [r for r in self.tables[table]
                             if r["project_id"] == b["project_id"]
                             and r.get("version") == b.get("version")]
                if duplicate:
                    return resp(409, {"message": "duplicate audio-mix version"})
            if table in {"graphics_runs", "caption_runs", "color_runs"}:
                duplicate = [r for r in self.tables[table]
                             if r["project_id"] == b["project_id"]
                             and r.get("version") == b.get("version")]
                if duplicate:
                    return resp(409, {"message": f"duplicate {table} version"})
            if table == "candidate_runs":
                duplicate = [r for r in self.tables[table]
                             if r.get("batch_id") == b.get("batch_id")
                             and (r.get("candidate_key") == b.get("candidate_key")
                                  or r.get("candidate_index") == b.get("candidate_index"))]
                if duplicate:
                    return resp(409, {"message": "duplicate editorial candidate"})
                # Mirror migration 0023: at most one bridged candidate per
                # picture_edit_run (i.e. per source timeline).
                if b.get("generation_kind") == "bridged" and any(
                        r.get("generation_kind") == "bridged"
                        and r.get("picture_edit_run_id") == b.get("picture_edit_run_id")
                        for r in self.tables[table]):
                    return resp(409, {"message": "duplicate bridged candidate for "
                                                 "picture_edit_run"})
                # Mirror migration 0016 candidate_runs_bridged_ancestry_check.
                lineage = ("music_sound_run_id", "audio_mix_run_id",
                           "graphics_run_id", "caption_run_id", "color_run_id")
                kind = b.get("generation_kind")
                present = [k for k in lineage if b.get(k) is not None]
                if kind == "bridged" and present:
                    return resp(400, {"message": "bridged candidate must not carry "
                                                 "music/audio/graphics/caption/color lineage"})
                # Mirror enforce_editorial_candidate_refs exact bridged ancestry: the
                # picture_edit_run must descend from the candidate's preproduction_run.
                if kind == "bridged" and b.get("picture_edit_run_id") is not None:
                    pe = [r for r in self.tables["picture_edit_runs"]
                          if r.get("id") == b.get("picture_edit_run_id")]
                    if pe and pe[0].get("preproduction_run_id") != b.get("preproduction_run_id"):
                        return resp(400, {"message": "bridged candidate picture_edit_run "
                                                     "does not descend from its preproduction_run"})
                if kind in ("initial", "revised") and len(present) != len(lineage):
                    return resp(400, {"message": "initial/revised candidate requires "
                                                 "full music/audio/graphics/caption/color lineage"})
            if table == "critic_runs":
                duplicate = [r for r in self.tables[table]
                             if r.get("candidate_run_id") == b.get("candidate_run_id")
                             and r.get("critic_kind") == b.get("critic_kind")
                             and r.get("version", 1) == b.get("version", 1)]
                if duplicate:
                    return resp(409, {"message": "duplicate critic version"})
            if table == "publishability_reports":
                duplicate = [r for r in self.tables[table]
                             if r.get("candidate_run_id") == b.get("candidate_run_id")
                             and r.get("version", 1) == b.get("version", 1)]
                if duplicate:
                    return resp(409, {"message": "duplicate publishability version"})
            if table == "tournament_runs":
                duplicate = [r for r in self.tables[table]
                             if r.get("batch_id") == b.get("batch_id")
                             or (r.get("project_id") == b.get("project_id")
                                 and r.get("version") == b.get("version"))]
                if duplicate:
                    return resp(409, {"message": "duplicate tournament version"})
            if table == "editor_documents":
                duplicate = [r for r in self.tables[table]
                             if r.get("candidate_run_id") == b.get("candidate_run_id")
                             and r.get("version") == b.get("version")]
                if duplicate:
                    return resp(409, {"message": "duplicate editor version"})
            if table == "editor_operations":
                duplicate = [r for r in self.tables[table]
                             if r.get("operation_id") == b.get("operation_id")
                             or (r.get("result_document_id") == b.get("result_document_id")
                                 and r.get("operation_index") == b.get("operation_index"))]
                if duplicate:
                    return resp(409, {"message": "duplicate editor operation"})
            if table == "editor_render_requests":
                duplicate = [r for r in self.tables[table]
                             if r.get("pipeline_job_id") == b.get("pipeline_job_id")]
                if duplicate:
                    return resp(409, {"message": "duplicate editor render request"})
            if table == "user_corrections" and b.get("human_edit_session_id"):
                duplicate = [r for r in self.tables[table]
                             if r.get("human_edit_session_id") == b["human_edit_session_id"]
                             and r.get("operation_index") == b.get("operation_index")]
                if duplicate:
                    return resp(409, {"message": "duplicate operation index"})
            b.setdefault("id", str(uuid.uuid4()))
            b.setdefault("created_at", _now())
            self.tables[table].append(b)
            out.append(copy.deepcopy(b))
        return resp(201, out)

    def patch(self, table, filters, body):
        if table in self.fail_tables:
            return resp(500, {"message": "simulated failure"})
        matched = []
        for r in self.tables[table]:
            if all(self._match(r, c) for c in filters.split("&")
                   if c and not c.startswith(("select=", "order=", "limit="))):
                r.update(body)
                matched.append(copy.deepcopy(r))
        # status-event trigger emulation
        if table == "projects" and "status" in body:
            for m in matched:
                self.tables["project_status_events"].append({
                    "id": str(uuid.uuid4()), "project_id": m["id"],
                    "to_status": body["status"],
                    "reason": body.get("status_reason"), "created_at": _now()})
        return resp(200, matched)

    # ---------- httpx routing (module-level monkeypatch target) ----------
    def _route(self, method, url, **kw):
        if "/rest/v1/" in url:
            rest = url.split("/rest/v1/", 1)[1]
            table, _, filters = rest.partition("?")
            if method == "POST":
                return self.insert(table, kw.get("json"))
            if method == "PATCH":
                return self.patch(table, filters, kw.get("json"))
            if method == "GET":
                return resp(200, self.query(table, filters))
        if "/storage/v1/object/sign/" in url:
            key = url.split("/storage/v1/object/sign/", 1)[1]
            if key in self.storage:
                return resp(200, {"signedURL": f"/signed/{key}?token=fake"})
            return resp(400, {"statusCode": "404", "message": "Object not found"})
        if "/storage/v1/bucket/" in url:
            return resp(200, {"id": url.rsplit("/", 1)[1], "public": False})
        if "/storage/v1/object/" in url and method == "POST":
            key = url.split("/storage/v1/object/", 1)[1]
            self.storage[key] = kw.get("content", b"x")
            return resp(200, {"Key": key})
        return resp(404, {"message": f"unrouted {method} {url}"})

    def httpx_post(self, url, **kw):
        return self._route("POST", url, **kw)

    def httpx_patch(self, url, **kw):
        return self._route("PATCH", url, **kw)

    def httpx_get(self, url, **kw):
        return self._route("GET", url, **kw)

    # ---------- convenience ----------
    def add_user(self, email, operator=False):
        uid = str(uuid.uuid4())
        token = f"tok-{uid[:8]}"
        self.users[token] = {"id": uid, "email": email}
        self.tables["profiles"].append({"id": str(uuid.uuid4()), "user_id": uid,
                                        "display_name": email.split("@")[0],
                                        "created_at": _now()})
        if operator:
            self.tables["operators"].append({"user_id": uid, "created_at": _now()})
        return uid, token

    def add_project(self, user_id, name="Test", status="draft"):
        r = self.insert("projects", {"user_id": user_id, "name": name,
                                     "status": status})
        return r.json()[0]

    def verify_user(self, token):
        from app import supa
        if token in self.users:
            return self.users[token]
        raise supa.AuthError("invalid or expired token (fake)")


def install(monkeypatch, fake: FakeSupabase):
    """Wire the fake into supa + httpx module functions used by the backend."""
    import httpx

    from app import supa

    monkeypatch.setattr(supa, "verify_user", fake.verify_user)
    monkeypatch.setattr(supa, "db_select",
                        lambda t, f, s="*": fake.select(t, f, s))
    monkeypatch.setattr(supa, "db_update",
                        lambda t, f, p: fake.patch(t, f, p).raise_for_status())
    monkeypatch.setattr(supa, "storage_download",
                        lambda b, p, d: open(d, "wb").write(
                            fake.storage.get(f"{b}/{p}", b"")))
    monkeypatch.setattr(supa, "storage_upload",
                        lambda b, p, s, content_type="video/mp4":
                        fake.storage.__setitem__(f"{b}/{p}",
                                                 open(s, "rb").read()))
    monkeypatch.setattr(supa, "storage_remove",
                        lambda b, p: fake.storage.pop(f"{b}/{p}", None))

    def _remove_prefix(bucket, prefix):
        victims = [k for k in list(fake.storage) if k.startswith(f"{bucket}/{prefix}")]
        for k in victims:
            del fake.storage[k]
        return len(victims)
    monkeypatch.setattr(supa, "storage_remove_prefix", _remove_prefix)
    monkeypatch.setattr(httpx, "post", fake.httpx_post)
    monkeypatch.setattr(httpx, "patch", fake.httpx_patch)
    monkeypatch.setattr(httpx, "get", fake.httpx_get)
