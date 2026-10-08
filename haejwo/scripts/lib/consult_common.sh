#!/usr/bin/env bash
# consult_common.sh — shared internals of haejwo's two reviewer runners (codex_consult.sh on a Claude
# host, claude_consult.sh on a Codex host). SOURCING DOES NOTHING: only constants and functions; every
# side effect is a function the entrypoint calls, and hjw_common_init is the ONE owner of the traps.
# The entrypoint sets, all BEFORE hjw_forward_if_stale (the pre-forward guard derives the same paths):
#   HJW_LIB this directory;  HJW_SELF its own absolute physical path ($HJW_LIB derives from it)
#   HJW_RUNNER_KIND   codex|claude — temp prefixes, host-relative config, usage and failure labels
#   HJW_OUT_SIBLINGS  suffixes derived from ${OUT%.*} (codex: its events streams; claude: none)
#   HJW_OUT_APPENDS   suffixes appended to the whole $OUT (codex: `.tmp`, written only by an older
#                     install a hop may reach; claude: none)
# and OWNS: the REVIEWER CONTRACT, print_help, the CLI argv and redirections, the effective brief, the
# timeout default, codex's classifier/stderr scan/effort/sandbox, the non-git policy, and ARTIFACTS.
# This library never invents a vendor's artifact paths (claude has no events stream).
# *[origin: 2.13 shared-internals extraction — the two runners had drifted apart once already
# (host-relative config); the runner fixtures in tests/test_hooks.py exercise this library through
# BOTH entrypoints and gate every change here]*

# Sentinel terminator for every path a helper hands back: command substitution strips trailing newlines
# and a directory name may legally end in one. *[origin: ship review Z3/Z5 — lossless path transport]*
SNAP_META_END=$'\004'"__HJW_SNAP_END__"

strip_sentinel() {
  # Sets $META_VAL to $1 minus one trailing sentinel; fails if absent (a truncated value is never a path).
  META_VAL=""
  case "$1" in
    *"$SNAP_META_END") META_VAL="${1%"$SNAP_META_END"}"; return 0 ;;
    *) return 1 ;;
  esac
}

trim() { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

# Wall clock for every helper and the reviewer call: a hang must not hang the slot, and a timeout is
# "detection unavailable", never "nothing changed". Enforced by python3 (exit 124, as timeout(1)), not the
# `timeout` binary. *[origin: the `timeout`-present branch made the bound optional exactly where hangs are most likely]*
bounded() {
  local secs="$1"; shift
  python3 "$HJW_LIB/bounded.py" "$secs" "$@"
}

# ---- self-forwarding: a runner invoked from a STALE cache path (2.18) ----
# *[origin: field defect 2026-09-28 (khnp-rag): `/reload-plugins` did not re-inject the SessionStart brief,
# the host kept invoking the literal 2.16.1 path (old cache versions stay on disk) and three consults ran
# with the 2.16.1 defect]* Instruction-following failed, so the RUNNER forwards itself to the installed version.
# FAIL OPEN at every step: any missing/ambiguous/invalid input or a failed exec means "run locally".
# HOST-SCOPED: the registry derives from the invoked runner's own <plugins> prefix — a documented no-op on
# a Codex host (no registry there), never a cross-host read.
HJW_PLUGIN_VERSION=""
HJW_PLUGIN_VERSION_LOADED=0

hjw_plugin_version_load() {
  # Sets HJW_PLUGIN_VERSION once, from the runner's OWN root manifest (derived from $HJW_SELF, never the
  # environment); unknown = "" and the header prints `plugin=unknown`.
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
  # $@ = the entrypoint's argv, forwarded byte for byte. Runs before parsing, stdin, traps, temp files,
  # config and chdir, so a forwarded run is indistinguishable from invoking the installed runner directly.
  local root plugins dir name raw version target esc_own esc_ver esc_target

  # (1) Hop marker: a forwarded run never forwards again; unset so no reviewer process can see it.
  if [ -n "${HJW_FORWARDED:-}" ]; then
    unset HJW_FORWARDED
    return 0
  fi

  # (2) Own version. Without it there is nothing to compare, so nothing to do.
  hjw_plugin_version_load
  [ -n "$HJW_PLUGIN_VERSION" ] || return 0

  # (3) Structural gate: exactly <plugins>/cache/haejwo/haejwo/<ver>/scripts/<runner>; a development
  # checkout is never redirected.
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

  # (4,5) Candidate selection + target validation in one bounded helper (a hung or huge JSON must not
  # stall); prints the winner or nothing.
  raw="$(bounded 20 python3 "$HJW_LIB/forward.py" target \
           "$plugins/installed_plugins.json" "$dir" "$HJW_PLUGIN_VERSION" \
           "$HJW_SELF" "$name" 2>/dev/null)" || return 0
  # The sentinel marks where each value stops: an install path may legally end in a newline.
  case "$raw" in
    *"$SNAP_META_END") ;;
    *) return 0 ;;
  esac
  raw="${raw%"$SNAP_META_END"}"
  version="${raw%%"$SNAP_META_END"*}"
  target="${raw#*"$SNAP_META_END"}"
  [ -n "$version" ] && [ -n "$target" ] || return 0

  # (5b) The artifact guard holds ACROSS the hop: the registry may name an OLDER install with no guard, so
  # this version judges the argv first (refusal = exit 2, nothing forwarded); an argv it rejects stays here.
  hjw_preforward_guard "$@" || return 0

  # (6) Forward once. The hop line is the whole audit trail: never suppressed, escaped to ONE line.
  hjw_esc_display "$HJW_PLUGIN_VERSION"; esc_own="$HJW_ESC"
  hjw_esc_display "$version"; esc_ver="$HJW_ESC"
  hjw_esc_display "$target"; esc_target="$HJW_ESC"
  printf '# runner %s is stale — forwarding to %s (%s)\n' \
    "$esc_own" "$esc_ver" "$esc_target" >&2
  export HJW_FORWARDED=1
  # `execfail`: a failed exec returns instead of exiting, so a broken cache entry still reviews locally.
  # bash then also prints its own unescaped error; the tests assert only our hop and fallback lines.
  shopt -s execfail
  exec "$target" "$@"
  shopt -u execfail
  printf '# forwarding failed — running %s locally\n' "$esc_own" >&2
  unset HJW_FORWARDED
  return 0
}

# ---- argument parsing ----
# ONE side-effect-free scanner serves the pre-forward guard and the parse proper, so they never disagree
# about `-o` or the brief. No extension hook: an unused mechanism is a future divergence.
hjw_scan_args() {
  # Sets MODE, OUT, HJW_REST, BRIEF. Returns 0 parsed, 1 help, 2 rejected ($HJW_SCAN_ERR = one-line reason).
  MODE=""
  OUT=""
  HJW_REST=()
  BRIEF=""
  HJW_SCAN_ERR=""
  while [ $# -gt 0 ]; do
    case "$1" in
      # --resume (removed 2.13): implicit latest-thread selection misroutes under concurrent sessions;
      # rejected while parsing. *[origin: cross-vendor decision round 2026-09-21]*
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
  # Judges every artifact path the argv makes KNOWN before a hop, with the local run's scanner and
  # derivation; read-only (stdin untouched). A `-` brief without `-o` judges only the $TMPDIR directory.
  # Returns 0 = forward (clean, or help), 1 = do not forward (the local parser reports it); refusal exits 2.
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
  # Sets HJW_TMP_DIRS: the directory every mktemp artifact lands in, judged as the raw path.
  HJW_TMP_DIRS=("$HJW_TMP_DIR")
  return 0
}

hjw_tmp_guard() {
  # The local run's judgment of the mktemp directory — the same one the pre-forward guard makes.
  hjw_tmp_dirs
  hjw_artifact_dir_guard "${HJW_TMP_DIRS[@]}"
}

cleanup() {
  [ -n "$TMPBRIEF" ] && rm -f "$TMPBRIEF"
  [ -n "$EFFECTIVE_BRIEF" ] && rm -f "$EFFECTIVE_BRIEF"
  [ -n "$SNAPDIR" ] && rm -rf "$SNAPDIR"
}

hjw_common_init() {
  # Called ONCE, right after parsing: shared state, traps, a stdin brief materialized to TMPBRIEF (deleted
  # on exit), $OUT/$LOG derived, every artifact inside the reviewed repository refused. SNAPDIR holds the
  # change-detection snapshots as files (a 2000-entry fingerprint set does not belong in argv/env).
  TMPBRIEF=""
  EFFECTIVE_BRIEF=""
  SNAPDIR=""
  # `plugin=<own version>`: after a forward the log must prove WHICH version ran (cached manifest read).
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
  # bash does not fire EXIT on an uncaught INT/TERM: exit through cleanup with the runner's OWN status;
  # cleanup is idempotent, so re-entering it via the EXIT trap is safe.
  trap 'cleanup; exit 130' INT
  trap 'cleanup; exit 143' TERM
  if [ "$BRIEF" = "-" ]; then
    # Judge every known path (always $TMPDIR; with `-o` the reply and its derivations) BEFORE reading stdin.
    # Without `-o` the reply derives from the temp brief's name and is judged below, after that one write.
    hjw_artifact_set - "$OUT"
    hjw_tmp_guard
    [ "${#HJW_KNOWN[@]}" -gt 0 ] && hjw_artifact_guard "${HJW_KNOWN[@]}"
    BRIEF="$(mktemp "${TMPDIR:-/tmp}/${HJW_RUNNER_KIND}_brief.XXXXXX.md")" || {
      echo "cannot create a temp file (is ${TMPDIR:-/tmp} writable?)" >&2; exit 4; }
    TMPBRIEF="$BRIEF"
    cat > "$BRIEF"
  fi
  [ -f "$BRIEF" ] || { echo "brief file not found: $BRIEF" >&2; exit 2; }

  # Every artifact is judged HERE, before anything truncates or removes one: change detection excludes
  # artifacts BY DESIGN, so an artifact naming a project file would be overwritten silently.
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
  # THE derivation of a run's artifact paths, shared by the local run and hjw_preforward_guard so they
  # cannot drift. $1 = brief (`-` = stdin), $2 = `-o` value or "". Pure: sets HJW_ART_OUT, HJW_ART_LOG,
  # HJW_TMP_DIR and HJW_KNOWN (reply, log, siblings, appends; empty for `-` without `-o`). mktemp names are
  # unknown, so their DIRECTORY is judged (`detect.py artifacts --dir`). Relative paths: the invoking cwd.
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
  # -o x.log would alias the log onto the reply; the log takes its own name. *[origin: ship review Z4]*
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
# GUARANTEE: every artifact (reply, log, events, temp and effective brief, plus the legacy `.events.2.jsonl`
# and `$OUT.tmp` an older install still writes) lies outside the invoking worktree and its git dirs, judged
# lexically and through symlinks. NOT covered: other worktrees of the repository, a hostile concurrent
# replacement — it guards accidental paths (a typo in `-o`, a brief inside the repo with no `-o`).
# It belongs to the INVOKED runner (2.21+) and holds across a hop, even to an older install without a
# guard; a pre-2.21 runner invoked DIRECTLY is outside it. Argv-known paths and $TMPDIR are judged first; a
# refusal exits 2 with ONE line before any paid call or truncation ($LOG included); a check that cannot run
# refuses too. Relative paths resolve against the ORIGINAL cwd, hence before any chdir.
# *[origin: `-o` naming a tracked file was silently overwritten — change detection excludes artifacts by design]*
hjw_artifact_guard() {
  # $@ = artifact FILE paths.
  hjw_artifact_check "" "$@"
}

hjw_artifact_dir_guard() {
  # $@ = mktemp's DIRECTORIES (containment only); a separate entry point because `-o` may be any string.
  hjw_artifact_check --dir "$@"
}

hjw_artifact_check() {
  # $1 = "" (files) or --dir; detect.py's <cwd> is always "" (the invoking cwd).
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
# OWNERSHIP, not the shell's word: CLAUDE_PLUGIN_DATA in a host's Bash is the LAST LOADED plugin's data dir.
# *[origin: measured 2026-09-28 — in a three-plugin session the runner read another vendor's dir, found "no
# config", and ran three consults at the CLI default in a sandbox the owner had configured away from]*
# EXACTLY ONE owner path, in order: structural (runner in <plugins>/cache/haejwo/haejwo/<ver>/scripts/ ->
# <plugins>/data/haejwo-haejwo/config.json; env ignored) > env (CLAUDE_PLUGIN_DATA whose basename is exactly
# haejwo-haejwo) > derived (${HOME}/.codex|.claude/plugins/data/haejwo-haejwo/config.json by /.codex/ in
# $HJW_SELF) > none. NO further fallback: a missing or malformed owner file never yields another path, which
# could resurrect a stale danger-full-access consent. Freshness was settled earlier by hjw_forward_if_stale.
# A foreign CLAUDE_PLUGIN_DATA (judged on the variable alone) is ignored and disclosed once.
# A naming heuristic, not authentication: whoever can choose these paths already chose this runner.
# Derived in SHELL (a path may contain a newline; no command substitution); host detection reads the
# selected path's TEXT, never its canonical target.
hjw_data_dir_basename() {
  # Sets HJW_BASENAME: $1's last component after stripping ALL trailing separators. *[origin: measured
  # 2026-09-28 — `${1%/}` stripped one, so `haejwo-haejwo//` fell through to `derived`]*
  local d="$1"
  while [ "$d" != "${d%/}" ]; do d="${d%/}"; done
  HJW_BASENAME="${d##*/}"
}

hjw_esc_display() {
  # Sets HJW_ESC: $1 with \n \r \t escaped and other control characters as `?` — DISPLAY ONLY (CFG_PATH keeps
  # the raw bytes), one-way on purpose. A global, not stdout: `$(...)` would eat the trailing newline it shows.
  local s="$1"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  s="${s//[[:cntrl:]]/?}"
  HJW_ESC="$s"
}

hjw_config_resolve() {
  # Sets CFG_PATH, CFG_SOURCE, CFG_FOREIGN_NOTE. No stdout: a diagnostic would be mistaken for the path.
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
          # A real install is never at /; an empty prefix would name /data/haejwo-haejwo.
          [ -n "$plugins" ] && { CFG_PATH="$plugins/data/haejwo-haejwo/config.json"; CFG_SOURCE="structural"; }
          ;;
      esac
      ;;
  esac

  # (b) env — only when it names haejwo's OWN data dir.
  if [ -z "$CFG_PATH" ] && [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
    hjw_data_dir_basename "$CLAUDE_PLUGIN_DATA"
    if [ "$HJW_BASENAME" = "haejwo-haejwo" ]; then
      # The variable's own spelling stays the path: a trailing separator is not significant.
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

  # (d) Report only a FOREIGN variable — keyed on the variable, not on CFG_SOURCE != env, which called
  # haejwo's own dir foreign under a structural install. *[origin: measured 2026-09-28]*
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
  # The foreign-variable note, once, into $LOG and stderr — called AFTER the header write truncates $LOG.
  [ -n "$CFG_FOREIGN_NOTE" ] || return 0
  [ "$CFG_FOREIGN_REPORTED" = 1 ] && return 0
  CFG_FOREIGN_REPORTED=1
  printf '%s\n' "$CFG_FOREIGN_NOTE" >> "$LOG" 2>/dev/null
  printf '%s\n' "$CFG_FOREIGN_NOTE" >&2
  return 0
}

hjw_config_values() {
  # Prints model=/effort=/ignored= (non-string keys), or NOTHING when the `codex` block describes the OTHER
  # vendor's reviewer (host-relative; a vendorless custom root follows the runner kind, config.py). A parse
  # failure is "no config". *[origin: a live smoke launched the claude reviewer with the codex host's own model name]*
  [ -n "$CFG_PATH" ] && [ -f "$CFG_PATH" ] || return 0
  bounded 60 python3 "$HJW_LIB/config.py" values "$HJW_RUNNER_KIND" "$CFG_PATH" 2>/dev/null
}

hjw_config_sandbox() {
  # Read on ANY host (it describes how THIS runner launches). Printed raw: the caller's allowlist decides,
  # so the transport cannot trim an exotic value into a valid one.
  [ -n "$CFG_PATH" ] && [ -f "$CFG_PATH" ] || return 0
  bounded 60 python3 "$HJW_LIB/config.py" sandbox "$CFG_PATH" 2>/dev/null
}

hjw_config_status() {
  # ok | absent | malformed for the owner path; `none` belongs to CFG_SOURCE (no path was selected).
  [ -n "$CFG_PATH" ] || { printf 'none'; return 0; }
  bounded 60 python3 "$HJW_LIB/config.py" status "$CFG_PATH" 2>/dev/null
}

hjw_config_load() {
  # Sets CFG_PATH/SOURCE/STATUS and the CFG_* values; emits the "ignored" notes in config order. The
  # foreign-variable note waits for hjw_config_disclose ($LOG does not exist yet).
  hjw_config_resolve
  CFG_STATUS="$(hjw_config_status)"
  [ -n "$CFG_STATUS" ] || CFG_STATUS="absent"
  # The header is ONE line: control characters are escaped for display only, never in CFG_PATH.
  hjw_esc_display "${CFG_PATH:-none}"
  CFG_DISP="config=$HJW_ESC ($CFG_SOURCE) config_status=$CFG_STATUS"
  CFG_MODEL=""; CFG_EFFORT=""; CFG_IGNORED=""
  local values old_ifs k
  values="$(hjw_config_values)"
  CFG_MODEL="$(printf '%s\n' "$values" | sed -n 's/^model=//p')"
  CFG_EFFORT="$(printf '%s\n' "$values" | sed -n 's/^effort=//p')"
  CFG_IGNORED="$(printf '%s\n' "$values" | sed -n 's/^ignored=//p')"
  # Both runners read model and effort (2.24), so every non-string key's note is actionable.
  if [ -n "$CFG_IGNORED" ]; then
    old_ifs="$IFS"; IFS=','
    for k in $CFG_IGNORED; do
      [ -n "$k" ] && echo "note: config codex.$k ignored (not a string)" >&2
    done
    IFS="$old_ifs"
  fi
  return 0
}

# ---- change detection (A5): file-backed before/after snapshots ----
# Attribution is NOT established: a concurrent save looks like a reviewer edit, so the failure says
# "attribution unknown" and never proposes reverting.
git_snapshot() {
  # $1 = snapshot JSON; non-zero = detection unavailable (fail closed). JSON because filenames may hold
  # newlines or tabs. $ARTIFACTS is the entrypoint's list of runner-owned paths.
  bounded 60 python3 "$HJW_LIB/detect.py" snapshot "$WORKDIR" "$1" "${ARTIFACTS[@]}"
}

snapshot_diff() {
  # $1/$2 = before/after JSON; prints truncated=/unreadable=/changed=. An unreadable snapshot exits non-zero.
  bounded 60 python3 "$HJW_LIB/detect.py" compare "$1" "$2"
}

hjw_git_preflight() {
  # Creates SNAPDIR and probes git: inside a repo, genuinely not a repo (the documented non-git path), or
  # "git could not tell" (broken repo, permissions, timeout) — the third is never read as "not a repo".
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

# Fail BEFORE spending a reviewer run: a consult whose no-edit contract cannot be verified is worthless.
detect_fail_now() {
  # Bypasses fail(): no report block, only this message and git's own explanation, which also goes to
  # $LOG (opened before the preflight) for a caller who keeps only the log.
  [ -n "$SNAPDIR" ] && [ -s "$SNAPDIR/detect.err" ] && { echo "# ---- change detection stderr ----"; cat "$SNAPDIR/detect.err"; } >> "$LOG"
  echo "✗ ${HJW_RUNNER_KIND}_consult FAILED (mode=$MODE, 0s):${COVERAGE_NOTE:-}" >&2
  echo "  - $DETECT_MSG" >&2
  [ -n "$SNAPDIR" ] && [ -s "$SNAPDIR/detect.err" ] && sed 's/^/  /' "$SNAPDIR/detect.err" >&2
  exit 1
}

hjw_detect_before() {
  # 0 = BEFORE snapshot taken (or the run already failed out); 1 = genuinely not a git repo, where VENDOR
  # policy decides in the entrypoint (codex: a read-only sandbox may enforce; claude: always refuses).
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

# Accumulates $1 in $FAIL_MSG, which hjw_fail_header prints on stderr.
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
      # Surface WHY into the log: the message is a contract string, git's error is the diagnostic.
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
# Built from explicit vendor fields; never guesses a vendor's disclosures (codex: effort/sandbox; claude: effort only).
hjw_fail_header() {
  echo "✗ ${HJW_RUNNER_KIND}_consult FAILED (mode=$MODE, ${DUR}s):${COVERAGE_NOTE:-}" >&2
  printf '%s' "$FAIL_MSG" >&2
}

hjw_fail_tail() {
  # Tail of every failure block, and the exit: a non-zero CLI status passes through.
  echo "  --- last 12 log lines ($LOG) ---" >&2
  tail -12 "$LOG" >&2
  [ "$rc" -ne 0 ] && exit "$rc" || exit 1
}

hjw_log_coverage_note() {
  [ -n "$COVERAGE_NOTE" ] && echo "# ---- change detection:$COVERAGE_NOTE ----" >> "$LOG"
  return 0
}
