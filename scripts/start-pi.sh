#!/usr/bin/env bash
# Start the Pi bridge on the host. Loopback only: anything that can reach this
# port can spend your subscription quota.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/pi-bridge"

# "node_modules exists" is not "node_modules is current": re-run npm ci whenever
# package-lock.json changed since the last install (see scripts/bridge-deps.ps1).
stamp=node_modules/.northstar-lock.sha256
want="$( (sha256sum package-lock.json 2>/dev/null || shasum -a 256 package-lock.json) | cut -d' ' -f1)"
if [ "$(cat "$stamp" 2>/dev/null)" != "$want" ]; then
  echo "pi-bridge dependencies are missing or out of date; running npm ci"
  npm ci --no-audit --no-fund
  printf '%s' "$want" > "$stamp"
fi

export PI_BRIDGE_HOST="${PI_BRIDGE_HOST:-127.0.0.1}"
export PI_BRIDGE_PORT="${PI_BRIDGE_PORT:-31415}"

# PI_PROVIDER / PI_MODEL / PI_THINKING_LEVEL are deliberately NOT defaulted
# here: the bridge reads them from .env at the repository root, which is the
# single place the analyzer is configured. Export one inline to override it
# for a single run.

echo "Starting Pi bridge on http://$PI_BRIDGE_HOST:$PI_BRIDGE_PORT"
echo "  analyzer runs with NO TOOLS: no filesystem, no shell, no repository access."
echo "  The startup banner reports which model it will classify with."
echo
exec node src/server.ts
