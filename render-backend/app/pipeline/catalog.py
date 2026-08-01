"""Stage 7: merge mechanical + audio + transcript + semantic + motion artifacts
into canonical, searchable Segment records (one per detected scene)."""
from __future__ import annotations

from .schemas import (
    AudioArtifact,
    MechanicalArtifact,
    MotionArtifact,
    ScenesArtifact,
    Segment,
    SemanticArtifact,
    TranscriptArtifact,
)


def _transcript_in(tr: TranscriptArtifact | None, start: float, end: float) -> str | None:
    if not tr or not tr.sentences:
        return None
    hits = [s.text for s in tr.sentences if s.start < end and s.end > start]
    return " ".join(hits) or None


def _audio_score(audio: AudioArtifact | None, start: float, end: float) -> float:
    if not audio or not audio.has_audio:
        return 0.0
    score = 0.7
    if audio.clipped:
        score -= 0.3
    if audio.integrated_lufs is not None:
        # sweet spot around -14..-23 LUFS
        if audio.integrated_lufs < -35 or audio.integrated_lufs > -8:
            score -= 0.2
    sil = sum(max(0.0, min(end, r.end) - max(start, r.start))
              for r in audio.silence_ranges)
    dur = max(0.01, end - start)
    if sil / dur > 0.8:
        score -= 0.2
    return round(max(0.0, min(1.0, score)), 3)


def build_catalog(asset_id: str,
                  scenes: ScenesArtifact,
                  mech: MechanicalArtifact,
                  audio: AudioArtifact | None = None,
                  transcript: TranscriptArtifact | None = None,
                  semantic: SemanticArtifact | None = None,
                  motion: MotionArtifact | None = None) -> list[Segment]:
    mech_by = {m.index: m for m in mech.scenes}
    sem_by = {s.scene_index: s for s in (semantic.segments if semantic else [])}
    mot_by = {m.index: m for m in (motion.scenes if motion else [])}

    segments: list[Segment] = []
    for sc in scenes.scenes:
        m = mech_by.get(sc.index)
        sem = sem_by.get(sc.index)
        mot = mot_by.get(sc.index)

        problems: list[str] = list(sem.problems) if sem else []
        if m:
            if m.black_frame_fraction > 0.5:
                problems.append("mostly_black")
            if m.frozen_frame_fraction > 0.6:
                problems.append("mostly_frozen")
            if m.focus_score < 0.2:
                problems.append("soft_focus")
            if m.exposure_score < 0.3:
                problems.append("bad_exposure")

        # prefer the provider's tighter usable range when it is sane
        start, end = sc.start, sc.end
        if sem and sem.usable_start is not None and sem.usable_end is not None \
                and sc.start <= sem.usable_start < sem.usable_end <= sc.end:
            start, end = sem.usable_start, sem.usable_end

        motion_intensity = mot.motion_intensity if mot else \
            (min(1.0, m.motion_energy_mean / 18.0) if m else 0.0)

        search_bits = []
        if sem:
            search_bits.append(sem.search_description)
            search_bits.append(sem.action)
            search_bits += sem.subjects
            search_bits.append(sem.location)
        tr_text = _transcript_in(transcript, sc.start, sc.end)
        if tr_text:
            search_bits.append(tr_text)
        if not search_bits:
            search_bits.append(f"scene {sc.index} {sc.start:.1f}-{sc.end:.1f}s "
                               f"motion={motion_intensity:.2f}")

        segments.append(Segment(
            segmentId=f"seg_{__import__('re').sub(r'[^A-Za-z0-9]+', '', asset_id.rsplit('.', 1)[0])[-12:]}_{sc.index:03d}",
            assetId=asset_id,
            sourceStart=round(start, 3), sourceEnd=round(end, 3),
            subjects=sem.subjects if sem else [],
            action=sem.action if sem else "",
            shotType=sem.shot_type if sem else "",
            cameraMovement=sem.camera_movement if sem else "",
            location=sem.location if sem else "",
            transcript=tr_text,
            storyUses=list(sem.story_uses) if sem else [],
            emotion=sem.emotion if sem else "",
            motionIntensity=round(motion_intensity, 3),
            focusScore=m.focus_score if m else 0,
            exposureScore=m.exposure_score if m else 0,
            stabilityScore=m.shake_score if m else 0,
            audioScore=_audio_score(audio, sc.start, sc.end),
            semanticRelevance=round(
                (sem.emotional_intensity + sem.physical_intensity) / 2, 3) if sem else 0,
            duplicateGroupId=(f"dup_{m.duplicate_group}"
                              if m and m.duplicate_group is not None else None),
            problems=problems,
            searchText=" ".join(b for b in search_bits if b).strip()))
    return segments
