#!/usr/bin/env bash
# claude_consult.sh — hardened headless Claude runner (haejwo's reviewer slot
# on a CODEX host). Mirror of codex_consult.sh: on a Codex host the
# different-model independent reviewer is Claude (principle 9 symmetry).
#
# Same contract as codex_consult.sh: feed a self-contained brief via stdin or
# file, capture the final reply, NEVER trust the exit code alone — any of
# {rc!=0 | empty reply | worktree changed} exits non-zero.
#
# Direct edit tools (Edit/Write/NotebookEdit) are disabled via
# --disallowedTools on every run. Bash remains available to the reviewer
# (claude -p has no read-only sandbox concept) — covered by the post-run
# git-snapshot change detection below, not by tool blocking. That detection
# only covers the git-tracked scope and assumes a single writer (see the
# Limitation note further down); it is not a security boundary.
#
# Every input the reviewer sees (initial run, --resume) is prefixed with a
# standing REVIEWER CONTRACT (below) that forbids edits/installs/config
# changes — enforced by instruction, backstopped by --disallowedTools and the
# change-detection gate.
#
# Usage:
#   claude_consult.sh [--mode consult] [--resume] [-o out.md] brief.md
#   echo "..." | claude_consult.sh --mode consult -
#
# Mode (safety gate — enforced via git snapshot, since `claude -p` runs
# with the invoking user's permissions and has no read-only sandbox):
#   consult   (only mode) non-editing contract with post-run change detection —
#             FAILS if the worktree changed.
#   --resume  continue the most recent Claude session in this directory
#             (message = stdin/brief; reply = stdout).
#
# `--mode implement` was removed in 2.10 (cross-vendor worker routing is a
# non-goal); use the standalone collab tool for manual implement runs.
#
# Env:
#   CLAUDE_MODEL    force a model (passed as --model; optional).
#   CLAUDE_TIMEOUT  seconds; default 600. 0 = unlimited.
set -uo pipefail

# REVIEWER CONTRACT: prepended to every brief this script sends to claude, on
# every input path (initial run, --resume). Durable owner policy — not
# brief-specific, do not let callers override it.
REVIEWER_CONTRACT='REVIEWER CONTRACT: analyze and reply only. Do NOT modify files, install
anything, or change any configuration (packages, MCP servers, global or
user settings). If you need a missing capability, STATE THE NEED in your
reply — the host decides.
---'

print_help() {
  cat <<'EOF'
claude_consult.sh — feed a self-contained brief to headless claude; capture reply.

Usage:
  claude_consult.sh [--mode consult] [--resume] [-o out.md] brief.md
  echo "..." | claude_consult.sh --mode consult -

Mode:
  consult   (only mode) non-editing contract with post-run change detection; FAILS if the run changed the worktree.
  --resume  continue the most recent session (multi-round memory).

--mode implement was removed in 2.10 (cross-vendor worker routing is a
non-goal); use the standalone collab tool for manual implement runs.

Env: CLAUDE_MODEL, CLAUDE_TIMEOUT (default 600).

Exit code: non-zero on ANY of {claude rc!=0, empty reply, consult-changed-files}.
A silent rc=0 failure is never reported as success.

Direct edit tools (Edit/Write/NotebookEdit) are disabled via
--disallowedTools; Bash remains available and is covered by post-run
git-snapshot change detection (git-tracked scope only, single-writer
assumption), not by tool blocking.
EOF
}

# ---- argument parsing (mirrors codex_consult.sh) ----
MODE=""
OUT=""
RESUME=0
while [ $# -gt 0 ]; do
  case "$1" in
    --resume) RESUME=1; shift ;;
    --mode)   [ $# -ge 2 ] || { echo "--mode requires a value (consult)" >&2; exit 2; }; MODE="$2"; shift 2 ;;
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
case "$MODE" in
  consult) ;;
  implement)
    echo "--mode implement was removed in 2.10 (cross-vendor worker routing is a non-goal); use the standalone collab tool for manual implement runs." >&2
    exit 2
    ;;
  *)
    echo "invalid --mode: $MODE (consult)" >&2
    exit 2
    ;;
esac

BRIEF="${1:-}"
[ -z "$BRIEF" ] && { echo "brief file required. usage: claude_consult.sh [--mode consult] [-o out] brief.md|-" >&2; exit 2; }

# TMPBRIEF: stdin brief -> temp file. EFFECTIVE_BRIEF (contract + blank line +
# brief) is what's actually fed to claude on every input path. Both deleted
# on exit.
TMPBRIEF=""
EFFECTIVE_BRIEF=""
cleanup() {
  [ -n "$TMPBRIEF" ] && rm -f "$TMPBRIEF"
  [ -n "$EFFECTIVE_BRIEF" ] && rm -f "$EFFECTIVE_BRIEF"
}
trap cleanup EXIT
if [ "$BRIEF" = "-" ]; then
  BRIEF="$(mktemp "${TMPDIR:-/tmp}/claude_brief.XXXXXX.md")"
  TMPBRIEF="$BRIEF"
  cat > "$BRIEF"
fi
[ -f "$BRIEF" ] || { echo "brief file not found: $BRIEF" >&2; exit 2; }

[ -z "$OUT" ] && OUT="${BRIEF%.md}.reply.md"
LOG="${OUT%.*}.log"

# ---- effective brief: REVIEWER CONTRACT + blank line + original brief ----
EFFECTIVE_BRIEF="$(mktemp "${TMPDIR:-/tmp}/claude_effective.XXXXXX.md")"
{ printf '%s\n\n' "$REVIEWER_CONTRACT"; cat "$BRIEF"; } > "$EFFECTIVE_BRIEF"

TIMEOUT="${CLAUDE_TIMEOUT:-600}"
MODEL_FLAG=(); [ -n "${CLAUDE_MODEL:-}" ] && MODEL_FLAG=(--model "$CLAUDE_MODEL")

command -v claude >/dev/null 2>&1 || { echo "claude CLI not installed (check claude --version)" >&2; exit 3; }

# ---- git snapshot (mode gate; same limitation notes as codex_consult.sh) ----
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
  echo "# claude_consult v1.1  mode=$MODE model=${CLAUDE_MODEL:-default} timeout=${TIMEOUT}s  $(date 2>/dev/null)"
  printf '# claude '; claude --version 2>&1 | head -1
  echo "# ---- claude -p ----"
} > "$LOG"

echo "→ Claude (mode=$MODE, model=${CLAUDE_MODEL:-default}, timeout=${TIMEOUT}s, resume=$RESUME, brief=$BRIEF) ..." >&2
RESUME_FLAG=(); [ "$RESUME" = 1 ] && RESUME_FLAG=(--continue)
# Direct edit tools disabled — the reviewer analyzes and replies only.
RUN=(claude -p "${RESUME_FLAG[@]}" "${MODEL_FLAG[@]}" --disallowedTools "Edit,Write,NotebookEdit")
START=$SECONDS
if command -v timeout >/dev/null 2>&1 && [ "$TIMEOUT" != 0 ]; then
  timeout "$TIMEOUT" "${RUN[@]}" < "$EFFECTIVE_BRIEF" > "$OUT" 2>> "$LOG"
else
  "${RUN[@]}" < "$EFFECTIVE_BRIEF" > "$OUT" 2>> "$LOG"
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

# ---- mode gate (side-effect verification) ----
if [ "$GIT_OK" = 1 ]; then
  if [ "$CHANGED" = 1 ]; then
    fail "consult run CHANGED the worktree — read-only contract violated (claude -p runs unsandboxed). Inspect 'git diff' and revert."
  fi
else
  fail "consult outside a git repo — cannot verify the no-edit contract (claude -p is unsandboxed). Run inside a git repo."
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
