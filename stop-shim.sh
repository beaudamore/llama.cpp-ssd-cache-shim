#!/usr/bin/env bash
# Saves every dirty idle slot to SSD, then stops the shim (macOS / Linux). Run before stopping llama-server.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHIM_ENV="${SHIM_ENV:-$HERE/.env}"
"${PYTHON:-python3}" "$HERE/ssd_cache_shim.py" --env "$SHIM_ENV" --flush || true
port="$(grep -E '^LISTEN_PORT=' "$SHIM_ENV" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' )"
port="${port:-8090}"
if command -v lsof >/dev/null; then
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
else
  pids="$(ss -ltnp 2>/dev/null | awk -v p=":$port" '$4 ~ p {print $NF}' | grep -o 'pid=[0-9]*' | cut -d= -f2 || true)"
fi
if [[ -n "$pids" ]]; then kill $pids; echo "shim stopped (pid $pids)"; else echo "shim not running on :$port"; fi
