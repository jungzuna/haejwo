#!/usr/bin/env bash
# codex_consult.sh — headless Codex reviewer runner (haejwo's reviewer slot on a Claude host).
# Feeds REVIEWER CONTRACT + a brief to `codex exec` on stdin; consult only; cwd = work root; usage: --help.
# Shared mechanics live in lib/consult_common.sh; this file owns the vendor policy: contract text, `codex exec`
# argv, effort/sandbox, the JSONL event classifier, the events artifact and the non-git policy.
# NEVER trust the exit code alone: no reply, a failure event or a changed repo fails; prose-only failure is not detected.
# Config is host-relative: codex.model/effort are ignored under /.codex/ (the block describes Claude); `models_codex`
# never selects this reviewer. Each value is disclosed with its source; unselected = `cli-default (identity unverified)`.
# *[origin: reviewer replies discussing sandbox/tool errors self-failed — only TOP-LEVEL JSONL failure events count]*
# *[origin: a live smoke launched the claude reviewer with the codex host's own model name]*
# *[origin: `-o` naming a tracked file was silently overwritten — change detection excludes artifacts by design]*
# *[origin: ship review Z4]* — `-o x.log` would alias the log onto the reply; the log then takes `$OUT.log`.
set -uo pipefail
umask 077  # every artifact (reply, log, events, temp files) is private to the invoking user

# ---- shared internals ----
# $0 -> ABSOLUTE PHYSICAL path: realpath (the minimal-PATH fixture has no readlink), else python3, else $PWD.
# Every required file is checked before any artifact exists: an incomplete install never half-runs a paid review.
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
HJW_RUNNER_KIND=codex
# Declared BEFORE forwarding and init: the artifact guard derives from them, on a hop too.
HJW_OUT_SIBLINGS=(.events.jsonl)

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
codex_consult.sh — feed a self-contained brief to codex exec; capture reply.

Usage:
  codex_consult.sh [--mode consult] [-o out.md] brief.md
  echo "..." | codex_consult.sh --mode consult -

Mode:
  consult   (only mode) non-editing contract with post-run change detection; FAILS if the repository changed during the run.
  --resume: removed in 2.13 — escalation and follow-up rounds use a NEW session
  --snapshot: removed in 2.22 — review the live working copy; pause writes during the review, or review a worktree you prepared
  --mode implement: removed in 2.10 (cross-vendor worker routing is a non-goal)

Env (env > config > default; empty = unset): CODEX_SANDBOX, CODEX_EFFORT (default medium),
  CODEX_MODEL, CODEX_TIMEOUT (default 600s at every effort; 0 = unlimited).
Config keys codex.consult_sandbox, codex.model, codex.effort; env wins. model/effort are
  ignored under /.codex/ (there the codex block describes Claude).

Exit code: non-zero on ANY of {codex rc!=0, empty reply, codex failure event, missing event
  stream, codex tracing error, repository changed, change detection unavailable}; a failure
  the reviewer reports only in prose is not detected.
EOF
}

# ---- argument parsing, shared state, traps, $OUT/$LOG ----
hjw_parse_args "$@"
hjw_common_init
EVENTS="${OUT%.*}${HJW_OUT_SIBLINGS[0]}"

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
# ---- model: env CODEX_MODEL > config codex.model > CLI default (whitespace-only = UNSET on both paths) ----
ENV_MODEL="$(trim "${CODEX_MODEL:-}")"
if [ -n "$ENV_MODEL" ]; then
  MODEL="$ENV_MODEL"; MODEL_SRC="env"
elif [ -n "$CFG_MODEL" ]; then
  MODEL="$CFG_MODEL"; MODEL_SRC="config"
else
  MODEL=""; MODEL_SRC="cli-default"
fi

# ---- effort: env CODEX_EFFORT > config codex.effort > runner default medium ----
# Invalid ENV = caller input = exit 2 (as CODEX_SANDBOX); invalid CONFIG = one note + runner default.
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
      echo "note: config codex.effort '$CFG_EFFORT' invalid; using runner-default medium" >&2
      EFFORT="medium"; EFFORT_SRC="runner-default"
      ;;
  esac
else
  # Owner policy (2.14): medium; `high` for design/plan/diff rounds, `xhigh` for architecture/security/deadlock.
  EFFORT="medium"; EFFORT_SRC="runner-default"
fi

# Wall clock DECOUPLED from effort (2.14): the brief (how much to read) sets the need. CODEX_TIMEOUT overrides.
TIMEOUT="${CODEX_TIMEOUT:-600}"

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
  echo "# codex_consult  $HJW_PLUGIN_DISP  mode=$MODE $SANDBOX_DISP $MODEL_DISP $EFFORT_DISP timeout=${TIMEOUT}s $CFG_DISP  $(date 2>/dev/null)"
  printf '# codex '; bounded 20 codex --version 2>&1 | head -1
} > "$LOG"
# After the header write (which truncates $LOG), so the foreign-CLAUDE_PLUGIN_DATA note survives with the header.
hjw_config_disclose
echo "# ---- codex exec ----" >> "$LOG"

# ---- change detection (A5): file-backed before/after snapshots ----
WORKDIR="$(pwd)"
# $LOG cannot collide with the previous reply/events — the aliasing rule renamed it.
rm -f "$OUT" "$EVENTS"
hjw_git_preflight
ARTIFACTS=("$OUT" "$LOG" "$EVENTS" "$TMPBRIEF" "$EFFECTIVE_BRIEF")

if ! hjw_detect_before; then
  if [ "$SANDBOX" != read-only ]; then
    # Not a git repo AND the sandbox cannot block writes: refuse before the paid call. Read-only outside a
    # repo stays allowed: the sandbox IS the enforcement. *[origin 2026-09-21 audit item 3]*
    REFUSE_MSG="consult outside a git repo with sandbox=$SANDBOX (not read-only) — cannot verify the no-edit contract. Use read-only or run inside a git repo."
    printf '# ---- precondition refused: %s ----\n' "$REFUSE_MSG" >> "$LOG" 2>/dev/null
    echo "$REFUSE_MSG" >&2
    exit 2
  fi
fi

# ---- event-stream classifier (provenance: codex's own JSONL events) ----
CLS_PARSED=0; CLS_KNOWN=0; CLS_MALFORMED=0
CLS_FAIL_TYPE=""; CLS_FAIL_MSG=""; CLS_MODEL_UNAVAIL=0; CLS_PRE_EXEC=0
classify_events() {
  # $1 = events file, $2 = requested model. Sets the CLS_* globals.
  CLS_PARSED=0; CLS_KNOWN=0; CLS_MALFORMED=0
  CLS_FAIL_TYPE=""; CLS_FAIL_MSG=""; CLS_MODEL_UNAVAIL=0; CLS_PRE_EXEC=0
  local raw
  raw="$(bounded 60 python3 - "$1" "$2" <<'PY'
import json, re, sys

path = sys.argv[1]
requested = sys.argv[2] if len(sys.argv) > 2 else ""
# "present" requires one event codex actually emits: anonymous objects prove nothing ran.
KNOWN = {"thread.started", "turn.started", "turn.completed", "turn.failed",
         "item.started", "item.updated", "item.completed", "error"}
MODEL_ERR = re.compile(r"unknown model|model not found|not available|unsupported model", re.I)

parsed = known = malformed = 0
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
                continue
            if not isinstance(ev, dict):
                malformed += 1
                continue
            parsed += 1
            etype = ev.get("type")
            if etype in KNOWN:
                known += 1
            if etype == "item.started":
                seen_item_started = True
            # Only TOP-LEVEL failure objects count: a reviewer quoting an error string must not fail its own run.
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
    "parsed=%d\nknown=%d\nmalformed=%d\nfail_type=%s\n"
    "pre_exec=%d\nmodel_unavail=%d\nfail_msg=%s\n"
    % (parsed, known, malformed, fail_type, pre_exec, model_unavail, fail_msg))
PY
)"
  CLS_PARSED="$(printf '%s\n' "$raw" | sed -n 's/^parsed=//p')"
  CLS_KNOWN="$(printf '%s\n' "$raw" | sed -n 's/^known=//p')"
  CLS_MALFORMED="$(printf '%s\n' "$raw" | sed -n 's/^malformed=//p')"
  CLS_FAIL_TYPE="$(printf '%s\n' "$raw" | sed -n 's/^fail_type=//p')"
  CLS_PRE_EXEC="$(printf '%s\n' "$raw" | sed -n 's/^pre_exec=//p')"
  CLS_MODEL_UNAVAIL="$(printf '%s\n' "$raw" | sed -n 's/^model_unavail=//p')"
  CLS_FAIL_MSG="$(printf '%s\n' "$raw" | sed -n 's/^fail_msg=//p')"
  CLS_PARSED="${CLS_PARSED:-0}"; CLS_KNOWN="${CLS_KNOWN:-0}"
  CLS_MALFORMED="${CLS_MALFORMED:-0}"
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

echo "→ Codex (mode=$MODE, $SANDBOX_DISP, $MODEL_DISP, $EFFORT_DISP, timeout=${TIMEOUT}s, brief=$BRIEF) ..." >&2
START=$SECONDS
run_attempt 1 "$EVENTS" codex exec --json -s "$SANDBOX" --skip-git-repo-check --cd "$WORKDIR" "${MODEL_FLAG[@]}" "${EFFORT_FLAG[@]}" -o "$OUT" -
rc=$?
DUR=$((SECONDS - START))

# A pre-execution model rejection is not retried (2.22): it fails with codex's own error plus one hint.
classify_events "$EVENTS" "$MODEL"

hjw_detect_after

# ---- failure classifier (never trust the exit code alone) ----
# Classification CONTINUES after an rc/empty failure so the diagnostics survive into the report.

# (i) exit code
if [ "$rc" -eq 124 ]; then fail "timed out after ${TIMEOUT}s (tune with CODEX_TIMEOUT)"
elif [ "$rc" -ne 0 ]; then fail "codex exit code $rc"; fi

# (ii) empty reply
[ -s "$OUT" ] || fail "empty reply (codex produced no final answer)"

# (iii) codex's own failure events
echo "# ---- events: parsed=$CLS_PARSED known=$CLS_KNOWN malformed=$CLS_MALFORMED ($EVENTS) ----" >> "$LOG"
if [ -n "$CLS_FAIL_TYPE" ]; then
  fail "codex reported $CLS_FAIL_TYPE: $CLS_FAIL_MSG"
fi
if [ "$rc" -eq 0 ] && [ "$CLS_KNOWN" -eq 0 ]; then
  fail "no event stream (rc=0) — cannot verify the run"
fi

# (iv) tracing errors on THIS attempt's stderr, ANCHORED at column 0: prose and echoed content cannot match.
# Always runs. ROLLBACK TRIGGER: an anchored-scan failure on a COMPLETE, VALID reply.
TRACE_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z[[:space:]]+ERROR[[:space:]]+codex_core'
HOOK_BLOCK_RE='Command blocked by PreToolUse hook'
TRACE_ALL="$(awk '/^# ---- attempt [0-9]+ stderr ----$/ { buf=""; next } { buf = buf $0 "\n" } END { printf "%s", buf }' "$LOG" 2>/dev/null | grep -E "$TRACE_RE")"
if [ -n "$TRACE_ALL" ]; then
  # A hook denying a REVIEWER command is the gate working: note it, keep the reply (observed live 2026-09-14).
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

# (v) change detection
hjw_change_verdict
# No else: outside a git repo only the read-only sandbox gets here, and the sandbox enforces the contract.

# ---- result ----
if [ "$FAILED" = 1 ]; then
  hjw_fail_header
  if [ -n "$MODEL" ] && [ "$CLS_MODEL_UNAVAIL" = 1 ] && [ "$CLS_PRE_EXEC" = 1 ]; then
    echo "  hint: model '$MODEL' ($MODEL_SRC) was rejected before execution — set CODEX_MODEL or codex.model to an available model, or unset both for the CLI default" >&2
  fi
  hjw_fail_tail
fi

hjw_log_coverage_note
echo "=== Codex reply ($OUT) — mode=$MODE, ${DUR}s, $MODEL_DISP, $EFFORT_DISP, $SANDBOX_DISP ===$COVERAGE_NOTE"
[ -n "${HOOK_BLOCK_NOTE:-}" ] && echo "$HOOK_BLOCK_NOTE"
cat "$OUT"
exit 0
