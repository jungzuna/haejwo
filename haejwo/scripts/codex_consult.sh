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

# REVIEWER CONTRACT: prepended to every brief this script sends to codex, on
# every input path (initial run, model-fallback retry). Durable owner policy
# — not brief-specific, do not let callers override it.
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

# ---- argument parsing ----
MODE=""
OUT=""
SNAPSHOT=0
while [ $# -gt 0 ]; do
  case "$1" in
    # --resume was removed in 2.13: implicit latest-thread selection
    # misroutes under concurrent sessions (the runner cannot tell which
    # thread is the caller's). Rejected during PARSING — before any CLI
    # call, snapshot capture or preflight work.
    # *[origin: cross-vendor decision round 2026-09-21]*
    --resume) echo "--resume was removed in 2.13 (implicit latest-thread selection misroutes under concurrent sessions); start a NEW session with a self-contained brief" >&2; exit 2 ;;
    --snapshot) SNAPSHOT=1; shift ;;
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
# --snapshot state. ORIG is the ORIGINAL repository root, SNAP the detached
# worktree, SNAPMETA the capture scratch dir (patch + computed disclosure).
# Who owns SNAP is never inferred from a marker this script wrote — cleanup
# asks git (`worktree list --porcelain`), so an interruption between `mktemp`
# and `worktree add` cannot leave the wrong removal strategy behind.
# Sentinel terminator for every path this script receives from python.
# Command substitution strips trailing newlines and a directory name may
# legally END in one, so the SENTINEL — not the shell — marks where a value
# stops. *[origin: ship review Z3/Z5 — lossless path transport]*
SNAP_META_END=$'\004'"__HJW_SNAP_END__"
META_VAL=""
strip_sentinel() {
  # Sets $META_VAL to $1 minus exactly one trailing sentinel; fails if the
  # sentinel is absent (a truncated value must never pass as a path).
  META_VAL=""
  case "$1" in
    *"$SNAP_META_END") META_VAL="${1%"$SNAP_META_END"}"; return 0 ;;
    *) return 1 ;;
  esac
}
ORIG=""
SNAP=""
SNAPMETA=""
SNAP_SHA=""
SNAP_TAG=""
SNAP_NOTE=""
SNAP_CLEANUP_DONE=0
SNAP_CLEANUP_FAILED=0
SNAP_CLEANUP_REPORTED=0
cleanup() {
  # Snapshot removal runs FIRST: it needs `bounded` (and therefore BOUNDED_PY),
  # which the very next lines delete. Guarded by $SNAP so the early exits above
  # — which run before snapshot_cleanup is even defined — stay silent.
  if [ -n "$SNAP" ]; then snapshot_cleanup; report_snapshot_cleanup; fi
  [ -n "$TMPBRIEF" ] && rm -f "$TMPBRIEF"
  [ -n "$EFFECTIVE_BRIEF" ] && rm -f "$EFFECTIVE_BRIEF"
  [ -n "$BOUNDED_PY" ] && rm -f "$BOUNDED_PY"
  [ -n "$SNAPMETA" ] && rm -rf "$SNAPMETA"
  [ -n "$SNAPDIR" ] && rm -rf "$SNAPDIR"
}
# Armed HERE — before the capture creates anything — so there is no window in
# which a snapshot exists with no trap to remove it.
trap cleanup EXIT
# A snapshot worktree must not outlive an interrupted run either; bash does not
# fire the EXIT trap for an uncaught INT/TERM, so catch both and exit through it.
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
if [ "$BRIEF" = "-" ]; then
  BRIEF="$(mktemp "${TMPDIR:-/tmp}/codex_brief.XXXXXX.md")" || {
    echo "cannot create a temp file (is ${TMPDIR:-/tmp} writable?)" >&2; exit 4; }
  TMPBRIEF="$BRIEF"
  cat > "$BRIEF"
fi
[ -f "$BRIEF" ] || { echo "brief file not found: $BRIEF" >&2; exit 2; }

[ -z "$OUT" ] && OUT="${BRIEF%.md}.reply.md"
LOG="${OUT%.*}.log"
# -o x.log would derive the SAME path for the log and the reply; give the log
# its own name so the runner never overwrites the answer it captured.
# *[origin: ship review Z4]*
[ "$LOG" = "$OUT" ] && LOG="$OUT.log"
EVENTS="${OUT%.*}.events.jsonl"
EVENTS2="${OUT%.*}.events.2.jsonl"

# Under --snapshot the reviewer's working root becomes the snapshot, so every
# caller path is resolved to an absolute one HERE, before anything chdirs: a
# relative brief or -o would otherwise be read from / written into a directory
# that is deleted on exit. Only the final component is left unresolved (the
# reply/log do not exist yet). Non-snapshot runs are untouched — they never
# chdir, so their relative paths keep meaning exactly what they always meant.
# *[origin: B8 snapshot spec 2a]*
canon_path() {
  # Terminated with the sentinel so a path ending in a newline survives the
  # command substitution below. *[origin: ship review Z3]*
  python3 -c 'import os, sys
p = os.path.abspath(sys.argv[1])
d, b = os.path.dirname(p), os.path.basename(p)
sys.stdout.write(os.path.join(os.path.realpath(d), b) + sys.argv[2])' "$1" "$SNAP_META_END"
}
if [ "$SNAPSHOT" = 1 ]; then
  for _v in BRIEF OUT LOG EVENTS EVENTS2; do
    _canon="$(canon_path "${!_v}")" || { echo "snapshot unavailable: cannot resolve $_v to an absolute path: ${!_v}" >&2; exit 2; }
    strip_sentinel "$_canon" || { echo "snapshot unavailable: cannot resolve $_v to an absolute path: ${!_v}" >&2; exit 2; }
    [ -n "$META_VAL" ] || { echo "snapshot unavailable: cannot resolve $_v to an absolute path: ${!_v}" >&2; exit 2; }
    printf -v "$_v" '%s' "$META_VAL"
  done
  unset _v _canon
  # Canonicalization can make two textually different paths the SAME file
  # (a symlinked directory); re-apply the log-aliasing rule on the resolved
  # pair. *[origin: ship review Z4]*
  [ "$LOG" = "$OUT" ] && LOG="$OUT.log"
fi

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

# ---- --snapshot: capture the repository into a detached worktree ----
# Every step's status is checked and EVERY failure refuses before the paid
# call: a snapshot that is silently incomplete is worse than no snapshot,
# because the reviewer's conclusions would be about a repository that never
# existed. Reasons are printed loudly and nothing partial survives (the
# EXIT/INT/TERM traps are armed above, BEFORE anything is created).
# *[origin: B8 snapshot spec 2 — fail closed and loud on capture]*
read_meta() {
  # Command substitution strips trailing newlines, so the capture writes every
  # value with a sentinel terminator and the SENTINEL — not the shell — marks
  # the end: a repository path may legally end in a newline and $(cat) alone
  # would silently corrupt it. The result lands in $META_VAL, never in a
  # substitution (which would strip it all over again).
  local raw
  META_VAL=""
  raw="$(cat "$1" 2>/dev/null)" || return 1
  case "$raw" in
    *"$SNAP_META_END") META_VAL="${raw%"$SNAP_META_END"}"; return 0 ;;
    *) return 1 ;;
  esac
}

snapshot_refuse() {
  local reason="$1"
  [ -n "$reason" ] || reason="capture failed (no reason recorded)"
  # Best effort into the log as well: a caller who keeps only $LOG must still
  # learn why the snapshot was refused.
  printf '# ---- snapshot unavailable: %s ----\n' "$reason" >> "$LOG" 2>/dev/null
  echo "snapshot unavailable: $reason" >&2
  exit 2
}

snapshot_read_or_refuse() {
  # $1 = meta file, $2 = what it holds (for the failure message).
  read_meta "$1" || snapshot_refuse "capture produced no $2"
}

snapshot_preflight() {
  # $1 = starting directory, $2 = meta dir. Writes `orig` and `sha`, or
  # `refuse`. Runs BEFORE anything is created, so a refusal here costs nothing.
  bounded 120 python3 - "$1" "$2" <<'PY'
import os, subprocess, sys

workdir, meta = sys.argv[1] or os.getcwd(), sys.argv[2]
SENT = "\x04__HJW_SNAP_END__"


def write_meta(name, value):
    # Sentinel-terminated so the shell can read the value back byte-exactly.
    try:
        with open(os.path.join(meta, name), "w", encoding="utf-8",
                  errors="surrogateescape") as f:
            f.write(value + SENT)
        return True
    except Exception:
        return False


def refuse(reason):
    if not write_meta("refuse", reason):
        # The meta dir itself is unwritable — say so on stderr rather than
        # exiting with a status the caller cannot explain.
        sys.stderr.write("snapshot capture: %s\n" % reason)
    sys.exit(3)


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True)


def detail(r):
    return r.stderr.decode("utf-8", "replace").strip() or ("rc=%d" % r.returncode)


r = git(workdir, "rev-parse", "--show-toplevel")
if r.returncode != 0:
    refuse("not a git repository (%s)" % detail(r))
# ONLY the trailing newline git appends — a directory name may legally end in
# a space, and .strip() would silently point every path elsewhere.
root = r.stdout.decode("utf-8", "surrogateescape")
if root.endswith("\n"):
    root = root[:-1]
if not root:
    refuse("git could not name the repository root")

r = git(root, "rev-parse", "--verify", "HEAD")
if r.returncode != 0:
    refuse("unborn HEAD — there is no commit to snapshot")
sha = r.stdout.decode("ascii", "replace").strip()

r = git(root, "diff", "--name-only", "--diff-filter=U")
if r.returncode != 0:
    refuse("cannot list unmerged paths (%s)" % detail(r))
if r.stdout.strip():
    refuse("unresolved merge conflicts in the working tree — resolve them first")

# A gitlink is a POINTER to another repository: `worktree add` recreates the
# pointer but never the submodule's contents, so the reviewer would read an
# empty directory and believe it.
for args in (("ls-tree", "-r", "HEAD", "-z"), ("ls-files", "-s", "-z")):
    r = git(root, *args)
    if r.returncode != 0:
        refuse("cannot inspect the tree/index (%s)" % detail(r))
    for entry in r.stdout.split(b"\0"):
        if entry.startswith(b"160000 "):
            refuse("gitlink (submodule) entry present — a worktree snapshot "
                   "cannot reproduce its contents")

# An embedded untracked repository is listed by git as a single directory and
# would be copied as an opaque blob (or not at all) — refuse instead of
# guessing which of the two the caller meant.
r = git(root, "ls-files", "--others", "--exclude-standard", "-z")
if r.returncode != 0:
    refuse("cannot list untracked files (%s)" % detail(r))
for raw in r.stdout.split(b"\0"):
    if not raw:
        continue
    rel = raw.decode("utf-8", "surrogateescape").rstrip("/")
    p = os.path.join(root, rel)
    # islink first: a symlink is copied AS A LINK and never followed, so a
    # repository behind one is never traversed by this capture.
    if not os.path.islink(p) and os.path.isdir(p) and os.path.exists(os.path.join(p, ".git")):
        refuse("embedded untracked repository: %s" % rel)

if not write_meta("orig", root) or not write_meta("sha", sha):
    refuse("cannot record the repository root and HEAD")
PY
}

snapshot_build() {
  # $1 = ORIG, $2 = SHA, $3 = SNAP, $4 = meta dir, rest = caller paths that
  # must NOT live inside the snapshot. Writes `tag`, `note` and `log`, or
  # `refuse`. Every git call and every write is status-checked.
  bounded 600 python3 - "$@" <<'PY'
import hashlib, os, shutil, subprocess, sys, time

root, sha, snap, meta = sys.argv[1:5]
guard = [p for p in sys.argv[5:] if p]
CAP = 2000
SENT = "\x04__HJW_SNAP_END__"


def write_meta(name, value, sentinel=True):
    try:
        with open(os.path.join(meta, name), "w", encoding="utf-8",
                  errors="surrogateescape") as f:
            f.write(value + SENT if sentinel else value)
        return True
    except Exception:
        return False


def refuse(reason):
    if not write_meta("refuse", reason):
        sys.stderr.write("snapshot capture: %s\n" % reason)
    sys.exit(3)


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True)


def detail(r):
    return r.stderr.decode("utf-8", "replace").strip() or ("rc=%d" % r.returncode)


def gitck(cwd, *args):
    # There is no "best effort" inside a capture: a git call whose status we
    # ignored would put an unknown amount of the repository into the snapshot.
    r = git(cwd, *args)
    if r.returncode != 0:
        refuse("git %s failed: %s" % (" ".join(args), detail(r)))
    return r.stdout


def under(child, parent):
    c, p = os.path.realpath(child), os.path.realpath(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


# The snapshot must never live inside the repository it copies: it would show
# up in the original's own status and in its own change detection.
if under(snap, root):
    refuse("the temp directory lies inside the repository (%s) — point TMPDIR "
           "outside it" % root)
for p in guard:
    if under(p, snap):
        refuse("a caller path lies inside the snapshot and would be deleted "
               "with it: %s" % p)

gitck(root, "worktree", "add", "--detach", snap, sha)


def stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def tracked_patch():
    # Working tree vs the captured commit: this reproduces the NET content,
    # NOT the separate staged/unstaged states (documented in the brief note).
    return gitck(root, "diff", "--binary", "--no-ext-diff", "--no-textconv", sha)


def untracked_list():
    raw = gitck(root, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted(x for x in raw.split(b"\0") if x)


def fingerprint(path_abs):
    if os.path.islink(path_abs):
        return "link:" + hashlib.sha1(
            os.readlink(path_abs).encode("utf-8", "surrogateescape")).hexdigest()
    h = hashlib.sha1()
    with open(path_abs, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


start = stamp()
patch = tracked_patch()
patch_path = os.path.join(meta, "capture.patch")
try:
    with open(patch_path, "wb") as f:
        f.write(patch)
except Exception as exc:
    refuse("cannot stage the working-tree patch: %s" % exc)
if patch:
    r = git(snap, "apply", "--binary", "--index", patch_path)
    if r.returncode != 0:
        refuse("cannot replay the working-tree patch into the snapshot: %s" % detail(r))
names = gitck(root, "diff", "--name-only", "--no-ext-diff", "--no-textconv", "-z", sha)
n_paths = len([x for x in names.split(b"\0") if x])

eligible = untracked_list()
n_eligible = len(eligible)
partial = n_eligible > CAP
selected = eligible[:CAP]
src_prints = []
dst_prints = []
for raw in selected:
    rel = raw.decode("utf-8", "surrogateescape")
    src = os.path.join(root, rel)
    dst = os.path.join(snap, rel)
    try:
        before = fingerprint(src)
    except Exception as exc:
        # NEVER downgraded to a coverage note: an unreadable file is a hole in
        # the snapshot, not a cap omission.
        refuse("cannot read untracked file %r: %s" % (rel, exc))
    try:
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.islink(src):
            # Copied AS A LINK: following it could pull in content from
            # outside the repository and silently widen the snapshot.
            if os.path.lexists(dst):
                os.remove(dst)
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
    except Exception as exc:
        refuse("cannot copy untracked file %r into the snapshot: %s" % (rel, exc))
    try:
        after = fingerprint(dst)
    except Exception as exc:
        refuse("cannot verify the copied file %r: %s" % (rel, exc))
    # The copy is not atomic either. Hashing only the SOURCE would certify
    # content the snapshot does not actually hold if a writer changed the file
    # mid-copy, so the destination is hashed too and the two must agree.
    if after != before:
        refuse("untracked file changed during capture: %s" % rel)
    src_prints.append((raw, before))
    dst_prints.append((raw, after))
end = stamp()

# The digest certifies what the snapshot HOLDS, so it is computed over the
# destination fingerprints.
digest = hashlib.sha256()
digest.update(sha.encode("ascii"))
digest.update(b"\0")
digest.update(patch)
digest.update(b"\0")
for raw, fp in sorted(dst_prints):
    digest.update(raw + b"\t" + fp.encode("ascii") + b"\n")
snapshot_digest = digest.hexdigest()

# Drift re-check: capture is a sequence of git calls, not an atomic operation,
# so a writer active in the original can tear it. Recompute from the ORIGINAL
# and compare against the pre-copy source fingerprints; REFUSE rather than
# review a state that never existed.
drifted = tracked_patch() != patch or untracked_list() != eligible
if not drifted:
    for raw, fp in src_prints:
        try:
            if fingerprint(os.path.join(root, raw.decode("utf-8", "surrogateescape"))) != fp:
                drifted = True
                break
        except Exception:
            drifted = True
            break
if drifted:
    refuse("original changed during capture — retry")

ignored_raw = gitck(root, "ls-files", "--others", "--ignored", "--exclude-standard",
                    "--directory", "-z")
n_ignored = len([x for x in ignored_raw.split(b"\0") if x])

tag = "snapshot=" + sha[:7]
if n_paths or selected:
    tag += "+dirty(%d,%d%s)" % (n_paths, len(selected), ",partial" if partial else "")

note = ("SNAPSHOT: this run executes in a detached snapshot of %s at %s — HEAD %s plus "
        "uncommitted changes captured between %s and %s (capture is not atomic). Paths "
        "under %s in the brief refer to the same files under %s; read the snapshot, never "
        "the original. Ignored untracked files are omitted (tracked files are included "
        "regardless of ignore rules), so dependencies and configuration may be missing: "
        "report a missing capability instead of installing anything or reading omitted "
        "files from the original. Preserved symlinks may resolve outside the snapshot; do "
        "not follow them. Do not write."
        % (root, snap, sha, start, end, root, snap))
if partial:
    note += (" Untracked coverage is partial (first %d of %d eligible files)."
             % (CAP, n_eligible))

log = [
    "# ---- snapshot: %s ----" % snap,
    "# snapshot origin: %s" % root,
    "# snapshot HEAD: %s" % sha,
    "# snapshot capture: %s .. %s (NOT atomic; drift re-checked)" % (start, end),
    "# snapshot patch: bytes=%d paths=%d (net working-tree content, not staged/unstaged states)"
    % (len(patch), n_paths),
    "# snapshot untracked: copied=%d eligible=%d cap=%d" % (len(selected), n_eligible, CAP),
    "# snapshot digest: sha256 %s" % snapshot_digest,
    "# snapshot omissions: ignored_entries=%d (directories collapsed) cap_omitted=%d unreadable=0"
    % (n_ignored, max(0, n_eligible - len(selected))),
]

# `log` is appended to $LOG verbatim, so it carries no sentinel.
if not write_meta("tag", tag) or not write_meta("note", note) \
        or not write_meta("log", "\n".join(log) + "\n", sentinel=False):
    refuse("cannot record the snapshot disclosure")
PY
}

snapshot_capture() {
  # Orchestrates the capture and leaves ORIG/SNAP/SNAP_TAG/SNAP_NOTE set.
  # Refuses (exit 2) on ANY failure — never returns a partial snapshot.
  SNAPMETA="$(mktemp -d "${TMPDIR:-/tmp}/hjw_snapmeta.XXXXXX")" || \
    snapshot_refuse "cannot create a temp directory (is ${TMPDIR:-/tmp} writable?)"
  # "" = use python's own os.getcwd(): $(pwd) would have stripped a trailing
  # newline from a directory name that legally carries one.
  # *[origin: ship review Z3]*
  snapshot_preflight "" "$SNAPMETA"
  if [ $? -ne 0 ]; then
    read_meta "$SNAPMETA/refuse" || META_VAL=""
    snapshot_refuse "$META_VAL"
  fi
  snapshot_read_or_refuse "$SNAPMETA/orig" "repository root"; ORIG="$META_VAL"
  snapshot_read_or_refuse "$SNAPMETA/sha" "HEAD"; SNAP_SHA="$META_VAL"
  [ -n "$ORIG" ] && [ -n "$SNAP_SHA" ] || snapshot_refuse "capture produced no origin/HEAD"
  SNAP="$(mktemp -d "${TMPDIR:-/tmp}/hjw_snap.XXXXXX")" || \
    snapshot_refuse "cannot create a temp directory (is ${TMPDIR:-/tmp} writable?)"
  snapshot_build "$ORIG" "$SNAP_SHA" "$SNAP" "$SNAPMETA" \
    "$BRIEF" "$OUT" "$LOG" "$EVENTS" "$EVENTS2"
  if [ $? -ne 0 ]; then
    # A failure AFTER `worktree add` still leaves a worktree behind; the EXIT
    # trap reconciles that against git's own bookkeeping, not a marker file.
    read_meta "$SNAPMETA/refuse" || META_VAL=""
    snapshot_refuse "$META_VAL"
  fi
  snapshot_read_or_refuse "$SNAPMETA/tag" "disclosure tag"; SNAP_TAG="$META_VAL"
  snapshot_read_or_refuse "$SNAPMETA/note" "brief note"; SNAP_NOTE="$META_VAL"
  [ -n "$SNAP_TAG" ] && [ -n "$SNAP_NOTE" ] || snapshot_refuse "capture produced no disclosure"
  cat "$SNAPMETA/log" >> "$LOG" || \
    snapshot_refuse "cannot append the snapshot record to the log: $LOG"
  # The reviewer is told WHERE it is running and what the snapshot omits,
  # between the standing contract and the caller's brief. A brief the reviewer
  # would read without that note is worse than no run at all.
  # && between the three writes: a redirection that succeeds while a later
  # write fails would hand the reviewer a brief with the contract but no
  # snapshot note — worse than no run at all. *[origin: ship review Z2]*
  { printf '%s\n\n' "$REVIEWER_CONTRACT" && printf '%s\n\n' "$SNAP_NOTE" && cat "$BRIEF"; } \
    > "$EFFECTIVE_BRIEF" || \
    snapshot_refuse "cannot write the effective brief: $EFFECTIVE_BRIEF"
}

snapshot_registered() {
  # $1 = ORIG, $2 = candidate path. Runs the listing ITSELF: a NUL-delimited
  # porcelain stream cannot survive command substitution (bash drops NULs),
  # and the non-z form C-quotes exotic paths. git prints its OWN resolved
  # path, which may differ textually from the mktemp path this script holds,
  # so compare realpaths.
  # Exit 0 = registered, 1 = definitely NOT registered, anything else = could
  # not tell — and the caller treats "could not tell" as registered.
  # *[origin: ship review Z1]*
  bounded 60 python3 - "$1" "$2" <<'PY'
import os, subprocess, sys

orig, want = sys.argv[1], sys.argv[2]


def paths():
    # Preferred: NUL-delimited, never quoted (git >= 2.36).
    r = subprocess.run(["git", "-C", orig, "worktree", "list", "--porcelain", "-z"],
                       capture_output=True)
    if r.returncode == 0:
        return [f[len(b"worktree "):] for f in r.stdout.split(b"\0")
                if f.startswith(b"worktree ")]
    # Fallback for a git without -z. A C-quoted entry is one this format
    # cannot round-trip, so refuse to guess: raise and be treated as
    # "could not tell" (fail-safe = registered).
    r = subprocess.run(["git", "-C", orig, "worktree", "list", "--porcelain"],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("worktree list rc=%d" % r.returncode)
    out = []
    for line in r.stdout.split(b"\n"):
        if not line.startswith(b"worktree "):
            continue
        value = line[len(b"worktree "):]
        if value.startswith(b'"'):
            raise RuntimeError("C-quoted worktree path — cannot parse safely")
        out.append(value)
    return out


try:
    target = os.path.realpath(want)
    for raw in paths():
        if os.path.realpath(raw.decode("utf-8", "surrogateescape")) == target:
            sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(2)
PY
}

snapshot_cleanup() {
  # Ownership is reconciled against git's OWN bookkeeping rather than a marker
  # this script wrote: a registered worktree may be removed only by git, and
  # anything else is safe to delete only when we asked mktemp to create it
  # under the system temp root. A repo-wide `worktree prune` is NEVER run — a
  # concurrent worktree of the same repository is none of this runner's
  # business.
  [ "$SNAP_CLEANUP_DONE" = 1 ] && return 0
  SNAP_CLEANUP_DONE=1
  [ -n "$SNAP" ] || return 0
  # Leave the snapshot before removing it: git refuses to remove a worktree
  # that is the current directory, and this runner may have chdir'd into it.
  [ -n "$ORIG" ] && cd "$ORIG" 2>/dev/null
  local rrc out
  snapshot_registered "$ORIG" "$SNAP"
  rrc=$?
  # ONLY a definite "not registered" (rc 1) may take the direct path. A
  # listing we could not read or parse counts as REGISTERED: attempting the
  # git removal and reporting its failure beats rm -rf'ing a live worktree.
  if [ "$rrc" -ne 1 ]; then
    # Captured into a variable, NEVER redirected into $LOG: an unwritable log
    # must not be misreported as a cleanup failure.
    out="$(bounded 60 git -C "$ORIG" worktree remove --force "$SNAP" 2>&1)" || SNAP_CLEANUP_FAILED=1
    [ -n "$out" ] && printf '# ---- snapshot cleanup ----\n%s\n' "$out" >> "$LOG" 2>/dev/null
  else
    case "$SNAP" in
      # Only a directory this runner asked mktemp to create, and only its
      # result decides: a failed rm is a leftover like any other.
      "${TMPDIR:-/tmp}"/*) rm -rf "$SNAP" || SNAP_CLEANUP_FAILED=1 ;;
      *) SNAP_CLEANUP_FAILED=1 ;;  # not ours to delete — say it is left behind
    esac
  fi
  return 0
}

report_snapshot_cleanup() {
  # A leftover worktree is an operator problem (it pins objects and keeps a
  # stale entry in the repository), so it is never swallowed by a successful
  # review — but it is announced AFTER the reply, which is still valid.
  [ "$SNAP_CLEANUP_FAILED" = 1 ] || return 0
  [ "$SNAP_CLEANUP_REPORTED" = 1 ] && return 0
  SNAP_CLEANUP_REPORTED=1
  echo "snapshot cleanup failed: $SNAP" >&2
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

command -v codex >/dev/null 2>&1 || { echo "codex CLI not installed (check codex --version)" >&2; exit 3; }

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
# --snapshot: capture FIRST, then point everything downstream at the snapshot.
# Change detection, the reviewer's --cd and the artifact exclusions all read
# $WORKDIR, so the gate verifies the copy the reviewer actually saw. Writes to
# the ORIGINAL working tree (or to global config) during the run are invisible
# to it BY DESIGN — that is the price of isolation, disclosed in the brief.
START_EXTRA=""
RESULT_EXTRA=""
if [ "$SNAPSHOT" = 1 ]; then
  snapshot_capture
  WORKDIR="$SNAP"
  START_EXTRA=", $SNAP_TAG"
  RESULT_EXTRA=", $SNAP_TAG"
fi
# Previous artifacts are cleared only once the snapshot is secured: a capture
# refusal must not destroy the reply/events of the caller's LAST run. $LOG
# cannot collide with them — the aliasing rule above already renamed it.
rm -f "$OUT" "$EVENTS" "$EVENTS2"
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
  echo "✗ codex_consult FAILED (mode=$MODE, 0s${RESULT_EXTRA:-}):${COVERAGE_NOTE:-}" >&2
  echo "  - $DETECT_MSG" >&2
  [ -n "$SNAPDIR" ] && [ -s "$SNAPDIR/detect.err" ] && sed 's/^/  /' "$SNAPDIR/detect.err" >&2
  exit 1
}
if [ "$DETECT_OK" != 1 ]; then
  detect_fail_now
elif [ "$GIT_OK" = 1 ]; then
  git_snapshot "$SNAPDIR/before.json" 2>>"$SNAPDIR/detect.err" || DETECT_OK=0
  [ "$DETECT_OK" = 1 ] || detect_fail_now
elif [ "$SANDBOX" != read-only ]; then
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
fi
# No else: outside a git repo only the read-only sandbox reaches this point
# (every other sandbox exits 2 in the preflight, before the paid call), and
# there the sandbox itself enforces the contract.

# ---- result ----
if [ "$FAILED" = 1 ]; then
  echo "✗ codex_consult FAILED (mode=$MODE, ${DUR}s${RESULT_EXTRA:-}):${COVERAGE_NOTE:-}" >&2
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
