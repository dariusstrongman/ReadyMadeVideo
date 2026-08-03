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
- Name (example): `stromation-raw-footage`, region `us-east-1` (match `AWS_REGION`).
- **Block Public Access: ON (all four)**. No public ACLs, no public policy.
- **Default encryption: SSE-S3 (AES256)** (code also sets it per object/upload).
- Keep the existing key layout: `users/{userId}/projects/{projectId}/raw-footage/{assetId}/{filename}`
  (exports, if routed to S3, live under `users/{userId}/projects/{projectId}/renders/…`).

### 2. IAM policy (least privilege — scope to the bucket + `users/*` prefix)
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "StromationRawFootageObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": "arn:aws:s3:::stromation-raw-footage/users/*"
    },
    {
      "Sid": "StromationBucketProbe",
      "Effect": "Allow",
      "Action": ["s3:ListBucketMultipartUploads", "s3:ListBucket"],
      "Resource": "arn:aws:s3:::stromation-raw-footage",
      "Condition": { "StringLike": { "s3:prefix": ["users/*"] } }
    }
  ]
}
```
Attach to a dedicated IAM user (Railway env keys) or, preferred, an instance role.
`s3:HeadBucket` is covered by `s3:ListBucket`; head-object is covered by `s3:GetObject`.

### 3. Bucket CORS (browser PUTs the parts; ETag must be exposed)
```json
[
  {
    "AllowedOrigins": ["https://app.stromation.com", "http://localhost:5173"],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }
]
```
`ExposeHeaders: ["ETag"]` is **required** — the client reads each part's ETag from
the PUT response to complete the multipart.

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
| `AWS_S3_BUCKET` | enables S3 uploads; the raw-footage bucket name |
| `AWS_REGION` | e.g. `us-east-1` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | least-privilege IAM creds (or use an instance role) |
| `MAX_UPLOAD_BYTES` | optional; defaults to `2147483648` (2 GiB) |
| `RAW_UPLOAD_PART_SIZE` | optional; defaults to `16777216` (16 MiB) |
| `S3_PART_URL_EXPIRE_S` / `S3_GET_URL_EXPIRE_S` | optional presign TTLs (default 3600) |
| `EXPORT_STORAGE_PROVIDER` | set to `s3` to also route completed exports to S3 (default `supabase`) |

### 6. Supabase migration
Apply `supabase/migrations/20260803_0016_s3_raw_uploads.sql` (media_assets
provenance columns + `raw_upload_sessions`, RLS locked to the service role).
**Not applied automatically** — run it against `iadzcnzgbtuigyodeqas` when ready.

---

## Verifying connectivity
- Deployed backend: `GET https://api.stromation.com/readyz/s3` →
  `{"enabled": true, "reachable": true, "region": "…"}` (no secrets returned).
  `{"reachable": false, "error": "…"}` indicates a creds/permission/region problem.
- Local: not possible without local AWS creds + boto3; creds live only in Railway.

## Security properties
- Browser never receives AWS credentials — only short-lived presigned URLs.
- Object keys are server-built from the verified user/project; clients cannot pick
  a bucket or path, and filenames are sanitized (no traversal).
- `raw_upload_sessions` is service-role only (RLS-enabled, no authenticated policy).
- Every endpoint verifies the Supabase JWT + project ownership; finalize is the
  sole creator of `media_assets`, and only after full validation.

## Known limitations / follow-ups
- Finalize validates via FFprobe over a presigned GET; a hostile file whose moov
  atom forces large seeks could make ffprobe read more than the header — bounded
  by the ffprobe timeout, not a byte cap.
- Parts upload sequentially (simple, resumable); parallel part uploads are a future
  throughput optimization.
- Free-tier Supabase storage quota no longer bounds footage size, but AWS storage
  cost/lifecycle for 2 GB objects should be monitored.
