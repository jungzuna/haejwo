#!/usr/bin/env bash
# codex_consult.sh — hardened headless Codex runner (haejwo's reviewer slot).
#
# What: feeds a self-contained brief to `codex exec` via stdin and captures
#       Codex's final reply to a file. `--mode implement` lets Codex edit
#       files. Project-agnostic — the invoking directory is the work root
#       (run it from the project root).
#
# Core design (production hardening): NEVER trust the exit code alone.
#   codex can fail silently with rc=0 (measured on sandbox-constrained hosts).
#   Any of {rc!=0 | empty reply | structured codex runtime error |
#   (consult) worktree changed | (implement) zero changes} exits non-zero.
#
# Usage:
#   codex_consult.sh [--mode consult|implement] [--resume] [-o out.md] brief.md
#   echo "..." | codex_consult.sh --mode consult -   # stdin brief (deleted on exit)
#
# Modes (safety gates):
#   consult   (default) read-only advice. FAILS if the worktree changed after
#             the run (danger-full-access cannot block edits — enforce in code).
#   implement Codex edits files. FAILS on zero changes (ALLOW_EMPTY_DIFF=1 allows
#             an intentional no-op).
#   --resume  continue the LAST codex session (model/effort/sandbox inherited;
#             message = stdin/brief; reply = stdout). For multi-round debate.
#
# Env:
#   CODEX_SANDBOX   read-only|workspace-write|danger-full-access (default by mode).
#                   sandbox-constrained hosts: only danger-full-access can touch files.
#   CODEX_EFFORT    low|medium|high (default)|xhigh — scale to the decision's
#                   stakes; xhigh for the hardest calls only, low for probes.
#   CODEX_MODEL     force a specific model (optional).
#   CODEX_TIMEOUT   seconds; default by effort (150/300/600/1200). 0 = unlimited.
#   ALLOW_EMPTY_DIFF=1     allow an intentional no-op implement run.
#   CODEX_ALLOW_MARKERS=1  disable the runtime-error scan (safety valve; rarely needed).
#
# Verification discipline: after implement, ALWAYS review `git diff` yourself and
#   run project checks before any commit.
# Waiting discipline: run in the background and wait for ONE completion event —
#   no sleep/pgrep polling loops.
set -uo pipefail

print_help() {
  cat <<'EOF'
codex_consult.sh — feed a self-contained brief to codex exec; capture reply/edits.

Usage:
  codex_consult.sh [--mode consult|implement] [--resume] [-o out.md] brief.md
  echo "..." | codex_consult.sh --mode consult -

Modes:
  consult   (default) read-only; FAILS if the run changed the worktree.
  implement Codex edits files; FAILS on zero changes (ALLOW_EMPTY_DIFF=1 allows no-op).
  --resume  continue the last codex session (multi-round memory).

Env: CODEX_SANDBOX, CODEX_EFFORT (default high), CODEX_MODEL,
  CODEX_TIMEOUT (default by effort), ALLOW_EMPTY_DIFF=1, CODEX_ALLOW_MARKERS=1.

Exit code: non-zero on ANY of {codex rc!=0, empty reply, codex runtime error,
  consult-changed-files, implement-changed-nothing}. A silent rc=0 failure is
  never reported as success.
EOF
}

# ---- argument parsing ----
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
    -)        break ;;  # bare '-' = stdin brief (positional) — must match before '-*'
    -*)       echo "unknown option: $1" >&2; exit 2 ;;
    *)        break ;;
  esac
done

MODE="${MODE:-consult}"
case "$MODE" in consult|implement) ;; *) echo "invalid --mode: $MODE (consult|implement)" >&2; exit 2 ;; esac

BRIEF="${1:-}"
[ -z "$BRIEF" ] && { echo "brief file required. usage: codex_consult.sh [--mode ..] [-o out] brief.md|-" >&2; exit 2; }

# stdin brief -> temp file, deleted on exit (keeps sensitive content out of /tmp).
TMPBRIEF=""
cleanup() { [ -n "$TMPBRIEF" ] && rm -f "$TMPBRIEF"; }
trap cleanup EXIT
if [ "$BRIEF" = "-" ]; then
  BRIEF="$(mktemp "${TMPDIR:-/tmp}/codex_brief.XXXXXX.md")"
  TMPBRIEF="$BRIEF"
  cat > "$BRIEF"
fi
[ -f "$BRIEF" ] || { echo "brief file not found: $BRIEF" >&2; exit 2; }

[ -z "$OUT" ] && OUT="${BRIEF%.md}.reply.md"
LOG="${OUT%.*}.log"

# ---- sandbox default by mode (CODEX_SANDBOX wins) ----
if [ -n "${CODEX_SANDBOX:-}" ]; then SANDBOX="$CODEX_SANDBOX"
elif [ "$MODE" = implement ]; then SANDBOX="workspace-write"
else SANDBOX="read-only"; fi

# ---- effort / timeout ----
# Reviewer effort scales with the DECISION'S stakes, not a fixed pin
# (uniform max dilutes "spend budget where judgment compounds"). Callers
# set CODEX_EFFORT per call: medium for routine checks, high for standard
# consults (default), xhigh only for architecture forks / security-critical
# calls / final deadlock rounds. Non-reasoning probes stay explicit low.
EFFORT="${CODEX_EFFORT-high}"
case "$EFFORT" in low) DEF_TO=150;; medium) DEF_TO=300;; high) DEF_TO=600;; xhigh) DEF_TO=1200;; *) DEF_TO=600;; esac
TIMEOUT="${CODEX_TIMEOUT:-$DEF_TO}"

MODEL_FLAG=(); [ -n "${CODEX_MODEL:-}" ] && MODEL_FLAG=(-m "$CODEX_MODEL")
EFFORT_FLAG=(); [ -n "$EFFORT" ] && EFFORT_FLAG=(-c "model_reasoning_effort=\"$EFFORT\"")

command -v codex >/dev/null 2>&1 || { echo "codex CLI not installed (check codex --version)" >&2; exit 3; }

# ---- git snapshot (edit attribution + mode gates; pre-existing dirt stays separate) ----
# Limitation: if ANOTHER process mutates the repo during the run, attribution can
# be wrong (concurrent formatter/hook/editor save). Assumes a single writer —
# use worktree isolation/locking if you need concurrent runs.
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
  echo "# codex_consult v0.2  mode=$MODE sandbox=$SANDBOX effort=${EFFORT:-default} timeout=${TIMEOUT}s  $(date 2>/dev/null)"
  printf '# codex '; codex --version 2>&1 | head -1
  echo "# ---- codex exec ----"
} > "$LOG"

echo "→ Codex (mode=$MODE, sandbox=$SANDBOX, effort=${EFFORT:-default}, timeout=${TIMEOUT}s, resume=$RESUME, brief=$BRIEF) ..." >&2
START=$SECONDS
if [ "$RESUME" = 1 ]; then
  # Persistent session: continue the latest codex thread (model/effort/sandbox
  # inherited from the original session — no flags). message=stdin, reply=stdout.
  echo "# ---- codex exec resume --last ----" >> "$LOG"
  if command -v timeout >/dev/null 2>&1 && [ "$TIMEOUT" != 0 ]; then
    timeout "$TIMEOUT" codex exec --skip-git-repo-check resume --last < "$BRIEF" > "$OUT" 2>> "$LOG"
  else
    codex exec --skip-git-repo-check resume --last < "$BRIEF" > "$OUT" 2>> "$LOG"
  fi
else
  RUN=(codex exec -s "$SANDBOX" --skip-git-repo-check --cd "$WORKDIR" "${MODEL_FLAG[@]}" "${EFFORT_FLAG[@]}" -o "$OUT" -)
  if command -v timeout >/dev/null 2>&1 && [ "$TIMEOUT" != 0 ]; then
    timeout "$TIMEOUT" "${RUN[@]}" < "$BRIEF" >> "$LOG" 2>&1
  else
    "${RUN[@]}" < "$BRIEF" >> "$LOG" 2>&1
  fi
fi
rc=$?
DUR=$((SECONDS - START))

AFTER="$(git_snapshot)"
CHANGED=0; { [ "$GIT_OK" = 1 ] && [ "$BEFORE" != "$AFTER" ]; } && CHANGED=1

# ---- failure classifier (never trust the exit code alone) ----
FAILED=0; FAIL_MSG=""
fail() { FAILED=1; FAIL_MSG="${FAIL_MSG}  - $1"$'\n'; }

if [ "$rc" -eq 124 ]; then fail "timed out after ${TIMEOUT}s (tune with CODEX_TIMEOUT)"
elif [ "$rc" -ne 0 ]; then fail "codex exit code $rc"; fi

[ -s "$OUT" ] || fail "empty reply (codex produced no final answer)"

# Scan codex's STRUCTURED runtime errors only, in $LOG only. Raw prose markers
# (e.g. a sandbox-tool name) false-positive whenever codex merely DISCUSSES
# sandboxes while reasoning, so only structured runtime errors count as failures.
CODEX_ERR='ERROR codex_core|sandbox helper failed|apply_patch verification failed|tools::router: error=|failed with status exit status'
if [ "${CODEX_ALLOW_MARKERS:-0}" != 1 ] && grep -iEq "$CODEX_ERR" "$LOG" 2>/dev/null; then
  fail "codex runtime error detected (sandbox/patch/tool failure) — a constrained sandbox can silently no-op writes; try CODEX_SANDBOX=danger-full-access. (see $LOG; if this is a false positive: CODEX_ALLOW_MARKERS=1)"
fi

# ---- mode gates (side-effect verification) ----
if [ "$GIT_OK" = 1 ]; then
  if [ "$MODE" = consult ] && [ "$CHANGED" = 1 ]; then
    fail "consult run CHANGED the worktree — read-only contract violated (danger-full-access cannot block edits). Inspect 'git diff' and revert."
  fi
  if [ "$MODE" = implement ] && [ "$CHANGED" = 0 ] && [ "${ALLOW_EMPTY_DIFF:-0}" != 1 ]; then
    fail "implement run changed NOTHING — likely a silent failure (a constrained sandbox can no-op writes). If a no-op was intended: ALLOW_EMPTY_DIFF=1."
  fi
else
  # Not a git repo: diff-based gates unavailable.
  if [ "$MODE" = consult ] && [ "$SANDBOX" != read-only ]; then
    fail "consult outside a git repo with sandbox=$SANDBOX (not read-only) — cannot verify the no-edit contract. Use read-only or run inside a git repo."
  elif [ "$MODE" = implement ]; then
    echo "⚠ not a git repo — implement edit-verification gate skipped (changes unverifiable)" >&2
  fi
fi

# ---- result ----
if [ "$FAILED" = 1 ]; then
  echo "✗ codex_consult FAILED (mode=$MODE, ${DUR}s):" >&2
  printf '%s' "$FAIL_MSG" >&2
  echo "  --- last 12 log lines ($LOG) ---" >&2
  tail -12 "$LOG" >&2
  [ "$rc" -ne 0 ] && exit "$rc" || exit 1
fi

echo "=== Codex reply ($OUT) — mode=$MODE, ${DUR}s, effort=${EFFORT:-default}, sandbox=$SANDBOX ==="
cat "$OUT"
exit 0
