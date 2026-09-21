#!/usr/bin/env python3
"""Golden differential harness for the two reviewer runners (2.13 step 2).

WHY
    tests/baseline/2.13-step1/ holds a byte-exact copy of
    `haejwo/scripts/codex_consult.sh` and `claude_consult.sh` as they stood at
    commit 6d09729 — the two runners BEFORE the shared-internals extraction.
    Every scenario below runs TWICE, once against that frozen baseline and
    once against the working-tree scripts, in two separately built, identical
    environments; every observable byte is then compared. Anything the
    refactor changes that is not explained by one of the documented
    normalizations below is a FAILURE.

    It exists to gate the extraction: the extraction must be provably
    behavior-preserving, not plausibly so.

WHAT IS COMPARED (G3/H4)
    exit code; stdout and stderr as SEPARATE channels (plus, for the cleanup
    scenario only, a second fixture that merges them because their ORDER is the
    behavior under test); how many `--version` probes the CLI stub saw, apart
    from reviewer calls; per reviewer call the argv (newline AND NUL-delimited
    forms), stdin, cwd, the exported environment (`env -0`; isolation and
    fixture variables kept with their values mapped through the recorded-path
    table, ambient variables as sha256:16), the umask, the completion stamp and
    the handshake acknowledgment; the ordered list, the bytes and the
    type/mode/link-target of every artifact; an explicit TMPDIR inventory
    (name, type, mode, size, link target); `git status --porcelain -z`, a
    sha256 fingerprint of every tracked file, and `git worktree list
    --porcelain` of the scratch repository afterwards; and, where the fixture
    asks for it, the recorded descendant pid and whether it is gone.

WHAT IS NORMALIZED (G4/H1) — and NOTHING else
    N1  PATHS THAT WERE ACTUALLY RECORDED FOR THIS RUN, and only those: the
        directories the harness itself created (run root, scratch repo, brief,
        output, bin, capture, home, config, TMPDIR, the runner's own
        directory), plus generated paths read back from DESIGNATED STRUCTURAL
        SOURCES — a call's `cwd` file, absolute elements of a call's argv, the
        real filesystem listings of the artifact and temp directories, the
        runner's own `# ---- snapshot: <path> ----` record line, and mktemp
        results anchored at the recorded TMPDIR prefix. A bare temp-looking
        NAME in free text is never touched: prose that says
        `codex_brief.abc123.md` is compared byte for byte.
    N2  git's worktree administrative name (.git/worktrees/<name>), which git
        may deduplicate with a suffix. Ownership/cleanup is asserted from
        `git worktree list`.
    N3  DESIGNATED METADATA FIELDS ONLY: the log header's date line, the
        `# snapshot capture:` interval line, the snapshot note's capture window
        inside the effective brief, the stub's completion stamp, the recorded
        descendant pid, and the duration token inside the runner's own
        result/failure lines. Arbitrary prose — including a reply that says
        "timed out after 4s" — is NOT normalized.
    N4  (folded into N3's designation list) the recorded descendant pid.
    N5  native shell diagnostic source locations (`<script>: line N:`), and
        ONLY for the explicitly enumerated relocated operations in
        N5_RELOCATED — EMPTY in this commit, so line numbers must match
        byte-for-byte.

    Tokens are emitted between private-use-area markers (U+E000/U+E001) that
    cannot occur in the captured bytes, and any item that ALREADY carries a
    marker or a literal token form fails the run instead of being mapped.

    Deliberately kept EXACT: the fixtures' own event timestamps and thread ids,
    the scratch repositories' HEAD ids (fixed identities and dates make them
    deterministic — every scenario asserts both sides got the same HEAD),
    content hashes and snapshot digests.

Stdlib only; invoked from tests/test_hooks.py via `run(check, toolkit)`.
"""
import difflib
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

BASELINE_COMMIT = "6d09729b6cdcec628941349fb2582c40650eea86"
BASELINE_SHORT = "6d09729"
# H3: the expected bytes are pinned HERE, in code, as well as in the bundle's
# SHA256SUMS. A bundle edited together with its own checksum file still fails.
BASELINE_SHA256 = {
    "codex_consult.sh": "c95846b8d3012b85fbf7698d7fb42d4655bb16b0f17d2a23876accc5e08f220d",
    "claude_consult.sh": "2a838332e4fc2171a0f0e56832932bd97376ebd072f49974ecfd8834665c1873",
}
HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE_DIR = os.path.join(HERE, "baseline", "2.13-step1")
REPO_ROOT = os.path.dirname(HERE)
RUNNER_FILES = ("codex_consult.sh", "claude_consult.sh")

# Fixed git identity AND dates. The scratch repositories' commit ids are part
# of the compared output (`snapshot=<sha7>`, `HEAD a→b`, `git worktree list`),
# so they have to be identical on both sides — which they are, byte for byte,
# once identity, dates and content are pinned.
GIT_FIXED = {
    "GIT_AUTHOR_NAME": "haejwo golden",
    "GIT_AUTHOR_EMAIL": "golden@example.invalid",
    "GIT_COMMITTER_NAME": "haejwo golden",
    "GIT_COMMITTER_EMAIL": "golden@example.invalid",
    "GIT_AUTHOR_DATE": "1767322445 +0000",
    "GIT_COMMITTER_DATE": "1767322445 +0000",
}

# Every env var either runner reads: popped from the inherited environment so
# an ambient value can never reach one side only.
RUNNER_ENV_VARS = ("CODEX_MODEL", "CODEX_EFFORT", "CODEX_SANDBOX", "CLAUDE_MODEL",
                   "CODEX_ALLOW_MARKERS", "CODEX_TIMEOUT", "CLAUDE_TIMEOUT",
                   "CLAUDE_PLUGIN_DATA")

# H4/I3: the environment the stub dumps is compared in full.
# Nothing is discarded. Variables the harness sets or the runners read are
# recorded with their VALUE — including HOME/PATH/TMPDIR/CLAUDE_PLUGIN_DATA,
# whose values are per-run PATHS and are therefore mapped by the recorded-path
# table like any other path (PATH entry by entry: recorded directories map,
# ambient ones stay exact). Every other (ambient) variable is recorded as
# name + digest: an added, removed or changed one still fails the comparison,
# without a failing CI log printing the operator's own environment.
ENV_ISOLATION = ("PATH", "HOME", "TMPDIR", "CLAUDE_PLUGIN_DATA")
ENV_VERBATIM_PREFIXES = ("STUB_",)
ENV_VERBATIM = set(RUNNER_ENV_VARS) | set(GIT_FIXED) | set(ENV_ISOLATION) | {
    "LC_ALL", "LANG", "TZ", "PWD", "OLDPWD", "SHLVL", "_"}

RUN_TIMEOUT = 180
WORKERS = 8

OK_EVENTS = (
    '{"type":"thread.started","thread_id":"stub"}',
    '{"type":"turn.started"}',
    '{"type":"item.started","item":{"type":"agent_message"}}',
    '{"type":"item.completed","item":{"type":"agent_message","text":"fine"}}',
    '{"type":"turn.completed"}',
)


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _dec(data):
    return data.decode("utf-8", "surrogateescape")


# --------------------------------------------------------------------------
# G1/H3 — baseline provenance
# --------------------------------------------------------------------------
def _verify_baseline(check, scripts_root):
    """Verify the bundled baseline against the digests pinned in THIS file and
    against the bundle's SHA256SUMS, and — when the git history is present —
    against the commit itself. CI checks out shallow, so `git show <old sha>`
    is NOT available there; that case prints one line and continues on the
    bundled bytes, which both digest sources still pin."""
    sums_path = os.path.join(BASELINE_DIR, "SHA256SUMS")
    try:
        sums_text = open(sums_path, encoding="utf-8").read()
    except Exception as exc:
        check("golden: baseline SHA256SUMS is readable", False, str(exc))
        return False, ""
    want = {}
    commit = ""
    for line in sums_text.splitlines():
        if line.startswith("commit "):
            commit = line.split(None, 1)[1].strip()
        elif line and not line.startswith("#"):
            want[line.split()[-1]] = line.split()[0]
    check("golden: SHA256SUMS pins the frozen baseline commit %s" % BASELINE_SHORT,
          commit == BASELINE_COMMIT, commit)

    bad = []
    for name in RUNNER_FILES:
        try:
            got = hashlib.sha256(_read_bytes(os.path.join(BASELINE_DIR, name))).hexdigest()
        except Exception as exc:
            bad.append("%s: %s" % (name, exc))
            continue
        if got != BASELINE_SHA256[name]:
            bad.append("%s: %s != pinned %s" % (name, got, BASELINE_SHA256[name]))
        if got != want.get(name):
            bad.append("%s: %s != SHA256SUMS %s" % (name, got, want.get(name)))
    check("golden: bundled baseline matches BOTH the pinned digests and SHA256SUMS",
          not bad and commit == BASELINE_COMMIT, "; ".join(bad))
    if bad or commit != BASELINE_COMMIT:
        return False, ""

    have_history = subprocess.run(
        ["git", "cat-file", "-e", BASELINE_COMMIT + "^{commit}"],
        cwd=REPO_ROOT, capture_output=True).returncode == 0
    if have_history:
        drift = []
        for name in RUNNER_FILES:
            p = subprocess.run(["git", "show", "%s:haejwo/scripts/%s" % (BASELINE_COMMIT, name)],
                               cwd=REPO_ROOT, capture_output=True)
            if p.returncode != 0:
                drift.append("%s: git show rc=%d" % (name, p.returncode))
            elif p.stdout != _read_bytes(os.path.join(BASELINE_DIR, name)):
                drift.append("%s: bundled bytes differ from %s" % (name, BASELINE_SHORT))
        check("golden: bundled baseline equals %s in git history" % BASELINE_SHORT,
              not drift, "; ".join(drift))
        if drift:
            return False, ""
    else:
        print("  baseline: bundled bytes only (git history unavailable)")

    # The baseline runs from a directory that MIRRORS haejwo/scripts/, so any
    # relative lookup a runner makes behaves exactly as it does in the repo.
    mirror = os.path.join(scripts_root, "haejwo", "scripts")
    os.makedirs(mirror, exist_ok=True)
    for name in RUNNER_FILES:
        shutil.copy2(os.path.join(BASELINE_DIR, name), os.path.join(mirror, name))
    return True, mirror


# --------------------------------------------------------------------------
# H2 — tokens that cannot occur in literal bytes
# --------------------------------------------------------------------------
MARK_OPEN, MARK_CLOSE = "", ""          # Unicode private use area
_LITERAL_TOKEN_RE = re.compile(
    r"\{(?:[A-Za-z][A-Za-z0-9_-]*#\d+|RUN|REPO|TMP|SCRIPTS|dur|log-date"
    r"|capture-ts|stub-end|pid)\}")


def _tok(label):
    return MARK_OPEN + label + MARK_CLOSE


class TokenLiteral(Exception):
    """A captured item already contains a token marker or a literal token
    form — mapping it would be indistinguishable from the real thing."""


def _guard_literals(items):
    for name, text in items:
        for blob, where in ((name, "name"), (text, "body")):
            if MARK_OPEN in blob or MARK_CLOSE in blob:
                raise TokenLiteral("item %r %s already contains a token marker" % (name, where))
            m = _LITERAL_TOKEN_RE.search(blob)
            if m:
                raise TokenLiteral("item %r %s already contains the literal token form %s"
                                   % (name, where, m.group(0)))


# --------------------------------------------------------------------------
# G4/H1 — normalization
# --------------------------------------------------------------------------
# mktemp templates the runners use. Only ever matched when anchored to a
# recorded directory (see `_anchored_stems`), never as a bare name.
TEMP_FAMILIES = ("hjw_snapmeta", "hjw_snap", "hjw_bounded",
                 "codex_effective", "claude_effective",
                 "codex_brief", "claude_brief",
                 "codex_snap", "claude_snap")
_STEM = r"(?:%s)\.[A-Za-z0-9]{6}" % "|".join(TEMP_FAMILIES)
_SNAP_RECORD_RE = re.compile(r"(?m)^# ---- snapshot: (.*) ----$")
_WT_ADMIN_RE = re.compile(r"(\.git/worktrees/)([^/\s\x00\"']+)")
_CLEANUP_RECORD = "# ---- snapshot cleanup ----"

# N3 — each pattern is anchored to the exact field that carries the value, and
# each is applied only to the channels/items that field can appear in.
_LOG_HEADER_DATE_RE = re.compile(
    r"(?m)^(# (?:codex|claude)_consult v[0-9.]+ .*?timeout=\d+s  ).*$")
_SNAP_CAPTURE_RE = re.compile(
    r"(?m)^(# snapshot capture: )\S+ \.\. \S+( \(NOT atomic)")
_SNAP_NOTE_PREFIX = "SNAPSHOT: this run executes in a detached snapshot of"
_SNAP_NOTE_RE = re.compile(
    r"(captured between )\S+( and )\S+( \(capture is not atomic\))")
_RESULT_DUR_RE = re.compile(
    r"(?m)^(=== (?:Codex|Claude) reply \(.*\) — mode=consult, )\d+s")
_FAILURE_DUR_RE = re.compile(
    r"(?m)^(✗ (?:codex|claude)_consult FAILED \(mode=consult, )\d+s")
_TIMED_OUT_RE = re.compile(
    r"(?m)^(  - timed out after )\d+s( \(tune with (?:CODEX|CLAUDE)_TIMEOUT\))$")
_STUB_END_RE = re.compile(r"\A\d+\.\d+\Z")
_PID_RE = re.compile(r"\A\d+\Z")

RUNNER_CHANNELS = ("stdout", "stderr", "combined")

# N5: shell diagnostics (`<script>: line N: ...`) whose line number legitimately
# moves because the operation was RELOCATED by the extraction. Each entry names
# one operation and is reviewed by hand. Empty in this commit: nothing has moved
# yet, so every diagnostic — line number included — must match byte for byte.
N5_RELOCATED = ()


def _anchored_stems(items, tmpdirs):
    """Generated temp paths, found ONLY where a recorded temp directory prefix
    anchors them. Returns complete path stems in first-appearance order."""
    found = []
    seen = set()
    pats = [re.compile(re.escape(d) + "/" + _STEM) for d in tmpdirs if d]
    for name, text in items:
        for pat in pats:
            for m in list(pat.finditer(name)) + list(pat.finditer(text)):
                if m.group(0) not in seen:
                    seen.add(m.group(0))
                    found.append(m.group(0))
    return found


def _path_table(capture):
    """Build the N1 replacement table: recorded path -> token. Keys are
    COMPLETE paths (never bare components); a token is handed out exactly once,
    so it can never stand for two different paths."""
    table = {}
    used = set()
    order = [(p, lab, False) for p, lab in capture["known"]]
    order += [(p, None, True)
              for p in _anchored_stems(capture["items"], capture["tmpdirs"])]
    registered = []
    gen = 0
    for path, label, is_stem in order:
        if not path:
            continue
        if label is None and not is_stem and any(
                path == r or path.startswith(r.rstrip("/") + "/") for r in registered):
            # already inside a recorded directory: that directory's token maps
            # the prefix and the basename stays readable and comparable.
            continue
        variants = [path]
        try:
            real = os.path.realpath(path)
        except Exception:
            real = path
        if real != path:
            variants.append(real)
        registered.extend(variants)
        if all(v in table for v in variants):
            continue
        if label is None:
            gen += 1
            label = "gen#%d" % gen
        assert label not in used, "token label reused: %s" % label
        used.add(label)
        for v in variants:
            table.setdefault(v, _tok(label))
    # Longest first: a shorter recorded path must never eat a longer one.
    return sorted(table.items(), key=lambda kv: -len(kv[0]))


def _n1_paths(text, ctx):
    """N1: replace the paths this run actually recorded — nothing else."""
    for raw, token in ctx["paths"]:
        if raw in text:
            text = text.replace(raw, token)
    return text


def _wt_sub(text, ctx):
    def sub(m):
        key = ("wt", m.group(2))
        if key not in ctx["wt"]:
            ctx["wt"][key] = _tok("wt#%d" % (len(ctx["wt"]) + 1))
        return m.group(1) + ctx["wt"][key]

    return _WT_ADMIN_RE.sub(sub, text)


def _n2_worktree_admin(text, item, ctx):
    """N2: git's own administrative worktree name (it may append a suffix to
    deduplicate), in the STRUCTURALLY IDENTIFIED sources only — the
    `git worktree list --porcelain` capture and the runner's own
    `# ---- snapshot: …` / `# ---- snapshot cleanup ----` record lines in its
    log. Free text (a reply, a brief, prose on stdout/stderr) is never touched:
    prose that mentions `.git/worktrees/alpha` is compared byte for byte.
    Ownership/cleanup is asserted from `git worktree list` itself."""
    if item == "git/worktrees":
        return _wt_sub(text, ctx)
    if not (item in RUNNER_CHANNELS or item.endswith(".log")):
        return text
    out, in_cleanup = [], False
    for line in text.split("\n"):
        if line.startswith("# ---- "):
            in_cleanup = line.startswith(_CLEANUP_RECORD)
        structural = in_cleanup or line.startswith("# ---- snapshot")
        out.append(_wt_sub(line, ctx) if structural else line)
    return "\n".join(out)


def _n3_designated_fields(text, item):
    """N3: clock/duration values, in the designated fields ONLY."""
    if item.endswith(".end"):                       # the stub's completion stamp
        return _STUB_END_RE.sub(_tok("stub-end"), text)
    if item == "pid/file":                          # the recorded descendant pid
        return _PID_RE.sub(_tok("pid"), text)
    if item.endswith(".stdin"):
        # The capture window, on the runner's OWN snapshot-note line only —
        # identified by the line it starts with. The rest of the brief is the
        # caller's prose and is compared byte for byte.
        repl = r"\1%s\2%s\3" % (_tok("capture-ts"), _tok("capture-ts"))
        return "\n".join(_SNAP_NOTE_RE.sub(repl, line)
                          if line.startswith(_SNAP_NOTE_PREFIX) else line
                          for line in text.split("\n"))
    log_like = item in RUNNER_CHANNELS or item.endswith(".log")
    if log_like:
        # the runner's own log header line and its snapshot capture record
        text = _LOG_HEADER_DATE_RE.sub(r"\1" + _tok("log-date"), text)
        text = _SNAP_CAPTURE_RE.sub(
            r"\1%s .. %s\2" % (_tok("capture-ts"), _tok("capture-ts")), text)
    if item in RUNNER_CHANNELS:
        # the measured duration inside the runner's result/failure lines
        text = _RESULT_DUR_RE.sub(r"\1" + _tok("dur") + "s", text)
        text = _FAILURE_DUR_RE.sub(r"\1" + _tok("dur") + "s", text)
        text = _TIMED_OUT_RE.sub(r"\1" + _tok("dur") + r"s\2", text)
    return text


def _n5_shell_lines(text):
    """N5: source locations in bash's own diagnostics, for relocated
    operations only. The list is empty in this commit, so this is a documented
    no-op and every `<script>: line N:` must match exactly."""
    for pattern in N5_RELOCATED:  # pragma: no cover - populated by the extraction
        text = re.sub(pattern,
                      lambda m: m.group(0).split(": line ")[0] + ": line " + _tok("n") + ":",
                      text)
    return text


def _normalize(text, item, ctx):
    text = _n1_paths(text, ctx)
    text = _n2_worktree_admin(text, item, ctx)
    text = _n3_designated_fields(text, item)
    return _n5_shell_lines(text)


def _normalized_items(capture):
    """Normalize one run's items IN ORDER (names included: an artifact's name
    can itself be a recorded path). Raises TokenLiteral when a captured item
    already carries a token marker or literal token form."""
    _guard_literals(capture["items"])
    ctx = {"paths": _path_table(capture), "wt": {}}
    return [(_normalize(name, name, ctx), _normalize(text, name, ctx))
            for name, text in capture["items"]]


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------
def _repr_lines(text):
    return [repr(line) for line in text.split("\n")]


def _differences(base, cand):
    """Returns (ok, detail, differing_item_names). A structural problem (item
    sets differ, a literal token) is reported as a difference too."""
    try:
        a, b = _normalized_items(base), _normalized_items(cand)
    except TokenLiteral as exc:
        return False, "token literal: %s" % exc, ["<token-literal>"]
    names_a, names_b = [n for n, _ in a], [n for n, _ in b]
    if names_a != names_b:
        only_a = [n for n in names_a if n not in names_b]
        only_b = [n for n in names_b if n not in names_a]
        return False, ("item set differs: baseline-only=%s candidate-only=%s"
                       % (only_a[:6], only_b[:6])), ["<item-set>"]
    diffs = [na for (na, ta), (_, tb) in zip(a, b) if ta != tb]
    if not diffs:
        return True, "", []
    first = diffs[0]
    ta = dict(a)[first]
    tb = dict(b)[first]
    diff = list(difflib.unified_diff(_repr_lines(ta), _repr_lines(tb),
                                     "baseline/" + first, "candidate/" + first,
                                     lineterm="", n=2))
    return False, "first differing item: %s\n    %s" % (first, "\n    ".join(diff[:30])), diffs


def _compare(base, cand):
    ok, detail, _ = _differences(base, cand)
    return ok, detail


# --------------------------------------------------------------------------
# G5/H5/H7 — the scenario matrix
# --------------------------------------------------------------------------
TRACE_LINE = ("2026-01-02T03:04:05.123456Z  ERROR codex_core::exec: "
              "sandbox helper failed\n")
HOOK_BLOCK_LINE = ("2026-01-02T03:04:05.123456Z  ERROR codex_core::exec: "
                   "Command blocked by PreToolUse hook: [haejwo gate] ...\n")


def _scenarios():
    """Every scenario runs for BOTH runners unless `runners` narrows it.

    Knobs (all optional): repo (committed|dirty|plain|gitlink|embedded|
    conflict|big|None=not a repo), snapshot, out, brief (file|stdin|
    newline-name), args, env (a `V_` prefix expands to the runner's vendor, so
    V_TIMEOUT becomes CODEX_TIMEOUT / CLAUDE_TIMEOUT), config, git_stub,
    mktemp, files, stub_env, combined, host_write, pidfile, timeout_limit,
    tmpdir, pre_files, tmp_artifacts, signal, relocate."""
    S = []

    # ---- clean runs, both input paths ----
    S.append(dict(name="file-brief-clean"))
    S.append(dict(name="stdin-brief-clean", brief="stdin", tmp_artifacts=True))

    # ---- model/effort resolution ----
    S.append(dict(name="config-model", config={"codex": {"model": "cfg-model"}}))
    S.append(dict(name="env-model-wins", config={"codex": {"model": "cfg-model"}},
                  env={"V_MODEL": "env-model"}))
    S.append(dict(name="config-effort-invalid", runners=("codex",),
                  config={"codex": {"effort": "insane"}}))

    # ---- --snapshot: the whole capture contract ----
    S.append(dict(name="snapshot-clean", repo="dirty", snapshot=True, out="out/reply.md"))
    # H7: acknowledgment handshake — the reviewer BLOCKS until the host's write
    # is acknowledged and records it, so "during the review" is proven.
    S.append(dict(name="snapshot-host-write-midrun", repo="dirty", snapshot=True,
                  out="out/reply.md",
                  stub_env={"STUB_RELEASE_FILE": "{run}/release",
                            "STUB_WAIT_ACK": "{run}/host-write.ack"},
                  host_write=("unstaged.txt", "the host kept working\n")))
    S.append(dict(name="snapshot-reviewer-writes", repo="dirty", snapshot=True,
                  out="out/reply.md",
                  stub_env={"STUB_TOUCH_FILE": "reviewer-wrote-this.txt"}))
    for kind, repo in (("unborn", "plain"), ("gitlink", "gitlink"),
                       ("conflict", "conflict"), ("embedded", "embedded")):
        S.append(dict(name="snapshot-refuse-" + kind, repo=repo, snapshot=True,
                      out="out/reply.md"))
    S.append(dict(name="snapshot-apply-failure", repo="dirty", snapshot=True,
                  out="out/reply.md", git_stub=("apply",)))
    S.append(dict(name="snapshot-capture-drift", repo="dirty", snapshot=True,
                  out="out/reply.md", git_stub=("drift", 2, "unstaged.txt")))
    S.append(dict(name="snapshot-cap-partial", repo="big", snapshot=True,
                  out="out/reply.md"))
    # H5: the SAME fixture twice — merged streams (their order is the behavior)
    # and separate streams (channel identity). Streams are merged nowhere else.
    S.append(dict(name="snapshot-cleanup-failure-merged", repo="dirty", snapshot=True,
                  out="out/reply.md", git_stub=("worktree-remove",), combined=True))
    S.append(dict(name="snapshot-cleanup-failure-split", repo="dirty", snapshot=True,
                  out="out/reply.md", git_stub=("worktree-remove",)))
    S.append(dict(name="snapshot-artifact-inside", repo="dirty", snapshot=True,
                  out="PINNED", mktemp="pin-snap"))
    S.append(dict(name="snapshot-effective-brief-unwritable", repo="dirty", snapshot=True,
                  out="out/reply.md", mktemp="block-effective", native_diagnostics=True))

    # ---- signals: an interrupted run must clean up identically (H7) ----
    for sig in ("TERM", "INT"):
        S.append(dict(name="signal-" + sig.lower(), repo="dirty", snapshot=True,
                      out="out/reply.md", signal=sig,
                      stub_env={"STUB_RELEASE_FILE": "{run}/release",
                                "STUB_SLEEP": "2"}))

    # ---- relocation: the runner reached through an unusual path (H7) ----
    S.append(dict(name="relocated-space-in-path", relocate="space"))
    S.append(dict(name="relocated-via-symlink", relocate="symlink"))
    S.append(dict(name="brief-name-ends-in-newline", brief="newline-name",
                  out="out/reply.md"))

    # ---- timeout + descendant ----
    S.append(dict(name="timeout-descendant", env={"V_TIMEOUT": "2"},
                  stub_env={"STUB_SLEEP": "6", "STUB_SPAWN_PIDFILE": "{run}/descendant.pid"},
                  pidfile=True, timeout_limit=2))

    # ---- non-git preconditions ----
    S.append(dict(name="nongit-refusal", repo=None,
                  env={"CODEX_SANDBOX": "workspace-write"}))
    S.append(dict(name="nongit-readonly-allowed", repo=None, runners=("codex",),
                  env={"CODEX_SANDBOX": "read-only"}))

    # ---- codex event stream / stderr classifier ----
    S.append(dict(name="events-turn-failed", runners=("codex",), files={"ev.jsonl": "".join(
        l + "\n" for l in ('{"type":"thread.started","thread_id":"stub"}',
                           '{"type":"turn.failed","error":{"message":"stream disconnected before completion"}}'))},
        stub_env={"STUB_EVENTS_FILE": "{run}/ev.jsonl"}))
    S.append(dict(name="events-malformed-mixed", runners=("codex",), files={"ev.jsonl": "".join(
        l + "\n" for l in ("not json at all", "[1, 2, 3]") + OK_EVENTS + ('{"broken": ',))},
        stub_env={"STUB_EVENTS_FILE": "{run}/ev.jsonl"}))
    S.append(dict(name="events-anonymous-objects", runners=("codex",),
                  files={"ev.jsonl": "{}\n{}\n{}\n"},
                  stub_env={"STUB_EVENTS_FILE": "{run}/ev.jsonl"}))
    S.append(dict(name="events-model-fallback", runners=("codex",),
                  config={"codex": {"fallback_model": "fb-model"}},
                  env={"CODEX_MODEL": "totally-fake-model"},
                  files={"ev1.jsonl": "".join(l + "\n" for l in (
                      '{"type":"thread.started","thread_id":"stub"}',
                      '{"type":"turn.failed","error":{"message":"unknown model: totally-fake-model"}}'))},
                  stub_env={"STUB_EVENTS_FILE_1": "{run}/ev1.jsonl",
                            "STUB_RC_1": "1", "STUB_NO_OUT_1": "1"}))
    S.append(dict(name="stderr-trace-anchored", runners=("codex",),
                  files={"trace.txt": TRACE_LINE},
                  stub_env={"STUB_STDERR_FILE": "{run}/trace.txt"}))
    S.append(dict(name="stderr-hook-block", runners=("codex",),
                  files={"trace.txt": HOOK_BLOCK_LINE},
                  stub_env={"STUB_STDERR_FILE": "{run}/trace.txt"}))

    # ---- temp-file guard, artifact naming, removed flags ----
    S.append(dict(name="unusable-tmpdir", tmpdir="missing"))
    S.append(dict(name="output-alias-log", out="out/x.log",
                  pre_files={"out/x.log": "PREVIOUS REPLY\n"}))
    S.append(dict(name="resume-refusal", args=["--resume"]))
    return S


# --------------------------------------------------------------------------
# one run: build a pristine environment, execute, capture
# --------------------------------------------------------------------------
def _fmt(value, mapping):
    return value.format(**mapping) if isinstance(value, str) else value


def _git_out(repo, args):
    env = dict(os.environ)
    env.update(GIT_FIXED)
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, env=env)


def _git_text(repo, args):
    if not repo or not os.path.exists(os.path.join(repo, ".git")):
        return "<not a git repository>"
    p = _git_out(repo, args)
    if p.returncode != 0:
        return "<git %s failed rc=%d>" % (" ".join(args), p.returncode)
    return _dec(p.stdout)


def _tracked_fingerprints(repo):
    """H4: sha256 per tracked path — `git status` alone cannot tell a reverted
    edit from no edit at all."""
    if not repo or not os.path.exists(os.path.join(repo, ".git")):
        return "<not a git repository>"
    p = _git_out(repo, ["ls-files", "-z"])
    if p.returncode != 0:
        return "<git ls-files failed rc=%d>" % p.returncode
    lines = []
    for raw in sorted(x for x in p.stdout.split(b"\0") if x):
        rel = _dec(raw)
        full = os.path.join(repo, rel)
        try:
            if os.path.islink(full):
                fp = "link:" + os.readlink(full)
            else:
                fp = "sha256:" + hashlib.sha256(_read_bytes(full)).hexdigest()
        except FileNotFoundError:
            fp = "absent"
        except Exception as exc:
            fp = "unreadable:%s" % exc
        lines.append("%s %s" % (repr(rel), fp))
    return "\n".join(lines)


def _pid_running(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _entry_meta(full, with_size=True):
    """H4: type, mode and link target of one recorded entry, keyed by its
    ABSOLUTE path (a recorded path, so it normalizes; a relative name would
    not)."""
    try:
        st = os.lstat(full)
    except Exception as exc:
        return "%s <unstatable: %s>" % (repr(full), exc)
    if stat.S_ISLNK(st.st_mode):
        kind, extra = "link", " target=%s" % repr(os.readlink(full))
    elif stat.S_ISDIR(st.st_mode):
        kind, extra = "dir", ""
    else:
        kind, extra = "file", (" size=%d" % st.st_size if with_size else "")
    return "%s type=%s mode=%04o%s" % (repr(full), kind, stat.S_IMODE(st.st_mode), extra)


def _walk(base):
    """Sorted (relpath, fullpath) for every entry under `base`."""
    out = []
    if not os.path.isdir(base):
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for n in sorted(dirnames) + sorted(filenames):
            full = os.path.join(dirpath, n)
            out.append((os.path.relpath(full, base), full))
    out.sort()
    return out


def _prepare(scen, runner, side, scripts_dir, root, tk):
    """Build one pristine run environment. Nothing is shared with any other
    run: its own repo, HOME, TMPDIR, CLAUDE_PLUGIN_DATA, stub bin and capture
    dir, brief and output dirs. Returns the plan plus the list of paths the
    harness CREATED (the seed of the N1 table)."""
    # `side` is a SINGLE CHARACTER by contract (see SIDE_KEYS): the run root's
    # length is part of every artifact's recorded size, because the runner
    # embeds paths in the log it writes. Equal-length roots on both sides keep
    # those sizes comparable without normalizing a number.
    assert len(side) == 1, "side keys must be one character: %r" % side
    run_dir = os.path.join(root, side, "%s.%s" % (scen["name"], runner))
    os.makedirs(run_dir, exist_ok=True)
    known = []

    def reg(path, label):
        if path:
            known.append((path, label))

    d = {}
    for part in ("home", "tmp", "bin", "cap", "briefs", "out", "cfg"):
        d[part] = os.path.join(run_dir, part)
        os.makedirs(d[part], exist_ok=True)

    kind = scen.get("repo", "committed")
    if kind is None:
        repo = None
        cwd = os.path.join(run_dir, "notarepo")
        os.makedirs(cwd, exist_ok=True)
        reg(cwd, "notarepo")
    else:
        repo = tk["repos"][kind]("repo", run_dir)
        cwd = repo

    tk["make_stub"](d["bin"], runner, d["cap"])
    git_stub = scen.get("git_stub")
    if git_stub:
        if git_stub[0] == "drift":
            tk["make_snapshot_git_stub"](d["bin"], "drift", nth=git_stub[1],
                                         touch=os.path.join(repo, git_stub[2]))
        else:
            tk["make_snapshot_git_stub"](d["bin"], git_stub[0])

    mapping = {"run": run_dir, "repo": repo or "", "tmp": d["tmp"],
               "home": d["home"], "out": d["out"]}

    pinned_snap = os.path.join(d["tmp"], "pinned-snap")
    blocker = os.path.join(run_dir, "blocker-dir")
    if scen.get("mktemp") == "pin-snap":
        tk["make_mktemp_stub"](d["bin"], [("hjw_snap.XXXXXX", pinned_snap)])
        reg(pinned_snap, "pinned-snap")
    elif scen.get("mktemp") == "block-effective":
        tk["make_mktemp_stub"](d["bin"], [("_effective.XXXXXX.md", blocker)])
        reg(blocker, "blocker-dir")

    for rel, text in scen.get("files", {}).items():
        tk["write_file"](os.path.join(run_dir, rel), text)
    for rel, text in scen.get("pre_files", {}).items():
        tk["write_file"](os.path.join(run_dir, rel), text)

    brief_name = "brief.md\n" if scen.get("brief") == "newline-name" else "brief.md"
    brief_path = os.path.join(d["briefs"], brief_name)
    tk["write_file"](brief_path, "Golden differential brief body.\n")
    stdin_data = b""
    if scen.get("brief") == "stdin":
        brief_arg = "-"
        stdin_data = b"Golden differential stdin brief body.\n"
    else:
        brief_arg = brief_path

    out_spec = scen.get("out")
    if out_spec == "PINNED":
        out_path = os.path.join(pinned_snap, "reply.md")
    elif out_spec:
        out_path = os.path.join(run_dir, out_spec)
    else:
        out_path = None

    args = []
    if scen.get("snapshot"):
        args.append("--snapshot")
    if out_path:
        args += ["-o", out_path]
    args += list(scen.get("args", []))
    args.append(brief_arg)

    # relocation (H7): the COMPLETE tree travels, so a future lib/ goes with it
    eff_scripts = scripts_dir
    if scen.get("relocate") == "space":
        eff_scripts = os.path.join(run_dir, "re located tree", "haejwo", "scripts")
        shutil.copytree(scripts_dir, eff_scripts,
                        ignore=shutil.ignore_patterns("__pycache__"))
    script = os.path.join(eff_scripts, "%s_consult.sh" % runner)
    if scen.get("relocate") == "symlink":
        link = os.path.join(run_dir, "relocated-%s.sh" % runner)
        os.symlink(script, link)
        reg(link, "runner-symlink")
        script = link
    reg(eff_scripts, "scripts")

    env = dict(os.environ)
    for var in RUNNER_ENV_VARS:
        env.pop(var, None)
    env.update(GIT_FIXED)
    tmpdir = (os.path.join(d["tmp"], "does-not-exist")
              if scen.get("tmpdir") == "missing" else d["tmp"])
    env.update({
        "PATH": d["bin"] + os.pathsep + os.environ.get("PATH", ""),
        "HOME": d["home"],
        "TMPDIR": tmpdir,
        "LC_ALL": "C", "LANG": "C", "TZ": "UTC",
    })
    cfg_payload = scen.get("config")
    if cfg_payload is None or runner == "codex":
        cfg_dir = os.path.join(d["cfg"], "data")
    else:
        # HOST-RELATIVE config: the claude runner honors codex.model only when
        # the resolved path is a codex host's (under /.codex/); the codex
        # runner ignores it exactly there. Same payload, right path per side.
        cfg_dir = os.path.join(d["cfg"], ".codex", "plugins", "data", "haejwo-haejwo")
    os.makedirs(cfg_dir, exist_ok=True)
    if cfg_payload is not None:
        with open(os.path.join(cfg_dir, "config.json"), "w") as f:
            json.dump(cfg_payload, f)
    env["CLAUDE_PLUGIN_DATA"] = cfg_dir

    for key, value in scen.get("env", {}).items():
        if key.startswith("V_"):
            key = runner.upper() + key[1:]
        env[key] = _fmt(value, mapping)
    for key, value in scen.get("stub_env", {}).items():
        env[key] = _fmt(value, mapping)

    # The run's own directories are recorded paths by definition; the deepest
    # ones first so a nested directory keeps its own token.
    for part in ("cfg", "cap", "bin", "out", "briefs", "tmp", "home"):
        reg(d[part], part)
    reg(tmpdir, "tmpdir")
    reg(cfg_dir, "config-dir")
    reg(repo, "repo")
    reg(run_dir, "run")

    return {
        "scen": scen, "runner": runner, "side": side, "run_dir": run_dir,
        "dirs": d, "repo": repo, "cwd": cwd, "env": env, "script": script,
        "args": args, "stdin": stdin_data, "out_path": out_path,
        "known": known, "tmpdirs": [d["tmp"], tmpdir],
    }


def _host_writer(release, target, text, ack_path, observed):
    """H7: wait for the reviewer's readiness marker, write into the ORIGINAL,
    then acknowledge — the reviewer blocks on that acknowledgment, so the write
    is PROVEN to have landed while the review was still running."""
    deadline = time.time() + 60
    while time.time() < deadline and not os.path.isfile(release):
        time.sleep(0.02)
    if not os.path.isfile(release):
        return
    with open(target, "w") as f:
        f.write(text)
    with open(ack_path, "w") as f:
        f.write("host-write-acked")
    observed.append(True)


def _await_marker(path, deadline_s=60):
    deadline = time.time() + deadline_s
    while time.time() < deadline and not os.path.isfile(path):
        time.sleep(0.02)
    return os.path.isfile(path)


def _execute(plan):
    """Run one side and capture everything."""
    scen = plan["scen"]
    writer = None
    observed = []
    if scen.get("host_write"):
        rel, text = scen["host_write"]
        writer = threading.Thread(target=_host_writer, args=(
            plan["env"]["STUB_RELEASE_FILE"], os.path.join(plan["repo"], rel),
            text, plan["env"]["STUB_WAIT_ACK"], observed))
        writer.start()

    cmd = ["bash", plan["script"]] + plan["args"]
    started = time.time()
    signal_note = None
    if scen.get("signal"):
        # H7: a synchronized signal — delivered only once the reviewer has said
        # it is running, so the interrupt always lands in the same place.
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=plan["cwd"], env=plan["env"])
        released = _await_marker(plan["env"]["STUB_RELEASE_FILE"])
        proc.send_signal(signal.SIGTERM if scen["signal"] == "TERM" else signal.SIGINT)
        out_b, err_b = proc.communicate(timeout=RUN_TIMEOUT)
        rc, combined_b = proc.returncode, b""
        signal_note = "%s delivered after readiness marker: %s" % (
            scen["signal"], "yes" if released else "NO (fixture proved nothing)")
    elif scen.get("combined"):
        # ONE stream, for the cleanup fixture only: the ORDER of the reply
        # (stdout) and the cleanup failure (stderr) is the behavior under test.
        # Its separate-stream twin keeps channel identity under test as well.
        stream = os.path.join(plan["run_dir"], "combined.stream")
        with open(stream, "wb") as fh:
            p = subprocess.run(cmd, input=plan["stdin"], stdout=fh,
                               stderr=subprocess.STDOUT, cwd=plan["cwd"],
                               env=plan["env"], timeout=RUN_TIMEOUT)
        rc, out_b, err_b, combined_b = p.returncode, b"", b"", _read_bytes(stream)
    else:
        p = subprocess.run(cmd, input=plan["stdin"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, cwd=plan["cwd"],
                           env=plan["env"], timeout=RUN_TIMEOUT)
        rc, out_b, err_b, combined_b = p.returncode, p.stdout, p.stderr, b""
    elapsed = time.time() - started
    if writer is not None:
        writer.join()

    known = list(plan["known"])
    cap = plan["dirs"]["cap"]
    items = [("rc", str(rc))]
    if scen.get("combined"):
        items.append(("combined", _dec(combined_b)))
    else:
        items.append(("stdout", _dec(out_b)))
        items.append(("stderr", _dec(err_b)))
    if signal_note:
        items.append(("signal/delivery", signal_note))

    vfile = os.path.join(cap, "_version_calls")
    items.append(("version_probes",
                  open(vfile).read().strip() if os.path.isfile(vfile) else "0"))
    n = 0
    while os.path.isfile(os.path.join(cap, "call_%d.stdin" % (n + 1))):
        n += 1
    items.append(("calls/count", str(n)))
    for i in range(1, n + 1):
        for suffix in ("argv", "argv0", "stdin", "cwd", "umask", "ack", "end"):
            path = os.path.join(cap, "call_%d.%s" % (i, suffix))
            text = _dec(_read_bytes(path)) if os.path.isfile(path) else "<absent>"
            items.append(("calls/%d.%s" % (i, suffix), text))
            if suffix == "cwd" and text.strip():
                known.append((text.strip(), None))       # designated: the cwd file
            if suffix == "argv":
                for line in text.split("\n"):            # designated: argv elements
                    if line.startswith("/"):
                        known.append((line, None))
        env_path = os.path.join(cap, "call_%d.env0" % i)
        items.append(("calls/%d.env" % i, _env_item(env_path)))

    art_dirs = [("briefs", plan["dirs"]["briefs"])]
    if plan["out_path"]:
        art_dirs.append(("out", os.path.dirname(plan["out_path"])))
    if scen.get("tmp_artifacts"):
        # A stdin brief derives its artifacts from the temp brief, so they land
        # in TMPDIR rather than in a caller directory.
        art_dirs.append(("tmp", plan["dirs"]["tmp"]))
    listing, meta, blobs = [], [], []
    for label, base in art_dirs:
        for rel, full in _walk(base):
            if scen.get("tmp_artifacts") and label == "tmp" and \
                    not rel.endswith((".md", ".log", ".jsonl")):
                continue
            known.append((full, None))                   # designated: real listing
            listing.append(repr(full))
            meta.append(_entry_meta(full, with_size=False))
            if os.path.isfile(full) and not os.path.islink(full):
                try:
                    blobs.append(("artifacts@" + full, _dec(_read_bytes(full))))
                except Exception as exc:
                    blobs.append(("artifacts@" + full, "<unreadable: %s>" % exc))
    items.append(("artifacts/list", "\n".join(listing)))
    items.append(("artifacts/meta", "\n".join(meta)))
    items += blobs

    # H4: TMPDIR coverage is stated explicitly — name, type, mode, size.
    tmp_inv = []
    for rel, full in _walk(plan["dirs"]["tmp"]):
        known.append((full, None))                       # designated: real listing
        tmp_inv.append(_entry_meta(full))
    items.append(("tmp/inventory(name,type,mode,size)", "\n".join(tmp_inv)))

    items.append(("git/status", _git_text(plan["repo"], ["status", "--porcelain", "-z"])))
    items.append(("git/tracked-sha256", _tracked_fingerprints(plan["repo"])))
    items.append(("git/worktrees", _git_text(plan["repo"], ["worktree", "list", "--porcelain"])))

    if scen.get("pidfile"):
        pid_path = plan["env"]["STUB_SPAWN_PIDFILE"]
        raw = open(pid_path).read().strip() if os.path.isfile(pid_path) else ""
        items.append(("pid/file", raw or "<absent>"))
        gone = "no-pid-recorded"
        if raw.isdigit() and int(raw) > 0:
            pid, deadline = int(raw), time.time() + 5
            while _pid_running(pid) and time.time() < deadline:
                time.sleep(0.05)
            gone = "still-running" if _pid_running(pid) else "gone"
        items.append(("pid/gone", gone))
    if scen.get("host_write"):
        items.append(("host/wrote-midrun", "yes" if observed else "no"))

    # designated: the runner's OWN record of the snapshot it built
    for name, text in list(items):
        if name.endswith(".log"):
            for m in _SNAP_RECORD_RE.finditer(text):
                known.append((m.group(1), None))

    head = _git_text(plan["repo"], ["rev-parse", "HEAD"]).strip() or "unborn"
    return {"items": items, "known": known, "tmpdirs": plan["tmpdirs"], "rc": rc,
            "elapsed": elapsed, "head": head, "run_dir": plan["run_dir"]}


def _env_item(path):
    """The stub's `env -0` dump, sorted. Nothing is dropped: values outside
    ENV_VERBATIM are recorded as a digest — see the note there."""
    if not os.path.isfile(path):
        return "<absent>"
    kept = []
    for raw in _read_bytes(path).split(b"\0"):
        if not raw:
            continue
        name, _, value = _dec(raw).partition("=")
        if name in ENV_VERBATIM or name.startswith(ENV_VERBATIM_PREFIXES):
            kept.append("%s=%s" % (name, value))
        else:
            kept.append("%s=sha256:%s" % (
                name, hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()[:16]))
    return "\n".join(sorted(kept))


def _run_side(scen, runner, side, scripts_dir, root, tk):
    return _execute(_prepare(scen, runner, side, scripts_dir, root, tk))


# --------------------------------------------------------------------------
# G6 — cross-vendor parity on the SHARED behaviors only
# --------------------------------------------------------------------------
_VENDOR_MAP = (("Codex", "<V>"), ("Claude", "<V>"),
               ("CODEX", "<VU>"), ("CLAUDE", "<VU>"),
               ("codex", "<v>"), ("claude", "<v>"))
# Explicitly NOT compared across vendors: the codex event-stream classifier and
# its events artifact, the effort/sandbox disclosure fields, and the non-git
# policy (read-only codex runs outside a repo; claude always refuses).
PARITY_EXCLUDED = ("event-stream classifier + events artifact", "effort disclosure",
                   "sandbox disclosure", "non-git policy")


def _vendor(text):
    for raw, token in _VENDOR_MAP:
        text = text.replace(raw, token)
    return text


def _item(capture, name):
    for n, t in _normalized_items(capture):
        if n == name:
            return t
    return "<no such item: %s>" % name


def _lines_with(text, needle):
    return "\n".join(line for line in text.split("\n") if needle in line)


def _shared_result_line(text):
    """The reviewer's result line, reduced to the fields BOTH runners emit:
    vendor, reply path, mode, duration, model, snapshot tag, coverage note.
    Effort and sandbox are codex-only disclosures and are dropped here."""
    line = _lines_with(text, " reply (").split("\n")[0]
    if not line:
        return "<no result line>"
    head, _, tail = line.partition(" — ")
    fields = [f.strip() for f in tail.split(" ===")[0].split(", ")]
    keep = [f for f in fields if f.startswith(("mode=", "model=", "snapshot="))
            or f == _tok("dur") + "s"]
    note = line.split(" ===", 1)[1] if " ===" in line else ""
    return _vendor(head) + " — " + ", ".join(keep) + " ===" + _vendor(note)


def _parity_fields(capture, kind):
    if kind == "contract":
        return _item(capture, "calls/1.stdin").split("---", 1)[0]
    if kind == "snapshot-note":
        stdin_text = _item(capture, "calls/1.stdin")
        start = stdin_text.find("SNAPSHOT: this run executes")
        return _vendor(stdin_text[start:start + 900]) if start >= 0 else "<no note>"
    if kind == "result-line":
        return _shared_result_line(_item(capture, "stdout"))
    if kind == "change-detection":
        return _vendor(_lines_with(_item(capture, "stderr"),
                                   "repository changed during the run"))
    if kind == "cleanup-order":
        combined = _item(capture, "combined")
        reply = combined.find(" reply (")
        fail = combined.find("snapshot cleanup failed: ")
        order = "reply-then-cleanup" if 0 <= reply < fail else "OTHER(%d,%d)" % (reply, fail)
        return "%s | rc=%s | %s" % (order, _item(capture, "rc"),
                                    _vendor(_lines_with(combined, "snapshot cleanup failed: ")))
    if kind == "aliasing":
        # The events stream is a codex-only artifact (PARITY_EXCLUDED); what
        # both runners share is that the reply keeps x.log and the runner's own
        # log moves to x.log.log.
        listing = "\n".join(l for l in _item(capture, "artifacts/list").split("\n")
                            if ".events." not in l)
        reply = [t for n, t in _normalized_items(capture)
                 if n.startswith("artifacts@") and n.endswith("/x.log")]
        return "%s | %s" % (listing, reply[0] if reply else "<no reply artifact>")
    if kind == "timeout":
        return "%s | rc=%s" % (_vendor(_lines_with(_item(capture, "stderr"), "timed out after")),
                               _item(capture, "rc"))
    if kind == "resume":
        return "%s | rc=%s" % (_vendor(_item(capture, "stderr")), _item(capture, "rc"))
    raise AssertionError(kind)


# --------------------------------------------------------------------------
# H1/H2 — negative controls and token self-tests (memory-only)
# --------------------------------------------------------------------------
def _synthetic(items, known=(), tmpdirs=()):
    return {"items": list(items), "known": list(known), "tmpdirs": list(tmpdirs)}


def _negative_controls(check):
    """The three probes the cross-vendor review used to break the previous
    normalization, plus the token invariants — all in memory, no subprocess."""
    tmp = "/golden/tmp"

    # (a) a temp-looking NAME in free prose is NOT a recorded path
    a = _synthetic([("stdout", "reply: see codex_brief.abc123.md for details\n")],
                   tmpdirs=[tmp])
    b = _synthetic([("stdout", "reply: see codex_brief.xyz789.md for details\n")],
                   tmpdirs=[tmp])
    ok, _, _ = _differences(a, b)
    check("golden negative control: a temp-looking name in PROSE is not normalized away",
          not ok, "the harness called two different prose strings identical")

    # (b) a literal token form in the captured bytes is rejected outright
    lit = _synthetic([("stdout", "reply mentions {codex_brief#1} verbatim\n")])
    ok, detail, _ = _differences(lit, lit)
    check("golden negative control: a literal token form in captured bytes FAILS the run",
          not ok and "token literal" in detail, detail)
    mark = _synthetic([("stdout", "reply mentions %s verbatim\n" % _tok("run"))])
    ok, detail, _ = _differences(mark, mark)
    check("golden negative control: a pre-existing token MARKER fails the run",
          not ok and "token literal" in detail, detail)

    # (c) a duration in reply prose is not a designated field
    a = _synthetic([("stdout", "the tool we called timed out after 4s, it says\n")])
    b = _synthetic([("stdout", "the tool we called timed out after 9s, it says\n")])
    ok, _, _ = _differences(a, b)
    check("golden negative control: a duration in reply PROSE is not normalized away",
          not ok, "the harness called 4s and 9s identical")

    # (I1) a worktree admin name in reply PROSE is not runner metadata
    a = _synthetic([("stdout", "Use .git/worktrees/alpha here\n")])
    b = _synthetic([("stdout", "Use .git/worktrees/beta here\n")])
    ok, _, _ = _differences(a, b)
    check("golden negative control: a worktree admin name in PROSE is not normalized away",
          not ok, "the harness called two different prose strings identical")
    wt = "worktree /x\nHEAD abc\n"
    ok, detail, _ = _differences(
        _synthetic([("git/worktrees", wt), ("x.log", "# ---- snapshot cleanup ----\n"
                                           "fatal: .git/worktrees/alpha is locked\n")]),
        _synthetic([("git/worktrees", wt), ("x.log", "# ---- snapshot cleanup ----\n"
                                           "fatal: .git/worktrees/beta is locked\n")]))
    check("golden: a worktree admin name IS normalized in the runner's cleanup record",
          ok, detail)

    # (I2) the capture-window phrase in the caller's own brief prose
    note = (_SNAP_NOTE_PREFIX + " /r at /s — HEAD abc plus uncommitted changes "
            "captured between %s and %s (capture is not atomic). Do not write.")
    prose = "the earlier run captured between %s and %s (capture is not atomic)"
    ok, _, _ = _differences(
        _synthetic([("calls/1.stdin", prose % ("T1", "T2"))]),
        _synthetic([("calls/1.stdin", prose % ("T3", "T4"))]))
    check("golden negative control: the capture-window phrase in BRIEF prose is not "
          "normalized away", not ok, "the harness called two different briefs identical")
    ok, detail, _ = _differences(
        _synthetic([("calls/1.stdin", note % ("T1", "T2"))]),
        _synthetic([("calls/1.stdin", note % ("T3", "T4"))]))
    check("golden: the capture window IS normalized on the runner's snapshot-note line",
          ok, detail)

    # the designated fields still ARE normalized (the mapping is not dead)
    fmt = "✗ codex_consult FAILED (mode=consult, %ds):\n  - timed out after %ds (tune with CODEX_TIMEOUT)\n"
    ok, detail, _ = _differences(_synthetic([("stderr", fmt % (4, 4))]),
                                 _synthetic([("stderr", fmt % (9, 9))]))
    check("golden: the DESIGNATED duration fields are still normalized", ok, detail)

    # H2: repeated references map consistently; distinct paths never collide
    p1, p2 = tmp + "/hjw_snap.aaaaaa", tmp + "/hjw_snap.bbbbbb"
    both = _synthetic([("stdout", p1 + " and again " + p1 + "\n"),
                       ("stderr", "still " + p1 + "\n")], tmpdirs=[tmp])
    other = _synthetic([("stdout", p2 + " and again " + p2 + "\n"),
                        ("stderr", "still " + p2 + "\n")], tmpdirs=[tmp])
    ok, detail, _ = _differences(both, other)
    check("golden token: repeated references to one path map consistently", ok, detail)
    mixed = _synthetic([("stdout", p1 + " and again " + p2 + "\n"),
                        ("stderr", "still " + p1 + "\n")], tmpdirs=[tmp])
    ok, _, _ = _differences(both, mixed)
    check("golden token: two DISTINCT paths never collapse into one token", not ok,
          "distinct paths shared a token")
    norm = dict(_normalized_items(mixed))["stdout"]
    check("golden token: tokens are emitted inside private-use markers",
          MARK_OPEN in norm and MARK_CLOSE in norm and "hjw_snap.aaaaaa" not in norm, norm)


# --------------------------------------------------------------------------
# H6 — non-vacuity: the comparison must BIND, in the right channel
# --------------------------------------------------------------------------
# (label, scenario, edits to codex_consult.sh, channel pattern, how many items
# may differ). "Detected in its intended channel ONLY" = every differing item
# matches the pattern AND there are exactly that many of them.
MUTATIONS = (
    ("stdout disclosure", "file-brief-clean",
     [(b'echo "=== Codex reply', b'echo "=== Codex repl_')], r"\Astdout\Z", 1),
    ("stderr start line", "file-brief-clean",
     [(b'echo "\xe2\x86\x92 Codex (mode=', b'echo "\xe2\x86\x92 Codex [mode=')],
     r"\Astderr\Z", 1),
    ("argv", "file-brief-clean",
     [(b'run_attempt 1 "$EVENTS" codex exec --json',
       b'run_attempt 1 "$EVENTS" codex exec --json --golden-selftest-flag')],
     r"\Acalls/1\.argv0?\Z", 2),
    ("exported HOME", "file-brief-clean",
     [(b'run_attempt 1 "$EVENTS" codex exec --json',
       b'export HOME="$HOME/golden-selftest-home"\nrun_attempt 1 "$EVENTS" codex exec --json')],
     r"\Acalls/1\.env\Z", 1),
    ("postcondition (a file left behind)", "file-brief-clean",
     [(b'[ -n "$BOUNDED_PY" ] && rm -f "$BOUNDED_PY"',
       b'[ -n "$BOUNDED_PY" ] && : "$BOUNDED_PY"')],
     r"\Atmp/inventory", 1),
)
# The artifact mutant is built separately: it appends to $OUT AFTER `cat "$OUT"`,
# so only the reply FILE can catch it.
ARTIFACT_MUTANT = ("reply artifact", "file-brief-clean",
                   r"\Aartifacts@.*brief\.reply\.md\Z", 1)

# One character per side (see _prepare): baseline, candidate, then the
# self-test trees.
SIDE_KEYS = {"baseline": "b", "candidate": "c", "copy-control": "0"}


def _mutant_tree(scripts_dir, dest, edits):
    """A COMPLETE copy of the candidate tree (so a future lib/ travels with
    it), with `edits` applied to codex_consult.sh. Returns "" when an edit does
    not apply exactly once — a self-test that silently changed nothing would be
    worthless."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copytree(scripts_dir, dest, ignore=shutil.ignore_patterns("__pycache__"))
    target = os.path.join(dest, "codex_consult.sh")
    data = _read_bytes(target)
    for old, new in edits:
        if data.count(old) != 1:
            return ""
        data = data.replace(old, new)
    with open(target, "wb") as f:
        f.write(data)
    return dest


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def run(check, tk):
    started = time.time()
    root = tempfile.mkdtemp(dir=tk["tmp_root"], prefix="golden-")
    ok, baseline_scripts = _verify_baseline(check, os.path.join(root, "baseline"))
    if not ok:
        check("golden: differential ran", False, "baseline verification failed")
        return

    _negative_controls(check)

    cand_scripts = tk["scripts_dir"]
    scenarios = _scenarios()
    saved = {k: os.environ.get(k) for k in GIT_FIXED}
    os.environ.update(GIT_FIXED)  # the repo factories commit through it too
    try:
        clean = [s for s in scenarios if s["name"] == "file-brief-clean"][0]
        # H6: an UNMUTATED complete copy must pass first — otherwise "the
        # mutants differ" would only prove that copying the tree differs.
        control_dir = _mutant_tree(cand_scripts, os.path.join(root, "copy-control",
                                                              "haejwo", "scripts"), [])
        mutants = []
        for idx, (label, scen_name, edits, pattern, count) in enumerate(MUTATIONS):
            mdir = _mutant_tree(cand_scripts,
                                os.path.join(root, "mutant-%d" % idx, "haejwo", "scripts"),
                                edits)
            mutants.append((label, scen_name, mdir, pattern, count, str(idx + 1)))
        # the artifact mutant is appended after `cat "$OUT"`, so it can only be
        # caught by the reply FILE — never by stdout.
        art_dir = os.path.join(root, "mutant-artifact", "haejwo", "scripts")
        shutil.copytree(cand_scripts, art_dir, ignore=shutil.ignore_patterns("__pycache__"))
        art_target = os.path.join(art_dir, "codex_consult.sh")
        art_src = _read_bytes(art_target)
        label, scen_name, pattern, count = ARTIFACT_MUTANT
        if art_src.endswith(b"\nexit 0\n"):
            with open(art_target, "wb") as f:
                f.write(art_src[:-len(b"\nexit 0\n")]
                        + b"\nprintf 'GOLDEN-SELFTEST-MUTANT\\n' >> \"$OUT\"\nexit 0\n")
            mutants.append((label, scen_name, art_dir, pattern, count, "9"))
        else:
            mutants.append((label, scen_name, "", pattern, count, "9"))

        jobs = []
        for scen in scenarios:
            for runner in scen.get("runners", ("codex", "claude")):
                jobs.append((scen, runner, "baseline", baseline_scripts))
                jobs.append((scen, runner, "candidate", cand_scripts))

        results = {}
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {}
            for scen, runner, side, scripts in jobs:
                futures[(scen["name"], runner, side)] = pool.submit(
                    _run_side, scen, runner, SIDE_KEYS[side], scripts, root, tk)
            if control_dir:
                futures[("selftest", "copy-control", "x")] = pool.submit(
                    _run_side, clean, "codex", SIDE_KEYS["copy-control"],
                    control_dir, root, tk)
            for label, scen_name, mdir, _pat, _cnt, key in mutants:
                if mdir:
                    futures[("selftest", label, "mutant")] = pool.submit(
                        _run_side, [s for s in scenarios if s["name"] == scen_name][0],
                        "codex", key, mdir, root, tk)
            for key, fut in futures.items():
                try:
                    results[key] = fut.result()
                except Exception as exc:
                    results[key] = exc

        # ---- differential, one scenario at a time, in declaration order ----
        pairs = 0
        for scen in scenarios:
            for runner in scen.get("runners", ("codex", "claude")):
                name = "%s [%s]" % (scen["name"], runner)
                base = results.get((scen["name"], runner, "baseline"))
                cand = results.get((scen["name"], runner, "candidate"))
                if isinstance(base, Exception) or isinstance(cand, Exception):
                    check("golden %s: both sides ran" % name, False,
                          "baseline=%r candidate=%r" % (base, cand))
                    continue
                pairs += 1
                check("golden %s: scratch repos share one HEAD (ids stay exact)" % name,
                      base["head"] == cand["head"], "%s != %s" % (base["head"], cand["head"]))
                same, detail = _compare(base, cand)
                check("golden %s: baseline and candidate are byte-identical" % name,
                      same, detail)
                limit = scen.get("timeout_limit")
                if limit:
                    check("golden %s: the wall clock held (elapsed < %ds + 3)" % (name, limit),
                          base["elapsed"] < limit + 3 and cand["elapsed"] < limit + 3,
                          "baseline=%.1fs candidate=%.1fs" % (base["elapsed"], cand["elapsed"]))
                if scen.get("pidfile"):
                    gone = [_item(c, "pid/gone") for c in (base, cand)]
                    check("golden %s: the recorded descendant is gone on both sides" % name,
                          gone == ["gone", "gone"], str(gone))
                if scen.get("native_diagnostics"):
                    # The one fixture that reaches bash's OWN diagnostics — the
                    # case N5 exists for. With N5_RELOCATED empty the source
                    # locations are compared exactly, so this asserts both that
                    # the fixture still produces them and that they matched.
                    diag = [l for l in _item(cand, "stderr").split("\n")
                            if re.search(r"_consult\.sh: line \d+:", l)]
                    check("golden %s: native shell diagnostics present and matched exactly"
                          % name, bool(diag) and same, "\n".join(diag) or "no diagnostic")
                if scen.get("signal"):
                    want_rc = 143 if scen["signal"] == "TERM" else 130
                    rcs = [base["rc"], cand["rc"]]
                    check("golden %s: exits %d on both sides" % (name, want_rc),
                          rcs == [want_rc, want_rc], str(rcs))
                    delivery = [_item(c, "signal/delivery") for c in (base, cand)]
                    check("golden %s: the signal really landed mid-run on both sides" % name,
                          all("after readiness marker: yes" in d for d in delivery),
                          str(delivery))
                    check("golden %s: no worktree survives the interrupt" % name,
                          all("hjw_snap" not in _item(c, "git/worktrees")
                              for c in (base, cand)),
                          _item(cand, "git/worktrees"))
                if scen.get("host_write"):
                    wrote = [_item(c, "host/wrote-midrun") for c in (base, cand)]
                    acks = [_item(c, "calls/1.ack") for c in (base, cand)]
                    check("golden %s: the reviewer ACKNOWLEDGED the host write mid-review"
                          % name,
                          wrote == ["yes", "yes"]
                          and all(a.strip() == "observed contents=host-write-acked"
                                  for a in acks), "%s %s" % (wrote, acks))

        # ---- G6 parity on the shared behaviors (candidate side) ----
        parity = (
            ("contract prepend", "file-brief-clean", "contract"),
            ("snapshot note", "snapshot-clean", "snapshot-note"),
            ("result line (shared fields)", "snapshot-clean", "result-line"),
            ("change-detection message", "snapshot-reviewer-writes", "change-detection"),
            ("cleanup ordering", "snapshot-cleanup-failure-merged", "cleanup-order"),
            ("output aliasing", "output-alias-log", "aliasing"),
            ("timeout", "timeout-descendant", "timeout"),
            ("--resume refusal", "resume-refusal", "resume"),
        )
        for title, scen_name, kind in parity:
            cx = results.get((scen_name, "codex", "candidate"))
            cl = results.get((scen_name, "claude", "candidate"))
            if isinstance(cx, Exception) or isinstance(cl, Exception) or cx is None or cl is None:
                check("golden parity: %s" % title, False, "missing run")
                continue
            a, b = _parity_fields(cx, kind), _parity_fields(cl, kind)
            check("golden parity: %s matches across vendors" % title, a == b,
                  "codex=%r claude=%r" % (a[:400], b[:400]))
        check("golden parity: vendor distinctions stay excluded",
              len(PARITY_EXCLUDED) == 4, str(PARITY_EXCLUDED))

        # ---- H6 non-vacuity: the comparison binds, in the right channel ----
        base_for = {}
        for _label, scen_name, _mdir, _pat, _cnt, _key in mutants:
            base_for[scen_name] = results.get((scen_name, "codex", "baseline"))
        control = results.get(("selftest", "copy-control", "x"))
        if control_dir and not isinstance(control, Exception) and control is not None:
            same, detail = _compare(base_for.get("file-brief-clean"), control)
            check("golden self-test: an UNMUTATED complete tree copy still matches",
                  same, detail)
        else:
            check("golden self-test: an UNMUTATED complete tree copy still matches",
                  False, repr(control))
        for label, scen_name, mdir, pattern, count, _key in mutants:
            mres = results.get(("selftest", label, "mutant"))
            if not mdir or isinstance(mres, Exception) or mres is None:
                check("golden self-test: %s mutation is detected" % label, False,
                      "mutation did not apply / did not run: %r" % (mres,))
                continue
            ok, _detail, diffs = _differences(base_for.get(scen_name), mres)
            in_channel = len(diffs) == count and all(re.search(pattern, d) for d in diffs)
            check("golden self-test: a %s mutation is REPORTED, in its channel only" % label,
                  not ok and in_channel,
                  "differing items: %s (expected %d matching %s)" % (diffs, count, pattern))

        check("golden: N5 relocated-diagnostic list is empty in this commit",
              N5_RELOCATED == (), str(N5_RELOCATED))
        print("  golden: %d scenario pairs (%d runs) in %.1fs"
              % (pairs, len(results), time.time() - started))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(root, ignore_errors=True)
