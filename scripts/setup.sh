#!/usr/bin/env bash
# Unix equivalent of setup.ps1. The primary machine is Windows; this exists so
# the repository is not Windows-only.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "Adaptive LLM Router - setup"
echo "Repository: $root"

fail=0
need() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "   OK    $1 $("$1" --version 2>&1 | head -1)"
  else
    echo "   FAIL  $1 not found"
    fail=1
  fi
}

echo
echo "== Checking prerequisites"
need docker
need node

if command -v node >/dev/null 2>&1; then
  major="$(node --version | sed 's/^v//' | cut -d. -f1)"
  if [ "$major" -lt 22 ]; then
    echo "   FAIL  Node $major is too old; the bridge runs TypeScript directly and needs 22.6+"
    fail=1
  fi
fi

if ! command -v pi >/dev/null 2>&1; then
  echo "   WARN  the 'pi' CLI is not on PATH: npm install -g @mariozechner/pi-coding-agent"
fi

echo
echo "== Preparing directories"
mkdir -p "$root/data"
echo "   OK    data/"

if [ ! -f "$root/.env" ] && [ -f "$root/.env.example" ]; then
  cp "$root/.env.example" "$root/.env"
  echo "   OK    created .env from .env.example"
fi

if [ "$fail" -eq 0 ]; then
  echo
  echo "== Installing Pi bridge dependencies"
  (cd "$root/pi-bridge" && npm install --no-audit --no-fund)
fi

echo
echo "== Checking Pi authentication"
auth="$HOME/.pi/agent/auth.json"
if [ -f "$auth" ]; then
  # Provider NAMES only. Never print a credential value.
  echo "   OK    Pi holds credentials for: $(node -e "console.log(Object.keys(require('$auth')).join(', '))" 2>/dev/null || echo 'unknown')"
  echo "   WARN  credentials can still be expired; the bridge checks at startup and says so"
else
  echo "   WARN  Pi is not authenticated. Run 'pi' and use /login."
fi

[ "$fail" -ne 0 ] && { echo; echo "Fix the failures above first."; exit 1; }

cat <<'NEXT'

Next steps

  1. pi            then /login   (once)
  2. docker compose up -d
  3. ./scripts/start-pi.sh
  4. ./scripts/install-skills.sh
  5. open http://localhost:3000

NEXT
