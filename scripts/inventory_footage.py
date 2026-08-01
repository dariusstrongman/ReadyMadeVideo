#!/usr/bin/env python3
"""Phase 3: safe, read-only inventory of real footage. Never modifies originals.

Usage: python scripts/inventory_footage.py <source_dir> <report_dir>
Writes inventory.json + inventory.md into <report_dir> (gitignored).
"""
import hashlib
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SENSITIVE_KEYS = ("location", "iso6709", "gps", "author", "artist", "comment",
                  "device", "model", "make", "software", "android", "manufacturer")
UPLOAD_LIMIT = 50 * 1024 * 1024


def probe(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", path],
                       capture_output=True, timeout=120)
    if p.returncode != 0:
        return None, p.stderr.decode(errors="replace")[:200]
    return json.loads(p.stdout.decode()), None


def decode_check(path):
    """Decode a short slice to prove FFmpeg can actually read the stream."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-t", "2", "-i", path,
                        "-f", "null", "-"], capture_output=True, timeout=180)
    return p.returncode == 0, p.stderr.decode(errors="replace")[:200]


def head_hash(path, nbytes=4 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(nbytes))
    return h.hexdigest()[:16]


def main():
    src, report_dir = sys.argv[1], sys.argv[2]
    os.makedirs(report_dir, exist_ok=True)
    rows = []
    for name in sorted(os.listdir(src)):
        path = os.path.join(src, name)
        if not os.path.isfile(path):
            continue
        size = os.path.getsize(path)
        entry = {"filename": name, "extension": os.path.splitext(name)[1].lower(),
                 "size_bytes": size, "size_mb": round(size / 1048576, 1),
                 "head_sha256_16": head_hash(path),
                 "exceeds_upload_limit": size > UPLOAD_LIMIT}
        meta, err = probe(path)
        if meta is None:
            entry.update({"probe_error": err, "decodable": False,
                          "corrupt": True})
            rows.append(entry)
            continue
        fmt = meta.get("format", {})
        v = next((s for s in meta.get("streams", [])
                  if s.get("codec_type") == "video"), {})
        a = next((s for s in meta.get("streams", [])
                  if s.get("codec_type") == "audio"), None)
        try:
            n, d = v.get("avg_frame_rate", "0/1").split("/")
            fps = round(int(n) / int(d), 2) if int(d) else None
        except Exception:
            fps = None
        rotation = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rotation = int(sd["rotation"]) % 360
        w, h = v.get("width", 0), v.get("height", 0)
        eff_w, eff_h = (h, w) if rotation in (90, 270) else (w, h)
        tags = {**{k.lower(): str(vv) for k, vv in (fmt.get("tags") or {}).items()},
                **{k.lower(): str(vv) for k, vv in (v.get("tags") or {}).items()}}
        sensitive = {k: v_ for k, v_ in tags.items()
                     if any(s in k for s in SENSITIVE_KEYS)}
        ok, decode_err = decode_check(path)
        entry.update({
            "duration_s": round(float(fmt.get("duration") or 0), 2),
            "width": w, "height": h, "rotation": rotation,
            "effective": f"{eff_w}x{eff_h}",
            "orientation": "portrait" if eff_h > eff_w else "landscape",
            "fps": fps,
            "video_codec": v.get("codec_name"),
            "audio_codec": a.get("codec_name") if a else None,
            "has_audio": a is not None,
            "creation_time": tags.get("creation_time"),
            "gps_present": any("location" in k or "iso6709" in k or "gps" in k
                               for k in tags),
            "sensitive_metadata": sensitive,
            "decodable": ok, "decode_error": None if ok else decode_err,
            "corrupt": not ok,
        })
        rows.append(entry)

    dup_groups = {}
    for r in rows:
        dup_groups.setdefault((r["size_bytes"], r["head_sha256_16"]),
                              []).append(r["filename"])
    duplicates = [v for v in dup_groups.values() if len(v) > 1]

    summary = {
        "clip_count": len(rows),
        "total_duration_s": round(sum(r.get("duration_s", 0) for r in rows), 1),
        "total_size_mb": round(sum(r["size_bytes"] for r in rows) / 1048576, 1),
        "largest_file": max(rows, key=lambda r: r["size_bytes"])["filename"],
        "unsupported_files": [r["filename"] for r in rows
                              if r["extension"] not in (".mp4", ".mov", ".m4v")],
        "corrupt_files": [r["filename"] for r in rows if r.get("corrupt")],
        "gps_clips": [r["filename"] for r in rows if r.get("gps_present")],
        "over_upload_limit": [r["filename"] for r in rows
                              if r["exceeds_upload_limit"]],
        "exact_duplicates": duplicates,
    }
    out = {"source_dir": src, "summary": summary, "clips": rows}
    with open(os.path.join(report_dir, "inventory.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    lines = ["# Project One footage inventory\n",
             f"Source: `{src}` (read-only — originals untouched)\n"]
    lines.append(f"- clips: **{summary['clip_count']}** · total "
                 f"**{summary['total_duration_s']}s** · "
                 f"**{summary['total_size_mb']} MB**")
    lines.append(f"- largest: {summary['largest_file']}")
    lines.append(f"- over 50 MB upload limit: {len(summary['over_upload_limit'])}")
    lines.append(f"- GPS/location metadata: {summary['gps_clips'] or 'none'}")
    lines.append(f"- corrupt: {summary['corrupt_files'] or 'none'}")
    lines.append(f"- exact duplicates: {summary['exact_duplicates'] or 'none'}\n")
    lines.append("| file | MB | dur s | effective | fps | vcodec | audio | GPS | ok |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append(f"| {r['filename']} | {r['size_mb']} | "
                     f"{r.get('duration_s', '?')} | {r.get('effective', '?')} "
                     f"({r.get('orientation', '?')}, rot {r.get('rotation', '?')}) | "
                     f"{r.get('fps', '?')} | {r.get('video_codec', '?')} | "
                     f"{r.get('audio_codec') or 'none'} | "
                     f"{'YES' if r.get('gps_present') else 'no'} | "
                     f"{'ok' if r.get('decodable') else 'CORRUPT'} |")
    with open(os.path.join(report_dir, "inventory.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
