"""Supabase helpers for the render backend.

- verify_user(): validates the caller's JWT against GoTrue (/auth/v1/user).
- DB reads/writes use PostgREST with the SERVICE ROLE key (server-only env);
  every handler still checks row ownership explicitly against the verified
  user id — service role is an implementation detail, not an authz bypass.
- Storage download/upload with the service role (buckets are private).
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

_service_headers = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
}


class AuthError(Exception):
    pass


def verify_user(bearer_token: str) -> dict:
    """Return the GoTrue user object for a JWT, or raise AuthError."""
    if not bearer_token:
        raise AuthError("missing bearer token")
    r = httpx.get(f"{SUPABASE_URL}/auth/v1/user",
                  headers={"apikey": SERVICE_KEY,
                           "Authorization": f"Bearer {bearer_token}"},
                  timeout=15)
    if r.status_code != 200:
        raise AuthError(f"invalid or expired token ({r.status_code})")
    return r.json()


def db_select(table: str, filters: str, select: str = "*") -> list[dict]:
    r = httpx.get(f"{SUPABASE_URL}/rest/v1/{table}?select={select}&{filters}",
                  headers=_service_headers, timeout=30)
    r.raise_for_status()
    return r.json()


def db_update(table: str, filters: str, patch: dict) -> None:
    r = httpx.patch(f"{SUPABASE_URL}/rest/v1/{table}?{filters}",
                    headers={**_service_headers, "Content-Type": "application/json",
                             "Prefer": "return=minimal"},
                    json=patch, timeout=30)
    r.raise_for_status()


def storage_download(bucket: str, path: str, dest_file: str) -> None:
    with httpx.stream("GET", f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}",
                      headers=_service_headers, timeout=300) as r:
        r.raise_for_status()
        with open(dest_file, "wb") as f:
            for chunk in r.iter_bytes(1024 * 1024):
                f.write(chunk)


def storage_upload(bucket: str, path: str, src_file: str,
                   content_type: str = "video/mp4") -> None:
    with open(src_file, "rb") as f:
        r = httpx.post(f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}",
                       headers={**_service_headers, "Content-Type": content_type,
                                "x-upsert": "true"},
                       content=f.read(), timeout=300)
    r.raise_for_status()
