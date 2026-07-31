# Project One — fitness footage capture guide

Shoot list for a 60–90 s edit from 20–60 clips (30–90 min raw max). Phone,
camera, action-cam and drone all fine. The coverage validator
(`GET /projects/{id}/coverage`, rules-based over the segment catalog) checks the
same categories after upload and reports what is likely missing — it never
invents footage.

## Required coverage
| # | Category | What to shoot | Aim for |
|---|---|---|---|
| 1 | Establishing location | wide, calm shot of where this happens (track, gym, trail) | 2–3 clips, 10–20 s |
| 2 | Preparation | gearing up, lacing shoes, warm-up | 2–3 clips |
| 3 | Details | close-ups: shoes, hands, equipment, chalk, watch | 3–5 clips, hold steady 5 s+ |
| 4 | Wide action | full body + environment during the work | 3–5 clips |
| 5 | Medium action | waist-up effort shots | 3–5 clips |
| 6 | Close-up effort | face, breathing, sweat, grip | 2–4 clips |
| 7 | Tracking / follow | move WITH the athlete (walk, gimbal, car window) | 2–3 clips |
| 8 | Peak effort | the hardest moment — sprint finish, last rep, summit | 2–3 clips, do not cut early |
| 9 | Completion | stopping, hands on knees, watch stop, rack the bar | 2 clips |
| 10 | Natural audio | 10–20 s where breathing / footsteps / ambience are CLEAN (no talking, no wind blast) | 1–2 clips |
| 11 | Reflection / payoff | calm after: looking back at the route, sunset, walk-off | 2 clips |
| 12 | Drone (optional) | high wide orbit or push-in | 1–2 clips |
| 13 | Narration (optional) | one or two spoken lines about why this session matters | 30 s max |

## Technical rules
- Horizontal or vertical is fine — tell us the target platform; don't mix aims mid-shoot.
- Hold every shot at least 6 seconds; the editor trims, it cannot extend.
- Small no-gos the analyzer flags automatically: lens pointed at the sun (blown
  exposure), pocket footage (black), tripod left running (frozen), heavy shake.
- Do not delete "boring" clips — establishing and reflection footage is exactly
  what auto-edits usually lack.

## What happens next
Upload → analysis (proxies, scenes, quality, transcript, semantics, motion) →
coverage report → operator-supervised draft → your review → final render. The
system reports missing categories honestly instead of pretending.
