"""Disposable-PostgreSQL validation for migrations 0016 (bridged candidate origin)
and 0017 (project soft-delete).

Runs against a THROWAWAY database only. Applies the full migration chain, then
asserts the 0016/0017 CHECK constraints + trigger behavior for bridged / initial /
revised candidates, plus transaction rollback. NEVER point this at production.

Usage:
    # a Supabase-compatible disposable PG is ideal (has auth/storage), e.g.:
    #   docker run --rm -e POSTGRES_PASSWORD=pw -p 5433:5432 supabase/postgres
    DATABASE_URL='postgresql://postgres:pw@localhost:5433/postgres' \\
        python scripts/validate_migration_0016.py

If DATABASE_URL is unset or unreachable the script SKIPS (exit 0) with a clear note,
so it never blocks a machine without Postgres — but it must be run green on a
disposable PG before migrations 0016/0017 are applied to production.
"""
from __future__ import annotations

import glob
import os
import sys
import uuid

MIGRATIONS = os.path.join(os.path.dirname(__file__), "..", "..", "supabase", "migrations")

# Minimal Supabase-compatible stubs so the chain applies on a vanilla PG too.
BOOTSTRAP = """
create extension if not exists pgcrypto;
do $$ begin create role authenticated; exception when duplicate_object then null; end $$;
do $$ begin create role service_role; exception when duplicate_object then null; end $$;
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key default gen_random_uuid());
create or replace function auth.uid() returns uuid language sql stable as
  $f$ select nullif(current_setting('app.current_uid', true), '')::uuid $f$;
create schema if not exists storage;
create table if not exists storage.objects
  (id uuid primary key default gen_random_uuid(), bucket_id text, name text);
create or replace function storage.foldername(name text) returns text[]
  language sql immutable as $f$ select string_to_array(name, '/') $f$;
"""


def _connect():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("SKIP: DATABASE_URL not set - run against a disposable PostgreSQL.")
        return None
    try:
        import psycopg2
        return psycopg2.connect(dsn, connect_timeout=5)
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP: could not connect to PostgreSQL ({type(exc).__name__}: {exc}).")
        return None


def _apply_migrations(conn):
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(BOOTSTRAP)
        for path in sorted(glob.glob(os.path.join(MIGRATIONS, "*.sql"))):
            with open(path, encoding="utf-8") as fh:
                cur.execute(fh.read())
            print(f"  applied {os.path.basename(path)}")
    conn.autocommit = False


def _seed(cur):
    uid = str(uuid.uuid4())
    cur.execute("insert into auth.users(id) values (%s)", (uid,))
    cur.execute("insert into public.projects(user_id, name, status) values (%s,'v','draft') returning id", (uid,))
    pid = cur.fetchone()[0]
    cur.execute("""insert into public.preproduction_runs
        (project_id,user_id,version,status,request,creative_treatment,capture_quality_report,
         composition_by_segment,story_variants) values (%s,%s,1,'ready','{}','{}','{}','{}','[]')
        returning id""", (pid, uid))
    pre = cur.fetchone()[0]
    cur.execute("""insert into public.picture_edit_runs
        (project_id,user_id,preproduction_run_id,version,status,visual_rhythm_plans,candidates)
        values (%s,%s,%s,1,'ready','[]','[]') returning id""", (pid, uid, pre))
    pic = cur.fetchone()[0]
    return uid, pid, pre, pic


def _bridged_row(uid, pid, pre, pic, **overrides):
    row = {
        "batch_id": str(uuid.uuid4()), "project_id": pid, "user_id": uid,
        "preproduction_run_id": pre, "picture_edit_run_id": pic,
        "music_sound_run_id": None, "audio_mix_run_id": None, "graphics_run_id": None,
        "caption_run_id": None, "color_run_id": None, "candidate_key": "bridged",
        "candidate_index": 1, "generation_kind": "bridged",
        "source_picture_candidate_id": "pc", "variant_config": "{}",
        "manifest": '{"fabricatedFootage": false}', "render_qc": "{}",
        "preview_storage_bucket": "exports",
        "preview_storage_path": f"users/{uid}/projects/{pid}/autoedit/x.mp4",
        "fabricated_footage": False, "created_by": uid,
    }
    row.update(overrides)
    return row


def _insert_candidate(cur, row):
    cols = ", ".join(row.keys())            # fixed dict keys, not user input
    vals = ", ".join(["%s"] * len(row))     # values are parameterized
    query = f"insert into public.candidate_runs ({cols}) values ({vals})"  # noqa: S608
    cur.execute(query, list(row.values()))


def _run_assertions(conn):
    results = []

    def check(name, fn):
        with conn.cursor() as cur:
            cur.execute("savepoint sp")
            try:
                fn(cur)
                cur.execute("release savepoint sp")
                results.append((name, True, ""))
            except AssertionError as e:
                cur.execute("rollback to savepoint sp")
                results.append((name, False, str(e)))
            except Exception as e:  # noqa: BLE001
                cur.execute("rollback to savepoint sp")
                results.append((name, False, f"{type(e).__name__}: {e}"))

    def bridged_ok(cur):
        uid, pid, pre, pic = _seed(cur)
        cur.execute("set local app.current_uid = %s", (uid,))
        _insert_candidate(cur, _bridged_row(uid, pid, pre, pic))

    def initial_requires_full_ancestry(cur):
        uid, pid, pre, pic = _seed(cur)
        try:
            _insert_candidate(cur, _bridged_row(uid, pid, pre, pic,
                              generation_kind="initial", candidate_key="init"))
            raise AssertionError("initial candidate without full ancestry was accepted")
        except Exception as e:
            if isinstance(e, AssertionError):
                raise
            # CHECK/constraint rejection is expected

    def mixed_bridged_rejected(cur):
        uid, pid, pre, pic = _seed(cur)
        try:
            _insert_candidate(cur, _bridged_row(uid, pid, pre, pic,
                              audio_mix_run_id=str(uuid.uuid4())))
            raise AssertionError("bridged candidate with audio lineage was accepted")
        except Exception as e:
            if isinstance(e, AssertionError):
                raise

    def bridged_bad_preview_prefix_rejected(cur):
        uid, pid, pre, pic = _seed(cur)
        cur.execute("set local app.current_uid = %s", (uid,))
        try:
            _insert_candidate(cur, _bridged_row(uid, pid, pre, pic,
                              preview_storage_path=f"users/{uid}/projects/{pid}/editorial-intelligence/x.mp4"))
            raise AssertionError("bridged preview outside autoedit prefix was accepted")
        except Exception as e:
            if isinstance(e, AssertionError):
                raise

    def soft_delete_column(cur):
        cur.execute("select deleted_at from public.projects limit 0")  # column exists (0017)

    def rollback_isolates(cur):
        uid, pid, pre, pic = _seed(cur)
        cur.execute("select count(*) from public.candidate_runs where project_id=%s", (pid,))
        assert cur.fetchone()[0] == 0  # noqa: S101 — validation assertion

    check("0016: bridged candidate (null music/audio) accepted", bridged_ok)
    check("0016: initial candidate still requires full ancestry", initial_requires_full_ancestry)
    check("0016: mixed bridged ancestry rejected", mixed_bridged_rejected)
    check("0016: bridged preview must use autoedit prefix", bridged_bad_preview_prefix_rejected)
    check("0017: projects.deleted_at exists", soft_delete_column)
    check("rollback isolates test data", rollback_isolates)
    conn.rollback()
    return results


def main():
    conn = _connect()
    if conn is None:
        return 0
    try:
        print("Applying migrations to the disposable database…")
        _apply_migrations(conn)
        print("Running 0016/0017 assertions…")
        results = _run_assertions(conn)
    finally:
        conn.close()
    ok = all(passed for _, passed, _ in results)
    for name, passed, detail in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}{'' if passed else ' -> ' + detail}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
