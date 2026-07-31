"""Resumable, idempotent pipeline runner.

Two modes:

LOCAL (no cloud — development/fixtures):
  python -m app.pipeline.runner --file path/to/video.mp4 --out workdir [--stages ...]
  Artifacts are written as JSON files into the workdir.

CLOUD (against Supabase, per uploaded asset):
  python -m app.pipeline.runner --asset <media_asset_id> [--stages ...] [--force]
  Downloads the original, runs stages, stores every artifact row in
  asset_analysis (+ proxy/thumbs/wav in private storage) and canonical segments
  in the segments table. A completed (asset, kind) artifact is skipped unless
  --force — that is the resumability contract.

Stage order: probe -> proxy -> scenes -> mechanical -> audio -> transcript
             -> semantic -> motion -> catalog
Stages degrade gracefully: transcript/semantic are recorded as 'failed' with an
error message when their provider is unconfigured or errors; catalog still
builds (degraded) from whatever artifacts exist.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

from .catalog import build_catalog
from .mechanical import audio_stage, mechanical_stage
from .media import probe_stage, proxy_stage
from .motion import motion_stage
from .scenes import scenes_stage
from .schemas import (
    AudioArtifact,
    MechanicalArtifact,
    MotionArtifact,
    ProbeArtifact,
    ScenesArtifact,
    SemanticArtifact,
    TranscriptArtifact,
)
from .semantic import get_video_understanding_provider
from .transcribe import get_transcription_provider

ALL_STAGES = ["probe", "proxy", "scenes", "mechanical", "audio",
              "transcript", "semantic", "motion", "catalog"]


# ---------------- artifact stores ----------------
class LocalStore:
    """Writes artifacts as JSON files (dev/fixture mode)."""

    def __init__(self, out_dir: str):
        self.out = out_dir
        os.makedirs(out_dir, exist_ok=True)

    def load(self, kind: str):
        p = os.path.join(self.out, f"{kind}.json")
        if os.path.exists(p):
            return json.load(open(p, encoding="utf-8"))
        return None

    def save(self, kind: str, data, status="completed", error=None, paths=None):
        rec = {"kind": kind, "status": status, "error_message": error,
               "data": data, "storage_paths": paths}
        with open(os.path.join(self.out, f"{kind}.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2)

    def save_segments(self, asset_id: str, segments):
        with open(os.path.join(self.out, "segments.json"), "w", encoding="utf-8") as f:
            json.dump([s.model_dump() for s in segments], f, indent=2)


class CloudStore:
    """Persists artifacts to asset_analysis / segments + private storage."""

    def __init__(self, asset: dict):
        from .. import supa
        self.supa = supa
        self.asset = asset

    def load(self, kind: str):
        rows = self.supa.db_select(
            "asset_analysis",
            f"asset_id=eq.{self.asset['id']}&kind=eq.{kind}&status=eq.completed")
        return {"data": rows[0]["data"], "storage_paths": rows[0]["storage_paths"]} \
            if rows else None

    def save(self, kind: str, data, status="completed", error=None, paths=None):
        import httpx
        r = httpx.post(
            f"{self.supa.SUPABASE_URL}/rest/v1/asset_analysis",
            headers={"apikey": self.supa.SERVICE_KEY,
                     "Authorization": f"Bearer {self.supa.SERVICE_KEY}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"asset_id": self.asset["id"], "project_id": self.asset["project_id"],
                  "user_id": self.asset["user_id"], "kind": kind, "version": 1,
                  "status": status, "error_message": error, "data": data,
                  "storage_paths": paths},
            timeout=60)
        r.raise_for_status()

    def upload_file(self, local: str, remote_rel: str, ctype: str) -> str:
        a = self.asset
        path = (f"users/{a['user_id']}/projects/{a['project_id']}"
                f"/analysis/{a['id']}/{remote_rel}")
        self.supa.storage_upload("raw-footage", path, local, content_type=ctype)
        return path

    def save_segments(self, asset_id: str, segments):
        import httpx
        rows = [{"segment_key": s.segmentId, "asset_id": asset_id,
                 "project_id": self.asset["project_id"],
                 "user_id": self.asset["user_id"],
                 "source_start": s.sourceStart, "source_end": s.sourceEnd,
                 "data": s.model_dump(), "search_text": s.searchText,
                 "schema_version": s.schemaVersion} for s in segments]
        r = httpx.post(
            f"{self.supa.SUPABASE_URL}/rest/v1/segments",
            headers={"apikey": self.supa.SERVICE_KEY,
                     "Authorization": f"Bearer {self.supa.SERVICE_KEY}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=rows, timeout=60)
        r.raise_for_status()


# ---------------- runner ----------------
def run_pipeline(src: str, store, asset_id: str, stages=None, force=False,
                 workdir=None, upload_cb=None, context: str = "") -> dict:
    stages = stages or ALL_STAGES
    workdir = workdir or tempfile.mkdtemp(prefix="stromation-pipeline-")
    state: dict = {}
    def log(m):
        print(f"[pipeline] {m}", flush=True)

    def cached(kind):
        if force:
            return None
        return store.load(kind)

    # probe
    if "probe" in stages:
        c = cached("probe")
        if c:
            state["probe"] = ProbeArtifact(**c["data"])
            log("probe: cached")
        else:
            state["probe"] = probe_stage(src)
            store.save("probe", state["probe"].model_dump())
            log(f"probe: {state['probe'].duration:.1f}s "
                f"{state['probe'].video.width}x{state['probe'].video.height}")
    probe = state.get("probe") or ProbeArtifact(**store.load("probe")["data"])

    # proxy
    proxy_path = None
    if "proxy" in stages:
        c = cached("proxy")
        if c and c.get("storage_paths", {}).get("local_proxy") \
                and os.path.exists(c["storage_paths"]["local_proxy"]):
            proxy_path = c["storage_paths"]["local_proxy"]
            log("proxy: cached")
        else:
            files = proxy_stage(src, workdir, probe)
            proxy_path = files["proxy"]
            paths = {"local_proxy": files["proxy"], "local_wav": files["wav"],
                     "thumb_count": len(files["thumbs"])}
            if upload_cb:
                paths.update(upload_cb(files))
            store.save("proxy", {"thumb_count": len(files["thumbs"])}, paths=paths)
            log(f"proxy: built + {len(files['thumbs'])} thumbs")
            state["wav"] = files["wav"]
    proxy_path = proxy_path or src
    wav_path = state.get("wav") or os.path.join(workdir, "audio.wav")

    # scenes
    if "scenes" in stages:
        c = cached("scenes")
        if c:
            state["scenes"] = ScenesArtifact(**c["data"])
            log("scenes: cached")
        else:
            state["scenes"] = scenes_stage(proxy_path, probe.duration)
            store.save("scenes", state["scenes"].model_dump())
            log(f"scenes: {len(state['scenes'].scenes)} detected")
    if "scenes" not in state:
        cached_scenes = store.load("scenes")
        if not cached_scenes:
            raise RuntimeError("scenes artifact required - run the 'scenes' stage first")
        state["scenes"] = ScenesArtifact(**cached_scenes["data"])
    scenes = state["scenes"]

    # mechanical
    if "mechanical" in stages:
        c = cached("mechanical")
        if c:
            state["mechanical"] = MechanicalArtifact(**c["data"])
            log("mechanical: cached")
        else:
            state["mechanical"] = mechanical_stage(proxy_path, scenes)
            store.save("mechanical", state["mechanical"].model_dump())
            log("mechanical: analyzed")

    # audio
    if "audio" in stages:
        c = cached("audio")
        if c:
            state["audio"] = AudioArtifact(**c["data"])
            log("audio: cached")
        else:
            state["audio"] = audio_stage(src, probe.has_audio)
            store.save("audio", state["audio"].model_dump())
            log(f"audio: lufs={state['audio'].integrated_lufs}")

    # transcript
    if "transcript" in stages:
        c = cached("transcript")
        if c:
            state["transcript"] = TranscriptArtifact(**c["data"])
            log("transcript: cached")
        else:
            provider = get_transcription_provider()
            if provider is None or not probe.has_audio:
                store.save("transcript", None, status="failed",
                           error="no provider configured or no audio stream")
                log("transcript: skipped (no provider/audio)")
            else:
                try:
                    state["transcript"] = provider.transcribe(wav_path)
                    store.save("transcript", state["transcript"].model_dump())
                    log(f"transcript: {len(state['transcript'].words)} words")
                except Exception as e:
                    store.save("transcript", None, status="failed", error=str(e)[:500])
                    log(f"transcript: FAILED {e}")

    # semantic
    if "semantic" in stages:
        c = cached("semantic")
        if c:
            state["semantic"] = SemanticArtifact(**c["data"])
            log("semantic: cached")
        else:
            provider = get_video_understanding_provider()
            if provider is None:
                store.save("semantic", None, status="failed",
                           error="no VideoUnderstandingProvider configured")
                log("semantic: skipped (no provider)")
            else:
                try:
                    state["semantic"] = provider.analyze(proxy_path, scenes, context)
                    store.save("semantic", state["semantic"].model_dump())
                    log(f"semantic: {len(state['semantic'].segments)} scene records "
                        f"({provider.name})")
                except Exception as e:
                    store.save("semantic", None, status="failed", error=str(e)[:500])
                    log(f"semantic: FAILED {e}")

    # motion
    if "motion" in stages:
        c = cached("motion")
        if c:
            state["motion"] = MotionArtifact(**c["data"])
            log("motion: cached")
        else:
            state["motion"] = motion_stage(proxy_path, scenes)
            store.save("motion", state["motion"].model_dump())
            log("motion: analyzed")

    # catalog — pull any cached artifacts not computed this run
    if "catalog" in stages:
        def _cached_model(kind, model_cls):
            if state.get(kind) is not None:
                return state[kind]
            c = store.load(kind)
            return model_cls(**c["data"]) if c and c.get("data") else None

        sem = _cached_model("semantic", SemanticArtifact)
        segs = build_catalog(asset_id, scenes,
                             _cached_model("mechanical", MechanicalArtifact),
                             audio=_cached_model("audio", AudioArtifact),
                             transcript=_cached_model("transcript", TranscriptArtifact),
                             semantic=sem,
                             motion=_cached_model("motion", MotionArtifact))
        store.save("catalog", {"count": len(segs), "degraded": sem is None})
        store.save_segments(asset_id, segs)
        state["segments"] = segs
        log(f"catalog: {len(segs)} segments "
            f"({'DEGRADED - no semantic' if sem is None else 'full'})")

    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="local mode: source video file")
    ap.add_argument("--out", help="local mode: artifact output dir")
    ap.add_argument("--asset", help="cloud mode: media_asset id")
    ap.add_argument("--stages", nargs="*", default=None, choices=ALL_STAGES)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--context", default="", help="creative context hint for semantic stage")
    args = ap.parse_args()

    if args.file:
        out = args.out or (os.path.splitext(args.file)[0] + "_analysis")
        store = LocalStore(out)
        run_pipeline(args.file, store, asset_id=os.path.basename(args.file),
                     stages=args.stages, force=args.force, workdir=out,
                     context=args.context)
        print(f"artifacts -> {out}")
    elif args.asset:
        from .. import supa
        assets = supa.db_select("media_assets", f"id=eq.{args.asset}")
        if not assets:
            sys.exit("asset not found")
        asset = assets[0]
        store = CloudStore(asset)
        tmp = tempfile.mkdtemp(prefix="stromation-src-")
        try:
            src = os.path.join(tmp, os.path.basename(asset["storage_path"]))
            supa.storage_download("raw-footage", asset["storage_path"], src)

            def upload_cb(files):
                paths = {"proxy": store.upload_file(files["proxy"], "proxy.mp4", "video/mp4"),
                         "wav": store.upload_file(files["wav"], "audio.wav", "audio/wav")}
                if files["thumbs"]:
                    paths["thumb_0"] = store.upload_file(
                        files["thumbs"][0], "thumb_0.jpg", "image/jpeg")
                return paths

            run_pipeline(src, store, asset_id=asset["id"], stages=args.stages,
                         force=args.force, workdir=tmp, upload_cb=upload_cb,
                         context=args.context)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        ap.error("provide --file (local) or --asset (cloud)")


if __name__ == "__main__":
    main()
