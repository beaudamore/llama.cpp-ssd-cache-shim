#!/usr/bin/env bash
# Runs the shim in the foreground (macOS / Linux). Config comes from .env next to this script, or $SHIM_ENV.
# llama-server must be running with --slot-save-path <SLOT_SAVE_DIR>; the shim waits for it.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHIM_ENV="${SHIM_ENV:-$HERE/.env}"
if [[ -f "$SHIM_ENV" ]]; then
  dir="$(grep -E '^SLOT_SAVE_DIR=' "$SHIM_ENV" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')"
  [[ -n "$dir" ]] && mkdir -p "$dir"
fi
exec "${PYTHON:-python3}" "$HERE/ssd_cache_shim.py" --env "$SHIM_ENV"
