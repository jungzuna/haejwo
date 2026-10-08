"""haejwo hooks — shared helpers.

Philosophy: this is a DELEGATION gate, not a security boundary.
On any ambiguity or internal error the hooks FAIL OPEN (allow) so a broken
gate can never brick a session. All state lives under CLAUDE_PLUGIN_DATA,
keyed by session_id, so concurrent sessions never collide.
"""
import errno
import json
import os
import re
import subprocess
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:  # non-POSIX: lock becomes a no-op (fail open)
    fcntl = None

DEFAULT_CONFIG = {
    "version": 1,
    "configured": False,
    "gate": {
        "enabled": True,
        "max_files_per_turn": 2,
        "bash_guard": True,
        "delegation_guard": True,
    },
    "code_extensions": [
        "py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "java", "go", "rs",
        "c", "cc", "cpp", "h", "hpp", "cs", "rb", "php", "swift", "kt",
        "scala", "sh", "bash", "zsh", "sql", "vue", "svelte", "ipynb",
    ],
    # Directory COMPONENTS that never count as project code (metadata dirs).
    # Temp files are exempted by resolved-prefix against the system tempdir,
    # NOT by substring — a repo's own tmp/ subdir still counts as code — and
    # ONLY outside the active project (its git toplevel, else cwd): a repo
    # cloned under /tmp is gated like any other (is_code_file).
    "exempt_dir_components": [".git", "node_modules", ".claude", ".codex"],
    # Owner policy (2026-09-21, effort revised 2.14 and 2.20): EXECUTION
    # defaults to Opus and the roles differ by reasoning EFFORT, not by model
    # family — the agent files pin it: default-worker `high` (2.20, measured
    # 2026-10-03: unpinned, it inherited an xhigh session at ~2x the cost and
    # time of high for the same oracle score), task-worker `low`. deep-reasoner
    # carries no effort key and runs at the SESSION's effort — it is the
    # judgment tier. The independent reviewer is separate and defaults to
    # `medium` in the runner. The cheaper Budget preset is opt-in via
    # /haejwo:setup.
    # Codex analog: the host model for all three roles, with deep-reasoner at
    # the host's own effort (reasoning_effort omitted) and default-worker /
    # task-worker at medium / low (unchanged by 2.20).
    "models": {
        "deep_reasoner": "inherit",
        "default_worker": "opus",
        "task_worker": "opus",
    },
    # Codex-host tiers (native spawn_agent model/reasoning_effort params).
    # "inherit" = omit the model param, so every role runs on the host model
    # and only reasoning_effort separates them. setup edits these; never
    # auto-rewrite user pins.
    "models_codex": {
        "deep_reasoner": "inherit",
        "default_worker": "inherit",
        "task_worker": "inherit",
    },
    "codex": {"enabled": False, "verified_at": None},
}


def read_payload():
    """Bounded stdin read — a hook must NEVER hang the session.
    Origin: a real incident (a shell wrapper swallowed EOF; sessions froze).
    Without this we'd block until the harness's 10s hook timeout — per
    tool call. Alarm fires -> fail open (delegation gate, not security)."""
    try:
        import signal

        def _timeout(signum, frame):
            raise TimeoutError()

        old = signal.signal(signal.SIGALRM, _timeout)
        signal.alarm(2)
        try:
            data = sys.stdin.read()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
        return json.loads(data)
    except Exception:
        return None


def paths(argv):
    """Resolve (plugin_root, plugin_data) from argv with env/home fallbacks."""
    root = argv[1] if len(argv) > 1 and argv[1] else os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    data = argv[2] if len(argv) > 2 and argv[2] else os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if not data or "${" in data:  # unsubstituted placeholder safety
        data = os.path.expanduser("~/.claude/plugins/data/haejwo-haejwo")
    return root, data


def load_config_with_status(data_dir):
    """ONE read, two answers: (effective config, readability status).

    Callers that only ENFORCE want the config and nothing else — defaults on
    any problem, fail open. A check that DENIES because the config says so
    also needs to know the config was actually readable: denying on a file we
    could not parse would enforce a pin the user never set (origin 2026-09-14,
    tier-pin check). Reading once means the two answers can never describe
    different file contents.

    status: "absent" (no config.json) | "malformed" (unparseable, or a
    top-level value that is not a JSON object) | "ok". Never raises.
    """
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        path = os.path.join(data_dir, "config.json")
    except Exception:
        return cfg, "absent"
    try:
        # utf-8-sig: tolerate a BOM from Windows/editor-saved config
        # (origin: recurring real-world BOM corruption incidents).
        with open(path, encoding="utf-8-sig") as f:
            user = json.load(f)
    except FileNotFoundError:
        return cfg, "absent"
    except Exception:
        try:
            return cfg, ("malformed" if os.path.exists(path) else "absent")
        except Exception:
            return cfg, "absent"
    if not isinstance(user, dict):
        return cfg, "malformed"  # [], null, 42: valid JSON, unusable config
    try:
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except Exception:
        pass
    return cfg, "ok"


def load_config(data_dir):
    return load_config_with_status(data_dir)[0]


def config_status(data_dir):
    return load_config_with_status(data_dir)[1]


def gate_disabled_by_env():
    return os.environ.get("HAEJWO_GATE", "").lower() in ("off", "0", "false")


def _safe_sid(session_id):
    return re.sub(r"[^A-Za-z0-9_-]", "-", str(session_id) or "unknown")[:80]


def state_file(data_dir, session_id):
    return os.path.join(data_dir, "state", _safe_sid(session_id) + ".json")


def load_state(data_dir, session_id):
    try:
        with open(state_file(data_dir, session_id)) as f:
            st = json.load(f)
        if isinstance(st, dict):
            st.setdefault("files", [])
            return st
    except Exception:
        pass
    return {"prompt_id": None, "files": [], "updated_at": 0}


def _err_name(exc):
    """A short, stable label for a persistence failure: the errno name
    (EACCES, EISDIR, ...) when there is one, else the exception type."""
    code = getattr(exc, "errno", None)
    return errno.errorcode.get(code, type(exc).__name__) if code else type(exc).__name__


def save_state(data_dir, session_id, state):
    """Persist the session state. Returns None on success, or a short error
    label (errno name) on failure — never raises (fail open, P4); callers
    that count the budget surface the label (state_not_persisted_note)."""
    try:
        path = state_file(data_dir, session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        state["updated_at"] = time.time()
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
        return None
    except Exception as e:
        return _err_name(e)


def state_not_persisted_note(err):
    """Said on EVERY call whose budget bookkeeping did not land (origin
    2026-10-08 cold-loop D15: save failures were swallowed, so an operator
    never learned the budget had stopped counting). Deliberately NOT a
    once-note: the once-flag would live in the same unwritable state."""
    return (f"[haejwo gate] state not persisted ({err}); the edit budget is "
            f"not being counted")


def carry_session_flags(prev, state):
    """Copy SESSION-scoped once-note flags from `prev` into a fresh turn
    `state` and return it. Every turn reset (turn_reset.py, gate.py's lazy
    and stale paths) rebuilds the state dict; without this the once-per-
    session notes (malformed config; tier pin not passable) repeat after
    every reset."""
    if prev.get("cfg_malformed_noted"):
        state["cfg_malformed_noted"] = True
    if isinstance(prev.get("pin_unpassable_noted"), list):
        state["pin_unpassable_noted"] = list(prev["pin_unpassable_noted"])
    return state


# Tier-pin values the Claude Code Agent tool's `model` parameter accepted when
# measured on 2026-10-02. Compatibility boundary: extend ONLY by measurement.
# Full ids are rejected by the tool; account-specific aliases (e.g. `fable`)
# may appear in some accounts' enum but are not in the measured set.
PASSABLE_MODEL_ALIASES = frozenset({"sonnet", "opus", "haiku"})


CONFIG_MALFORMED_NOTE = (
    "[haejwo] config.json is unreadable (malformed JSON) — enforcement is "
    "disabled (fail-open) until it is repaired; run /haejwo:setup or fix the "
    "file"
)


def malformed_note_once(data_dir, session_id):
    """The malformed-config note, ONCE per session; None on later calls.

    Shared by gate.py, bash_guard.py and delegation_gate.py: whichever hook
    fires first spends the one emission, the others stay silent that session.
    The "already told them" flag lives in the session state file (turn_reset
    preserves it across turns). If that state is unwritable the flag never
    sticks and the note repeats — the fail-open direction: a repeated note
    costs a line of context, suppressing it would hide that enforcement is
    off.
    """
    try:
        with state_lock(data_dir, session_id) as lk:
            if not lk.acquired:
                return CONFIG_MALFORMED_NOTE  # no unlocked read/write; repeat
            state = load_state(data_dir, session_id)
            if state.get("cfg_malformed_noted"):
                return None
            state["cfg_malformed_noted"] = True
            save_state(data_dir, session_id, state)
        return CONFIG_MALFORMED_NOTE
    except Exception:
        return CONFIG_MALFORMED_NOTE


def prune_state(data_dir, max_age_days=7):
    try:
        sdir = os.path.join(data_dir, "state")
        cutoff = time.time() - max_age_days * 86400
        for name in os.listdir(sdir):
            # observations.jsonl / .jsonl.1 are size-bounded by observe()'s
            # own rotation, not age-bounded here; .1 is retained P13 audit
            # evidence and pruning it by age would defeat the rotation.
            if name in ("observations.jsonl", "observations.jsonl.1"):
                continue
            p = os.path.join(sdir, name)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.unlink(p)
    except Exception:
        pass


def is_subagent(payload):
    """Documented: agent_id/agent_type are populated only inside subagents."""
    return bool(payload.get("agent_id") or payload.get("agent_type"))


_OBSERVATIONS_LOCK_ID = "__observations__"  # dedicated lock name, not a session id


def _try_lock(path, timeout=0.3, interval=0.02):
    """Best-effort exclusive flock: LOCK_NB with bounded retries.

    Returns the open handle on success, or None if the lock stayed busy (the
    caller then proceeds UNLOCKED). An observation must never block a decision
    (P4): a contended observations lock degrades to an unlocked append — at
    worst an interleaved line — rather than stalling a gate hook that a user
    is waiting on. Origin 2026-09-14: the decision path may not wait on the
    audit path.
    """
    if not fcntl:
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = open(path, "w")
    except Exception:
        return None
    deadline = time.time() + timeout
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
            time.sleep(interval)


def observe(data_dir, record):
    """Best-effort empirical log (e.g. to answer: do hooks fire in subagents?).

    observations.jsonl rotates to observations.jsonl.1 (overwriting any
    previous .1) once it exceeds 200KB, instead of being unlinked, so the
    prior generation survives as P13 audit evidence. Bound stays ~400KB
    total (current file + one prior generation).

    The lock is NON-blocking with bounded retries; a busy lock means the
    append happens unlocked rather than the caller waiting (see _try_lock).
    """
    try:
        sdir = os.path.join(data_dir, "state")
        os.makedirs(sdir, exist_ok=True)
        path = os.path.join(sdir, "observations.jsonl")
        fh = _try_lock(state_file(data_dir, _OBSERVATIONS_LOCK_ID) + ".lock")
        try:
            try:
                if os.path.exists(path) and os.path.getsize(path) > 200_000:
                    os.replace(path, path + ".1")
            except Exception:
                pass  # rotation is best-effort; still append the record below
            record["ts"] = round(time.time(), 1)
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
    except Exception:
        pass


def code_ext(path, cfg):
    ext = path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""
    return ext if ext in cfg["code_extensions"] else None


_TEMP_PREFIXES = None


def _temp_prefixes():
    global _TEMP_PREFIXES
    if _TEMP_PREFIXES is None:
        cands = {tempfile.gettempdir(), "/tmp", "/var/tmp"}
        _TEMP_PREFIXES = {os.path.realpath(c).rstrip("/") + "/" for c in cands if c}
    return _TEMP_PREFIXES


def canonical(path, cwd=""):
    """Resolve to one physical identity: cwd-join relative paths, realpath the rest."""
    try:
        p = path.replace("\\", "/")
        if not os.path.isabs(p):
            p = os.path.join(cwd or "/", p)
        return os.path.realpath(p)
    except Exception:
        return path


_PROJECT_ROOTS = {}  # cwd -> resolved project root (or None); per hook process


def _project_root(cwd):
    """The active project's root: the git toplevel of `cwd` (bounded 2 s
    probe), else `cwd` itself when git fails, is absent or times out. None
    when there is no usable cwd — the caller then keeps the temp exemption
    (uncertainty fails open, P4). Cached per process: one probe per hook."""
    if cwd in _PROJECT_ROOTS:
        return _PROJECT_ROOTS[cwd]
    root = None
    try:
        if cwd and os.path.isabs(cwd) and os.path.isdir(cwd):
            root = os.path.realpath(cwd)
            try:
                # The probe asks about cwd, so an inherited GIT_DIR /
                # GIT_WORK_TREE must not answer for some other repository.
                env = {k: v for k, v in os.environ.items()
                       if k not in ("GIT_DIR", "GIT_WORK_TREE")}
                p = subprocess.run(
                    ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, timeout=2,
                    stdin=subprocess.DEVNULL, env=env)
                top = p.stdout.strip()
                if p.returncode == 0 and top and os.path.isabs(top):
                    root = os.path.realpath(top)
            except Exception:
                pass  # git absent / timed out: the cwd itself is the project
    except Exception:
        root = None
    _PROJECT_ROOTS[cwd] = root
    return root


def _temp_exempt(cpath, cwd):
    """A resolved path under a temp prefix is exempt ONLY when it lies outside
    the active project. Origin 2026-10-08 cold-loop D1: any repository under
    /tmp was ungated, CI workspaces included; the exemption exists for the
    session scratchpad, which sits outside the project. A project root that
    IS a temp dir the path is under, or an ancestor of one (cwd=/tmp,
    cwd=/), is not a project boundary — the exemption then stands as
    before."""
    probe = cpath.rstrip("/") + "/"
    prefixes = _temp_prefixes()
    hit = [pre for pre in prefixes if probe.startswith(pre)]
    if not hit:
        return False
    root = _project_root(cwd)
    if not root:
        return True
    root_s = root.rstrip("/") + "/"
    if any(pre.startswith(root_s) for pre in hit):
        return True
    return not probe.startswith(root_s)


def is_code_file(path, cfg, cwd=""):
    if not path:
        return False
    cpath = canonical(path, cwd)
    # temp locations: exempt by resolved PREFIX only (not substring), and
    # only outside the active project (_temp_exempt)
    if _temp_exempt(cpath, cwd):
        return False
    # metadata dirs: exempt by exact path component
    parts = set(cpath.split("/"))
    if parts & set(cfg["exempt_dir_components"]):
        return False
    return code_ext(cpath, cfg) is not None


class state_lock:
    """Advisory per-session lock so concurrent hook processes can't race the
    read-check-write of the counter (undercount).

    BOUNDED: LOCK_NB with retries for at most LOCK_WAIT seconds (origin
    2026-10-08 cold-loop D10: a blocking flock let one stuck holder turn
    every later call into a host-side hook timeout). When the lock is not
    taken, `.acquired` is False and `.error` says why; the caller must then
    ALLOW WITHOUT touching state — no unlocked read/check/write. Without
    fcntl (non-POSIX) there is no lock to take: `.acquired` stays True and
    callers proceed as before."""

    LOCK_WAIT = 2.0

    def __init__(self, data_dir, session_id):
        self.path = state_file(data_dir, session_id) + ".lock"
        self.fh = None
        self.acquired = False
        self.error = None

    def __enter__(self):
        if not fcntl:
            self.acquired = True
            return self
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self.fh = open(self.path, "w")
        except Exception as e:
            self.fh, self.error = None, _err_name(e)
            return self
        deadline = time.monotonic() + self.LOCK_WAIT
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
                return self
            except BlockingIOError:
                if time.monotonic() < deadline:
                    time.sleep(0.05)
                    continue
                self.error = "lock busy"
            except Exception as e:
                self.error = _err_name(e)
            try:
                self.fh.close()
            except Exception:
                pass
            self.fh = None
            return self

    def __exit__(self, *exc):
        try:
            if self.fh:
                fcntl.flock(self.fh, fcntl.LOCK_UN)
                self.fh.close()
                self.fh = None
        except Exception:
            pass
        return False


def allow(additional_context=None):
    if additional_context:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "additionalContext": additional_context,
            }
        }))
    sys.exit(0)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)
