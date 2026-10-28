#!/bin/bash
# Blocks Claude from completing when code files were modified but not verified.
# Uses a timestamp marker so it only blocks ONCE per task — after Claude runs
# verification and tries to stop again, the marker lets it through.

INPUT=$(cat)
# Stop input has no stop reason; stop_hook_active is true when Claude is already
# continuing because of a Stop hook, so never block twice in a row.
if [ "$(echo "$INPUT" | jq -r '.stop_hook_active // false')" = "true" ]; then
  exit 0
fi

MARKER="/tmp/.claude-verify-gate"

# If marker exists and is less than 10 minutes old, we already reminded once — let it through
if [ -f "$MARKER" ]; then
  # GNU stat first: on Linux `stat -f` reports the filesystem, not the file.
  MTIME=$(stat -c %Y "$MARKER" 2>/dev/null || stat -f %m "$MARKER" 2>/dev/null || echo 0)
  AGE=$(( $(date +%s) - MTIME ))
  if [ "$AGE" -lt 600 ]; then
    exit 0
  else
    rm -f "$MARKER"
  fi
fi

# Collect modified AND new untracked code files
MODIFIED=""
CODE_PATTERN='\.(py|c|cpp|h|hpp|js|ts|rs|go)$'

check_repo() {
  local dir="$1"
  local changed=""
  # Tracked files with modifications (staged + unstaged)
  changed=$(cd "$dir" 2>/dev/null && git diff --name-only HEAD 2>/dev/null | grep -E "$CODE_PATTERN" || true)
  # New untracked code files
  local untracked
  untracked=$(cd "$dir" 2>/dev/null && git ls-files --others --exclude-standard 2>/dev/null | grep -E "$CODE_PATTERN" || true)
  if [ -n "$untracked" ]; then
    changed="${changed}${changed:+$'\n'}${untracked}"
  fi
  echo "$changed"
}

# Check current directory if it's a git repo
if git rev-parse --is-inside-work-tree &>/dev/null; then
  MODIFIED=$(check_repo ".")
fi

# Also check common subdirectory repos (like Controls/)
for subdir in Controls src; do
  if (cd "$subdir" 2>/dev/null && git rev-parse --is-inside-work-tree &>/dev/null); then
    SUB_MODIFIED=$(check_repo "$subdir")
    if [ -n "$SUB_MODIFIED" ]; then
      MODIFIED="${MODIFIED}${MODIFIED:+$'\n'}${SUB_MODIFIED}"
    fi
  fi
done

if [ -n "$MODIFIED" ]; then
  touch "$MARKER"
  FILES=$(echo "$MODIFIED" | tr '\n' ', ' | sed 's/,$//')
  # Stop hooks block through top-level decision/reason; Stop has no hookSpecificOutput.
  jq -n --arg files "$FILES" '{
      decision: "block",
      reason: ("VERIFICATION REQUIRED: Code files were modified. Before completing, you MUST spawn adversarial verification agents in parallel:\n1. Breaker (red team) — try to crash it, feed edge cases, show tracebacks\n2. Correctness Verifier — independently compute expected outputs, compare against actual\n3. Integration Auditor — run the full pipeline, check imports, check call sites\nAll agents must provide EVIDENCE (command output, not opinions). Modified files: " + $files)
  }'
  exit 0
fi

exit 0
