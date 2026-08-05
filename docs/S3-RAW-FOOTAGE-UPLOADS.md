# S3 Multipart Raw-Footage Uploads

Replaces the Supabase direct-upload path for customer raw footage with **direct
browser → S3 multipart uploads via presigned URLs** (up to 2 GB). Supabase stays
the system of record for auth + metadata. The video body never transits Vercel or
Railway — only small JSON control messages do.

```
Browser ──JSON──▶ FastAPI (Railway): initiate / sign-parts / complete / finalize / abort
Browser ──bytes─▶ S3 directly (presigned PUT per part)         ← 2 GB body path
Worker  ◀─bytes─ S3 (owned source download during processing)
```

## Upload flow (per file)
1. `POST /projects/{pid}/raw-uploads/initiate` `{filename, contentType, size}` →
   validates JWT + ownership + extension/MIME + size ≤ 2 GB, mints `assetId`,
   builds the server-owned key, opens an S3 multipart upload, stores a
   `raw_upload_sessions` row → returns `{sessionId, objectKey, uploadId, partSize, partCount}`.
2. `POST …/{sessionId}/sign-parts` `{partNumbers:[…]}` → presigned `upload_part` URLs.
3. Browser PUTs each `file.slice(...)` part straight to S3 (XHR progress; retries;
   pause/cancel; resume from `localStorage`). No full-file buffering in memory.
4. `POST …/{sessionId}/complete` `{parts:[{partNumber, etag}]}` → S3 completes the
   multipart (missing/invalid parts → 409 + cleanup).
5. `POST …/{sessionId}/finalize` → HEAD the object, enforce size ≤ 2 GB, exact
   declared-size match, allowed content-type, and **FFprobe over a presigned GET
   URL** confirming a real video stream; only then insert the validated
   `media_asset` (provider/bucket/key/etag/size/content-type/duration) and flip the
   project to `ready`. Any failure aborts + deletes the object and records a reason.
6. `POST …/{sessionId}/abort` → abort the multipart + delete any object.

## Validation flow (server, requirement 7)
HEAD object → size ≤ 2 GB → size == declared → content-type allowed → FFprobe
video stream present → create `media_asset` (never before). Invalid objects are
deleted; `raw_upload_sessions.status` becomes `failed`/`aborted` with `error_reason`.

---

## AWS resources you must provision

### 1. Bucket
- **Name: `stromation-video-assets`**, region `us-east-1` (must match `AWS_REGION`).
- **Block Public Access: ON (all four)**. No public ACLs, no public policy.
- **Default encryption: SSE-S3 (AES256)** (code also sets it per object/upload).
- Keep the existing key layout: `users/{userId}/projects/{projectId}/raw-footage/{assetId}/{filename}`
  (exports, if routed to S3, live under `users/{userId}/projects/{projectId}/renders/…`;
  the operator readiness canary uses `users/_readiness/…`). All are under `users/*`.

### 2. IAM policy (least privilege — scope to the bucket + `users/*` prefix)
AWS has no separate `CreateMultipartUpload`/`CompleteMultipartUpload` actions —
those are authorized by `s3:PutObject`. `HeadObject` is authorized by `s3:GetObject`,
and `HeadBucket` by `s3:ListBucket`. The `ListBucket` statement must **not** carry a
`users/*` prefix condition, or the HeadBucket readiness probe (which sends no prefix)
fails.
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "StromationVideoAssetObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": "arn:aws:s3:::stromation-video-assets/users/*"
    },
    {
      "Sid": "StromationBucketReadiness",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::stromation-video-assets"
    },
    {
      "Sid": "StromationMultipartEnumeration",
      "Effect": "Allow",
      "Action": "s3:ListBucketMultipartUploads",
      "Resource": "arn:aws:s3:::stromation-video-assets",
      "Condition": { "StringLike": { "s3:prefix": ["users/*"] } }
    }
  ]
}
```
Attach to a dedicated IAM user (Railway env keys) or, preferred, an instance role.
`ListBucketMultipartUploads` is only needed for server-side reconciliation/cleanup
that enumerates incomplete uploads (not currently called) — remove that statement if
it stays unused. **Revoke `AmazonS3FullAccess`** once this policy is attached and
verified via the operator canary.

### 3. Bucket CORS (browser PUTs the parts; ETag must be exposed)
Production — only what browser part upload needs (no GET/HEAD from the browser):
```json
[
  {
    "AllowedOrigins": ["https://app.stromation.com"],
    "AllowedMethods": ["PUT"],
    "AllowedHeaders": ["content-type"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }
]
```
`ExposeHeaders: ["ETag"]` is **required** — the client reads each part's ETag from
the PUT response to complete the multipart. For local dev add a separate rule with
`"AllowedOrigins": ["http://localhost:5173"]`.

### 4. Lifecycle rule (clean up abandoned multiparts)
```json
{
  "Rules": [
    {
      "ID": "abort-incomplete-multipart-uploads",
      "Status": "Enabled",
      "Filter": { "Prefix": "users/" },
      "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 7 }
    }
  ]
}
```

### 5. Railway environment variables
| Variable | Purpose |
|---|---|
| `AWS_S3_BUCKET` | enables S3 uploads; **must be `stromation-video-assets`** |
| `AWS_REGION` | e.g. `us-east-1` |
| `RAW_UPLOAD_TTL_S` | optional; upload-session lifetime (default 24 h) |
| `MAX_CONCURRENT_PROBES` | optional; cap on concurrent ffprobe validations (default 2) |
| `PROBE_SIZE_BYTES` / `PROBE_ANALYZE_DURATION_US` / `PROBE_RW_TIMEOUT_US` | optional ffprobe read caps |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | least-privilege IAM creds (or use an instance role) |
| `MAX_UPLOAD_BYTES` | optional; defaults to `2147483648` (2 GiB) |
| `RAW_UPLOAD_PART_SIZE` | optional; defaults to `16777216` (16 MiB) |
| `S3_PART_URL_EXPIRE_S` / `S3_GET_URL_EXPIRE_S` | optional presign TTLs (default 3600) |
| `EXPORT_STORAGE_PROVIDER` | set to `s3` to also route completed exports to S3 (default `supabase`) |

### 6. Supabase migration
Apply `supabase/migrations/20260805_0024_s3_raw_uploads.sql` (media_assets
provenance columns + `raw_upload_sessions`, RLS locked to the service role).
It depends on `0019_soft_delete_child_rls.sql` (uses `public.project_not_deleted()`),
so apply it only after 0019 is in place.
**Not applied automatically** — run it against `iadzcnzgbtuigyodeqas` when ready.

---

## Verifying connectivity
- Shallow (public): `GET https://api.stromation.com/readyz/s3` →
  `{"enabled": true, "reachable": true}` only (no region, bucket, or error class).
- Deep (operator JWT): `GET …/readyz/s3/canary` exercises multipart create/abort +
  object put/get/delete under `users/_readiness/…` (cleaned up in finally) and
  returns per-permission booleans + `ok`. Use this to confirm the least-privilege
  IAM policy before revoking `AmazonS3FullAccess`.
- Local: not possible without local AWS creds + boto3; creds live only in Railway.

## State machine (raw_upload_sessions)
`initiated → completing → completed → finalizing → finalized`, with `aborted` /
`failed` as terminal off-ramps. Transitions are **atomic conditional updates**
(claim the exact prior status), so duplicate/concurrent complete/finalize/abort
calls are idempotent and a completed/finalized session can never be downgraded to
failed. `abort` refuses while a session is `finalizing`. Sessions carry `expires_at`
(default 24 h); sign/complete on an expired session is rejected and cleaned up.

## Security properties
- Browser never receives AWS credentials — only short-lived presigned URLs.
- Object keys are server-built from the verified user/project; clients cannot pick
  a bucket or path, and filenames are sanitized (no traversal).
- **`media_assets` writes are service-role only** (migration 0016 drops the
  authenticated INSERT/UPDATE policy, keeping owner-scoped SELECT). Users cannot
  forge/mutate `storage_provider/bucket/key/etag` — finalize is the sole creator.
- **Every worker download re-validates ownership** (`media_store.download_media_asset`):
  `asset.user_id == project.user_id`, `asset.project_id == project.id`, and for S3
  `bucket == AWS_S3_BUCKET` + `key_belongs_to(key, user, project)`. A forged key can
  never make the worker fetch another user's object.
- `raw_upload_sessions` is service-role only (RLS-enabled, no authenticated policy).
- Media validation rejects attached cover artwork, audio-only files, zero/invalid
  duration, and insane dimensions; ffprobe runs under a concurrency cap with read
  caps so a 2 GB container cannot pull unbounded bytes through Railway.

## Known limitations / follow-ups
- ffprobe validation streams over a presigned GET with `probesize`/`analyzeduration`/
  `rw_timeout` caps and a global concurrency semaphore; a fully bounded dedicated
  media-validation worker/queue is a future hardening step.
- Parts upload sequentially (simple, resumable); parallel part uploads are a future
  throughput optimization.
- Intermediate proxy/wav artifacts still write to Supabase; only the 2 GB source and
  exports use S3. Monitor AWS storage cost/lifecycle for 2 GB objects.
- Migration 0016 must be applied only to disposable PostgreSQL first (RLS/constraint
  regression), then production — **not** applied automatically by this branch.
