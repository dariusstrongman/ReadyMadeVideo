# Dependency license audit — Stromation editing engine

Audited 2026-07-31 for use in a **closed, hosted commercial product**.

## In use now
| Dependency | License | Commercial hosted use | Notes |
|---|---|---|---|
| FFmpeg (gyan.dev full build, dev) | GPL | ✅ server-side | GPL matters on *distribution*; running server-side is fine. Docker image uses Debian's ffmpeg. Do not ship FFmpeg inside a distributed desktop app without review. |
| FastAPI / uvicorn / httpx / pydantic | MIT / BSD | ✅ | |
| React / Vite / react-router / supabase-js | MIT / Apache-2.0 | ✅ | |
| PySceneDetect | BSD-3 | ✅ | |
| OpenCV (opencv-python-headless) | Apache-2.0 | ✅ | |
| NumPy | BSD-3 | ✅ | |
| OpenAI Whisper API | API ToS | ✅ | usage-based; customer footage sent to OpenAI — disclosed in privacy policy |
| Gemini API | API ToS | ✅ | customer footage uploaded to Google Files API (48 h retention) — disclosed in privacy policy |

## Optional / planned providers
| Dependency | License | Status |
|---|---|---|
| faster-whisper (local STT) | MIT | OK when added (CTranslate2 MIT) |
| WhisperX | BSD-2 | OK when added |
| pyannote.audio | MIT (models gated on HF, some restricted) | audit specific model licenses before production use |
| DeepFilterNet | MIT/Apache dual | OK when added |
| librosa | ISC | OK when added |
| MediaPipe | Apache-2.0 | OK; blocked only by Python-version wheel availability |
| OpenTimelineIO | Apache-2.0 | **inspiration only** for our timeline format; safe to depend on directly too |
| Remotion | Source-available **commercial license** | ⚠️ requires a paid company license at our size — deferred until captions/motion-graphics justify it |

## Architecture references — DO NOT COPY CODE
| Project | License | Ruling |
|---|---|---|
| Crayotter | Noncommercial | **Architecture reference only. No code may be copied** into this commercial product. |
| OpenChatCut | AGPL-3.0 | **Not incorporated.** AGPL would obligate releasing our hosted service source. Do not vendor, import, or link without an explicit accepted decision. |

Nothing in the current codebase is derived from either project — all pipeline code
in `render-backend/app/pipeline/` was written from scratch against public API docs.
