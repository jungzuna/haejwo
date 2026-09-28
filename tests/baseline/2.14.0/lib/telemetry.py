#!/usr/bin/env python3
"""Consult telemetry (D4-lite) for haejwo's two reviewer runners.

  telemetry.py record <data-dir> key=value ...

ONE JSON line per armed runner invocation, appended to
`<data-dir>/state/consults.jsonl`. The caller is `hjw_telemetry_emit` in
`consult_common.sh`, which runs on the EXIT path that owns the traps — so the
record is written exactly once per run, with the status captured BEFORE cleanup
touched anything.

OWNERSHIP: the data dir is chosen by the SHELL (`hjw_telemetry_resolve`), never
by this file, and never from `CLAUDE_PLUGIN_DATA` alone — in a subagent's shell
that variable can point at ANOTHER plugin's data dir (measured). This file
writes where it is told and nowhere else; it creates only `state/` under that
dir.

NO VENDOR TEXT, EVER: a record must be safe to keep and safe to share, so it
carries no reviewer output, no error message and no free text of any kind. Every
string field is either a FIXED IDENTIFIER validated against a closed set here
(outcome, fail classes, attempt results, sources, runner, sandbox, effort,
usage counter names), a value in a VERIFIED format (the codex thread id, a
UUID — generic token syntax is not enough; the plugin version, a version
shape), a caller-chosen identifier with a hard bound (the model id, 80 chars;
the host session, 12), or generated here (run_id, timestamps). A value that
does not pass becomes `other` or null — never the value itself. The shell validates first; this file validates again, because
"it cannot happen upstream" is not a containment argument.
*[origin: cross-vendor review 2026-09-28 — a 40-character prefix of a vendor
error is still vendor text, and a secret can be shorter than 40 characters]*

BEST EFFORT, ALWAYS: every failure is reported on stdout as one line the runner
copies into its log, and the exit status is always 0. Telemetry may never change
a runner's behavior, output or exit code — the audit path never decides
anything (P4).

WHAT `changed` MEANS: 1 only when change detection actually SAW the repository
change. 0 is "no change was detected", which also covers a run whose detection
was unavailable — that case is not silent, it is named in `outcome` /
`fail_classes` (the runner fails such a run). Never read 0 as "verified clean"
without looking at the outcome.

WHAT `outcome` MEANS: `ok` is RUNNER success only — the reviewer produced a
reply, the no-edit contract verified, nothing failed the run. It says NOTHING
about whether the HOST accepted the reply or whether the review was any good.
Accepted-outcome economics (was the consult worth its cost?) needs a host-side
acceptance signal that does not exist yet and is explicitly DEFERRED; do not
read acceptance into these records.

Bounded: the file rotates to `consults.jsonl.1` (overwriting any previous one)
once it exceeds 200 KB, exactly like `hjw_common.observe()` — the prior
generation survives as audit evidence and the total stays ~400 KB.

Stdlib only (same rule as the rest of `scripts/lib`).
"""
import json
import os
import re
import sys
import time
import uuid

try:
    import fcntl
except ImportError:  # non-POSIX: the lock becomes a no-op, the append still runs
    fcntl = None

SCHEMA = 1
MAX_BYTES = 200_000
# The closed vocabularies. Kept in step with $HJW_TEL_CLASSES in
# consult_common.sh, which is where each identifier's meaning is documented.
_CLASSES = ("timeout", "exit-code", "empty-reply", "event-failure",
            "no-event-stream", "tracing-error", "repo-changed",
            "detection-unavailable", "non-git", "snapshot-unavailable",
            "cleanup-failed", "interrupted", "model-unavailable", "other")
_OUTCOMES = ("ok", "refused", "interrupted", "cleanup-failed")
_RESULTS = ("completed", "failed", "timeout", "model-unavailable")
_MODEL_SRCS = ("env", "config", "cli-default", "config fallback")
_EFFORT_SRCS = ("env", "config", "runner-default")
_RUNNERS = ("codex", "claude")
_SANDBOXES = ("read-only", "workspace-write", "danger-full-access")
_EFFORT_RE = re.compile(r"\A(?:low|medium|high|xhigh|max)\Z")
# A vendor-generated thread id is worth keeping (it is the only way to correlate
# a consult with the vendor's own session) but it is accepted ONLY in the
# vendor's verified id format: codex thread ids are UUIDs (8-4-4-4-12 hex). A
# generic "token syntax" is not a containment argument — SECRET_123456789 is
# token-shaped — so anything that is not a UUID is text, not an id, and is null.
# *[origin: cross-vendor review 2026-09-28, final round]*
_ID_RE = re.compile(r"\A[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
# codex's usage object is accounting only where its KEYS are the known counter
# names; a vendor could add any key, so unknown keys are dropped even when the
# value is an integer (an integer next to a secret-named key still persists the
# key). Closed set, extended only when codex's own schema does.
_USAGE_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
               "output_tokens", "reasoning_output_tokens", "total_tokens")
# The plugin version comes from haejwo's OWN manifest, but it is persisted only
# in a version's shape: dotted numerics plus an optional short pre-release/build
# suffix, bounded. Anything else is null.
_VERSION_RE = re.compile(r"\A[0-9]{1,5}(?:\.[0-9]{1,5}){0,3}(?:[-+][A-Za-z0-9.+-]{1,20})?\Z")
# A model id is caller-chosen, so it cannot come from a closed set — it gets a
# hard bound instead. 80 is well past every real id and far short of a payload.
MODEL_MAX = 80
# `aN.<key>` = field <key> of attempt N. The key set is closed: an unknown one
# is dropped rather than invented into the schema.
_ATTEMPT_RE = re.compile(r"\Aa([0-9]+)\.([a-z_]+)\Z")
_ATTEMPT_INTS = ("n", "child_rc")
_ATTEMPT_ORDER = ("n", "model", "model_src", "effort", "effort_src", "thread_id",
                  "child_rc", "usage", "result")
# Keys a runner may hand over. `events` is an INPUT only: the event stream's
# path, from which thread_id and usage are derived — the path itself never
# enters the record (a record carries no caller paths).
_ATTEMPT_IN = _ATTEMPT_ORDER + ("events",)


def _nz(value):
    """An empty shell value is an ABSENT one: it becomes JSON null, never ""."""
    return value if value else None


def _int(value):
    try:
        return int(value)
    except Exception:
        return None


def _enum(value, allowed):
    """A fixed identifier, or null. Never the rejected value."""
    return value if value in allowed else None


def _effort(value):
    """low|medium|high|xhigh|max, or null — the reviewer's and the host's effort
    share one vocabulary, and anything else is not an effort."""
    return value if value and _EFFORT_RE.match(value) else None


def _vendor_id(value):
    """A UUID-shaped vendor id, or null. Never a truncated blob, never a
    token that merely looks like an id."""
    return value if value and _ID_RE.match(value) else None


def _model(value):
    """A caller-chosen id under a hard bound. Bounded rather than rejected: the
    id is the whole point of `model_src=env`, and 80 chars still identifies it."""
    return value[:MODEL_MAX] if value else None


def _outcome(value):
    """`ok` / `refused` / `interrupted` / `cleanup-failed`, or `failed:<class>`
    with the class in the closed set. Anything else collapses to `failed:other`
    (if it claimed a failure) or `other` — never the value."""
    if value in _OUTCOMES:
        return value
    if value.startswith("failed:"):
        cls = value[len("failed:"):]
        return "failed:" + (cls if cls in _CLASSES else "other")
    return "other"


def _usage(value):
    """codex's own accounting, kept only where it IS accounting: the KNOWN
    counter names (`_USAGE_KEYS`) with integer values. An unknown key is dropped
    whatever its value (the key itself would persist), a non-integer value is
    dropped, a non-object is null, and an object with no known counter left is
    null too — there is nothing to account for."""
    if not isinstance(value, dict):
        return None
    kept = {k: v for k, v in value.items()
            if k in _USAGE_KEYS and isinstance(v, int) and not isinstance(v, bool)}
    return kept or None


def _plugin_version(self_path):
    """The runner's OWN plugin version, from `../.claude-plugin/plugin.json`
    relative to the runner file (<root>/scripts/<runner>.sh -> <root>). Any
    problem reading it is null — a telemetry field never guesses."""
    if not self_path:
        return None
    root = os.path.dirname(os.path.dirname(self_path))
    try:
        with open(os.path.join(root, ".claude-plugin", "plugin.json"),
                  encoding="utf-8-sig") as f:
            version = json.load(f).get("version")
    except Exception:
        return None
    # The manifest is haejwo's own, but "trusted source" is not a containment
    # argument either: a version is persisted only in a version's shape.
    return version if isinstance(version, str) and _VERSION_RE.match(version) else None


def _brief_bytes(path):
    """The size of the EFFECTIVE brief — what the reviewer was actually fed
    (standing contract + snapshot note + the caller's brief), not the caller's
    file alone, because that is the input the run was charged for. Null when the
    run never got as far as writing one."""
    try:
        return os.path.getsize(path)
    except Exception:
        return None


def _events(path):
    """(thread_id, usage) from codex's own JSONL event stream.

    Same provenance rule as the runner's classifier: only TOP-LEVEL objects are
    read. `usage` is the `turn.completed` event's own field, copied VERBATIM —
    this file never reshapes a vendor's accounting. The claude runner has no
    event stream and passes no path, so both stay null there."""
    thread_id = usage = None
    if not path:
        return thread_id, usage
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except Exception:
        return thread_id, usage
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            etype = event.get("type")
            if thread_id is None and etype == "thread.started":
                tid = event.get("thread_id")
                if isinstance(tid, str):
                    thread_id = tid
            if etype == "turn.completed" and "usage" in event:
                usage = event["usage"]
    return thread_id, usage


def _try_lock(path):
    """Best-effort exclusive flock: LOCK_NB with bounded retries, returning None
    when the lock stays busy — the append then happens UNLOCKED (at worst an
    interleaved line) rather than making a finished run wait on its own audit
    record. Same rule as `hjw_common._try_lock`."""
    if not fcntl:
        return None
    try:
        fh = open(path, "w")
    except Exception:
        return None
    deadline = time.time() + 0.3
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except Exception:
            if time.time() >= deadline:
                try:
                    fh.close()
                except Exception:
                    pass
                return None
            time.sleep(0.02)


def _append(data_dir, record):
    sdir = os.path.join(data_dir, "state")
    os.makedirs(sdir, exist_ok=True)
    path = os.path.join(sdir, "consults.jsonl")
    fh = _try_lock(path + ".lock")
    try:
        try:
            if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
                os.replace(path, path + ".1")
        except Exception:
            pass  # rotation is best effort; the record below still lands
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fh.close()
            except Exception:
                pass


def _parse(argv):
    """`key=value` pairs, split on the FIRST `=` so a value may contain any
    byte. Repeated `fc` builds the fail-class list in order; `aN.<key>` builds
    attempt N."""
    fields = {}
    fail_classes = []
    attempts = {}
    for raw in argv:
        key, _, value = raw.partition("=")
        if key == "fc":
            fail_classes.append(value)
            continue
        m = _ATTEMPT_RE.match(key)
        if m:
            if m.group(2) in _ATTEMPT_IN:
                attempts.setdefault(int(m.group(1)), {})[m.group(2)] = value
            continue
        fields[key] = value
    return fields, fail_classes, attempts


def cmd_record(argv):
    data_dir = argv[0]
    if not data_dir:
        return "record skipped: no data dir"
    fields, fail_classes, attempts = _parse(argv[1:])

    now = time.time()
    t0 = _int(fields.get("t0", ""))
    built = []
    for index in sorted(attempts):
        raw = attempts[index]
        thread_id, usage = _events(raw.get("events", ""))
        attempt = {}
        for key in _ATTEMPT_ORDER:
            if key == "thread_id":
                attempt[key] = _vendor_id(thread_id)
            elif key == "usage":
                attempt[key] = _usage(usage)
            elif key in _ATTEMPT_INTS:
                attempt[key] = _int(raw.get(key, ""))
            elif key == "model":
                attempt[key] = _model(raw.get(key, ""))
            elif key == "model_src":
                attempt[key] = _enum(raw.get(key, ""), _MODEL_SRCS)
            elif key == "effort":
                attempt[key] = _effort(raw.get(key, ""))
            elif key == "effort_src":
                attempt[key] = _enum(raw.get(key, ""), _EFFORT_SRCS)
            elif key == "result":
                attempt[key] = _enum(raw.get(key, ""), _RESULTS)
            else:
                attempt[key] = _nz(raw.get(key, ""))
        attempt["n"] = _int(raw.get("n", "")) or index
        built.append(attempt)

    session = fields.get("host_session", "")
    record = {
        "v": SCHEMA,
        "plugin_version": _plugin_version(fields.get("self", "")),
        "run_id": str(uuid.uuid4()),
        "ts_start": _iso(t0 if t0 is not None else now),
        "ts_end": _iso(now),
        "duration_total_s": _int(fields.get("duration_total_s", "")),
        "runner": _enum(fields.get("runner", ""), _RUNNERS),
        "host_session": session[:12] if session else None,
        "host_session_src": "CLAUDE_CODE_SESSION_ID" if session else None,
        "host_effort": _effort(fields.get("host_effort", "")),
        "brief_bytes": _brief_bytes(fields.get("brief_file", "")),
        "snapshot": _int(fields.get("snapshot", "")),
        "sandbox": _enum(fields.get("sandbox", ""), _SANDBOXES),
        "attempts": built,
        "runner_rc": _int(fields.get("runner_rc", "")),
        "outcome": _outcome(fields.get("outcome", "")),
        # Arity kept: an unmapped class is counted as `other`, not dropped.
        "fail_classes": [c if c in _CLASSES else "other" for c in fail_classes],
        "changed": _int(fields.get("changed", "")),
    }
    _append(data_dir, record)
    return ""


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


MODES = {"record": cmd_record}


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in MODES:
        sys.stdout.write("usage: telemetry.py record <data-dir> key=value ...\n")
        sys.exit(0)
    try:
        note = MODES[sys.argv[1]](sys.argv[2:])
    except Exception as exc:
        note = "record failed: %s: %s" % (type(exc).__name__, exc)
    if note:
        sys.stdout.write(note.replace("\n", " ") + "\n")
    # ALWAYS 0: a telemetry failure is a log line, never a runner outcome.
    sys.exit(0)


if __name__ == "__main__":
    main()
