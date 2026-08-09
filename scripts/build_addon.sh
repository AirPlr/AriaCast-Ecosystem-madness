#!/usr/bin/env bash
# Stages the shared ariacast_core package into the add-on's build context and
# builds the Docker image. The Home Assistant Supervisor's add-on builder
# uses `addon/ariacast_core/` as its *only* build context, so the shared
# core package (single source of truth at `custom_components/ariacast/core`)
# has to be copied in first — it can't be reached with a `..` COPY path.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDON_DIR="$REPO_ROOT/addon/ariacast_core"
CORE_SRC="$REPO_ROOT/custom_components/ariacast/core"
CORE_DST="$ADDON_DIR/app/ariacast_core"

rm -rf "$CORE_DST"
mkdir -p "$CORE_DST"
cp -R "$CORE_SRC"/. "$CORE_DST"/

echo "Staged shared core package: $CORE_SRC -> $CORE_DST"

if [[ "${1:-}" == "--build" ]]; then
  docker build -t ariacast_core:local "$ADDON_DIR"
  echo "Built image ariacast_core:local"
fi
