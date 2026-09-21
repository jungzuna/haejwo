#!/usr/bin/env bash
# codex_consult.sh — hardened headless Codex runner (haejwo's reviewer slot).
#
# What: feeds a self-contained brief to `codex exec` via stdin and captures
#       Codex's final reply to a file. Non-editing (consult) contract only —
#       project-agnostic — the invoking directory is the work root (run it
#       from the project root).
#
# Shape (2.13): the mechanics both reviewer runners share — parsing, paths,
#       the wall clock, --snapshot, change detection, config, cleanup — live
#       in `scripts/lib` (`consult_common.sh` + four python helpers). THIS
#       file owns everything vendor-specific: the REVIEWER CONTRACT text, the
#       `codex exec` argv, effort/sandbox, the event-stream classifier, the
#       model-unavailable retry, the events artifacts, and the non-git policy.
#       Two entrypoints, one set of mechanics, explicit vendor policy.
#
# Core design (production hardening): NEVER trust the exit code alone.
#   codex can fail silently with rc=0 (measured on sandbox-constrained hosts).
#   Any of {rc!=0 | empty reply | codex-reported failure EVENT | missing event
#   stream | codex tracing error | repository changed | change detection
#   unavailable} exits non-zero. Stated as a guarantee, narrowed to what is
#   actually checked: rc=0 with no reply, a reported failure event, or a
#   changed repository is never reported as success; a failure the reviewer
#   reports only in prose is not detected.
#
# Classification provenance (2.11): failures are read from codex's own JSONL
#   event stream (`codex exec --json`), not from a grep over mixed stdout.
#   Only TOP-LEVEL {"type":"turn.failed"|"error"} objects count; nested item
#   text / aggregated command output is NEVER inspected, so a reviewer that
#   merely QUOTES an error string can no longer fail its own run. The old
#   unanchored marker grep over the mixed log was removed entirely.
#   *[origin: reviewer replies discussing sandbox/tool errors self-failed]*
#
# Every input the reviewer sees (initial run, the model fallback retry) is
# prefixed with a standing REVIEWER CONTRACT (below) that forbids
# edits/installs/config changes — enforced by instruction here, and
# backstopped by the post-run change-detection gate.
#
# Usage:
#   codex_consult.sh [--mode consult] [--snapshot] [-o out.md] brief.md
#   echo "..." | codex_consult.sh --mode consult -   # stdin brief (deleted on exit)
#
# Mode (safety gate):
#   consult   (only mode) non-editing contract with post-run change detection —
#             FAILS if the repository changed after the run (danger-full-access
#             cannot block edits — enforce in code). `--mode implement` was
#             removed in 2.10 (cross-vendor worker routing is a non-goal) —
#             use the standalone collab tool for manual implement runs.
#   --resume: removed in 2.13 — escalation and follow-up rounds use a NEW session
#   --snapshot  runs the reviewer in a DETACHED WORKTREE snapshot of this
#             repository instead of the working copy — see "Snapshot" below.
#
# Snapshot (--snapshot, 2.11): HEAD plus the NET uncommitted working-tree
#   changes (one `git diff --binary <SHA>` patch replayed with `git apply
#   --index` — staged and unstaged states are NOT reproduced separately) plus
#   untracked non-ignored files (sorted, first 2000; symlinks copied AS LINKS,
#   never followed). Ignored files are omitted, so dependencies and
#   configuration may be missing — the reviewer is told to report a missing
#   capability rather than install anything. Capture is NOT atomic: it is
#   bracketed by timestamps and followed by a drift re-check that REFUSES
#   ("original changed during capture — retry") rather than shipping a torn
#   snapshot. Any capture failure exits 2 with `snapshot unavailable: <reason>`
#   BEFORE the paid call, and nothing partial survives. This is isolation from
#   the working copy, NOT containment: the snapshot shares the repository's
#   `.git` metadata, and writes to the ORIGINAL working tree or to global
#   config during the run are invisible to the change-detection gate (which
#   runs inside the snapshot). Refused up front: unborn HEAD, unresolved merge
#   conflicts, gitlinks (submodules) and embedded untracked repositories —
#   none of which a plain worktree snapshot can reproduce honestly.
#
# Config (${CLAUDE_PLUGIN_DATA}/config.json, or the derived path — see
# CODEX_SANDBOX below for the exact resolution rules). Keys read here:
#   codex.consult_sandbox   sandbox for consult runs (read on ANY host)
#   codex.model             default reviewer model (env CODEX_MODEL wins)
#   codex.effort            default reviewer effort (env CODEX_EFFORT wins)
#   codex.fallback_model    model to retry with when codex rejects the
#                           requested model pre-execution
# HOST-RELATIVE reading: the `codex` block describes the reviewer of the HOST
# that owns the data dir. On a CODEX host (resolved config path under
# /.codex/) that reviewer is CLAUDE, so this runner IGNORES
# codex.model/effort/fallback_model there and behaves as "no config" for them.
# *[origin: a live smoke launched the claude reviewer with the codex host's
# own model name]*
# NOT read here: the `efforts_codex` / `models_codex` config keys belong to
# codex-HOST worker tiers (spawn_agent parameters) — they never select this
# reviewer's model or effort.
#
# Env (env > config > default; an EMPTY env value counts as UNSET):
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
#   CODEX_EFFORT    low|medium|high (runner default)|xhigh — scale to the
#                   decision's stakes; xhigh for the hardest calls only, low
#                   for probes. An invalid ENV value EXITS 2 (caller input);
#                   an invalid CONFIG value notes and falls back to high.
#   CODEX_MODEL     force a specific reviewer model (optional; overrides
#                   config `codex.model`). Fixed for the whole consult
#                   session; escalation is always a NEW session. If codex
#                   pre-execution-rejects this model as unknown/unavailable,
#                   this script retries ONCE (with `codex.fallback_model` if
#                   configured, else the CLI default) and marks the reply —
#                   never retried twice, never persisted.
#   CODEX_TIMEOUT   seconds; default by effort (150/300/600/1200). 0 = unlimited.
#   CODEX_ALLOW_MARKERS=1  disables ONLY the stderr tracing-error scan
#                   (anchored `<ISO8601>Z ERROR codex_core` lines); the
#                   event-stream classifier always stays on.
#
# Disclosure discipline: every model/effort value is printed with its SOURCE
#   (env | config | runner-default | cli-default). An unselected model is
#   `cli-default (identity unverified)` — the runner does not know which
#   model answered.
#
# Change detection (scope, honestly): HEAD, tracked file status AND per-path
#   working-tree fingerprints, `git diff` / `git diff --cached` digests, and
#   the CONTENTS of untracked files (sorted, first 2000; presence is covered
#   for all of them). Runner-owned artifacts are excluded from the status,
#   untracked and diff-digest inputs alike. NOT covered: global/user config,
#   ignored files, anything outside the repo. Concurrent writers are not
#   distinguished — a detected change means "something changed", never "the
#   reviewer did it". Every helper runs under a python3-enforced wall clock
#   (no dependency on the `timeout` binary); any git/helper error or timeout
#   FAILS the run (fail closed), and a BEFORE-snapshot failure — including a
#   git probe that cannot tell us whether this is a repo — fails BEFORE codex
#   is invoked, so an unverifiable run is never paid for. Files this gate
#   cannot read are counted and disclosed, never skipped silently.
#
# Artifact naming rule: $LOG is derived from $OUT, so `-o x.log` would make
# the two the SAME file and the runner's own log would overwrite the reply it
# just captured. When that collision happens the log takes `$OUT.log` instead.
# Applies in every mode — the hazard predates --snapshot.
# *[origin: ship review Z4]*
#
# Verification discipline: this is a READ-ONLY reviewer slot — never trust it
#   to have made changes; workers implement, this only analyzes and replies.
# Waiting discipline: run in the background and wait for ONE completion event —
#   no sleep/pgrep polling loops.
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
HJW_RUNNER_KIND=codex

# REVIEWER CONTRACT: prepended to every brief this script sends to codex, on
# every input path (initial run, model-fallback retry). Durable owner policy
# — not brief-specific, do not let callers override it. Entrypoint-owned: the
# shared library never invents a vendor's standing instructions.
REVIEWER_CONTRACT='REVIEWER CONTRACT: analyze and reply only. Do NOT modify files, install
anything, or change any configuration (packages, MCP servers, global or
user settings). If you need a missing capability, STATE THE NEED in your
reply — the host decides.
---'

print_help() {
  cat <<'EOF'
codex_consult.sh — feed a self-contained brief to codex exec; capture reply.

Usage:
  codex_consult.sh [--mode consult] [--snapshot] [-o out.md] brief.md
  echo "..." | codex_consult.sh --mode consult -

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

Env (env > config > default; empty env value = unset): CODEX_SANDBOX,
  CODEX_EFFORT (runner default high), CODEX_MODEL, CODEX_TIMEOUT (default by
  effort), CODEX_ALLOW_MARKERS=1 (disables ONLY the stderr tracing-error scan;
  the event-stream classifier stays on).

Config keys (codex.consult_sandbox, codex.model, codex.effort,
  codex.fallback_model) are read from the plugin data config.json; env wins.
  model/effort/fallback_model are IGNORED when the config path is a codex
  host's (under /.codex/) — there the codex block describes Claude, not this
  reviewer.

Exit code: non-zero on ANY of {codex rc!=0, empty reply, codex failure event,
  missing event stream, codex tracing error, repository changed, change
  detection unavailable}. Guarantee, narrowed to what is actually checked:
  rc=0 with no reply, a reported failure event, or a changed repository is
  never reported as success; a failure the reviewer reports only in prose is
  not detected.
EOF
}

# ---- argument parsing, shared state, traps, $OUT/$LOG ----
hjw_parse_args "$@"
hjw_common_init
EVENTS="${OUT%.*}.events.jsonl"
EVENTS2="${OUT%.*}.events.2.jsonl"
# Under --snapshot the reviewer's working root becomes the snapshot, so the
# caller paths are resolved before anything chdirs. The events artifacts are
# this runner's alone — claude_consult.sh has no event stream to resolve.
if [ "$SNAPSHOT" = 1 ]; then
  hjw_canonicalize BRIEF OUT LOG EVENTS EVENTS2
fi

# ---- effective brief: REVIEWER CONTRACT + blank line + original brief ----
EFFECTIVE_BRIEF="$(mktemp "${TMPDIR:-/tmp}/codex_effective.XXXXXX.md")" || {
  echo "cannot create a temp file (is ${TMPDIR:-/tmp} writable?)" >&2; exit 4; }
{ printf '%s\n\n' "$REVIEWER_CONTRACT"; cat "$BRIEF"; } > "$EFFECTIVE_BRIEF"

# ---- config ----
hjw_config_load

# ---- sandbox: env CODEX_SANDBOX > config codex.consult_sandbox > read-only ----
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
  CFG_SANDBOX="$(hjw_config_sandbox)"
  case "$CFG_SANDBOX" in
    read-only|workspace-write|danger-full-access) SANDBOX="$CFG_SANDBOX" ;;
    *) SANDBOX="read-only" ;;
  esac
fi

# ---- model: env CODEX_MODEL > config codex.model > CLI default ----
# A whitespace-only value counts as UNSET on both paths: an empty env var is
# an absent setting, not a request for a model named "".
ENV_MODEL="$(trim "${CODEX_MODEL:-}")"
if [ -n "$ENV_MODEL" ]; then
  MODEL="$ENV_MODEL"; MODEL_SRC="env"
elif [ -n "$CFG_MODEL" ]; then
  MODEL="$CFG_MODEL"; MODEL_SRC="config"
else
  MODEL=""; MODEL_SRC="cli-default"
fi

# ---- effort: env CODEX_EFFORT > config codex.effort > runner default high ----
# Reviewer effort scales with the DECISION'S stakes, not a fixed pin (uniform
# max dilutes "spend budget where judgment compounds"). Invalid ENV value =
# caller input = loud exit 2 (same rule as CODEX_SANDBOX); invalid CONFIG
# value = one note + runner default, never a hard stop for a stale file.
ENV_EFFORT="$(trim "${CODEX_EFFORT:-}")"
if [ -n "$ENV_EFFORT" ]; then
  case "$ENV_EFFORT" in
    low|medium|high|xhigh) EFFORT="$ENV_EFFORT"; EFFORT_SRC="env" ;;
    *)
      echo "invalid CODEX_EFFORT: $ENV_EFFORT (must be one of: low, medium, high, xhigh)" >&2
      exit 2
      ;;
  esac
elif [ -n "$CFG_EFFORT" ]; then
  case "$CFG_EFFORT" in
    low|medium|high|xhigh) EFFORT="$CFG_EFFORT"; EFFORT_SRC="config" ;;
    *)
      echo "note: config codex.effort '$CFG_EFFORT' invalid; using runner-default high" >&2
      EFFORT="high"; EFFORT_SRC="runner-default"
      ;;
  esac
else
  EFFORT="high"; EFFORT_SRC="runner-default"
fi

case "$EFFORT" in low) DEF_TO=150;; medium) DEF_TO=300;; high) DEF_TO=600;; xhigh) DEF_TO=1200;; *) DEF_TO=600;; esac
TIMEOUT="${CODEX_TIMEOUT:-$DEF_TO}"

MODEL_FLAG=(); [ -n "$MODEL" ] && MODEL_FLAG=(-m "$MODEL")
EFFORT_FLAG=(-c "model_reasoning_effort=\"$EFFORT\"")

# ---- disclosure strings (value + WHERE it came from) ----
model_disp() {
  if [ -n "$1" ]; then printf 'model=%s (%s)' "$1" "$2"
  else printf 'model=cli-default (identity unverified)'; fi
}
MODEL_DISP="$(model_disp "$MODEL" "$MODEL_SRC")"
EFFORT_DISP="effort=$EFFORT ($EFFORT_SRC)"
SANDBOX_DISP="sandbox=$SANDBOX"

command -v codex >/dev/null 2>&1 || { echo "codex CLI not installed (check codex --version)" >&2; exit 3; }

{
  echo "# codex_consult v0.4  mode=$MODE $SANDBOX_DISP $MODEL_DISP $EFFORT_DISP timeout=${TIMEOUT}s  $(date 2>/dev/null)"
  printf '# codex '; bounded 20 codex --version 2>&1 | head -1
  echo "# ---- codex exec ----"
} > "$LOG"

# ---- change detection (A5): file-backed before/after snapshots ----
WORKDIR="$(pwd)"
# --snapshot: capture FIRST, then point everything downstream at the snapshot.
# Change detection, the reviewer's --cd and the artifact exclusions all read
# $WORKDIR, so the gate verifies the copy the reviewer actually saw. Writes to
# the ORIGINAL working tree (or to global config) during the run are invisible
# to it BY DESIGN — that is the price of isolation, disclosed in the brief.
if [ "$SNAPSHOT" = 1 ]; then
  # THIS runner's paths: a caller path inside the snapshot would be deleted
  # with it. The events artifacts exist only here.
  HJW_SNAP_GUARD=("$BRIEF" "$OUT" "$LOG" "$EVENTS" "$EVENTS2")
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
# Previous artifacts are cleared only once the snapshot is secured: a capture
# refusal must not destroy the reply/events of the caller's LAST run. $LOG
# cannot collide with them — the aliasing rule above already renamed it.
rm -f "$OUT" "$EVENTS" "$EVENTS2"
hjw_git_preflight
ARTIFACTS=("$OUT" "$LOG" "$EVENTS" "$EVENTS2" "$TMPBRIEF" "$EFFECTIVE_BRIEF")

if ! hjw_detect_before; then
  if [ "$SANDBOX" != read-only ]; then
    # Not a git repo AND the sandbox cannot block writes: nothing would verify
    # the no-edit contract for this run. Refuse BEFORE the paid call (exit 2,
    # like an unavailable snapshot) — this used to be a post-run failure that
    # still spent a reviewer call on a result it then discarded. A read-only
    # sandbox outside a repo stays allowed: the sandbox IS the enforcement.
    # *[origin 2026-09-21 audit item 3]*
    REFUSE_MSG="consult outside a git repo with sandbox=$SANDBOX (not read-only) — cannot verify the no-edit contract. Use read-only or run inside a git repo."
    printf '# ---- precondition refused: %s ----\n' "$REFUSE_MSG" >> "$LOG" 2>/dev/null
    echo "$REFUSE_MSG" >&2
    exit 2
  fi
fi

# ---- event-stream classifier (provenance: codex's own JSONL events) ----
CLS_PARSED=0; CLS_KNOWN=0; CLS_MALFORMED=0; CLS_MALFORMED_BEFORE=0
CLS_FAIL_TYPE=""; CLS_FAIL_MSG=""; CLS_MODEL_UNAVAIL=0; CLS_PRE_EXEC=0
classify_events() {
  # $1 = events file, $2 = requested model. Sets the CLS_* globals.
  CLS_PARSED=0; CLS_KNOWN=0; CLS_MALFORMED=0; CLS_MALFORMED_BEFORE=0
  CLS_FAIL_TYPE=""; CLS_FAIL_MSG=""; CLS_MODEL_UNAVAIL=0; CLS_PRE_EXEC=0
  local raw
  raw="$(bounded 60 python3 - "$1" "$2" <<'PY'
import json, re, sys

path = sys.argv[1]
requested = sys.argv[2] if len(sys.argv) > 2 else ""
# A stream of anonymous objects proves nothing ran: "present" requires at
# least one event codex actually emits.
KNOWN = {"thread.started", "turn.started", "turn.completed", "turn.failed",
         "item.started", "item.updated", "item.completed", "error"}
MODEL_ERR = re.compile(r"unknown model|model not found|not available|unsupported model", re.I)

parsed = known = malformed = malformed_before = 0
fail_type = fail_msg = ""
seen_item_started = False
pre_exec = 0
model_unavail = 0

try:
    fh = open(path, encoding="utf-8", errors="replace")
except Exception:
    fh = None
if fh is not None:
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                malformed += 1
                if not fail_type:
                    malformed_before += 1
                continue
            if not isinstance(ev, dict):
                malformed += 1
                if not fail_type:
                    malformed_before += 1
                continue
            parsed += 1
            etype = ev.get("type")
            if etype in KNOWN:
                known += 1
            if etype == "item.started":
                seen_item_started = True
            # Only TOP-LEVEL failure objects count. Nested item text and
            # aggregated command output are never inspected — a reviewer
            # quoting an error string must not fail its own run.
            if not fail_type and etype in ("turn.failed", "error"):
                fail_type = etype
                err = ev.get("error")
                if isinstance(err, dict) and isinstance(err.get("message"), str):
                    msg = err["message"]
                elif isinstance(ev.get("message"), str):
                    msg = ev["message"]
                else:
                    msg = line
                msg = " ".join(msg.split())[:300]
                fail_msg = msg
                pre_exec = 0 if seen_item_started else 1
                if requested and requested in msg and MODEL_ERR.search(msg):
                    model_unavail = 1

sys.stdout.write(
    "parsed=%d\nknown=%d\nmalformed=%d\nmalformed_before=%d\nfail_type=%s\n"
    "pre_exec=%d\nmodel_unavail=%d\nfail_msg=%s\n"
    % (parsed, known, malformed, malformed_before, fail_type, pre_exec,
       model_unavail, fail_msg))
PY
)"
  CLS_PARSED="$(printf '%s\n' "$raw" | sed -n 's/^parsed=//p')"
  CLS_KNOWN="$(printf '%s\n' "$raw" | sed -n 's/^known=//p')"
  CLS_MALFORMED="$(printf '%s\n' "$raw" | sed -n 's/^malformed=//p')"
  CLS_MALFORMED_BEFORE="$(printf '%s\n' "$raw" | sed -n 's/^malformed_before=//p')"
  CLS_FAIL_TYPE="$(printf '%s\n' "$raw" | sed -n 's/^fail_type=//p')"
  CLS_PRE_EXEC="$(printf '%s\n' "$raw" | sed -n 's/^pre_exec=//p')"
  CLS_MODEL_UNAVAIL="$(printf '%s\n' "$raw" | sed -n 's/^model_unavail=//p')"
  CLS_FAIL_MSG="$(printf '%s\n' "$raw" | sed -n 's/^fail_msg=//p')"
  CLS_PARSED="${CLS_PARSED:-0}"; CLS_KNOWN="${CLS_KNOWN:-0}"
  CLS_MALFORMED="${CLS_MALFORMED:-0}"; CLS_MALFORMED_BEFORE="${CLS_MALFORMED_BEFORE:-0}"
  CLS_PRE_EXEC="${CLS_PRE_EXEC:-0}"; CLS_MODEL_UNAVAIL="${CLS_MODEL_UNAVAIL:-0}"
}

# ---- run ----
run_attempt() {
  # $1 = attempt number, $2 = events sink, rest = command to run.
  local n="$1" sink="$2"; shift 2
  {
    echo "# ---- attempt $n: $SANDBOX_DISP $MODEL_DISP $EFFORT_DISP ----"
    echo "# ---- attempt $n stderr ----"
  } >> "$LOG"
  if [ "$TIMEOUT" != 0 ]; then
    bounded "$TIMEOUT" "$@" < "$EFFECTIVE_BRIEF" > "$sink" 2>> "$LOG"
  else
    "$@" < "$EFFECTIVE_BRIEF" > "$sink" 2>> "$LOG"
  fi
}

echo "→ Codex (mode=$MODE, $SANDBOX_DISP, $MODEL_DISP, $EFFORT_DISP, timeout=${TIMEOUT}s, brief=$BRIEF$START_EXTRA) ..." >&2
START=$SECONDS
CUR_EVENTS="$EVENTS"
run_attempt 1 "$EVENTS" codex exec --json -s "$SANDBOX" --skip-git-repo-check --cd "$WORKDIR" "${MODEL_FLAG[@]}" "${EFFORT_FLAG[@]}" -o "$OUT" -
rc=$?
DUR=$((SECONDS - START))

# ---- model-unavailable fallback ----
# A PRE-EXECUTION rejection of the requested model retries ONCE, never twice,
# never persisted. Positive ID requires all four: rc!=0 (not a timeout), a
# TOP-LEVEL failure event whose message both matches a known unknown-model
# pattern AND names the requested model, NO item.started before it (proof
# execution had not begun), and NO malformed line before it (a dropped line
# could have carried the item.started that proof depends on). Ambiguous
# errors are left to the classifier — a retry after real work would
# double-charge and double-review.
MODEL_FALLBACK=0
FALLBACK_MODEL=""
classify_events "$CUR_EVENTS" "$MODEL"
if [ -n "$MODEL" ] && [ "$rc" -ne 0 ] && [ "$rc" -ne 124 ] \
   && [ "$CLS_MODEL_UNAVAIL" = 1 ] && [ "$CLS_PRE_EXEC" = 1 ] && [ "$CLS_MALFORMED_BEFORE" -eq 0 ]; then
  FALLBACK_MODEL="$CFG_FALLBACK_MODEL"
  FB_FLAG=(); [ -n "$FALLBACK_MODEL" ] && FB_FLAG=(-m "$FALLBACK_MODEL")
  if [ -n "$FALLBACK_MODEL" ]; then
    MODEL_DISP="$(model_disp "$FALLBACK_MODEL" "config fallback; requested '$MODEL' unavailable")"
    echo "⚠ model '$MODEL' rejected pre-execution — retrying ONCE with config fallback '$FALLBACK_MODEL' ..." >&2
  else
    MODEL_DISP="model=cli-default (identity unverified; requested '$MODEL' unavailable)"
    echo "⚠ model '$MODEL' rejected pre-execution — retrying ONCE with the CLI default ..." >&2
  fi
  rm -f "$OUT"
  START=$SECONDS
  run_attempt 2 "$EVENTS2" codex exec --json -s "$SANDBOX" --skip-git-repo-check --cd "$WORKDIR" "${FB_FLAG[@]}" "${EFFORT_FLAG[@]}" -o "$OUT" -
  rc=$?
  DUR=$((SECONDS - START))
  MODEL_FALLBACK=1
  CUR_EVENTS="$EVENTS2"
  # The first attempt's events/traces must never fail a successful retry:
  # reclassify against attempt 2 only.
  classify_events "$CUR_EVENTS" "$FALLBACK_MODEL"
fi

hjw_detect_after

# ---- failure classifier (never trust the exit code alone) ----
# Classification CONTINUES after an rc/empty failure so the diagnostics
# (which event, which trace line) survive into the report.

# (i) exit code
if [ "$rc" -eq 124 ]; then fail "timed out after ${TIMEOUT}s (tune with CODEX_TIMEOUT)"
elif [ "$rc" -ne 0 ]; then fail "codex exit code $rc"; fi

# (ii) empty reply
[ -s "$OUT" ] || fail "empty reply (codex produced no final answer)"

# (iii) codex's own failure events
echo "# ---- events: parsed=$CLS_PARSED known=$CLS_KNOWN malformed=$CLS_MALFORMED ($CUR_EVENTS) ----" >> "$LOG"
if [ -n "$CLS_FAIL_TYPE" ]; then
  fail "codex reported $CLS_FAIL_TYPE: $CLS_FAIL_MSG"
fi
if [ "$rc" -eq 0 ] && [ "$CLS_KNOWN" -eq 0 ]; then
  fail "no event stream (rc=0) — cannot verify the run"
fi

# (iv) tracing errors on THIS attempt's stderr only, ANCHORED at column 0.
# Indented lines, prose, and echoed file content cannot match by construction —
# that is the whole point: the old unanchored grep failed runs whose reply
# merely discussed an error. CODEX_ALLOW_MARKERS=1 disables ONLY this scan.
if [ "${CODEX_ALLOW_MARKERS:-0}" != 1 ]; then
  TRACE_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z[[:space:]]+ERROR[[:space:]]+codex_core'
  HOOK_BLOCK_RE='Command blocked by PreToolUse hook'
  TRACE_ALL="$(awk '/^# ---- attempt [0-9]+ stderr ----$/ { buf=""; next } { buf = buf $0 "\n" } END { printf "%s", buf }' "$LOG" 2>/dev/null | grep -E "$TRACE_RE")"
  if [ -n "$TRACE_ALL" ]; then
    # A hook denying a command the REVIEWER tried to run is the gate doing
    # its job on the reviewer's side — not a codex failure, and not grounds
    # to throw away a complete reply. Record it, note it once, keep going.
    # Observed live 2026-09-14. Every OTHER anchored tracing error still fails.
    HOOK_BLOCKED="$(printf '%s\n' "$TRACE_ALL" | grep -F "$HOOK_BLOCK_RE")"
    TRACE_LINE="$(printf '%s\n' "$TRACE_ALL" | grep -vF "$HOOK_BLOCK_RE" | grep -m1 -E "$TRACE_RE")"
    if [ -n "$HOOK_BLOCKED" ]; then
      printf '%s\n' "$HOOK_BLOCKED" | while IFS= read -r hb_line; do
        [ -n "$hb_line" ] && echo "# ---- hook-blocked reviewer command: $hb_line ----" >> "$LOG"
      done
      HOOK_BLOCK_NOTE="note: a reviewer command was blocked by a hook (see log)"
    fi
    if [ -n "$TRACE_LINE" ]; then
      fail "codex tracing error: $TRACE_LINE"
    fi
  fi
fi

# (v) change detection
hjw_change_verdict
# No else: outside a git repo only the read-only sandbox reaches this point
# (every other sandbox exits 2 in the preflight, before the paid call), and
# there the sandbox itself enforces the contract.

# ---- result ----
if [ "$FAILED" = 1 ]; then
  hjw_fail_header
  if [ "$MODEL_FALLBACK" = 1 ]; then
    if [ -n "$FALLBACK_MODEL" ]; then
      echo "  (note: requested model '$MODEL' was unavailable; retried with '$FALLBACK_MODEL' (config fallback), still failed)" >&2
    else
      echo "  (note: requested model '$MODEL' was unavailable; retried with the CLI default, still failed)" >&2
    fi
  fi
  hjw_fail_tail
fi

# Persist the fallback disclosure into $OUT itself (not just stdout) so a
# caller reading the reply FILE programmatically also sees WHICH model
# answered — stdout alone is lost to any redirection/capture of this script.
FALLBACK_NOTE=""
if [ "$MODEL_FALLBACK" = 1 ]; then
  if [ -n "$FALLBACK_MODEL" ]; then
    FALLBACK_NOTE="note: requested model '$MODEL' unavailable; reviewed by '$FALLBACK_MODEL' (config fallback)"
  else
    FALLBACK_NOTE="note: requested model '$MODEL' unavailable; reviewed by CLI default (identity unverified) (assurance downgraded)"
  fi
  { printf '%s\n' "$FALLBACK_NOTE"; cat "$OUT"; } > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
fi

hjw_log_coverage_note
# The snapshot worktree is removed BEFORE the success line: announcing a
# finished run while the snapshot still exists would be a lie about the run's
# state. A removal failure is still reported — after the reply, which is valid.
if [ -n "$SNAP" ]; then snapshot_cleanup; fi
echo "=== Codex reply ($OUT) — mode=$MODE, ${DUR}s, $MODEL_DISP, $EFFORT_DISP, $SANDBOX_DISP$RESULT_EXTRA ===$COVERAGE_NOTE"
[ -n "$FALLBACK_NOTE" ] && echo "$FALLBACK_NOTE"
[ -n "${HOOK_BLOCK_NOTE:-}" ] && echo "$HOOK_BLOCK_NOTE"
cat "$OUT"
if [ -n "$SNAP" ]; then
  report_snapshot_cleanup
  [ "$SNAP_CLEANUP_FAILED" = 1 ] && exit 1
fi
exit 0
