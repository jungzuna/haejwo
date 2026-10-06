#!/usr/bin/env bash
# claude_consult.sh — headless Claude reviewer runner (haejwo's reviewer slot on a Codex host; mirror of
# codex_consult.sh — there the different-model independent reviewer is Claude, principle 9 symmetry).
# Feeds REVIEWER CONTRACT + a self-contained brief to `claude -p` on stdin and captures the final reply.
# Consult (non-editing) only; the invoking directory is the work root. Options, env, exit codes: --help.
# Shared mechanics live in lib/consult_common.sh; this file owns the vendor policy: contract text, the
# `claude -p` argv and its --disallowedTools, the timeout default, and the non-git policy (no sandbox
# exists here, so a non-repo is always refused).
# NEVER trust the exit code alone: rc=0 with no reply or a changed repository is never success; there is no
# event classifier on this path; a failure reported only in prose is not detected. Edit/Write/NotebookEdit
# are disallowed; Bash stays available and is covered by change detection, which is not a security
# boundary (scope: lib/detect.py).
# Config is host-relative: codex.model is read under /.codex/ and on a vendorless custom root (the runner
# kind names the host, lib/config.py), ignored under /.claude/. The model is disclosed with its source.
# *[origin: a live smoke launched claude with `--model gpt-6-astra`, the codex host's own reviewer model, read out of a claude-host config]*
# *[origin: `-o` naming a tracked file was silently overwritten — change detection excludes artifacts by design]* *[origin: ship review Z4]*
set -uo pipefail

# ---- shared internals ----
# $0 resolved to an ABSOLUTE PHYSICAL path (symlinked or relative invocation): realpath (the minimal-PATH
# fixture has no readlink), else python3, else lexical $PWD. Every required file is checked before any
# artifact exists: an incomplete install fails loudly, never half-runs a paid review.
HJW_SELF="$(realpath "$0" 2>/dev/null)"
case "$HJW_SELF" in
  /*) ;;
  *)  HJW_SELF="$(python3 -c 'import os, sys
sys.stdout.write(os.path.realpath(sys.argv[1]))' "$0" 2>/dev/null)" ;;
esac
case "$HJW_SELF" in
  /*) ;;
  *)  HJW_SELF="$PWD/${HJW_SELF:-$0}" ;;
esac
HJW_LIB="${HJW_SELF%/*}/lib"
for _hjw_f in consult_common.sh bounded.py detect.py config.py forward.py; do
  [ -f "$HJW_LIB/$_hjw_f" ] || {
    echo "consult runner library missing: $HJW_LIB/$_hjw_f" >&2; exit 3; }
done
unset _hjw_f
# shellcheck source=lib/consult_common.sh
. "$HJW_LIB/consult_common.sh" || {
  echo "consult runner library missing: $HJW_LIB/consult_common.sh" >&2; exit 3; }
HJW_RUNNER_KIND=claude
# Declared BEFORE forwarding and init: the artifact guard derives from them, on a hop too.
HJW_OUT_SIBLINGS=()  # no artifacts beyond the reply and the log
HJW_OUT_APPENDS=()

# FIRST action after sourcing (before parsing, stdin, traps, temp files, config, chdir): a stale remembered
# runner path forwards to the installed version. Fail open; an argv naming an artifact inside the repo exits 2.
hjw_forward_if_stale "$@"

# REVIEWER CONTRACT: prepended to every brief on every input path; durable owner policy, never caller-overridable.
REVIEWER_CONTRACT='REVIEWER CONTRACT: analyze and reply only. Do NOT modify files, install
anything, or change any configuration (packages, MCP servers, global or
user settings). If you need a missing capability, STATE THE NEED in your
reply — the host decides.
---'

print_help() {
  cat <<'EOF'
claude_consult.sh — feed a self-contained brief to headless claude; capture reply.

Usage:
  claude_consult.sh [--mode consult] [-o out.md] brief.md
  echo "..." | claude_consult.sh --mode consult -

Mode:
  consult   (only mode) non-editing contract with post-run change detection; FAILS if the repository changed during the run.
  --resume: removed in 2.13 — escalation and follow-up rounds use a NEW session
  --snapshot: removed in 2.22 — review the live working copy; pause writes during the review, or review a worktree you prepared
  --mode implement: removed in 2.10 (cross-vendor worker routing is a non-goal)

Env (env > config > default; empty = unset): CLAUDE_MODEL, CLAUDE_TIMEOUT (default 600; 0 = unlimited).
Config key codex.model supplies the default model on a Codex host (a config path under /.codex/, or a
  custom plugin root naming no vendor); under /.claude/ it describes the other vendor's reviewer and
  is ignored. Env wins.

Exit code: non-zero on ANY of {claude rc!=0, empty reply, repository changed, change detection
  unavailable}; there is no event classifier on this path; a failure the reviewer reports only in
  prose is not detected.

Edit/Write/NotebookEdit are disallowed; Bash remains available and is covered by post-run change
detection (HEAD, tracked status/fingerprints, untracked contents capped at 2000 files; global config
and ignored files never covered; concurrent writers not distinguished), not by tool blocking.
EOF
}

# ---- argument parsing, shared state, traps, $OUT/$LOG ----
hjw_parse_args "$@"
hjw_common_init

# ---- effective brief: REVIEWER CONTRACT + blank line + original brief ----
EFFECTIVE_BRIEF="$(mktemp "${TMPDIR:-/tmp}/claude_effective.XXXXXX.md")" || {
  echo "cannot create a temp file (is ${TMPDIR:-/tmp} writable?)" >&2; exit 4; }
{ printf '%s\n\n' "$REVIEWER_CONTRACT"; cat "$BRIEF"; } > "$EFFECTIVE_BRIEF"

# ---- config (same path rules as codex_consult.sh) ----
hjw_config_load

# ---- model: env CLAUDE_MODEL > config codex.model > CLI default ----
# A whitespace-only value counts as UNSET on both paths.
ENV_MODEL="$(trim "${CLAUDE_MODEL:-}")"
if [ -n "$ENV_MODEL" ]; then
  MODEL="$ENV_MODEL"; MODEL_SRC="env"
elif [ -n "$CFG_MODEL" ]; then
  MODEL="$CFG_MODEL"; MODEL_SRC="config"
else
  MODEL=""; MODEL_SRC="cli-default"
fi

TIMEOUT="${CLAUDE_TIMEOUT:-600}"
MODEL_FLAG=(); [ -n "$MODEL" ] && MODEL_FLAG=(--model "$MODEL")

if [ -n "$MODEL" ]; then MODEL_DISP="model=$MODEL ($MODEL_SRC)"
else MODEL_DISP="model=cli-default (identity unverified)"; fi

command -v claude >/dev/null 2>&1 || { echo "claude CLI not installed (check claude --version)" >&2; exit 3; }

{
  echo "# claude_consult  $HJW_PLUGIN_DISP  mode=$MODE $MODEL_DISP timeout=${TIMEOUT}s $CFG_DISP  $(date 2>/dev/null)"
  printf '# claude '; bounded 20 claude --version 2>&1 | head -1
} > "$LOG"
# After the header write (which truncates $LOG), so the foreign-CLAUDE_PLUGIN_DATA note survives with the header.
hjw_config_disclose
echo "# ---- claude -p ----" >> "$LOG"

# ---- change detection (A5): file-backed before/after snapshots ----
WORKDIR="$(pwd)"
# $LOG cannot collide with the previous reply — the aliasing rule renamed it.
rm -f "$OUT"
hjw_git_preflight
ARTIFACTS=("$OUT" "$LOG" "$TMPBRIEF" "$EFFECTIVE_BRIEF")

if ! hjw_detect_before; then
  # Not a git repo: the no-edit contract can never be verified, so refuse before the paid call (was a
  # post-run failure that paid for a discarded result). `claude -p` has no sandbox, so no read-only exception:
  # the codex runner's is a vendor capability, not shared policy. *[origin 2026-09-21 audit item 3]*
  REFUSE_MSG="consult outside a git repo — cannot verify the no-edit contract (claude -p is unsandboxed). Run inside a git repo."
  printf '# ---- precondition refused: %s ----\n' "$REFUSE_MSG" >> "$LOG" 2>/dev/null
  echo "$REFUSE_MSG" >&2
  exit 2
fi

# ---- run ----
echo "→ Claude (mode=$MODE, $MODEL_DISP, timeout=${TIMEOUT}s, brief=$BRIEF) ..." >&2
# Direct edit tools disabled — the reviewer analyzes and replies only.
RUN=(claude -p "${MODEL_FLAG[@]}" --disallowedTools "Edit,Write,NotebookEdit")
START=$SECONDS
echo "# ---- attempt 1: $MODEL_DISP ----" >> "$LOG"
echo "# ---- attempt 1 stderr ----" >> "$LOG"
if [ "$TIMEOUT" != 0 ]; then
  bounded "$TIMEOUT" "${RUN[@]}" < "$EFFECTIVE_BRIEF" > "$OUT" 2>> "$LOG"
else
  "${RUN[@]}" < "$EFFECTIVE_BRIEF" > "$OUT" 2>> "$LOG"
fi
rc=$?
DUR=$((SECONDS - START))

hjw_detect_after

# ---- failure classifier ----
if [ "$rc" -eq 124 ]; then fail "timed out after ${TIMEOUT}s (tune with CLAUDE_TIMEOUT)"
elif [ "$rc" -ne 0 ]; then fail "claude exit code $rc"; fi

[ -s "$OUT" ] || fail "empty reply (claude produced no final answer)"

# ---- mode gate (side-effect verification) ----
# GIT_OK is necessarily 1 here: the non-git case exits 2 in the preflight, before any reviewer call.
hjw_change_verdict

# ---- result ----
if [ "$FAILED" = 1 ]; then
  hjw_fail_header
  hjw_fail_tail
fi

hjw_log_coverage_note
echo "=== Claude reply ($OUT) — mode=$MODE, ${DUR}s, $MODEL_DISP ===$COVERAGE_NOTE"
cat "$OUT"
exit 0
