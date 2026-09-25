#!/usr/bin/env bash
# Install the canonical router-outcome skill into whichever agent skill
# directories exist. Only ever touches a directory named router-outcome.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$root/skills/router-outcome"
[ -f "$source_dir/SKILL.md" ] || { echo "Cannot find $source_dir/SKILL.md"; exit 1; }

installed=0
for base in "$HOME/.claude/skills" "$HOME/.codex/skills" "$HOME/.pi/skills" "$HOME/.pi/agent/skills" "$@"; do
  parent="$(dirname "$base")"
  if [ ! -d "$base" ]; then
    # Create only when the agent itself is installed.
    [ -d "$parent" ] || { echo "  skip       $base (not present)"; continue; }
    mkdir -p "$base"
  fi
  rm -rf "$base/router-outcome"
  cp -r "$source_dir" "$base/router-outcome"
  echo "  installed  $base/router-outcome"
  installed=$((installed + 1))
done

if [ "$installed" -eq 0 ]; then
  cat <<'MSG'

Nothing was installed: no supported skill directory was found.

Skill locations differ between agent versions, so rather than guess, copy it
by hand or pass the path:

  ./scripts/install-skills.sh /path/to/skills

MSG
  exit 1
fi

echo
echo "After a routed task, ask the agent: \"generate the router result receipt\""
