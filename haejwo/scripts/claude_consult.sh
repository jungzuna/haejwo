#!/usr/bin/env bash
# claude_consult.sh — hardened headless Claude runner (haejwo's reviewer slot
# on a CODEX host). Mirror of codex_consult.sh: on a Codex host the
# different-model independent reviewer is Claude (principle 9 symmetry).
#
# Shape (2.13): the mechanics both reviewer runners share — parsing, paths,
# the wall clock, --snapshot, change detection, config, cleanup — live in
# `scripts/lib` (`consult_common.sh` + four python helpers). THIS file owns
# everything vendor-specific: the REVIEWER CONTRACT text, the `claude -p`
# argv and its `--disallowedTools`, the chdir into the snapshot (claude has
# no --cd), the timeout default, and the non-git policy (no sandbox exists on
# this path, so a non-repo is always refused).
#
# Same contract as codex_consult.sh: feed a self-contained brief via stdin or
# file, capture the final reply, NEVER trust the exit code alone — any of
# {rc!=0 | empty reply | repository changed | change detection unavailable}
# exits non-zero. Stated as a guarantee, narrowed to what is actually checked:
# rc=0 with no reply or a changed repository is never reported as success;
# there is no event classifier on this path (codex_consult.sh has one); a
# failure the reviewer reports only in prose is not detected.
#
# Direct edit tools (Edit/Write/NotebookEdit) are disabled via
# --disallowedTools on every run. Bash remains available to the reviewer
# (claude -p has no read-only sandbox concept) — covered by the post-run
# change detection below, not by tool blocking. That detection is not a
# security boundary; its exact scope is spelled out under "Change detection".
#
# Every input the reviewer sees is prefixed with a standing REVIEWER CONTRACT
# (below) that forbids edits/installs/config changes — enforced by
# instruction, backstopped by --disallowedTools and the change-detection gate.
#
# Usage:
#   claude_consult.sh [--mode consult] [--snapshot] [-o out.md] brief.md
#   echo "..." | claude_consult.sh --mode consult -
#
# Mode (safety gate — enforced via change detection, since `claude -p` runs
# with the invoking user's permissions and has no read-only sandbox):
#   consult   (only mode) non-editing contract with post-run change detection —
#             FAILS if the repository changed during the run.
#   --resume: removed in 2.13 — escalation and follow-up rounds use a NEW session
#   --snapshot  runs the reviewer in a DETACHED WORKTREE snapshot of this
#             repository instead of the working copy — see "Snapshot" below.
#
# Snapshot (--snapshot, 2.11; identical capture to codex_consult.sh): HEAD plus
#   the NET uncommitted working-tree changes (one `git diff --binary <SHA>`
#   patch replayed with `git apply --index` — staged and unstaged states are
#   NOT reproduced separately) plus untracked non-ignored files (sorted, first
#   2000; symlinks copied AS LINKS, never followed). Ignored files are omitted,
#   so dependencies and configuration may be missing — the reviewer is told to
#   report a missing capability rather than install anything. Capture is NOT
#   atomic: it is bracketed by timestamps and followed by a drift re-check that
#   REFUSES ("original changed during capture — retry") rather than shipping a
#   torn snapshot. Any capture failure exits 2 with `snapshot unavailable:
#   <reason>` BEFORE the paid call, and nothing partial survives. This runner
#   `cd`s into the snapshot before invoking claude. It is isolation from the
#   working copy, NOT containment: the snapshot shares the repository's `.git`
#   metadata, and writes to the ORIGINAL working tree or to global config
#   during the run are invisible to the change-detection gate (which runs
#   inside the snapshot). Refused up front: unborn HEAD, unresolved merge
#   conflicts, gitlinks (submodules) and embedded untracked repositories.
#
# `--mode implement` was removed in 2.10 (cross-vendor worker routing is a
# non-goal); use the standalone collab tool for manual implement runs.
#
# Config (${CLAUDE_PLUGIN_DATA}/config.json, else the path derived from $0 —
# same resolution rules as codex_consult.sh: CLAUDE_PLUGIN_DATA set means ONLY
# that path, and a missing/unparsable file means NO config). Key read here:
#   codex.model   default reviewer model (env CLAUDE_MODEL wins)
# HOST-RELATIVE reading: the `codex` block describes the reviewer of the HOST
# that owns the data dir. This runner IS that reviewer only on a CODEX host,
# so codex.model is read ONLY when the resolved config path is under /.codex/;
# anywhere else it describes the OTHER vendor's reviewer and is ignored.
# *[origin: a live smoke launched claude with `--model gpt-6-astra`, the codex
# host's own reviewer model, read out of a claude-host config]*
# NOT read here: the `efforts_codex` / `models_codex` config keys belong to
# codex-HOST worker tiers (spawn_agent parameters) — they never select this
# reviewer's model.
#
# Env (env > config > default; an EMPTY env value counts as UNSET):
#   CLAUDE_MODEL    force a model (passed as --model; optional).
#   CLAUDE_TIMEOUT  seconds; default 600. 0 = unlimited.
#
# Disclosure discipline: the model is always printed with its SOURCE
#   (env | config | cli-default). An unselected model is `cli-default
#   (identity unverified)` — the runner does not know which model answered.
#
# Artifact naming rule: $LOG is derived from $OUT, so `-o x.log` would make
# the two the SAME file and the runner's own log would overwrite the reply it
# just captured. When that collision happens the log takes `$OUT.log` instead.
# Applies in every mode — the hazard predates --snapshot.
# *[origin: ship review Z4]*
#
# Change detection (scope, honestly): HEAD, tracked file status AND per-path
#   working-tree fingerprints, `git diff` / `git diff --cached` digests, and
#   the CONTENTS of untracked files (sorted, first 2000; presence is covered
#   for all of them). Runner-owned artifacts are excluded from the status,
#   untracked and diff-digest inputs alike. NOT covered: package installs,
#   MCP/user/global config changes, ignored files, anything outside the repo.
#   Concurrent writers are not distinguished — a detected change means
#   "something changed", never "the reviewer did it". Every helper runs under
#   a python3-enforced wall clock (no dependency on the `timeout` binary); any
#   git/helper error or timeout FAILS the run (fail closed), and a
#   BEFORE-snapshot failure — including a git probe that cannot tell us
#   whether this is a repo — fails BEFORE claude is invoked, so an
#   unverifiable run is never paid for. Files this gate cannot read are
#   counted and disclosed, never skipped silently.
set -uo pipefail

# ---- shared internals ----
# $0 is the only anchor a script has, and it must be resolved to an ABSOLUTE
# PHYSICAL path here: the runner may be reached through a symlink (resolve it,
# or `lib/` would be looked up next to the LINK) or relatively, and $HJW_LIB
# is used again AFTER the process has chdir'd — into the snapshot for the
# reviewer call, and back to $ORIG during cleanup. A relative $HJW_LIB would
# then be looked up in whichever directory the runner happened to land in.
# `realpath` is the resolver the minimal-PATH fixture provides (`readlink` is
# not on that list); when it is absent or fails, python3 — already a hard
# dependency — resolves it. The lexical $PWD form is a last resort that still
# yields an absolute path.
# EVERY required file is checked BEFORE any artifact exists: an incomplete
# install must fail loudly and cheaply, never half-run a paid review.
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
for _hjw_f in consult_common.sh bounded.py snapshot.py detect.py config.py; do
  [ -f "$HJW_LIB/$_hjw_f" ] || {
    echo "consult runner library missing: $HJW_LIB/$_hjw_f" >&2; exit 3; }
done
unset _hjw_f
# shellcheck source=lib/consult_common.sh
. "$HJW_LIB/consult_common.sh" || {
  echo "consult runner library missing: $HJW_LIB/consult_common.sh" >&2; exit 3; }
HJW_RUNNER_KIND=claude

# REVIEWER CONTRACT: prepended to every brief this script sends to claude, on
# every input path. Durable owner policy — not brief-specific, do not let
# callers override it. Entrypoint-owned: the shared library never invents a
# vendor's standing instructions.
REVIEWER_CONTRACT='REVIEWER CONTRACT: analyze and reply only. Do NOT modify files, install
anything, or change any configuration (packages, MCP servers, global or
user settings). If you need a missing capability, STATE THE NEED in your
reply — the host decides.
---'

print_help() {
  cat <<'EOF'
claude_consult.sh — feed a self-contained brief to headless claude; capture reply.

Usage:
  claude_consult.sh [--mode consult] [--snapshot] [-o out.md] brief.md
  echo "..." | claude_consult.sh --mode consult -

Mode:
  consult   (only mode) non-editing contract with post-run change detection; FAILS if the repository changed during the run.
  --resume: removed in 2.13 — escalation and follow-up rounds use a NEW session
  --snapshot  review a detached worktree snapshot (HEAD + net uncommitted
            changes + untracked non-ignored files, first 2000) instead of the
            live working copy. Ignored files are omitted; capture is not
            atomic (drift REFUSES); shared .git metadata means this is
            isolation, not containment.

--mode implement was removed in 2.10 (cross-vendor worker routing is a
non-goal); use the standalone collab tool for manual implement runs.

Env (env > config > default; empty env value = unset): CLAUDE_MODEL,
  CLAUDE_TIMEOUT (default 600). Config key codex.model supplies the default
  model, and ONLY when the config path is a codex host's (under /.codex/) —
  elsewhere that key describes the other vendor's reviewer. Env wins.

Exit code: non-zero on ANY of {claude rc!=0, empty reply, repository changed,
  change detection unavailable}. Guarantee, narrowed to what is actually
  checked: rc=0 with no reply or a changed repository is never reported as
  success; there is no event classifier on this path; a failure the reviewer
  reports only in prose is not detected.

Direct edit tools (Edit/Write/NotebookEdit) are disabled via
--disallowedTools; Bash remains available and is covered by post-run change
detection (HEAD + tracked status/fingerprints + untracked contents capped at
2000 files; global config and ignored files never covered; concurrent writers
are not distinguished), not by tool blocking.
EOF
}

# ---- argument parsing, shared state, traps, $OUT/$LOG ----
hjw_parse_args "$@"
hjw_common_init
# Under --snapshot this runner chdirs into the snapshot before invoking claude,
# so the caller paths are resolved first. There is no events stream on this
# path — codex_consult.sh owns those artifacts, this runner never names them.
if [ "$SNAPSHOT" = 1 ]; then
  hjw_canonicalize BRIEF OUT LOG
fi

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
  echo "# claude_consult v1.2  mode=$MODE $MODEL_DISP timeout=${TIMEOUT}s  $(date 2>/dev/null)"
  printf '# claude '; bounded 20 claude --version 2>&1 | head -1
  echo "# ---- claude -p ----"
} > "$LOG"

# ---- change detection (A5): file-backed before/after snapshots ----
WORKDIR="$(pwd)"
# --snapshot: capture FIRST, then point everything downstream at the snapshot.
# Change detection, this runner's chdir and the artifact exclusions all read
# $WORKDIR, so the gate verifies the copy the reviewer actually saw. Writes to
# the ORIGINAL working tree (or to global config) during the run are invisible
# to it BY DESIGN — that is the price of isolation, disclosed in the brief.
if [ "$SNAPSHOT" = 1 ]; then
  # THIS runner's paths: a caller path inside the snapshot would be deleted
  # with it. No events artifacts exist on this path.
  HJW_SNAP_GUARD=("$BRIEF" "$OUT" "$LOG")
  snapshot_capture
  # The reviewer is told WHERE it is running and what the snapshot omits,
  # between the standing contract and the caller's brief. A brief the reviewer
  # would read without that note is worse than no run at all.
  # && between the three writes: a redirection that succeeds while a later
  # write fails would hand the reviewer a brief with the contract but no
  # snapshot note — worse than no run at all. *[origin: ship review Z2]*
  { printf '%s\n\n' "$REVIEWER_CONTRACT" && printf '%s\n\n' "$SNAP_NOTE" && cat "$BRIEF"; } \
    > "$EFFECTIVE_BRIEF" || \
    snapshot_refuse "cannot write the effective brief: $EFFECTIVE_BRIEF"
  WORKDIR="$SNAP"
  START_EXTRA=", $SNAP_TAG"
  RESULT_EXTRA=", $SNAP_TAG"
fi
# The previous reply is cleared only once the snapshot is secured: a capture
# refusal must not destroy the reply of the caller's LAST run. $LOG cannot
# collide with it — the aliasing rule above already renamed it.
rm -f "$OUT"
hjw_git_preflight
ARTIFACTS=("$OUT" "$LOG" "$TMPBRIEF" "$EFFECTIVE_BRIEF")

if ! hjw_detect_before; then
  # Not a git repo: there is no before-snapshot to take, so the no-edit
  # contract can never be verified for this run. Refuse BEFORE the paid call
  # (exit 2, like an unavailable snapshot) — this used to be a post-run
  # failure that still spent a reviewer call on a result it then discarded.
  # `claude -p` has no sandbox to fall back on, so there is no read-only
  # exception here: the codex runner's one is a vendor capability, not a
  # shared policy. *[origin 2026-09-21 audit item 3]*
  REFUSE_MSG="consult outside a git repo — cannot verify the no-edit contract (claude -p is unsandboxed). Run inside a git repo."
  printf '# ---- precondition refused: %s ----\n' "$REFUSE_MSG" >> "$LOG" 2>/dev/null
  echo "$REFUSE_MSG" >&2
  exit 2
fi

# ---- run ----
echo "→ Claude (mode=$MODE, $MODEL_DISP, timeout=${TIMEOUT}s, brief=$BRIEF$START_EXTRA) ..." >&2
# `claude -p` has no --cd: the working root IS the process cwd, so the runner
# moves into the snapshot itself. Every path used after this point ($BRIEF,
# $OUT, $LOG, $EFFECTIVE_BRIEF) was made absolute up front for exactly this.
if [ -n "$SNAP" ]; then
  cd "$SNAP" || snapshot_refuse "cannot enter the snapshot: $SNAP"
fi
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
# GIT_OK is necessarily 1 here: the non-git case exits 2 in the preflight
# above, before any reviewer call.
hjw_change_verdict

# ---- result ----
if [ "$FAILED" = 1 ]; then
  hjw_fail_header
  hjw_fail_tail
fi

hjw_log_coverage_note
# The snapshot worktree is removed BEFORE the success line: announcing a
# finished run while the snapshot still exists would be a lie about the run's
# state. A removal failure is still reported — after the reply, which is valid.
if [ -n "$SNAP" ]; then snapshot_cleanup; fi
echo "=== Claude reply ($OUT) — mode=$MODE, ${DUR}s, $MODEL_DISP$RESULT_EXTRA ===$COVERAGE_NOTE"
cat "$OUT"
if [ -n "$SNAP" ]; then
  report_snapshot_cleanup
  [ "$SNAP_CLEANUP_FAILED" = 1 ] && exit 1
fi
exit 0
