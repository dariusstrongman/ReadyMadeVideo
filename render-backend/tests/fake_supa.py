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
                            "preproduction_runs")}
        self.storage: dict[str, bytes] = {}          # "bucket/path" -> data
        self.fail_tables: set[str] = set()           # simulate write failures
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
        rows = body if isinstance(body, list) else [body]
        out = []
        for b in rows:
            b = dict(b)
            if table == "pipeline_jobs":
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
    monkeypatch.setattr(httpx, "post", fake.httpx_post)
    monkeypatch.setattr(httpx, "patch", fake.httpx_patch)
    monkeypatch.setattr(httpx, "get", fake.httpx_get)
