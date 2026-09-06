#!/usr/bin/env bash
# Interpreter shim for the yantrikdb-hooks plugin.
#
# Claude Code runs hooks with a minimal environment, so "python3" is often not
# the interpreter that has yantrikdb installed. Resolution order:
#
#   1. $YANTRIKDB_PYTHON                      (explicit override)
#   2. the cached winner of a previous lookup
#   3. ~/.yantrikdb/venv/bin/python           (the documented install layout)
#   4. the shebang of the `yantrikdb-mcp` console script on PATH
#   5. python3 / python
#
# Steps 1 and 2 are trusted if the path is executable: re-probing the import
# on every prompt cost more than the hook itself. If the package has since
# been uninstalled the Python side notices, evicts the cache, and the next
# run resolves again. Every failure path exits 0 with no stdout: a missing
# interpreter must be a no-op, never a broken session.

set -uo pipefail

script="${1:-}"
[ -n "$script" ] || exit 0
shift || true

cache_dir="${CLAUDE_PLUGIN_DATA:-$HOME/.yantrikdb/claude-hooks}"
cache="$cache_dir/interpreter"

ok() { [ -n "${1:-}" ] && [ -x "$1" ] && "$1" -c 'import yantrikdb_mcp' >/dev/null 2>&1; }

py=""

if [ -n "${YANTRIKDB_PYTHON:-}" ] && [ -x "$YANTRIKDB_PYTHON" ]; then
  py="$YANTRIKDB_PYTHON"
elif [ -r "$cache" ]; then
  cached="$(cat "$cache" 2>/dev/null || true)"
  [ -n "$cached" ] && [ -x "$cached" ] && py="$cached"
fi

if [ -z "$py" ]; then
  for cand in "$HOME/.yantrikdb/venv/bin/python" "$HOME/.yantrikdb/venv/bin/python3"; do
    if ok "$cand"; then py="$cand"; break; fi
  done

  if [ -z "$py" ]; then
    mcp_bin="$(command -v yantrikdb-mcp 2>/dev/null || true)"
    if [ -n "$mcp_bin" ] && [ -r "$mcp_bin" ]; then
      shebang="$(head -c 256 "$mcp_bin" 2>/dev/null | head -n 1 | sed -n 's|^#!\([^ ]*\).*|\1|p')"
      ok "$shebang" && py="$shebang"
    fi
  fi

  if [ -z "$py" ]; then
    for cand in python3 python; do
      resolved="$(command -v "$cand" 2>/dev/null || true)"
      if ok "$resolved"; then py="$resolved"; break; fi
    done
  fi

  if [ -n "$py" ]; then
    mkdir -p "$cache_dir" 2>/dev/null || true
    printf '%s' "$py" > "$cache" 2>/dev/null || true
  fi
fi

[ -n "$py" ] || exit 0

exec "$py" "$(dirname "${BASH_SOURCE[0]}")/$script" "$@"
