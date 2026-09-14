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
#   Any of {rc!=0 | empty reply | codex-reported failure EVENT | missing event
#   stream | codex tracing error | repository changed | change detection
#   unavailable} exits non-zero.
#
# Classification provenance (2.11): failures are read from codex's own JSONL
#   event stream (`codex exec --json`), not from a grep over mixed stdout.
#   Only TOP-LEVEL {"type":"turn.failed"|"error"} objects count; nested item
#   text / aggregated command output is NEVER inspected, so a reviewer that
#   merely QUOTES an error string can no longer fail its own run. The old
#   unanchored marker grep over the mixed log was removed entirely.
#   *[origin: reviewer replies discussing sandbox/tool errors self-failed]*
#
# Every input the reviewer sees (initial run, --resume, the model fallback
# retry) is prefixed with a standing REVIEWER CONTRACT (below) that forbids
# edits/installs/config changes — enforced by instruction here, and
# backstopped by the post-run change-detection gate.
#
# Usage:
#   codex_consult.sh [--mode consult] [--resume] [-o out.md] brief.md
#   echo "..." | codex_consult.sh --mode consult -   # stdin brief (deleted on exit)
#
# Mode (safety gate):
#   consult   (only mode) non-editing contract with post-run change detection —
#             FAILS if the repository changed after the run (danger-full-access
#             cannot block edits — enforce in code). `--mode implement` was
#             removed in 2.10 (cross-vendor worker routing is a non-goal) —
#             use the standalone collab tool for manual implement runs.
#   --resume  continues the most recent eligible codex thread at its original
#             model/effort/sandbox (nothing is passed); concurrent sessions can
#             select the wrong thread — prefer a NEW session with a summary
#             brief; escalation is always a new session.
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
#                   session — never swap models mid-thread on a --resume
#                   call; escalation is always a NEW session. If codex
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
#   (env | config | runner-default | cli-default | inherited). An unselected
#   model is `cli-default (identity unverified)` — the runner does not know
#   which model answered; a --resume run inherits and verifies nothing.
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
  consult   (only mode) non-editing contract with post-run change detection; FAILS if the repository changed during the run.
  --resume  continues the most recent eligible codex thread at its original
            model/effort/sandbox (nothing is passed); concurrent sessions can
            select the wrong thread — prefer a NEW session with a summary
            brief; escalation is always a new session.

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
  detection unavailable}. A silent rc=0 failure is never reported as success.
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
# codex on every input path; also deleted on exit. SNAPDIR holds the
# before/after change-detection snapshots (files, not shell variables — a
# 2000-entry untracked fingerprint set does not belong in argv/env).
TMPBRIEF=""
EFFECTIVE_BRIEF=""
SNAPDIR=""
BOUNDED_PY=""
cleanup() {
  [ -n "$TMPBRIEF" ] && rm -f "$TMPBRIEF"
  [ -n "$EFFECTIVE_BRIEF" ] && rm -f "$EFFECTIVE_BRIEF"
  [ -n "$BOUNDED_PY" ] && rm -f "$BOUNDED_PY"
  [ -n "$SNAPDIR" ] && rm -rf "$SNAPDIR"
}
trap cleanup EXIT
if [ "$BRIEF" = "-" ]; then
  BRIEF="$(mktemp "${TMPDIR:-/tmp}/codex_brief.XXXXXX.md")" || {
    echo "cannot create a temp file (is ${TMPDIR:-/tmp} writable?)" >&2; exit 4; }
  TMPBRIEF="$BRIEF"
  cat > "$BRIEF"
fi
[ -f "$BRIEF" ] || { echo "brief file not found: $BRIEF" >&2; exit 2; }

[ -z "$OUT" ] && OUT="${BRIEF%.md}.reply.md"
LOG="${OUT%.*}.log"
EVENTS="${OUT%.*}.events.jsonl"
EVENTS2="${OUT%.*}.events.2.jsonl"

# ---- effective brief: REVIEWER CONTRACT + blank line + original brief ----
EFFECTIVE_BRIEF="$(mktemp "${TMPDIR:-/tmp}/codex_effective.XXXXXX.md")" || {
  echo "cannot create a temp file (is ${TMPDIR:-/tmp} writable?)" >&2; exit 4; }
{ printf '%s\n\n' "$REVIEWER_CONTRACT"; cat "$BRIEF"; } > "$EFFECTIVE_BRIEF"

trim() { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

# Every helper (git, python, the CLI probes) and the reviewer call itself run
# under a wall clock: a hung helper must not hang the reviewer slot, and a
# timeout counts as "detection unavailable", never as "nothing changed".
# The bound is enforced by python3 (already a hard dependency) rather than the
# `timeout` binary — a host without coreutils' timeout must not silently get
# an UNBOUNDED run. *[origin: the `timeout`-present branch made the bound
# optional exactly where hangs are most likely]* Exit 124 on timeout matches
# timeout(1), which the failure classifier already reads.
BOUNDED_PY="$(mktemp "${TMPDIR:-/tmp}/hjw_bounded.XXXXXX.py")" || {
  echo "cannot create a temp file (is ${TMPDIR:-/tmp} writable?)" >&2; exit 4; }
cat > "$BOUNDED_PY" <<'PY'
import os, signal, subprocess, sys

try:
    secs = float(sys.argv[1])
except Exception:
    sys.exit(2)
cmd = sys.argv[2:]
if not cmd:
    sys.exit(2)
try:
    # Own session/process group: a CLI that spawns helpers must not leave
    # them running after the bound expires — killing only the direct child
    # leaks the expensive descendants, which is the whole cost this bound
    # exists to cap.
    child = subprocess.Popen(cmd, start_new_session=True)
except FileNotFoundError:
    sys.exit(127)
except Exception:
    sys.exit(126)
try:
    sys.exit(child.wait(timeout=secs))
except subprocess.TimeoutExpired:
    for sig, grace in ((signal.SIGTERM, 2), (signal.SIGKILL, 1)):
        try:
            os.killpg(child.pid, sig)
        except Exception:
            pass
        try:
            child.wait(timeout=grace)
            if sig is signal.SIGTERM:
                continue  # still sweep the group with SIGKILL
        except Exception:
            pass
    sys.exit(124)
except Exception:
    sys.exit(126)
PY
bounded() {
  local secs="$1"; shift
  python3 "$BOUNDED_PY" "$secs" "$@"
}

# ---- config ----
# Path resolution (shared by every config key below): CLAUDE_PLUGIN_DATA set
# (non-empty) means ONLY that path — a missing file there is "no config" and
# NEVER falls back to the derived path, which could resurrect a stale
# danger-full-access setting from elsewhere.
config_file_path() {
  if [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
    printf '%s' "${CLAUDE_PLUGIN_DATA}/config.json"
  else
    local self_path
    self_path="$(realpath "$0" 2>/dev/null || printf '%s' "$0")"
    case "$self_path" in
      # ${HOME:-}: set -u safe; an empty $HOME yields a root-anchored path
      # that will not exist, so [ -f "$cfg" ] treats it as "no config".
      */.codex/*) printf '%s' "${HOME:-}/.codex/plugins/data/haejwo-haejwo/config.json" ;;
      *)          printf '%s' "${HOME:-}/.claude/plugins/data/haejwo-haejwo/config.json" ;;
    esac
  fi
}

CFG_PATH="$(config_file_path)"
CFG_IS_CODEX_HOST=0
case "$CFG_PATH" in */.codex/*) CFG_IS_CODEX_HOST=1 ;; esac

# One python3 call prints `model=`, `effort=`, `fallback_model=` and
# `ignored=` (keys present but not strings). Empty when absent or when the
# `codex` value is not an object. Any parse failure is "no config" — a
# reviewer runner never guesses.
config_codex_values() {
  [ -n "$CFG_PATH" ] && [ -f "$CFG_PATH" ] || return 0
  bounded 60 python3 - "$CFG_PATH" <<'PY' 2>/dev/null
import json, sys

vals = {"model": "", "effort": "", "fallback_model": ""}
ignored = []
try:
    with open(sys.argv[1], encoding="utf-8-sig") as f:
        cfg = json.load(f)
    codex = cfg.get("codex") if isinstance(cfg, dict) else None
    if isinstance(codex, dict):
        for key in ("model", "effort", "fallback_model"):
            if key not in codex:
                continue
            v = codex.get(key)
            if isinstance(v, str):
                vals[key] = " ".join(v.split())
            else:
                ignored.append(key)
except Exception:
    vals = {"model": "", "effort": "", "fallback_model": ""}
    ignored = []
sys.stdout.write("model=%s\neffort=%s\nfallback_model=%s\nignored=%s\n"
                 % (vals["model"], vals["effort"], vals["fallback_model"], ",".join(ignored)))
PY
}

config_consult_sandbox() {
  [ -n "$CFG_PATH" ] && [ -f "$CFG_PATH" ] || return 0
  bounded 60 python3 - "$CFG_PATH" <<'PY' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8-sig") as f:
        cfg = json.load(f)
    codex = cfg.get("codex") if isinstance(cfg, dict) else None
    v = codex.get("consult_sandbox") if isinstance(codex, dict) else None
    if isinstance(v, str):
        sys.stdout.write(v)
except Exception:
    pass
PY
}

CFG_MODEL=""; CFG_EFFORT=""; CFG_FALLBACK_MODEL=""; CFG_IGNORED=""
if [ "$CFG_IS_CODEX_HOST" = 0 ]; then
  CFG_VALUES="$(config_codex_values)"
  CFG_MODEL="$(printf '%s\n' "$CFG_VALUES" | sed -n 's/^model=//p')"
  CFG_EFFORT="$(printf '%s\n' "$CFG_VALUES" | sed -n 's/^effort=//p')"
  CFG_FALLBACK_MODEL="$(printf '%s\n' "$CFG_VALUES" | sed -n 's/^fallback_model=//p')"
  CFG_IGNORED="$(printf '%s\n' "$CFG_VALUES" | sed -n 's/^ignored=//p')"
  if [ -n "$CFG_IGNORED" ]; then
    OLD_IFS="$IFS"; IFS=','
    for k in $CFG_IGNORED; do
      [ -n "$k" ] && echo "note: config codex.$k ignored (not a string)" >&2
    done
    IFS="$OLD_IFS"
  fi
fi

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
  CFG_SANDBOX="$(config_consult_sandbox)"
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
if [ "$RESUME" = 1 ]; then
  # resume passes no flags: model, effort and sandbox all come from the
  # original thread and this runner cannot verify any of them.
  MODEL_DISP="model=inherited (unverified)"
  EFFORT_DISP="effort=inherited (unverified)"
  SANDBOX_DISP="sandbox=inherited (unverified)"
fi

command -v codex >/dev/null 2>&1 || { echo "codex CLI not installed (check codex --version)" >&2; exit 3; }

rm -f "$OUT" "$EVENTS" "$EVENTS2"
{
  echo "# codex_consult v0.4  mode=$MODE $SANDBOX_DISP $MODEL_DISP $EFFORT_DISP timeout=${TIMEOUT}s  $(date 2>/dev/null)"
  printf '# codex '; bounded 20 codex --version 2>&1 | head -1
  echo "# ---- codex exec ----"
} > "$LOG"

# ---- change detection (A5): file-backed before/after snapshots ----
# Attribution is NOT established here — a concurrent formatter/hook/editor save
# produces the same signal as a reviewer edit, so the failure message says
# "attribution unknown" and never proposes automatic reversion.
WORKDIR="$(pwd)"
SNAPDIR="$(mktemp -d "${TMPDIR:-/tmp}/codex_snap.XXXXXX")" || SNAPDIR=""
DETECT_OK=1
[ -n "$SNAPDIR" ] || DETECT_OK=0
# The git probe has THREE outcomes, not two: inside a repo, genuinely not a
# repo (the documented non-git path), and "git could not tell us" — a broken
# repo, a permission error, a timeout. Only the middle one may proceed; the
# third must never be silently read as "not a repo, nothing to verify".
GIT_OK=0
GIT_PROBE_ERR="$(bounded 60 git -C "$WORKDIR" rev-parse --is-inside-work-tree 2>&1 >/dev/null)"
GIT_PROBE_RC=$?
if [ "$GIT_PROBE_RC" -eq 0 ]; then
  GIT_OK=1
else
  case "$GIT_PROBE_RC:$GIT_PROBE_ERR" in
    128:*"not a git repository"*) GIT_OK=0 ;;
    *)
      DETECT_OK=0
      [ -n "$SNAPDIR" ] && printf 'change detection: git rev-parse rc=%s: %s\n' \
        "$GIT_PROBE_RC" "$GIT_PROBE_ERR" >> "$SNAPDIR/detect.err"
      ;;
  esac
fi
DETECT_MSG="change detection unavailable (git error) — cannot verify the no-edit contract"
ARTIFACTS=("$OUT" "$LOG" "$EVENTS" "$EVENTS2" "$TMPBRIEF" "$EFFECTIVE_BRIEF")

git_snapshot() {
  # $1 = snapshot JSON file. Non-zero exit = detection unavailable (fail
  # closed). Everything is serialized as JSON: filenames may contain newlines
  # or tabs, so no line/tab-delimited format is safe here.
  bounded 60 python3 - "$WORKDIR" "$1" "${ARTIFACTS[@]}" <<'PY'
import hashlib, json, os, subprocess, sys

workdir, outfile = sys.argv[1], sys.argv[2]
artifacts = [p for p in sys.argv[3:] if p]

def git(*args):
    r = subprocess.run(["git", "-C", workdir] + list(args), capture_output=True)
    if r.returncode != 0:
        detail = r.stderr.decode("utf-8", "replace").strip() or ("rc=%d" % r.returncode)
        raise RuntimeError("git %s: %s" % (" ".join(args), detail))
    return r.stdout

def dec(b):
    return b.decode("utf-8", "surrogateescape")

unreadable = 0

def fingerprint(path_abs):
    global unreadable
    try:
        if os.path.islink(path_abs):
            return "link:" + hashlib.sha1(
                os.readlink(path_abs).encode("utf-8", "surrogateescape")).hexdigest()
        h = hashlib.sha1()
        with open(path_abs, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        unreadable += 1
        return "unreadable"

try:
    # Paths from git are repo-ROOT relative; resolve the root so exclusions
    # and pathspecs line up even when the runner is invoked from a subdir.
    # ONLY the trailing newline git appends — a directory name may legally
    # end in a space, and .strip() would silently point every path elsewhere.
    root = dec(git("rev-parse", "--show-toplevel"))
    if root.endswith("\n"):
        root = root[:-1]
    workdir = root
    root_real = os.path.realpath(root)

    excluded = set(os.path.realpath(p) for p in artifacts)
    rel_excludes = []
    for p in sorted(excluded):
        rel = os.path.relpath(p, root_real)
        if rel != ".." and not rel.startswith(".." + os.sep):
            rel_excludes.append(rel)

    def is_excluded(rel):
        return os.path.realpath(os.path.join(root_real, rel)) in excluded

    try:
        head = dec(git("rev-parse", "--verify", "HEAD")).strip()
    except RuntimeError:
        head = "unborn"  # a repo with no commits is not a git error

    # --untracked-files=all: a collapsed `newdir/` entry would hide which
    # files appeared, and runner artifacts written into a fresh directory
    # could not be excluded.
    status_raw = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    # Runner-owned artifacts are excluded from the diff digests by pathspec —
    # a reply file written over a TRACKED path would otherwise self-trip the
    # gate.
    pathspec = []
    if rel_excludes:
        pathspec = ["--", "."] + [":(exclude,literal)%s" % r for r in rel_excludes]
    diff_sha = hashlib.sha1(git(*(["diff"] + pathspec))).hexdigest()
    cached_sha = hashlib.sha1(git(*(["diff", "--cached"] + pathspec))).hexdigest()
    others_raw = git("ls-files", "--others", "--exclude-standard", "-z")

    entries = [e for e in status_raw.split(b"\0") if e]
    paths = {}
    i = 0
    while i < len(entries):
        e = dec(entries[i])
        xy, path = e[:2], e[3:]
        i += 1
        origin = None
        if xy and ("R" in xy or "C" in xy) and i < len(entries):
            origin = dec(entries[i])
            i += 1
        for p in (path, origin):
            if not p or is_excluded(p):
                continue
            if xy == "??":
                # presence only; contents live in the capped untracked map
                paths[p] = [xy, None]
            elif "D" in xy:
                paths[p] = [xy, "deleted"]
            else:
                paths[p] = [xy, fingerprint(os.path.join(root_real, p))]

    others = sorted(set(dec(p) for p in others_raw.split(b"\0") if p))
    others = [p for p in others if not is_excluded(p)]
    truncated = len(others) > 2000
    if truncated:
        others = others[:2000]
    untracked = dict((p, fingerprint(os.path.join(root_real, p))) for p in others)

    # Counted across tracked AND untracked entries: "some files are opaque to
    # this gate" is a different limitation from "the untracked list was cut".
    coverage = {"truncated": truncated, "unreadable": unreadable}

    snap = {"head": head, "paths": paths, "untracked": untracked,
            "diff": diff_sha, "cached": cached_sha, "coverage": coverage}
    os.makedirs(os.path.dirname(os.path.abspath(outfile)), exist_ok=True)
    with open(outfile, "w", encoding="ascii") as f:
        json.dump(snap, f, ensure_ascii=True)
except Exception as exc:
    sys.stderr.write("change detection: %s\n" % exc)
    sys.exit(1)
PY
}

snapshot_diff() {
  # $1 = before JSON, $2 = after JSON. Prints `coverage=<flags>` and
  # `changed=<list>`. A missing/unreadable/unparsable snapshot EXITS non-zero —
  # a read error must never be mistaken for "nothing changed".
  bounded 60 python3 - "$1" "$2" <<'PY'
import json, sys

REQUIRED = ("head", "paths", "untracked", "diff", "cached", "coverage")

def load(path):
    with open(path, encoding="ascii") as f:
        snap = json.load(f)
    if not isinstance(snap, dict):
        raise ValueError("snapshot is not an object: %s" % path)
    for key in REQUIRED:
        if key not in snap:
            raise KeyError("%s missing %r" % (path, key))
    if not isinstance(snap["coverage"], dict):
        raise ValueError("%s: coverage is not an object" % path)
    return snap

try:
    before, after = load(sys.argv[1]), load(sys.argv[2])
except Exception as exc:
    sys.stderr.write("change detection: unreadable snapshot: %s\n" % exc)
    sys.exit(2)

def esc(path):
    # Keep the failure message to ONE line: repr only when the path carries
    # characters that would break it.
    if any(ch in path for ch in "\n\r\t") or any(ord(ch) < 32 for ch in path):
        return repr(path)
    return path

items = []
if before["head"] != after["head"]:
    items.append("HEAD %s→%s" % (before["head"][:7], after["head"][:7]))

for key in ("paths", "untracked"):
    b, a = before[key], after[key]
    if not isinstance(b, dict) or not isinstance(a, dict):
        sys.stderr.write("change detection: malformed %s map\n" % key)
        sys.exit(2)
    for p in sorted(set(b) | set(a)):
        if b.get(p) != a.get(p):
            items.append(esc(p))

# Aggregate digests only name the fact that SOMETHING tracked changed; emit
# them only when no path-level item was found (they add nothing otherwise).
if not items:
    if before["diff"] != after["diff"]:
        items.append("tracked contents (git diff)")
    if before["cached"] != after["cached"]:
        items.append("staged contents (git diff --cached)")

seen, uniq = set(), []
for item in items:
    if item not in seen:
        seen.add(item)
        uniq.append(item)
if len(uniq) > 20:
    rest = len(uniq) - 20
    uniq = uniq[:20] + ["+%d more" % rest]

truncated = 0
unreadable = 0
for snap in (before, after):
    cov = snap["coverage"]
    if cov.get("truncated"):
        truncated = 1
    try:
        unreadable = max(unreadable, int(cov.get("unreadable") or 0))
    except Exception:
        pass

sys.stdout.write("truncated=%d\nunreadable=%d\nchanged=%s\n"
                 % (truncated, unreadable, ", ".join(uniq)))
PY
}

# Fail BEFORE spending a reviewer run: a consult whose no-edit contract
# cannot be verified is worthless, so never pay for it.
detect_fail_now() {
  # The log is opened BEFORE the preflight so git's own explanation survives
  # here too — a caller who only keeps $LOG must still learn why the gate
  # could not run.
  [ -n "$SNAPDIR" ] && [ -s "$SNAPDIR/detect.err" ] && { echo "# ---- change detection stderr ----"; cat "$SNAPDIR/detect.err"; } >> "$LOG"
  echo "✗ codex_consult FAILED (mode=$MODE, 0s):" >&2
  echo "  - $DETECT_MSG" >&2
  [ -n "$SNAPDIR" ] && [ -s "$SNAPDIR/detect.err" ] && sed 's/^/  /' "$SNAPDIR/detect.err" >&2
  exit 1
}
if [ "$DETECT_OK" != 1 ]; then
  detect_fail_now
elif [ "$GIT_OK" = 1 ]; then
  git_snapshot "$SNAPDIR/before.json" 2>>"$SNAPDIR/detect.err" || DETECT_OK=0
  [ "$DETECT_OK" = 1 ] || detect_fail_now
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
# Does this codex support `--json` / `-o` on the resume path? Probed, not
# assumed — an older CLI keeps the plain stdout capture (no event stream).
RESUME_JSON=0
if [ "$RESUME" = 1 ]; then
  RESUME_HELP="$(bounded 20 codex exec resume --help 2>&1)"
  case "$RESUME_HELP" in *--json*) case "$RESUME_HELP" in *-o,*|*"-o "*|*--output-last-message*) RESUME_JSON=1 ;; esac ;; esac
fi

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

echo "→ Codex (mode=$MODE, $SANDBOX_DISP, $MODEL_DISP, $EFFORT_DISP, timeout=${TIMEOUT}s, resume=$RESUME, brief=$BRIEF) ..." >&2
START=$SECONDS
EVENTS_MODE=1
CUR_EVENTS="$EVENTS"
if [ "$RESUME" = 1 ]; then
  # Persistent session: continue the latest codex thread (model/effort/sandbox
  # inherited from the original session — no flags). message=stdin.
  if [ "$RESUME_JSON" = 1 ]; then
    run_attempt 1 "$EVENTS" codex exec --skip-git-repo-check resume --last --json -o "$OUT"
  else
    EVENTS_MODE=0
    CUR_EVENTS=""
    run_attempt 1 "$OUT" codex exec --skip-git-repo-check resume --last
  fi
else
  run_attempt 1 "$EVENTS" codex exec --json -s "$SANDBOX" --skip-git-repo-check --cd "$WORKDIR" "${MODEL_FLAG[@]}" "${EFFORT_FLAG[@]}" -o "$OUT" -
fi
rc=$?
DUR=$((SECONDS - START))

# ---- model-unavailable fallback (non-resume only) ----
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
if [ "$EVENTS_MODE" = 1 ]; then
  classify_events "$CUR_EVENTS" "$MODEL"
fi
if [ "$RESUME" = 0 ] && [ -n "$MODEL" ] && [ "$rc" -ne 0 ] && [ "$rc" -ne 124 ] \
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

if [ "$GIT_OK" = 1 ] && [ "$DETECT_OK" = 1 ]; then
  git_snapshot "$SNAPDIR/after.json" 2>>"$SNAPDIR/detect.err" || DETECT_OK=0
fi

# ---- failure classifier (never trust the exit code alone) ----
# Classification CONTINUES after an rc/empty failure so the diagnostics
# (which event, which trace line) survive into the report.
FAILED=0; FAIL_MSG=""
fail() { FAILED=1; FAIL_MSG="${FAIL_MSG}  - $1"$'\n'; }

# (i) exit code
if [ "$rc" -eq 124 ]; then fail "timed out after ${TIMEOUT}s (tune with CODEX_TIMEOUT)"
elif [ "$rc" -ne 0 ]; then fail "codex exit code $rc"; fi

# (ii) empty reply
[ -s "$OUT" ] || fail "empty reply (codex produced no final answer)"

# (iii) codex's own failure events
if [ "$EVENTS_MODE" = 1 ]; then
  echo "# ---- events: parsed=$CLS_PARSED known=$CLS_KNOWN malformed=$CLS_MALFORMED ($CUR_EVENTS) ----" >> "$LOG"
  if [ -n "$CLS_FAIL_TYPE" ]; then
    fail "codex reported $CLS_FAIL_TYPE: $CLS_FAIL_MSG"
  fi
  if [ "$rc" -eq 0 ] && [ "$CLS_KNOWN" -eq 0 ]; then
    fail "no event stream (rc=0) — cannot verify the run"
  fi
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
COVERAGE_NOTE=""
if [ "$GIT_OK" = 1 ]; then
  CHANGED_LIST=""
  if [ "$DETECT_OK" = 1 ]; then
    CMP="$(snapshot_diff "$SNAPDIR/before.json" "$SNAPDIR/after.json" 2>>"$SNAPDIR/detect.err")"
    [ $? -eq 0 ] || DETECT_OK=0
  fi
  if [ "$DETECT_OK" != 1 ]; then
    # Surface WHY into the log — the failure message is a contract string, the
    # git error behind it is the diagnostic the operator actually needs.
    [ -s "$SNAPDIR/detect.err" ] && { echo "# ---- change detection stderr ----"; cat "$SNAPDIR/detect.err"; } >> "$LOG"
    fail "$DETECT_MSG"
  else
    TRUNCATED="$(printf '%s\n' "$CMP" | sed -n 's/^truncated=//p')"
    UNREADABLE="$(printf '%s\n' "$CMP" | sed -n 's/^unreadable=//p')"
    CHANGED_LIST="$(printf '%s\n' "$CMP" | sed -n 's/^changed=//p')"
    [ "${TRUNCATED:-0}" = 1 ] && COVERAGE_NOTE="$COVERAGE_NOTE (untracked coverage partial: >2000 files)"
    [ "${UNREADABLE:-0}" -gt 0 ] 2>/dev/null && COVERAGE_NOTE="$COVERAGE_NOTE (some files unreadable: $UNREADABLE)"
    if [ -n "$CHANGED_LIST" ]; then
      fail "repository changed during the run — attribution unknown (concurrent writers are not distinguished); changed: $CHANGED_LIST; inspect 'git status'"
    fi
  fi
else
  # Not a git repo: change detection unavailable.
  if [ "$SANDBOX" != read-only ]; then
    fail "consult outside a git repo with sandbox=$SANDBOX (not read-only) — cannot verify the no-edit contract. Use read-only or run inside a git repo."
  fi
fi

# ---- result ----
if [ "$FAILED" = 1 ]; then
  echo "✗ codex_consult FAILED (mode=$MODE, ${DUR}s):" >&2
  printf '%s' "$FAIL_MSG" >&2
  if [ "$MODEL_FALLBACK" = 1 ]; then
    if [ -n "$FALLBACK_MODEL" ]; then
      echo "  (note: requested model '$MODEL' was unavailable; retried with '$FALLBACK_MODEL' (config fallback), still failed)" >&2
    else
      echo "  (note: requested model '$MODEL' was unavailable; retried with the CLI default, still failed)" >&2
    fi
  fi
  echo "  --- last 12 log lines ($LOG) ---" >&2
  tail -12 "$LOG" >&2
  [ "$rc" -ne 0 ] && exit "$rc" || exit 1
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

[ -n "$COVERAGE_NOTE" ] && echo "# ---- change detection:$COVERAGE_NOTE ----" >> "$LOG"
echo "=== Codex reply ($OUT) — mode=$MODE, ${DUR}s, $MODEL_DISP, $EFFORT_DISP, $SANDBOX_DISP ===$COVERAGE_NOTE"
[ -n "$FALLBACK_NOTE" ] && echo "$FALLBACK_NOTE"
[ -n "${HOOK_BLOCK_NOTE:-}" ] && echo "$HOOK_BLOCK_NOTE"
cat "$OUT"
exit 0
