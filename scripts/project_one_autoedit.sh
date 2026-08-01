#!/usr/bin/env bash
# Phase 8: the autonomous real-footage draft. Sources = ORIGINALS (final render
# quality); analysis came from proxies. Brief follows the creative direction:
# serious cinematic workout video, action-first opening, minimal title,
# natural audio, build intensity, end on the clearest completion moment.
set -e
cd "$(dirname "$0")/../render-backend"
SRC="C:/Users/Darius/Desktop/wordout"
P1="../project-one-local"
TARGET="${1:-32}"

CATALOGS=""
SOURCES=""
for d in "$P1"/analysis/*/; do
  name=$(basename "$d")
  [ -f "$d/segments.json" ] || continue
  CATALOGS="$CATALOGS $d"
  SOURCES="$SOURCES $name.mp4=$SRC/$name.mp4"
done

python -m app.pipeline.autoedit \
  --catalog $CATALOGS \
  --sources $SOURCES \
  --brief "Serious cinematic workout video of a real training session. Open on the strongest action moment immediately. Build intensity through the session. Use natural workout audio. Restrained, no gimmicks. End on the clearest completion or payoff moment." \
  --duration "$TARGET" \
  --platform vertical \
  --final \
  --out "$P1/timelines/run2"
