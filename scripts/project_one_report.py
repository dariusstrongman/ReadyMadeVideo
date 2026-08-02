#!/usr/bin/env python3
"""Phase 9: evaluation outputs for Project One.

Builds into <root>/reports/:
  contact-sheet.html  — every source clip: thumbnail, semantics, scores,
                        usable ranges, problems, selected?, reason
  timeline-report.md  — final timeline: position, source file+range, beat,
                        score, reason, speed/audio adjustments
  comparison.html     — v1 draft vs critic-revised vs (later) human-approved

Usage: python scripts/project_one_report.py <project-one-local-root> <run-dir>
"""
import base64
import html
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def b64_thumb(analysis_dir):
    tdir = os.path.join(analysis_dir, "thumbs")
    if not os.path.isdir(tdir):
        return None
    thumbs = sorted(os.listdir(tdir))
    if not thumbs:
        return None
    mid = thumbs[len(thumbs) // 2]
    with open(os.path.join(tdir, mid), "rb") as f:
        return base64.b64encode(f.read()).decode()


def main():
    root, run_dir = sys.argv[1], sys.argv[2]
    reports = os.path.join(root, "reports")
    os.makedirs(reports, exist_ok=True)
    analysis_root = os.path.join(root, "analysis")

    selection = load(os.path.join(run_dir, "selection.json"))
    blueprint = load(os.path.join(run_dir, "blueprint.json"))
    chosen = {}          # segmentId -> (beat, reason, start, end)
    for b in selection["beats"]:
        if b.get("chosen"):
            chosen[b["chosen"]] = (b["beatKey"], b["reason"],
                                   b["sourceStart"], b["sourceEnd"])

    # ---------- contact sheet ----------
    cards = []
    for name in sorted(os.listdir(analysis_root)):
        adir = os.path.join(analysis_root, name)
        segf = os.path.join(adir, "segments.json")
        if not os.path.exists(segf):
            continue
        segs = load(segf)
        thumb = b64_thumb(adir)
        probe = load(os.path.join(adir, "probe.json"))["data"]
        seg_rows = []
        for s in segs:
            sel = chosen.get(s["segmentId"])
            seg_rows.append(f"""
        <tr class="{'sel' if sel else ''}">
          <td class="mono">{s['segmentId'].rsplit('_', 1)[-1]}</td>
          <td class="mono">{s['sourceStart']:.1f}–{s['sourceEnd']:.1f}s</td>
          <td>{html.escape(s.get('action', '') or '—')}</td>
          <td>{html.escape(s.get('shotType', '') or '—')}</td>
          <td class="mono">m {s['motionIntensity']:.2f} · f {s['focusScore']:.2f}
              · e {s['exposureScore']:.2f} · st {s['stabilityScore']:.2f}</td>
          <td>{html.escape(', '.join(s.get('problems', [])) or '—')}</td>
          <td>{('<b>' + html.escape(sel[0]) + '</b>') if sel else 'not selected'}</td>
        </tr>
        <tr class="desc"><td></td><td colspan="6">{html.escape(s.get('searchText', '')[:220])}</td></tr>""")
        cards.append(f"""
    <div class="clip">
      <div class="head">
        {'<img src="data:image/jpeg;base64,' + thumb + '">' if thumb else ''}
        <div>
          <h2>{html.escape(name)}.mp4</h2>
          <p class="mono">{probe['duration']:.1f}s ·
             {probe['video']['width']}x{probe['video']['height']}
             (rot {probe.get('rotation', 0)}) · {probe['video']['codec']} ·
             audio {probe['audio']['codec'] if probe.get('audio') else 'none'} ·
             {len(segs)} segments</p>
        </div>
      </div>
      <table>
        <tr><th>seg</th><th>range</th><th>action</th><th>shot</th>
            <th>scores</th><th>problems</th><th>selected</th></tr>
        {''.join(seg_rows)}
      </table>
    </div>""")

    style = """
    body{background:#0a0a0f;color:#e6e9ef;font:14px/1.5 'Segoe UI',system-ui,sans-serif;margin:0;padding:24px}
    h1{font-size:1.3rem}h2{font-size:1rem;margin:0}
    .mono{font-family:Consolas,monospace;font-size:.75rem;color:#8b93a3}
    .clip{background:#14141c;border:1px solid #262636;border-radius:12px;padding:18px;margin-bottom:18px}
    .head{display:flex;gap:16px;align-items:center;margin-bottom:10px}
    .head img{width:120px;border-radius:8px}
    table{width:100%;border-collapse:collapse;font-size:.8rem}
    th{color:#8b93a3;text-align:left;padding:4px 8px;border-bottom:1px solid #262636}
    td{padding:4px 8px;border-bottom:1px solid #1c1c28;vertical-align:top}
    tr.sel td{background:rgba(0,212,255,.07)}
    tr.desc td{color:#606078;font-size:.72rem;border-bottom:1px solid #262636}
    video{width:260px;border-radius:10px;background:#000}
    .cols{display:flex;gap:18px;flex-wrap:wrap}
    .col{flex:1;min-width:280px;background:#14141c;border:1px solid #262636;border-radius:12px;padding:14px}
    """
    with open(os.path.join(reports, "contact-sheet.html"), "w",
              encoding="utf-8") as f:
        f.write(f"<!doctype html><meta charset='utf-8'><title>Project One contact sheet"
                f"</title><style>{style}</style>"
                f"<h1>Project One — source footage contact sheet</h1>"
                f"<p class='mono'>brief: {html.escape(blueprint['brief'])} · "
                f"target {blueprint['targetDuration']}s · generated from the "
                f"segment catalog + selection report</p>" + "".join(cards))

    # ---------- timeline report ----------
    lines = ["# Project One — final timeline report\n"]
    tls = sorted(f for f in os.listdir(run_dir)
                 if f.startswith("timeline_v") and f.endswith(".json"))
    final_tl = load(os.path.join(run_dir, tls[-1]))
    lines.append(f"Timeline version: **{tls[-1]}** · duration "
                 f"**{final_tl['duration']}s** · "
                 f"{final_tl['width']}x{final_tl['height']}@{final_tl['fps']}\n")
    lines.append("| # | timeline | source file | source range | dur | beat | "
                 "reason (truncated) | speed | volume |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, c in enumerate([c for t in final_tl["tracks"]
                           if t["type"] == "video" for c in t["clips"]]):
        beat = c.get("meta", {}).get("beat", "?")
        reason = ""
        for b in selection["beats"]:
            if b["beatKey"] == beat and b.get("reason"):
                reason = b["reason"][:80]
        lines.append(
            f"| {i + 1} | {c['timelineStart']:.1f}–{c['timelineEnd']:.1f}s "
            f"| {c['assetId']} | {c['sourceStart']:.1f}–{c['sourceEnd']:.1f}s "
            f"| {c['sourceEnd'] - c['sourceStart']:.1f}s | {beat} "
            f"| {reason} | x{c.get('speed', 1)} | {c.get('volume', 1)} |")
    with open(os.path.join(reports, "timeline-report.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ---------- comparison page ----------
    def vid_col(title, fname):
        p = os.path.join(run_dir, fname)
        if not os.path.exists(p):
            return f"<div class='col'><h2>{title}</h2><p>not available</p></div>"
        rel = os.path.relpath(p, reports).replace("\\", "/")
        return (f"<div class='col'><h2>{title}</h2>"
                f"<video src='{rel}' controls></video>"
                f"<p class='mono'>{fname}</p></div>")

    with open(os.path.join(reports, "comparison.html"), "w",
              encoding="utf-8") as f:
        f.write(f"<!doctype html><meta charset='utf-8'><title>Draft comparison"
                f"</title><style>{style}</style>"
                f"<h1>Project One — draft comparison</h1><div class='cols'>"
                + vid_col("v1 — autonomous first draft", "preview_v1.mp4")
                + vid_col("v2 — critic-revised", "preview_v2.mp4")
                + vid_col("final render", "final.mp4")
                + "<div class='col'><h2>human-approved</h2><p>pending founder "
                  "review — human changes will be a NEW version, logged as "
                  "operations and counted as correction time</p></div>"
                + "</div>")
    print(f"reports -> {reports}: contact-sheet.html, timeline-report.md, "
          f"comparison.html")


if __name__ == "__main__":
    main()
