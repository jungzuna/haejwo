#!/usr/bin/env bash
# claude_consult.sh — hardened headless Claude runner (haejwo's reviewer slot
# on a CODEX host). Mirror of codex_consult.sh: on a Codex host the
# different-model independent reviewer is Claude (principle 9 symmetry).
#
# Same contract as codex_consult.sh: feed a self-contained brief via stdin or
# file, capture the final reply, NEVER trust the exit code alone — any of
# {rc!=0 | empty reply | (consult) worktree changed | (implement) zero
# changes} exits non-zero.
#
# Usage:
#   claude_consult.sh [--mode consult|implement] [--resume] [-o out.md] brief.md
#   echo "..." | claude_consult.sh --mode consult -
#
# Modes (safety gates — enforced via git snapshot, since `claude -p` runs
# with the invoking user's permissions and has no read-only sandbox):
#   consult   (default) read-only advice. FAILS if the worktree changed.
#   implement Claude edits files. FAILS on zero changes (ALLOW_EMPTY_DIFF=1
#             allows an intentional no-op).
#   --resume  continue the most recent Claude session in this directory
#             (message = stdin/brief; reply = stdout).
#
# Env:
#   CLAUDE_MODEL    force a model (passed as --model; optional).
#   CLAUDE_TIMEOUT  seconds; default 600. 0 = unlimited.
#   ALLOW_EMPTY_DIFF=1  allow an intentional no-op implement run.
set -uo pipefail

print_help() {
  cat <<'EOF'
claude_consult.sh — feed a self-contained brief to headless claude; capture reply/edits.

Usage:
  claude_consult.sh [--mode consult|implement] [--resume] [-o out.md] brief.md
  echo "..." | claude_consult.sh --mode consult -

Modes:
  consult   (default) read-only; FAILS if the run changed the worktree.
  implement Claude edits files; FAILS on zero changes (ALLOW_EMPTY_DIFF=1 allows no-op).
  --resume  continue the most recent session (multi-round memory).

Env: CLAUDE_MODEL, CLAUDE_TIMEOUT (default 600), ALLOW_EMPTY_DIFF=1.

Exit code: non-zero on ANY of {claude rc!=0, empty reply, consult-changed-files,
implement-changed-nothing}. A silent rc=0 failure is never reported as success.
EOF
}

# ---- argument parsing (mirrors codex_consult.sh) ----
MODE=""
OUT=""
RESUME=0
while [ $# -gt 0 ]; do
  case "$1" in
    --resume) RESUME=1; shift ;;
    --mode)   [ $# -ge 2 ] || { echo "--mode requires a value (consult|implement)" >&2; exit 2; }; MODE="$2"; shift 2 ;;
    --mode=*) MODE="${1#--mode=}"; shift ;;
    -o)       [ $# -ge 2 ] || { echo "-o requires a value (output file)" >&2; exit 2; }; OUT="$2"; shift 2 ;;
    -o*)      OUT="${1#-o}"; shift ;;
    -h|--help) print_help; exit 0 ;;
    --)       shift; break ;;
    -)        break ;;  # bare '-' = stdin brief — must match before '-*'
    -*)       echo "unknown option: $1" >&2; exit 2 ;;
    *)        break ;;
  esac
done

MODE="${MODE:-consult}"
case "$MODE" in consult|implement) ;; *) echo "invalid --mode: $MODE (consult|implement)" >&2; exit 2 ;; esac

BRIEF="${1:-}"
[ -z "$BRIEF" ] && { echo "brief file required. usage: claude_consult.sh [--mode ..] [-o out] brief.md|-" >&2; exit 2; }

TMPBRIEF=""
cleanup() { [ -n "$TMPBRIEF" ] && rm -f "$TMPBRIEF"; }
trap cleanup EXIT
if [ "$BRIEF" = "-" ]; then
  BRIEF="$(mktemp "${TMPDIR:-/tmp}/claude_brief.XXXXXX.md")"
  TMPBRIEF="$BRIEF"
  cat > "$BRIEF"
fi
[ -f "$BRIEF" ] || { echo "brief file not found: $BRIEF" >&2; exit 2; }

[ -z "$OUT" ] && OUT="${BRIEF%.md}.reply.md"
LOG="${OUT%.*}.log"

TIMEOUT="${CLAUDE_TIMEOUT:-600}"
MODEL_FLAG=(); [ -n "${CLAUDE_MODEL:-}" ] && MODEL_FLAG=(--model "$CLAUDE_MODEL")

command -v claude >/dev/null 2>&1 || { echo "claude CLI not installed (check claude --version)" >&2; exit 3; }

# ---- git snapshot (mode gates; same limitation notes as codex_consult.sh) ----
WORKDIR="$(pwd)"
GIT_OK=0
git -C "$WORKDIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 && GIT_OK=1
git_snapshot() {
  [ "$GIT_OK" = 1 ] || return 0
  git -C "$WORKDIR" status --porcelain
  git -C "$WORKDIR" diff 2>/dev/null | sha1sum
  git -C "$WORKDIR" diff --cached 2>/dev/null | sha1sum
}
BEFORE="$(git_snapshot)"

# ---- run ----
rm -f "$OUT"
{
  echo "# claude_consult v1  mode=$MODE model=${CLAUDE_MODEL:-default} timeout=${TIMEOUT}s  $(date 2>/dev/null)"
  printf '# claude '; claude --version 2>&1 | head -1
  echo "# ---- claude -p ----"
} > "$LOG"

echo "→ Claude (mode=$MODE, model=${CLAUDE_MODEL:-default}, timeout=${TIMEOUT}s, resume=$RESUME, brief=$BRIEF) ..." >&2
RESUME_FLAG=(); [ "$RESUME" = 1 ] && RESUME_FLAG=(--continue)
RUN=(claude -p "${RESUME_FLAG[@]}" "${MODEL_FLAG[@]}")
START=$SECONDS
if command -v timeout >/dev/null 2>&1 && [ "$TIMEOUT" != 0 ]; then
  timeout "$TIMEOUT" "${RUN[@]}" < "$BRIEF" > "$OUT" 2>> "$LOG"
else
  "${RUN[@]}" < "$BRIEF" > "$OUT" 2>> "$LOG"
fi
rc=$?
DUR=$((SECONDS - START))

AFTER="$(git_snapshot)"
CHANGED=0; { [ "$GIT_OK" = 1 ] && [ "$BEFORE" != "$AFTER" ]; } && CHANGED=1

# ---- failure classifier ----
FAILED=0; FAIL_MSG=""
fail() { FAILED=1; FAIL_MSG="${FAIL_MSG}  - $1"$'\n'; }

if [ "$rc" -eq 124 ]; then fail "timed out after ${TIMEOUT}s (tune with CLAUDE_TIMEOUT)"
elif [ "$rc" -ne 0 ]; then fail "claude exit code $rc"; fi

[ -s "$OUT" ] || fail "empty reply (claude produced no final answer)"

# ---- mode gates (side-effect verification) ----
if [ "$GIT_OK" = 1 ]; then
  if [ "$MODE" = consult ] && [ "$CHANGED" = 1 ]; then
    fail "consult run CHANGED the worktree — read-only contract violated (claude -p runs unsandboxed). Inspect 'git diff' and revert."
  fi
  if [ "$MODE" = implement ] && [ "$CHANGED" = 0 ] && [ "${ALLOW_EMPTY_DIFF:-0}" != 1 ]; then
    fail "implement run changed NOTHING — likely a silent failure. If a no-op was intended: ALLOW_EMPTY_DIFF=1."
  fi
else
  if [ "$MODE" = consult ]; then
    fail "consult outside a git repo — cannot verify the no-edit contract (claude -p is unsandboxed). Run inside a git repo."
  else
    echo "⚠ not a git repo — implement edit-verification gate skipped (changes unverifiable)" >&2
  fi
fi

# ---- result ----
if [ "$FAILED" = 1 ]; then
  echo "✗ claude_consult FAILED (mode=$MODE, ${DUR}s):" >&2
  printf '%s' "$FAIL_MSG" >&2
  echo "  --- last 12 log lines ($LOG) ---" >&2
  tail -12 "$LOG" >&2
  [ "$rc" -ne 0 ] && exit "$rc" || exit 1
fi

echo "=== Claude reply ($OUT) — mode=$MODE, ${DUR}s, model=${CLAUDE_MODEL:-default} ==="
cat "$OUT"
exit 0
