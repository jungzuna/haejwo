#!/usr/bin/env bash
# claude_consult.sh — hardened headless Claude runner (haejwo's reviewer slot
# on a CODEX host). Mirror of codex_consult.sh: on a Codex host the
# different-model independent reviewer is Claude (principle 9 symmetry).
#
# Same contract as codex_consult.sh: feed a self-contained brief via stdin or
# file, capture the final reply, NEVER trust the exit code alone — any of
# {rc!=0 | empty reply | repository changed | change detection unavailable}
# exits non-zero.
#
# Direct edit tools (Edit/Write/NotebookEdit) are disabled via
# --disallowedTools on every run. Bash remains available to the reviewer
# (claude -p has no read-only sandbox concept) — covered by the post-run
# change detection below, not by tool blocking. That detection is not a
# security boundary; its exact scope is spelled out under "Change detection".
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
# Mode (safety gate — enforced via change detection, since `claude -p` runs
# with the invoking user's permissions and has no read-only sandbox):
#   consult   (only mode) non-editing contract with post-run change detection —
#             FAILS if the repository changed during the run.
#   --resume  continue the most recent Claude session in this directory
#             (message = stdin/brief; reply = stdout). NOTHING is passed on
#             this path — not even --model: the thread keeps its own model,
#             which is inherited and NOT verifiable from here.
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
#   CLAUDE_MODEL    force a model (passed as --model; optional). Ignored on
#                   --resume, which passes no flags at all.
#   CLAUDE_TIMEOUT  seconds; default 600. 0 = unlimited.
#
# Disclosure discipline: the model is always printed with its SOURCE
#   (env | config | cli-default | inherited). An unselected model is
#   `cli-default (identity unverified)` — the runner does not know which model
#   answered; a --resume run inherits and verifies nothing.
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
  consult   (only mode) non-editing contract with post-run change detection; FAILS if the repository changed during the run.
  --resume  continue the most recent session (multi-round memory); no flags
            are passed, so the model is inherited and unverifiable from here.

--mode implement was removed in 2.10 (cross-vendor worker routing is a
non-goal); use the standalone collab tool for manual implement runs.

Env (env > config > default; empty env value = unset): CLAUDE_MODEL,
  CLAUDE_TIMEOUT (default 600). Config key codex.model supplies the default
  model, and ONLY when the config path is a codex host's (under /.codex/) —
  elsewhere that key describes the other vendor's reviewer. Env wins.

Exit code: non-zero on ANY of {claude rc!=0, empty reply, repository changed,
  change detection unavailable}. A silent rc=0 failure is never reported as
  success.

Direct edit tools (Edit/Write/NotebookEdit) are disabled via
--disallowedTools; Bash remains available and is covered by post-run change
detection (HEAD + tracked status/fingerprints + untracked contents capped at
2000 files; global config and ignored files never covered; concurrent writers
are not distinguished), not by tool blocking.
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
# brief) is what's actually fed to claude on every input path. SNAPDIR holds
# the before/after change-detection snapshots (files, not shell variables — a
# 2000-entry untracked fingerprint set does not belong in argv/env). All
# removed on exit.
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
  BRIEF="$(mktemp "${TMPDIR:-/tmp}/claude_brief.XXXXXX.md")" || {
    echo "cannot create a temp file (is ${TMPDIR:-/tmp} writable?)" >&2; exit 4; }
  TMPBRIEF="$BRIEF"
  cat > "$BRIEF"
fi
[ -f "$BRIEF" ] || { echo "brief file not found: $BRIEF" >&2; exit 2; }

[ -z "$OUT" ] && OUT="${BRIEF%.md}.reply.md"
LOG="${OUT%.*}.log"

# ---- effective brief: REVIEWER CONTRACT + blank line + original brief ----
EFFECTIVE_BRIEF="$(mktemp "${TMPDIR:-/tmp}/claude_effective.XXXXXX.md")" || {
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

# ---- config (same path rules as codex_consult.sh) ----
config_file_path() {
  if [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
    printf '%s' "${CLAUDE_PLUGIN_DATA}/config.json"
  else
    local self_path
    self_path="$(realpath "$0" 2>/dev/null || printf '%s' "$0")"
    case "$self_path" in
      # ${HOME:-}: set -u safe; an empty $HOME yields a root-anchored path
      # that will not exist -> "no config".
      */.codex/*) printf '%s' "${HOME:-}/.codex/plugins/data/haejwo-haejwo/config.json" ;;
      *)          printf '%s' "${HOME:-}/.claude/plugins/data/haejwo-haejwo/config.json" ;;
    esac
  fi
}

CFG_PATH="$(config_file_path)"
CFG_IS_CODEX_HOST=0
case "$CFG_PATH" in */.codex/*) CFG_IS_CODEX_HOST=1 ;; esac

# One python3 call prints `model=`, `effort=`, `fallback_model=` and
# `ignored=` (keys present but not strings). Only the model line is used here
# — claude has no effort knob — but the helper stays identical to
# codex_consult.sh's so the two cannot drift.
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

CFG_MODEL=""
if [ "$CFG_IS_CODEX_HOST" = 1 ]; then
  CFG_VALUES="$(config_codex_values)"
  CFG_MODEL="$(printf '%s\n' "$CFG_VALUES" | sed -n 's/^model=//p')"
  CFG_IGNORED="$(printf '%s\n' "$CFG_VALUES" | sed -n 's/^ignored=//p')"
  case ",$CFG_IGNORED," in *,model,*) echo "note: config codex.model ignored (not a string)" >&2 ;; esac
fi

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
# --continue passes NOTHING: the thread keeps its own model, so passing
# --model here would silently contradict the thread it claims to continue.
if [ "$RESUME" = 1 ]; then
  MODEL_FLAG=()
  MODEL_DISP="model=inherited (unverified)"
fi

command -v claude >/dev/null 2>&1 || { echo "claude CLI not installed (check claude --version)" >&2; exit 3; }

rm -f "$OUT"
{
  echo "# claude_consult v1.2  mode=$MODE $MODEL_DISP timeout=${TIMEOUT}s  $(date 2>/dev/null)"
  printf '# claude '; bounded 20 claude --version 2>&1 | head -1
  echo "# ---- claude -p ----"
} > "$LOG"

# ---- change detection (A5): file-backed before/after snapshots ----
# Attribution is NOT established here — a concurrent formatter/hook/editor save
# produces the same signal as a reviewer edit, so the failure message says
# "attribution unknown" and never proposes automatic reversion.
WORKDIR="$(pwd)"
SNAPDIR="$(mktemp -d "${TMPDIR:-/tmp}/claude_snap.XXXXXX")" || SNAPDIR=""
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
ARTIFACTS=("$OUT" "$LOG" "$TMPBRIEF" "$EFFECTIVE_BRIEF")

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
  echo "✗ claude_consult FAILED (mode=$MODE, 0s):" >&2
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

# ---- run ----
echo "→ Claude (mode=$MODE, $MODEL_DISP, timeout=${TIMEOUT}s, resume=$RESUME, brief=$BRIEF) ..." >&2
RESUME_FLAG=(); [ "$RESUME" = 1 ] && RESUME_FLAG=(--continue)
# Direct edit tools disabled — the reviewer analyzes and replies only.
RUN=(claude -p "${RESUME_FLAG[@]}" "${MODEL_FLAG[@]}" --disallowedTools "Edit,Write,NotebookEdit")
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

if [ "$GIT_OK" = 1 ] && [ "$DETECT_OK" = 1 ]; then
  git_snapshot "$SNAPDIR/after.json" 2>>"$SNAPDIR/detect.err" || DETECT_OK=0
fi

# ---- failure classifier ----
FAILED=0; FAIL_MSG=""
fail() { FAILED=1; FAIL_MSG="${FAIL_MSG}  - $1"$'\n'; }

if [ "$rc" -eq 124 ]; then fail "timed out after ${TIMEOUT}s (tune with CLAUDE_TIMEOUT)"
elif [ "$rc" -ne 0 ]; then fail "claude exit code $rc"; fi

[ -s "$OUT" ] || fail "empty reply (claude produced no final answer)"

# ---- mode gate (side-effect verification) ----
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

[ -n "$COVERAGE_NOTE" ] && echo "# ---- change detection:$COVERAGE_NOTE ----" >> "$LOG"
echo "=== Claude reply ($OUT) — mode=$MODE, ${DUR}s, $MODEL_DISP ===$COVERAGE_NOTE"
cat "$OUT"
exit 0
