"""Autoedit orchestrator — the first-milestone loop, end to end:

  segment catalog -> story blueprint -> candidate selection -> timeline v1
  -> preview render -> mechanical validator -> critic -> ONE revision pass
  (configurable max) -> timeline v2 -> preview v2 [-> final render]

Every intermediate artifact is written to the run directory (auditable).
Local mode CLI:

  python -m app.pipeline.autoedit --catalog dir1 [dir2 ...] \
      --sources asset1=path1.mp4 asset2=path2.mp4 \
      --brief "..." --duration 20 --out rundir [--no-critic] [--final]

Each --catalog dir is a LocalStore artifact dir produced by the pipeline runner
(its segments.json supplies the catalog).
"""
from __future__ import annotations

import argparse
import json
import os

from ..renderer import probe
from ..timeline_ops import apply_operations, parse_operations
from .builder import build_timeline, resolve_asset_ids
from .critic import get_critic
from .planner import plan_story
from .preferences import LocalCorrectionStore, weight_adjustments
from .schemas import Segment
from .selector import DEFAULT_WEIGHTS, select_segments
from .validator import validate_timeline

MAX_REVISION_PASSES = int(os.environ.get("AUTOEDIT_MAX_REVISIONS", "2"))


def _save(out_dir: str, name: str, data) -> None:
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# Output frame shapes. The third value is the planner's platform vocabulary
# ("vertical" | "horizontal"), which drives beat structure; the first two are
# the exact timeline dimensions the renderer sizes to. Square plans like a
# vertical piece but renders to a 1:1 frame.
ASPECTS = {
    "16:9": (1920, 1080, "horizontal"),
    "9:16": (1080, 1920, "vertical"),
    "1:1":  (1080, 1080, "vertical"),
}


def dims_for_aspect(aspect: str | None) -> tuple[int, int, str]:
    """(width, height, planner_platform) for an aspect ratio; 16:9 if unknown."""
    return ASPECTS.get(aspect or "16:9", ASPECTS["16:9"])


def autoedit(segments: list[Segment],
             sources: dict[str, str],
             brief: str,
             out_dir: str,
             target_duration: float | None = None,
             platform: str = "horizontal",
             aspect_ratio: str | None = None,
             title_text: str | None = None,
             template_id: str = "fitness_v1",
             use_critic: bool = True,
             render_final: bool = False,
             corrections_path: str | None = None,
             max_revisions: int = MAX_REVISION_PASSES,
             cancel_check=None) -> dict:
    from ..renderer2 import render_timeline
    os.makedirs(out_dir, exist_ok=True)
    report: dict = {"brief": brief, "steps": []}

    def _cancelled(stage):
        if cancel_check and cancel_check():
            report["status"] = "cancelled"
            report["cancelled_at_stage"] = stage
            _save(out_dir, "run.json", report)
            return True
        return False
    asset_durations = {aid: probe(p).duration for aid, p in sources.items()}

    # The project's chosen shape decides both how the story is planned and the
    # exact frame it is built for. Without this the pipeline always planned
    # horizontal and letterboxed vertical footage into a 1920x1080 frame.
    out_w, out_h, aspect_platform = dims_for_aspect(aspect_ratio)
    if aspect_ratio:
        platform = aspect_platform
    report["aspectRatio"] = aspect_ratio or "16:9"

    # 1. plan
    blueprint = plan_story(brief, segments, target_duration, platform, template_id)
    _save(out_dir, "blueprint.json", blueprint.model_dump())
    report["steps"].append({"step": "plan", "beats": len(blueprint.beats),
                            "target": blueprint.targetDuration})

    if _cancelled("after_plan"):
        return report
    # 2. select (with preference-adjusted weights)
    weights = dict(DEFAULT_WEIGHTS)
    if corrections_path:
        adj = weight_adjustments(LocalCorrectionStore(corrections_path).load())
        for k, v in adj.items():
            weights[k] = round(weights.get(k, 0) + v, 3)
        report["weightAdjustments"] = adj
    selection = select_segments(blueprint, segments, weights)
    _save(out_dir, "selection.json", selection.model_dump())
    filled = [b for b in selection.beats if b.chosen]
    report["steps"].append({"step": "select", "filled": len(filled),
                            "unfilled": [b.beatKey for b in selection.beats
                                         if b.unfilled]})
    if not filled:
        report["status"] = "failed"
        report["error"] = "no beats could be filled from the catalog"
        _save(out_dir, "run.json", report)
        return report

    # 3. build timeline v1 (no manual clip ids anywhere)
    timeline = resolve_asset_ids(
        build_timeline(blueprint, selection, title_text=title_text,
                       width=out_w, height=out_h), segments)
    _save(out_dir, "timeline_v1.json", timeline)

    if _cancelled("before_preview"):
        return report
    # 4. preview v1
    preview1 = os.path.join(out_dir, "preview_v1.mp4")
    r1 = render_timeline(timeline, sources, preview1, profile="preview")
    report["steps"].append({"step": "preview_v1", **{k: r1[k] for k in
                                                     ("duration", "width", "height",
                                                      "size_bytes")}})

    # 5. mechanical validation
    vreport = validate_timeline(timeline, segments,
                                target_duration=blueprint.targetDuration,
                                asset_durations=asset_durations,
                                preview_path=preview1)
    _save(out_dir, "validator_v1.json", vreport.model_dump())
    report["steps"].append({"step": "validate_v1", "ok": vreport.ok,
                            "issues": len(vreport.issues)})

    # 6. critic + revision passes
    final_timeline = timeline
    passes = 0
    if _cancelled("before_critic"):
        return report
    if use_critic:
        critic = get_critic()
        if critic is None:
            report["steps"].append({"step": "critic", "skipped": "no provider"})
        else:
            from .revision import plan_revision_ops
            current_preview = preview1
            while passes < max_revisions:
                if _cancelled(f"revision_pass_{passes + 1}"):
                    return report
                verdict = critic.critique(current_preview, blueprint, final_timeline)
                _save(out_dir, f"critic_pass{passes + 1}.json", verdict.model_dump())
                report["steps"].append({
                    "step": f"critic_pass{passes + 1}",
                    "score": verdict.overallScore,
                    "requests": len(verdict.revisionRequests)})
                if not verdict.revisionRequests:
                    break
                raw_ops = plan_revision_ops(verdict, final_timeline, selection,
                                            segments)
                if not raw_ops:
                    report["steps"].append({"step": "revision",
                                            "note": "no applicable ops"})
                    break
                ops = parse_operations(raw_ops)
                result = apply_operations(final_timeline, ops,
                                          actor="revision_agent")
                final_timeline = result.timeline
                passes += 1
                _save(out_dir, f"revision_ops_pass{passes}.json",
                      {"applied": result.applied, "rejected": result.rejected})
                _save(out_dir, f"timeline_v{passes + 1}.json", final_timeline)
                current_preview = os.path.join(out_dir, f"preview_v{passes + 1}.mp4")
                r2 = render_timeline(final_timeline, sources, current_preview,
                                     profile="preview")
                v2 = validate_timeline(final_timeline, segments,
                                       target_duration=blueprint.targetDuration,
                                       asset_durations=asset_durations,
                                       preview_path=current_preview)
                _save(out_dir, f"validator_v{passes + 1}.json", v2.model_dump())
                report["steps"].append({"step": f"preview_v{passes + 1}",
                                        "duration": r2["duration"],
                                        "validator_ok": v2.ok})

    if _cancelled("before_final"):
        return report
    # 7. optional final render
    if render_final:
        final_path = os.path.join(out_dir, "final.mp4")
        rf = render_timeline(final_timeline, sources, final_path, profile="final")
        report["steps"].append({"step": "final_render", **{k: rf[k] for k in
                                                           ("duration", "width",
                                                            "height", "size_bytes")}})

    report["status"] = "completed"
    report["revisionPasses"] = passes
    _save(out_dir, "run.json", report)
    return report


def _load_catalog(dirs: list[str]) -> list[Segment]:
    segs: list[Segment] = []
    for d in dirs:
        p = os.path.join(d, "segments.json")
        for raw in json.load(open(p, encoding="utf-8")):
            segs.append(Segment(**raw))
    return segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", nargs="+", required=True,
                    help="LocalStore artifact dirs containing segments.json")
    ap.add_argument("--sources", nargs="+", required=True,
                    help="assetId=path pairs")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--duration", type=float)
    ap.add_argument("--platform", default="horizontal",
                    choices=["horizontal", "vertical"])
    ap.add_argument("--title")
    ap.add_argument("--template", default="fitness_v1")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-critic", action="store_true")
    ap.add_argument("--final", action="store_true")
    args = ap.parse_args()

    sources = dict(pair.split("=", 1) for pair in args.sources)
    segments = _load_catalog(args.catalog)
    report = autoedit(segments, sources, args.brief, args.out,
                      target_duration=args.duration, platform=args.platform,
                      title_text=args.title, template_id=args.template,
                      use_critic=not args.no_critic, render_final=args.final)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
