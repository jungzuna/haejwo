#!/usr/bin/env bash
# consult_common.sh — shared internals of haejwo's two reviewer runners
# (codex_consult.sh on a Claude host, claude_consult.sh on a Codex host).
#
# SOURCING DOES NOTHING. Only constants and functions are defined here: no
# traps, no temp files, no I/O, no CLI calls. Every side effect belongs to a
# function the entrypoint calls explicitly, and `hjw_common_init` is the ONE
# owner of the EXIT/INT/TERM traps — exactly one component installs traps, so
# there is never a second handler racing the first.
#
# The entrypoint sets BEFORE the first call:
#   HJW_LIB          directory holding this file and the python helpers
#   HJW_RUNNER_KIND  codex|claude — temp-file prefixes, host-relative config
#                    reading, usage text and failure labels
#   HJW_SELF         the runner's OWN absolute physical path — $HJW_LIB is
#                    derived from it, never from the environment alone
#   HJW_OUT_SIBLINGS suffixes of the artifacts it derives from ${OUT%.*}
#                    (codex: its events streams; claude: none), so the
#                    artifact guard checks them before the first write
#   HJW_OUT_APPENDS  suffixes of the artifacts it derives from the WHOLE
#                    $OUT (codex: `$OUT.tmp`, written only by an older
#                    install a hop may reach; claude: none), checked with
#                    the rest
# (all five BEFORE `hjw_forward_if_stale`: the pre-forward artifact check
# derives the same paths the local run will — see the artifact guard below)
# and OWNS (never inferred here): the REVIEWER CONTRACT text, print_help, the
# CLI argv and its redirections, both effective-brief writes, the timeout
# default, the event classifier + stderr scan + effort/sandbox (codex only),
# the non-git policy, and its own artifact list — `ARTIFACTS` for change
# detection. This library NEVER
# invents a vendor's artifact paths: the claude runner has no events stream,
# so nothing here may create, delete, exclude or guard one.
#
# *[origin: 2.13 shared-internals extraction — the two runners had drifted
# apart once already (host-relative config). The runner fixtures in
# tests/test_hooks.py exercise this library's whole contract through BOTH
# entrypoints, and they are what gates every change made here]*

# Sentinel terminator for every path a helper hands back. Command substitution
# strips trailing newlines and a directory name may legally END in one, so the
# SENTINEL — not the shell — marks where a value stops.
# *[origin: ship review Z3/Z5 — lossless path transport]*
SNAP_META_END=$'\004'"__HJW_SNAP_END__"

strip_sentinel() {
  # Sets $META_VAL to $1 minus exactly one trailing sentinel; fails if the
  # sentinel is absent (a truncated value must never pass as a path).
  META_VAL=""
  case "$1" in
    *"$SNAP_META_END") META_VAL="${1%"$SNAP_META_END"}"; return 0 ;;
    *) return 1 ;;
  esac
}

trim() { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

# Every helper (git, python, the CLI probes) and the reviewer call itself run
# under a wall clock: a hung helper must not hang the reviewer slot, and a
# timeout counts as "detection unavailable", never as "nothing changed".
# The bound is enforced by python3 (already a hard dependency) rather than the
# `timeout` binary — a host without coreutils' timeout must not silently get
# an UNBOUNDED run. *[origin: the `timeout`-present branch made the bound
# optional exactly where hangs are most likely]* Exit 124 on timeout matches
# timeout(1), which the failure classifier already reads. The enforcer is a
# FILE in this library (2.13): it used to be written to a fresh temp file on
# every run, which bought nothing and left one more thing to clean up.
bounded() {
  local secs="$1"; shift
  python3 "$HJW_LIB/bounded.py" "$secs" "$@"
}

# ---- self-forwarding: a runner invoked from a STALE cache path (2.18) ----
# FIELD DEFECT 2026-09-28 (khnp-rag session): the session started on 2.16.1,
# 2.17.0 was installed while it was open, and `/reload-plugins` refreshed
# hooks and commands but did NOT re-inject the SessionStart brief. The host
# kept invoking the LITERAL 2.16.1 path it still carried in context — old
# cache versions stay on disk — so three more plan consults ran with the
# 2.16.1 defect (`model=cli-default (identity unverified)`, no `config=`).
# Instruction-following is what failed here, so the fix cannot be more
# instructions: the RUNNER is the one component that learns the truth at the
# right moment, and it forwards ITSELF to the installed version.
#
# This heals only FUTURE staleness: an already-installed 2.17.0 runner has no
# forwarding code in it. From 2.18 on, a remembered path self-heals.
#
# FAIL OPEN at every step. No manifest, no registry, an unparseable one, an
# ambiguous one, an incomplete target, a failed exec — all of them mean "run
# locally". Forwarding is a convenience over the host's own bookkeeping file;
# it must never become the reason a review does not happen.
#
# HOST-SCOPED: the registry path is derived from the INVOKED runner's own
# `<plugins>` prefix, so a Codex-host runner can only ever consult
# `~/.codex/plugins/installed_plugins.json`. A Codex host keeps no such
# registry today, which makes forwarding a documented no-op there — never a
# cross-host read into Claude's registry.
HJW_PLUGIN_VERSION=""
HJW_PLUGIN_VERSION_LOADED=0

hjw_plugin_version_load() {
  # Sets HJW_PLUGIN_VERSION from the runner's OWN root manifest, at most once.
  # The root is derived from $HJW_SELF (runner = <root>/scripts/<name>), never
  # from the environment: which manifest describes this process is a question
  # only its own location can answer. An unknown version is "" — the header
  # then prints `plugin=unknown` rather than a guess.
  [ "$HJW_PLUGIN_VERSION_LOADED" = 1 ] && return 0
  HJW_PLUGIN_VERSION_LOADED=1
  local root raw
  root="${HJW_SELF%/*}"                     # .../scripts
  case "$root" in */scripts) ;; *) return 0 ;; esac
  root="${root%/scripts}"                   # .../<root>
  [ -n "$root" ] || return 0
  raw="$(bounded 20 python3 "$HJW_LIB/forward.py" version \
           "$root/.claude-plugin/plugin.json" 2>/dev/null)" || return 0
  strip_sentinel "$raw" || return 0
  HJW_PLUGIN_VERSION="$META_VAL"
  return 0
}

hjw_forward_if_stale() {
  # $@ = the entrypoint's OWN argv, forwarded byte for byte. Called as the
  # first action after sourcing — before argument parsing (a hop reads argv
  # only, never stdin: hjw_preforward_guard), stdin consumption,
  # traps, temp files, config selection or any chdir — so that a forwarded run
  # is indistinguishable from having invoked the installed runner directly:
  # same cwd, same stdin, same descriptors, same argv, same environment bar
  # the hop marker.
  local root plugins dir name raw version target esc_own esc_ver esc_target

  # (1) Hop marker. A forwarded run never forwards again, and the marker is
  # removed from the ENVIRONMENT here so no reviewer process the destination
  # spawns can ever see it. Nothing else happens on this branch.
  if [ -n "${HJW_FORWARDED:-}" ]; then
    unset HJW_FORWARDED
    return 0
  fi

  # (2) Own version. Without it there is nothing to compare, so nothing to do.
  hjw_plugin_version_load
  [ -n "$HJW_PLUGIN_VERSION" ] || return 0

  # (3) Structural gate — the exact installed-cache layout, nothing looser:
  # <plugins>/cache/haejwo/haejwo/<ver>/scripts/<runner>. A DEVELOPMENT
  # CHECKOUT is never redirected; a maintainer running the working tree gets
  # the working tree, whatever any registry says.
  dir="${HJW_SELF%/*}"                      # .../<ver>/scripts
  name="${HJW_SELF##*/}"
  [ -n "$name" ] || return 0
  case "$dir" in */scripts) ;; *) return 0 ;; esac
  root="${dir%/scripts}"                    # .../<ver>
  dir="${root%/*}"                          # .../cache/haejwo/haejwo
  case "$dir" in */cache/haejwo/haejwo) ;; *) return 0 ;; esac
  plugins="${dir%/cache/haejwo/haejwo}"
  # A real install is never at the filesystem root.
  [ -n "$plugins" ] || return 0

  # (4,5) Candidate selection and target validation, in one bounded helper:
  # both are JSON reads, and a hung or huge file must not stall a review that
  # has not started. Prints the winner or NOTHING at all.
  raw="$(bounded 20 python3 "$HJW_LIB/forward.py" target \
           "$plugins/installed_plugins.json" "$dir" "$HJW_PLUGIN_VERSION" \
           "$HJW_SELF" "$name" 2>/dev/null)" || return 0
  # The sentinel — not the shell — marks where each value stops: an install
  # path may legally end in a newline, which the substitution above eats.
  case "$raw" in
    *"$SNAP_META_END") ;;
    *) return 0 ;;
  esac
  raw="${raw%"$SNAP_META_END"}"
  version="${raw%%"$SNAP_META_END"*}"
  target="${raw#*"$SNAP_META_END"}"
  [ -n "$version" ] && [ -n "$target" ] || return 0

  # (5b) The artifact guard holds ACROSS the hop. The registry may name an
  # OLDER install (an intentional downgrade is followed), and a pre-2.21
  # runner has no artifact guard at all — so THIS version judges the argv
  # before it hands the run over. Refused: exit 2, nothing forwarded, nothing
  # written, no paid call. An argv this version would reject is not
  # forwarded either: the run stays here and is rejected by its own parser.
  hjw_preforward_guard "$@" || return 0

  # (6) Forward exactly once. The diagnostic is the whole audit trail for a
  # hop the caller never asked for, so it is never suppressed — and it is
  # escaped to ONE line, because a control character in a path must not be
  # able to forge extra output.
  hjw_esc_display "$HJW_PLUGIN_VERSION"; esc_own="$HJW_ESC"
  hjw_esc_display "$version"; esc_ver="$HJW_ESC"
  hjw_esc_display "$target"; esc_target="$HJW_ESC"
  printf '# runner %s is stale — forwarding to %s (%s)\n' \
    "$esc_own" "$esc_ver" "$esc_target" >&2
  export HJW_FORWARDED=1
  # A non-interactive bash EXITS when `exec` fails, which would turn a broken
  # cache entry into a review that silently never ran. `execfail` turns that
  # into a return, and the run continues here.
  # On that path bash ALSO prints its own error for the failed exec, and that
  # line is emitted by the shell — it is not escaped by hjw_esc_display. So
  # stderr carries three things, not two: our hop line, bash's message, and our
  # fallback line. What is guaranteed is one hop line and one fallback line,
  # which is what the tests assert; the total stderr line count is not ours.
  shopt -s execfail
  exec "$target" "$@"
  shopt -u execfail
  printf '# forwarding failed — running %s locally\n' "$esc_own" >&2
  unset HJW_FORWARDED
  return 0
}

# ---- argument parsing ----
# The CURRENT option set, unchanged: no new options and no extension hook —
# an unused mechanism is a future divergence with no caller to justify it.
# ONE scanner serves both readers of argv — the pre-forward guard and the
# parse proper — so the two can never disagree about which path is `-o` or
# which positional is the brief. It has no side effects beyond the variables
# it sets: no output, no exit, no stdin.
hjw_scan_args() {
  # Sets MODE, OUT, HJW_REST and BRIEF. Returns 0 = parsed, 1 =
  # help requested, 2 = rejected ($HJW_SCAN_ERR holds the one-line reason).
  MODE=""
  OUT=""
  HJW_REST=()
  BRIEF=""
  HJW_SCAN_ERR=""
  while [ $# -gt 0 ]; do
    case "$1" in
      # --resume was removed in 2.13: implicit latest-thread selection
      # misroutes under concurrent sessions (the runner cannot tell which
      # thread is the caller's). Rejected during PARSING — before any CLI
      # call or preflight work. (--snapshot, removed in 2.22, is now an
      # unknown option like any other.)
      # *[origin: cross-vendor decision round 2026-09-21]*
      --resume) HJW_SCAN_ERR="--resume was removed in 2.13 (implicit latest-thread selection misroutes under concurrent sessions); start a NEW session with a self-contained brief"; return 2 ;;
      --mode)   [ $# -ge 2 ] || { HJW_SCAN_ERR="--mode requires a value (consult)"; return 2; }; MODE="$2"; shift 2 ;;
      --mode=*) MODE="${1#--mode=}"; shift ;;
      -o)       [ $# -ge 2 ] || { HJW_SCAN_ERR="-o requires a value (output file)"; return 2; }; OUT="$2"; shift 2 ;;
      -o*)      OUT="${1#-o}"; shift ;;
      -h|--help) return 1 ;;
      --)       shift; break ;;
      -)        break ;;  # bare '-' = stdin brief (positional) — must match before '-*'
      -*)       HJW_SCAN_ERR="unknown option: $1"; return 2 ;;
      *)        break ;;
    esac
  done
  HJW_REST=("$@")

  MODE="${MODE:-consult}"
  case "$MODE" in
    consult) ;;
    implement)
      HJW_SCAN_ERR="--mode implement was removed in 2.10 (cross-vendor worker routing is a non-goal)."
      return 2
      ;;
    *)
      HJW_SCAN_ERR="invalid --mode: $MODE (consult)"
      return 2
      ;;
  esac

  BRIEF="${HJW_REST[0]:-}"
  [ -z "$BRIEF" ] && { HJW_SCAN_ERR="brief file required. usage: ${HJW_RUNNER_KIND}_consult.sh [--mode consult] [-o out] brief.md|-"; return 2; }
  return 0
}

hjw_parse_args() {
  local rc
  hjw_scan_args "$@"
  rc=$?
  case "$rc" in
    0) return 0 ;;
    1) print_help; exit 0 ;;
    *) printf '%s\n' "$HJW_SCAN_ERR" >&2; exit 2 ;;
  esac
}

hjw_preforward_guard() {
  # $@ = the entrypoint's argv. Judges every artifact path the argv makes
  # KNOWN before a hop, with the same scanner and the same derivation the
  # local run uses — read-only: stdin is never touched, nothing is created.
  # A `-` brief without `-o` names no reply path yet (it derives from a
  # mktemp name), so only the $TMPDIR directory is judged for it, exactly as
  # the local run does before reading stdin.
  # Returns 0 = forward (judged clean, or help: nothing is written on either
  # side); 1 = do NOT forward (this version rejects the argv — the local run
  # reports it). A refusal exits 2.
  local rc
  hjw_scan_args "$@"
  rc=$?
  [ "$rc" -eq 1 ] && return 0
  [ "$rc" -eq 0 ] || return 1
  hjw_artifact_set "$BRIEF" "$OUT"
  hjw_tmp_dirs
  hjw_artifact_dir_guard "${HJW_TMP_DIRS[@]}"
  [ "${#HJW_KNOWN[@]}" -gt 0 ] && hjw_artifact_guard "${HJW_KNOWN[@]}"
  return 0
}

hjw_tmp_dirs() {
  # Sets HJW_TMP_DIRS: the directory every mktemp artifact lands in
  # ($HJW_TMP_DIR), judged as the RAW path.
  HJW_TMP_DIRS=("$HJW_TMP_DIR")
  return 0
}

hjw_tmp_guard() {
  # The local run's judgment of the mktemp directory — the same one
  # hjw_preforward_guard makes before a hop.
  hjw_tmp_dirs
  hjw_artifact_dir_guard "${HJW_TMP_DIRS[@]}"
}

cleanup() {
  [ -n "$TMPBRIEF" ] && rm -f "$TMPBRIEF"
  [ -n "$EFFECTIVE_BRIEF" ] && rm -f "$EFFECTIVE_BRIEF"
  [ -n "$SNAPDIR" ] && rm -rf "$SNAPDIR"
}

hjw_common_init() {
  # Called ONCE per run, right after parsing. Initializes the shared state,
  # arms the traps, materializes a stdin brief, derives $OUT/$LOG and refuses
  # any artifact path inside the reviewed repository (hjw_artifact_guard).
  #
  # TMPBRIEF: stdin brief -> temp file, deleted on exit (keeps sensitive
  # content out of /tmp). EFFECTIVE_BRIEF (contract + blank line + brief) is
  # what is actually fed to the reviewer on every input path; also deleted on
  # exit. SNAPDIR holds the before/after change-detection snapshots (files,
  # not shell variables — a 2000-entry untracked fingerprint set does not
  # belong in argv/env).
  TMPBRIEF=""
  EFFECTIVE_BRIEF=""
  SNAPDIR=""
  # `plugin=<own version>` for the log header. After a forward the log must
  # prove WHICH version ran, not which one the caller typed — so this is read
  # from the RUNNING process's own manifest (cached; the forwarding step has
  # usually filled it already).
  hjw_plugin_version_load
  hjw_esc_display "${HJW_PLUGIN_VERSION:-unknown}"
  HJW_PLUGIN_DISP="plugin=$HJW_ESC"
  META_VAL=""
  # Config ownership state (hjw_config_resolve/_load/_disclose fill these in).
  CFG_PATH=""
  CFG_SOURCE="none"
  CFG_STATUS="none"
  CFG_DISP=""
  CFG_FOREIGN_NOTE=""
  CFG_FOREIGN_REPORTED=0
  COVERAGE_NOTE=""
  FAILED=0
  FAIL_MSG=""
  ARTIFACTS=()
  # Armed HERE — before any temp file exists.
  trap cleanup EXIT
  # Temp files must not outlive an interrupted run either; bash does not
  # fire the EXIT trap for an uncaught INT/TERM, so catch both and exit through
  # it with the runner's OWN status — the status the trap sees belongs to
  # whatever was interrupted. The explicit exit re-enters cleanup through the
  # EXIT trap; every step in there is idempotent.
  trap 'cleanup; exit 130' INT
  trap 'cleanup; exit 143' TERM
  if [ "$BRIEF" = "-" ]; then
    # Every path already KNOWN is judged BEFORE stdin is read and before the
    # temp brief exists: the $TMPDIR directory always, and with `-o` the reply
    # and everything derived from it. Without `-o` the reply path derives
    # from the temp brief's own name, so it is judged below — after that one
    # write, into the directory already cleared.
    hjw_artifact_set - "$OUT"
    hjw_tmp_guard
    [ "${#HJW_KNOWN[@]}" -gt 0 ] && hjw_artifact_guard "${HJW_KNOWN[@]}"
    BRIEF="$(mktemp "${TMPDIR:-/tmp}/${HJW_RUNNER_KIND}_brief.XXXXXX.md")" || {
      echo "cannot create a temp file (is ${TMPDIR:-/tmp} writable?)" >&2; exit 4; }
    TMPBRIEF="$BRIEF"
    cat > "$BRIEF"
  fi
  [ -f "$BRIEF" ] || { echo "brief file not found: $BRIEF" >&2; exit 2; }

  # Every artifact is checked HERE, before anything truncates or removes one:
  # the log header truncates $LOG, the entrypoint `rm -f`s $OUT (and codex's
  # events), and change detection excludes all of them from its verdict BY
  # DESIGN — so an artifact path naming a project file would be overwritten
  # silently. A stdin brief's temp file is judged again by its real name.
  hjw_artifact_set "$BRIEF" "$OUT"
  OUT="$HJW_ART_OUT"
  LOG="$HJW_ART_LOG"
  local _guard=("${HJW_KNOWN[@]}")
  # A stdin run judged the $TMPDIR directory above, before the temp brief.
  if [ -n "$TMPBRIEF" ]; then _guard+=("$TMPBRIEF"); else hjw_tmp_guard; fi
  hjw_artifact_guard "${_guard[@]}"
  return 0
}

hjw_artifact_set() {
  # THE derivation of a run's artifact paths — the one the local run and the
  # pre-forward guard (hjw_preforward_guard) both use, so they cannot drift.
  # $1 = brief (`-` = stdin), $2 = the `-o` value or "". Pure: sets
  # HJW_ART_OUT, HJW_ART_LOG, HJW_TMP_DIR and HJW_KNOWN (every path known
  # from these two alone: the reply, the log, the entrypoint's ${OUT%.*}
  # siblings ($HJW_OUT_SIBLINGS) and its whole-$OUT appends
  # ($HJW_OUT_APPENDS)). A `-` brief without `-o` knows none of them yet —
  # its reply derives from the temp brief's name — so HJW_KNOWN is empty.
  # HJW_TMP_DIR is where every mktemp artifact (TMPBRIEF, EFFECTIVE_BRIEF)
  # lands: their names are unknown until created, their directory is not —
  # so the DIRECTORY is judged (`detect.py artifacts --dir`), never a
  # hypothetical file name mktemp would not use. Relative paths stay
  # relative: they are judged against the invoking cwd.
  local _sfx _out="$2" _log
  HJW_TMP_DIR="${TMPDIR:-/tmp}"
  HJW_ART_OUT=""
  HJW_ART_LOG=""
  HJW_KNOWN=()
  if [ -z "$_out" ]; then
    [ "$1" = "-" ] && return 0
    _out="${1%.md}.reply.md"
  fi
  _log="${_out%.*}.log"
  # -o x.log would derive the SAME path for the log and the reply; give the log
  # its own name so the runner never overwrites the answer it captured.
  # *[origin: ship review Z4]*
  [ "$_log" = "$_out" ] && _log="$_out.log"
  HJW_ART_OUT="$_out"
  HJW_ART_LOG="$_log"
  HJW_KNOWN=("$_out" "$_log")
  for _sfx in ${HJW_OUT_SIBLINGS[@]+"${HJW_OUT_SIBLINGS[@]}"}; do
    HJW_KNOWN+=("${_out%.*}$_sfx")
  done
  for _sfx in ${HJW_OUT_APPENDS[@]+"${HJW_OUT_APPENDS[@]}"}; do
    HJW_KNOWN+=("$_out$_sfx")
  done
  return 0
}

# ---- artifact guard (2.21): never write inside the reviewed repository ----
# GUARANTEE, exactly: every runner artifact (reply, log, codex's events, the
# temp brief and effective brief — plus the legacy `.events.2.jsonl` and
# `$OUT.tmp` an older install reached by a hop still writes) lies outside
# the worktree the runner was invoked from and outside that worktree's git
# dirs, judged both lexically and through symlinks. NOT covered: other
# worktrees of the same repository outside this top level, and a hostile
# concurrent replacement after the check — it guards against accidental paths
# (a typo in `-o`, a brief kept inside the repo with no `-o`).
# SCOPE IN VERSIONS: the guarantee belongs to the INVOKED runner (2.21+), and
# it holds across a forwarding hop — including a hop to an OLDER install the
# registry selected, which may have no guard of its own: hjw_preforward_guard
# judges the argv-known paths (and the $TMPDIR directory) with this version's
# derivation before the exec. What a pre-2.21 runner invoked DIRECTLY does is
# outside it.
# ORDER, exactly: every path known from argv (`-o` and what derives from it)
# and the $TMPDIR directory are judged first; a stdin brief is read only
# after that. Without `-o` a stdin brief's reply path derives from the temp
# brief's name, so that temp brief — inside the already-cleared directory —
# is the one write that precedes the final check.
# A refusal exits 2 with ONE line, before any paid call and before any
# artifact is truncated or removed — $LOG included, since $LOG is one of the
# paths under judgment. Fail closed: a check that cannot run refuses too.
# Relative paths resolve against the ORIGINAL cwd, which is why this runs
# before any chdir.
hjw_artifact_guard() {
  # $@ = artifact FILE paths.
  hjw_artifact_check "" "$@"
}

hjw_artifact_dir_guard() {
  # $@ = the DIRECTORIES mktemp creates artifacts in (containment only). A
  # separate entry point, not a leading flag: an `-o` value may be any string.
  hjw_artifact_check --dir "$@"
}

hjw_artifact_check() {
  # $1 = "" (files) or --dir; the rest = paths. detect.py's <cwd> is always
  # "" here (the invoking cwd), so its own argv is never ambiguous.
  local rc _mode=()
  [ -n "$1" ] && _mode=("$1")
  shift
  bounded 60 python3 "$HJW_LIB/detect.py" artifacts ${_mode[@]+"${_mode[@]}"} "" "$@"
  rc=$?
  [ "$rc" -eq 0 ] && return 0
  [ "$rc" -ne 2 ] && echo "artifact path check unavailable (rc=$rc) — refusing before any write" >&2
  exit 2
}

# ---- config ----
# OWNERSHIP, not the shell's word for it. `CLAUDE_PLUGIN_DATA` in a host's
# Bash environment is the data dir of the LAST LOADED plugin, not haejwo's:
# measured 2026-09-28 in a three-plugin Claude Code session, where the
# reviewer runner read another vendor's dir, found "no config", and silently
# ran three consults at the CLI default model in a read-only sandbox the
# owner had explicitly configured away from. A config path is therefore
# DERIVED FROM OWNERSHIP and only accepted from the environment when the
# environment names haejwo's own dir.
#
# EXACTLY ONE owner path is selected, in this order, and the source is
# recorded with it:
#   structural  $HJW_SELF (the runner's own resolved path) sits in the
#               installed-cache layout <plugins>/cache/haejwo/haejwo/<ver>/
#               scripts/<runner> -> <plugins>/data/haejwo-haejwo/config.json.
#               The environment is IGNORED here: the install location is a
#               stronger statement of ownership than any variable.
#   env         else CLAUDE_PLUGIN_DATA is non-empty AND its basename is
#               exactly `haejwo-haejwo` (tests, local checkouts, a host that
#               really does export ours).
#   derived     else ${HOME}/.codex|.claude/plugins/data/haejwo-haejwo/
#               config.json, chosen by /.codex/ in $HJW_SELF's text.
#   none        no owner path determinable (e.g. empty $HOME with neither a
#               structural layout nor a haejwo-named env dir).
# After selection there is NO further fallback: a missing, unreadable or
# malformed file at the owner path is `absent`/`malformed`, NEVER another
# path. Canonical location establishes OWNERSHIP, not freshness — falling
# back once an owner config disappears could resurrect a stale
# danger-full-access consent from somewhere else. Freshness is a SEPARATE
# question, answered earlier and elsewhere: `hjw_forward_if_stale` has already
# decided whether this process should be the one reading a config at all, so
# by the time resolution runs, $HJW_SELF is the version that will do the work.
#
# A set-but-FOREIGN CLAUDE_PLUGIN_DATA (non-empty, basename != haejwo-haejwo)
# is ignored and DISCLOSED once, by `hjw_config_disclose` — after the
# entrypoint has written its log header, which truncates $LOG. Foreignness is
# judged on the VARIABLE ALONE, never on which source won: haejwo's own dir
# in the variable raises no note just because the structural path outranked
# it.
#
# OWNERSHIP HERE IS A NAMING HEURISTIC, not authentication. It answers the
# measured defect — ANOTHER PLUGIN's data dir arriving in the variable — and
# nothing beyond it: the structural branch accepts any path shaped like the
# installed cache (the <ver> component need not look like a version), and the
# env branch accepts any directory whose last component reads
# `haejwo-haejwo`, including a relative one or a symlink (the config is then
# read through it, at its target). Someone who can choose these paths already
# chose the runner this file is part of; ruling that out would need a trust
# source this layer does not have.
#
# The path is derived in SHELL, not in config.py: a config path may legally
# contain a newline, so it is assigned to a variable rather than carried
# through a command substitution (which strips trailing newlines). Host
# detection then reads the selected path's TEXT (never its canonical target)
# — both runners have always behaved that way.
hjw_data_dir_basename() {
  # Sets HJW_BASENAME to $1's last component with ALL trailing separators
  # stripped first. `${1%/}` strips exactly ONE, so `.../haejwo-haejwo/` and
  # `.../haejwo-haejwo//` — the same directory — classified differently, and
  # the `//` spelling fell through to `derived`, where a stale
  # danger-full-access consent could be resurrected (measured 2026-09-28).
  local d="$1"
  while [ "$d" != "${d%/}" ]; do d="${d%/}"; done
  HJW_BASENAME="${d##*/}"
}

hjw_esc_display() {
  # Sets HJW_ESC: $1 with the control characters that would SPLIT or garble a
  # one-line log header rendered as TEXT (\n, \r, \t), and any other control
  # character as `?`. DISPLAY ONLY — CFG_PATH keeps the raw bytes, since that
  # is the file actually opened; a config path may legally contain a newline.
  # One-way on purpose: a name holding a literal backslash-n and one holding a
  # real newline print the same. Assigns to a global rather than printing —
  # `$(...)` would eat exactly the trailing newline this is here to show.
  local s="$1"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  s="${s//[[:cntrl:]]/?}"
  HJW_ESC="$s"
}

hjw_config_resolve() {
  # Sets CFG_PATH, CFG_SOURCE and CFG_FOREIGN_NOTE. No stdout: diagnostics
  # here would be mistaken for the path itself.
  local dir base plugins
  CFG_PATH=""; CFG_SOURCE="none"; CFG_FOREIGN_NOTE=""

  # (a) structural — the exact installed-cache layout, nothing looser.
  dir="${HJW_SELF%/*}"                      # .../<ver>/scripts
  case "$dir" in
    */scripts)
      dir="${dir%/scripts}"                 # .../<ver>
      dir="${dir%/*}"                       # .../cache/haejwo/haejwo
      case "$dir" in
        */cache/haejwo/haejwo)
          plugins="${dir%/cache/haejwo/haejwo}"
          # A real install is never at the filesystem root; an empty prefix
          # would name /data/haejwo-haejwo, which is nobody's plugin dir.
          [ -n "$plugins" ] && { CFG_PATH="$plugins/data/haejwo-haejwo/config.json"; CFG_SOURCE="structural"; }
          ;;
      esac
      ;;
  esac

  # (b) env — only when it names haejwo's OWN data dir.
  if [ -z "$CFG_PATH" ] && [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
    hjw_data_dir_basename "$CLAUDE_PLUGIN_DATA"
    if [ "$HJW_BASENAME" = "haejwo-haejwo" ]; then
      # The variable's own spelling stays the operational path: a trailing
      # separator is not significant to any filesystem call.
      CFG_PATH="${CLAUDE_PLUGIN_DATA}/config.json"; CFG_SOURCE="env"
    fi
  fi

  # (c) derived — haejwo's canonical location under $HOME.
  if [ -z "$CFG_PATH" ] && [ -n "${HOME:-}" ]; then
    case "$HJW_SELF" in
      */.codex/*) CFG_PATH="${HOME}/.codex/plugins/data/haejwo-haejwo/config.json" ;;
      *)          CFG_PATH="${HOME}/.claude/plugins/data/haejwo-haejwo/config.json" ;;
    esac
    CFG_SOURCE="derived"
  fi

  # (d) a foreign variable is never silently discarded — and only a foreign
  # one is reported. Keyed on the VARIABLE, not on `$CFG_SOURCE != env`: with
  # a structural install the source is never `env`, so that test called
  # haejwo's own exported dir foreign ("CLAUDE_PLUGIN_DATA=haejwo-haejwo is
  # not haejwo's data dir", measured 2026-09-28).
  if [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
    hjw_data_dir_basename "$CLAUDE_PLUGIN_DATA"
    if [ "$HJW_BASENAME" != "haejwo-haejwo" ]; then
      hjw_esc_display "$HJW_BASENAME"; base="$HJW_ESC"
      hjw_esc_display "${CFG_PATH:-none}"
      CFG_FOREIGN_NOTE="# config: CLAUDE_PLUGIN_DATA=$base is not haejwo's data dir — using $HJW_ESC ($CFG_SOURCE)"
    fi
  fi
  return 0
}

hjw_config_disclose() {
  # The foreign-variable note, exactly once, into $LOG and onto stderr. The
  # entrypoint calls this AFTER its header write (`> "$LOG"`) — appending
  # during config loading would be truncated away by it.
  [ -n "$CFG_FOREIGN_NOTE" ] || return 0
  [ "$CFG_FOREIGN_REPORTED" = 1 ] && return 0
  CFG_FOREIGN_REPORTED=1
  printf '%s\n' "$CFG_FOREIGN_NOTE" >> "$LOG" 2>/dev/null
  printf '%s\n' "$CFG_FOREIGN_NOTE" >&2
  return 0
}

hjw_config_values() {
  # Prints `model=`, `effort=` and `ignored=` (keys present
  # but not strings) — or NOTHING at all when the `codex` block describes the
  # OTHER vendor's reviewer. HOST-RELATIVE: that block belongs to the reviewer
  # of the host that owns the data dir, so on a VENDOR path the codex runner
  # reads it only OUTSIDE a /.codex/ path and the claude runner only INSIDE
  # one. A custom plugin root names no vendor, and there the host follows the
  # RUNNER KIND instead — both runners read their own block (config.py). Any
  # parse failure is "no config" — a reviewer runner never guesses.
  # *[origin: a live smoke launched the claude reviewer with the codex host's
  # own model name]*
  [ -n "$CFG_PATH" ] && [ -f "$CFG_PATH" ] || return 0
  bounded 60 python3 "$HJW_LIB/config.py" values "$HJW_RUNNER_KIND" "$CFG_PATH" 2>/dev/null
}

hjw_config_sandbox() {
  # Read on ANY host: the sandbox describes how THIS runner is launched, not
  # which vendor the `codex` block's model keys belong to. Printed raw, so an
  # exotic value cannot be trimmed into a valid one by the transport — the
  # caller's allowlist decides.
  [ -n "$CFG_PATH" ] && [ -f "$CFG_PATH" ] || return 0
  bounded 60 python3 "$HJW_LIB/config.py" sandbox "$CFG_PATH" 2>/dev/null
}

hjw_config_status() {
  # ok | absent | malformed for the OWNER path — the read status, kept apart
  # from CFG_SOURCE (where the path came from). `none` belongs to the source,
  # not here: it means no path was ever selected to read.
  [ -n "$CFG_PATH" ] || { printf 'none'; return 0; }
  bounded 60 python3 "$HJW_LIB/config.py" status "$CFG_PATH" 2>/dev/null
}

hjw_config_load() {
  # Sets CFG_PATH/CFG_SOURCE/CFG_STATUS and the CFG_* values, and emits the
  # "ignored" notes in the order the config lists them — before any
  # sandbox/effort resolution, which is where the caller's own notes belong.
  # The foreign-variable note is NOT emitted here: $LOG does not exist yet
  # (see hjw_config_disclose).
  hjw_config_resolve
  CFG_STATUS="$(hjw_config_status)"
  [ -n "$CFG_STATUS" ] || CFG_STATUS="absent"
  # The header is ONE line: a path's control characters are escaped for
  # display here, never in CFG_PATH itself.
  hjw_esc_display "${CFG_PATH:-none}"
  CFG_DISP="config=$HJW_ESC ($CFG_SOURCE) config_status=$CFG_STATUS"
  CFG_MODEL=""; CFG_EFFORT=""; CFG_IGNORED=""
  local values old_ifs k
  values="$(hjw_config_values)"
  CFG_MODEL="$(printf '%s\n' "$values" | sed -n 's/^model=//p')"
  CFG_EFFORT="$(printf '%s\n' "$values" | sed -n 's/^effort=//p')"
  CFG_IGNORED="$(printf '%s\n' "$values" | sed -n 's/^ignored=//p')"
  if [ "$HJW_RUNNER_KIND" = codex ]; then
    if [ -n "$CFG_IGNORED" ]; then
      old_ifs="$IFS"; IFS=','
      for k in $CFG_IGNORED; do
        [ -n "$k" ] && echo "note: config codex.$k ignored (not a string)" >&2
      done
      IFS="$old_ifs"
    fi
  else
    # claude reads only codex.model, so only that key's note is actionable.
    case ",$CFG_IGNORED," in *,model,*) echo "note: config codex.model ignored (not a string)" >&2 ;; esac
  fi
  return 0
}

# ---- change detection (A5): file-backed before/after snapshots ----
# Attribution is NOT established here — a concurrent formatter/hook/editor save
# produces the same signal as a reviewer edit, so the failure message says
# "attribution unknown" and never proposes automatic reversion.
git_snapshot() {
  # $1 = snapshot JSON file. Non-zero exit = detection unavailable (fail
  # closed). Everything is serialized as JSON: filenames may contain newlines
  # or tabs, so no line/tab-delimited format is safe here. $ARTIFACTS is the
  # ENTRYPOINT's list of runner-owned paths.
  bounded 60 python3 "$HJW_LIB/detect.py" snapshot "$WORKDIR" "$1" "${ARTIFACTS[@]}"
}

snapshot_diff() {
  # $1 = before JSON, $2 = after JSON. Prints `coverage=<flags>` and
  # `changed=<list>`. A missing/unreadable/unparsable snapshot EXITS non-zero —
  # a read error must never be mistaken for "nothing changed".
  bounded 60 python3 "$HJW_LIB/detect.py" compare "$1" "$2"
}

hjw_git_preflight() {
  # Creates SNAPDIR and probes the repository. The git probe has THREE
  # outcomes, not two: inside a repo, genuinely not a repo (the documented
  # non-git path), and "git could not tell us" — a broken repo, a permission
  # error, a timeout. Only the middle one may proceed; the third must never be
  # silently read as "not a repo, nothing to verify".
  SNAPDIR="$(mktemp -d "${TMPDIR:-/tmp}/${HJW_RUNNER_KIND}_snap.XXXXXX")" || SNAPDIR=""
  DETECT_OK=1
  [ -n "$SNAPDIR" ] || DETECT_OK=0
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
  return 0
}

# Fail BEFORE spending a reviewer run: a consult whose no-edit contract
# cannot be verified is worthless, so never pay for it.
detect_fail_now() {
  # The log is opened BEFORE the preflight so git's own explanation survives
  # here too — a caller who only keeps $LOG must still learn why the gate
  # could not run.
  # This path fails the run WITHOUT going through fail(): there is no report
  # block to assemble here, only this one message and git's own explanation.
  [ -n "$SNAPDIR" ] && [ -s "$SNAPDIR/detect.err" ] && { echo "# ---- change detection stderr ----"; cat "$SNAPDIR/detect.err"; } >> "$LOG"
  echo "✗ ${HJW_RUNNER_KIND}_consult FAILED (mode=$MODE, 0s):${COVERAGE_NOTE:-}" >&2
  echo "  - $DETECT_MSG" >&2
  [ -n "$SNAPDIR" ] && [ -s "$SNAPDIR/detect.err" ] && sed 's/^/  /' "$SNAPDIR/detect.err" >&2
  exit 1
}

hjw_detect_before() {
  # Returns 0 when the BEFORE snapshot is taken (or the run has already been
  # failed out), 1 when this is genuinely NOT a git repository — where VENDOR
  # POLICY decides, in the entrypoint: codex allows a read-only sandbox to be
  # the enforcement, claude has no sandbox and always refuses.
  if [ "$DETECT_OK" != 1 ]; then
    detect_fail_now
  fi
  if [ "$GIT_OK" = 1 ]; then
    git_snapshot "$SNAPDIR/before.json" 2>>"$SNAPDIR/detect.err" || DETECT_OK=0
    [ "$DETECT_OK" = 1 ] || detect_fail_now
    return 0
  fi
  return 1
}

hjw_detect_after() {
  if [ "$GIT_OK" = 1 ] && [ "$DETECT_OK" = 1 ]; then
    git_snapshot "$SNAPDIR/after.json" 2>>"$SNAPDIR/detect.err" || DETECT_OK=0
  fi
  return 0
}

# $1 = the human failure message. It accumulates in $FAIL_MSG, which
# hjw_fail_header prints on stderr; a message never leaves $LOG/stderr.
fail() { FAILED=1; FAIL_MSG="${FAIL_MSG}  - $1"$'\n'; }

hjw_change_verdict() {
  # The change-detection verdict plus the coverage notes it discloses.
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
  return 0
}

# ---- result / failure reporting ----
# Assembled from EXPLICIT vendor fields the entrypoint passes in; this library
# never guesses which disclosures a vendor has (codex discloses effort and
# sandbox, claude has neither knob).
hjw_fail_header() {
  echo "✗ ${HJW_RUNNER_KIND}_consult FAILED (mode=$MODE, ${DUR}s):${COVERAGE_NOTE:-}" >&2
  printf '%s' "$FAIL_MSG" >&2
}

hjw_fail_tail() {
  # The tail of every failure block, and the exit itself: a non-zero CLI status
  # is passed through so the caller sees what the reviewer's own exit meant.
  echo "  --- last 12 log lines ($LOG) ---" >&2
  tail -12 "$LOG" >&2
  [ "$rc" -ne 0 ] && exit "$rc" || exit 1
}

hjw_log_coverage_note() {
  [ -n "$COVERAGE_NOTE" ] && echo "# ---- change detection:$COVERAGE_NOTE ----" >> "$LOG"
  return 0
}
