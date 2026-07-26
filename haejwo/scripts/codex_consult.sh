#!/usr/bin/env bash
# codex_consult.sh — hardened headless Codex runner (haejwo's reviewer slot).
#
# What: feeds a self-contained brief to `codex exec` via stdin and captures
#       Codex's final reply to a file. Non-editing (consult) contract only —
#       project-agnostic — the invoking directory is the work root (run it
#       from the project root).
#
# Core design (production hardening): NEVER trust the exit code alone.
#   codex can fail silently with rc=0 (measured on sandbox-constrained hosts).
#   Any of {rc!=0 | empty reply | structured codex runtime error |
#   worktree changed} exits non-zero.
#
# Every input the reviewer sees (initial run, --resume, the CODEX_MODEL
# fallback retry) is prefixed with a standing REVIEWER CONTRACT (below) that
# forbids edits/installs/config changes — enforced by instruction here, and
# backstopped by the post-run change-detection gate.
#
# Usage:
#   codex_consult.sh [--mode consult] [--resume] [-o out.md] brief.md
#   echo "..." | codex_consult.sh --mode consult -   # stdin brief (deleted on exit)
#
# Mode (safety gate):
#   consult   (only mode) non-editing contract with post-run change detection —
#             FAILS if the worktree changed after the run (danger-full-access
#             cannot block edits — enforce in code). `--mode implement` was
#             removed in 2.10 (cross-vendor worker routing is a non-goal) —
#             use the standalone collab tool for manual implement runs.
#   --resume  continue the LAST codex session (model/effort/sandbox inherited;
#             message = stdin/brief; reply = stdout). For multi-round debate.
#
# Env:
#   CODEX_SANDBOX   read-only|workspace-write|danger-full-access. Explicit
#                   caller input deserves a loud error, not a silent
#                   downgrade — an invalid value here EXITS 2 naming the
#                   three valid values. Priority: CODEX_SANDBOX env > config
#                   `codex.consult_sandbox` > read-only (mode default). The
#                   config path is ONLY ${CLAUDE_PLUGIN_DATA}/config.json
#                   when that env var is set (non-empty) — a missing file
#                   there means NO config; it never falls back to a derived
#                   path (which could resurrect a stale danger-full-access
#                   setting from elsewhere). Only when CLAUDE_PLUGIN_DATA is
#                   unset/empty is the path derived from $0's resolved path:
#                   under /.codex/ -> ~/.codex/plugins/data/haejwo-haejwo/config.json,
#                   else ~/.claude/plugins/data/haejwo-haejwo/config.json
#                   (empty $HOME there also means no config). A CONFIG value
#                   that is missing, unparsable, or not in the allowlist
#                   silently falls back to read-only — NEVER a dangerous
#                   value on error (only the ENV path errors loudly).
#   CODEX_EFFORT    low|medium|high (default)|xhigh — scale to the decision's
#                   stakes; xhigh for the hardest calls only, low for probes.
#   CODEX_MODEL     force a specific reviewer model (optional). Fixed for the
#                   whole consult session — never swap models mid-thread on a
#                   --resume call; escalate stakes via CODEX_EFFORT=xhigh
#                   instead, and use a frontier model for xhigh rounds. If
#                   codex exec pre-execution-rejects this model as
#                   unknown/unavailable, this script retries ONCE with the
#                   CLI default and marks the reply — never silently retried
#                   twice, never persisted.
#   CODEX_TIMEOUT   seconds; default by effort (150/300/600/1200). 0 = unlimited.
#   CODEX_ALLOW_MARKERS=1  disable the runtime-error scan (safety valve; rarely needed).
#
# Verification discipline: this is a READ-ONLY reviewer slot — never trust it
#   to have made changes; workers implement, this only analyzes and replies.
# Waiting discipline: run in the background and wait for ONE completion event —
#   no sleep/pgrep polling loops.
set -uo pipefail

# REVIEWER CONTRACT: prepended to every brief this script sends to codex, on
# every input path (initial run, --resume, model-fallback retry). Durable
# owner policy — not brief-specific, do not let callers override it.
REVIEWER_CONTRACT='REVIEWER CONTRACT: analyze and reply only. Do NOT modify files, install
anything, or change any configuration (packages, MCP servers, global or
user settings). If you need a missing capability, STATE THE NEED in your
reply — the host decides.
---'

print_help() {
  cat <<'EOF'
codex_consult.sh — feed a self-contained brief to codex exec; capture reply.

Usage:
  codex_consult.sh [--mode consult] [--resume] [-o out.md] brief.md
  echo "..." | codex_consult.sh --mode consult -

Mode:
  consult   (only mode) non-editing contract with post-run change detection; FAILS if the run changed the worktree.
  --resume  continue the last codex session (multi-round memory).

--mode implement was removed in 2.10 (cross-vendor worker routing is a
non-goal); use the standalone collab tool for manual implement runs.

Env: CODEX_SANDBOX, CODEX_EFFORT (default high), CODEX_MODEL,
  CODEX_TIMEOUT (default by effort), CODEX_ALLOW_MARKERS=1.

Exit code: non-zero on ANY of {codex rc!=0, empty reply, codex runtime error,
  consult-changed-files}. A silent rc=0 failure is never reported as success.
EOF
}

# ---- argument parsing ----
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
    -)        break ;;  # bare '-' = stdin brief (positional) — must match before '-*'
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
[ -z "$BRIEF" ] && { echo "brief file required. usage: codex_consult.sh [--mode consult] [-o out] brief.md|-" >&2; exit 2; }

# stdin brief -> temp file, deleted on exit (keeps sensitive content out of /tmp).
# EFFECTIVE_BRIEF (contract + blank line + brief) is what's actually fed to
# codex on every input path; also deleted on exit.
TMPBRIEF=""
EFFECTIVE_BRIEF=""
cleanup() {
  [ -n "$TMPBRIEF" ] && rm -f "$TMPBRIEF"
  [ -n "$EFFECTIVE_BRIEF" ] && rm -f "$EFFECTIVE_BRIEF"
}
trap cleanup EXIT
if [ "$BRIEF" = "-" ]; then
  BRIEF="$(mktemp "${TMPDIR:-/tmp}/codex_brief.XXXXXX.md")"
  TMPBRIEF="$BRIEF"
  cat > "$BRIEF"
fi
[ -f "$BRIEF" ] || { echo "brief file not found: $BRIEF" >&2; exit 2; }

[ -z "$OUT" ] && OUT="${BRIEF%.md}.reply.md"
LOG="${OUT%.*}.log"

# ---- effective brief: REVIEWER CONTRACT + blank line + original brief ----
EFFECTIVE_BRIEF="$(mktemp "${TMPDIR:-/tmp}/codex_effective.XXXXXX.md")"
{ printf '%s\n\n' "$REVIEWER_CONTRACT"; cat "$BRIEF"; } > "$EFFECTIVE_BRIEF"

# ---- sandbox: env CODEX_SANDBOX > config codex.consult_sandbox > read-only ----
config_consult_sandbox() {
  local cfg=""
  if [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
    # Env explicitly set: use ONLY this path. A missing config.json here
    # means "no config" — NEVER fall back to the derived path below, which
    # could resurrect a stale danger-full-access setting from elsewhere.
    cfg="${CLAUDE_PLUGIN_DATA}/config.json"
  else
    local self_path
    self_path="$(realpath "$0" 2>/dev/null || printf '%s' "$0")"
    case "$self_path" in
      # ${HOME:-}: set -u safe; an empty $HOME yields a root-anchored path
      # that will not exist, so [ -f "$cfg" ] below naturally treats it as
      # "no config" -> read-only, same as any other missing-file case.
      */.codex/*) cfg="${HOME:-}/.codex/plugins/data/haejwo-haejwo/config.json" ;;
      *)          cfg="${HOME:-}/.claude/plugins/data/haejwo-haejwo/config.json" ;;
    esac
  fi
  [ -f "$cfg" ] || return 0
  python3 -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8-sig") as f:
        cfg = json.load(f)
    v = cfg.get("codex", {}).get("consult_sandbox")
    if isinstance(v, str):
        sys.stdout.write(v)
except Exception:
    pass
' "$cfg" 2>/dev/null
}

if [ -n "${CODEX_SANDBOX:-}" ]; then
  # Explicit caller input deserves a loud error, not a silent downgrade.
  case "$CODEX_SANDBOX" in
    read-only|workspace-write|danger-full-access) SANDBOX="$CODEX_SANDBOX" ;;
    *)
      echo "invalid CODEX_SANDBOX: $CODEX_SANDBOX (must be one of: read-only, workspace-write, danger-full-access)" >&2
      exit 2
      ;;
  esac
else
  CFG_SANDBOX="$(config_consult_sandbox)"
  case "$CFG_SANDBOX" in
    read-only|workspace-write|danger-full-access) SANDBOX="$CFG_SANDBOX" ;;
    *) SANDBOX="read-only" ;;
  esac
fi

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

# ---- git snapshot (edit attribution + mode gate; pre-existing dirt stays separate) ----
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
  echo "# codex_consult v0.3  mode=$MODE sandbox=$SANDBOX effort=${EFFORT:-default} timeout=${TIMEOUT}s  $(date 2>/dev/null)"
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
    timeout "$TIMEOUT" codex exec --skip-git-repo-check resume --last < "$EFFECTIVE_BRIEF" > "$OUT" 2>> "$LOG"
  else
    codex exec --skip-git-repo-check resume --last < "$EFFECTIVE_BRIEF" > "$OUT" 2>> "$LOG"
  fi
else
  RUN=(codex exec -s "$SANDBOX" --skip-git-repo-check --cd "$WORKDIR" "${MODEL_FLAG[@]}" "${EFFORT_FLAG[@]}" -o "$OUT" -)
  if command -v timeout >/dev/null 2>&1 && [ "$TIMEOUT" != 0 ]; then
    timeout "$TIMEOUT" "${RUN[@]}" < "$EFFECTIVE_BRIEF" >> "$LOG" 2>&1
  else
    "${RUN[@]}" < "$EFFECTIVE_BRIEF" >> "$LOG" 2>&1
  fi
fi
rc=$?
DUR=$((SECONDS - START))

# ---- model-unavailable fallback (non-resume only): PRE-EXECUTION rejection of
# CODEX_MODEL retries ONCE with the CLI default, never twice, never persisted
# (the note below is stdout-only for this run; $OUT itself is untouched). A
# positive ID needs BOTH a known unknown-model stderr pattern AND the
# requested model name in the log — ambiguous/timeout/transport errors are
# left to the existing classifier below.
MODEL_FALLBACK=0
if [ "$RESUME" = 0 ] && [ -n "${CODEX_MODEL:-}" ] && [ "$rc" -ne 0 ] && [ "$rc" -ne 124 ]; then
  MODEL_ERR_RE='unknown model|model not found|not available'
  if grep -iEq "$MODEL_ERR_RE" "$LOG" 2>/dev/null && grep -qF "$CODEX_MODEL" "$LOG" 2>/dev/null; then
    echo "⚠ model '$CODEX_MODEL' rejected pre-execution — retrying ONCE with CLI default ..." >&2
    echo "# ---- model '$CODEX_MODEL' unavailable; retry with CLI default ----" >> "$LOG"
    rm -f "$OUT"
    RETRY=(codex exec -s "$SANDBOX" --skip-git-repo-check --cd "$WORKDIR" "${EFFORT_FLAG[@]}" -o "$OUT" -)
    START=$SECONDS
    if command -v timeout >/dev/null 2>&1 && [ "$TIMEOUT" != 0 ]; then
      timeout "$TIMEOUT" "${RETRY[@]}" < "$EFFECTIVE_BRIEF" >> "$LOG" 2>&1
    else
      "${RETRY[@]}" < "$EFFECTIVE_BRIEF" >> "$LOG" 2>&1
    fi
    rc=$?
    DUR=$((SECONDS - START))
    MODEL_FALLBACK=1
  fi
fi

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

# ---- mode gate (side-effect verification) ----
if [ "$GIT_OK" = 1 ]; then
  if [ "$CHANGED" = 1 ]; then
    fail "consult run CHANGED the worktree — read-only contract violated (danger-full-access cannot block edits). Inspect 'git diff' and revert."
  fi
else
  # Not a git repo: diff-based gate unavailable.
  if [ "$SANDBOX" != read-only ]; then
    fail "consult outside a git repo with sandbox=$SANDBOX (not read-only) — cannot verify the no-edit contract. Use read-only or run inside a git repo."
  fi
fi

# ---- result ----
if [ "$FAILED" = 1 ]; then
  echo "✗ codex_consult FAILED (mode=$MODE, ${DUR}s):" >&2
  printf '%s' "$FAIL_MSG" >&2
  [ "$MODEL_FALLBACK" = 1 ] && echo "  (note: model '$CODEX_MODEL' was unavailable; retried with CLI default, still failed)" >&2
  echo "  --- last 12 log lines ($LOG) ---" >&2
  tail -12 "$LOG" >&2
  [ "$rc" -ne 0 ] && exit "$rc" || exit 1
fi

# Persist the fallback disclosure into $OUT itself (not just stdout) so a
# caller reading the reply FILE programmatically also sees the assurance
# downgrade — stdout alone is lost to any redirection/capture of this script.
if [ "$MODEL_FALLBACK" = 1 ]; then
  FALLBACK_NOTE="note: requested model '$CODEX_MODEL' unavailable; reviewed by CLI default (assurance downgraded)"
  { printf '%s\n' "$FALLBACK_NOTE"; cat "$OUT"; } > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
fi

echo "=== Codex reply ($OUT) — mode=$MODE, ${DUR}s, effort=${EFFORT:-default}, sandbox=$SANDBOX ==="
[ "$MODEL_FALLBACK" = 1 ] && echo "note: requested model '$CODEX_MODEL' unavailable; reviewed by CLI default (assurance downgraded)"
cat "$OUT"
exit 0
