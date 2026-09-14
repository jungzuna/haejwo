"""haejwo hooks — shared helpers.

Philosophy: this is a DELEGATION gate, not a security boundary.
On any ambiguity or internal error the hooks FAIL OPEN (allow) so a broken
gate can never brick a session. All state lives under CLAUDE_PLUGIN_DATA,
keyed by session_id, so concurrent sessions never collide.
"""
import json
import os
import re
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
    # NOT by substring — a repo's own tmp/ subdir still counts as code.
    "exempt_dir_components": [".git", "node_modules", ".claude", ".codex"],
    "models": {
        "deep_reasoner": "inherit",
        "default_worker": "sonnet",
        "task_worker": "haiku",
    },
    # Codex-host tiers (native spawn_agent model/reasoning_effort params).
    # "inherit" = omit the model param so judgment never silently downgrades;
    # execution downshifts — that's the economic point. Exact names are
    # release-tested pins; setup edits them; never auto-rewrite user pins.
    "models_codex": {
        "deep_reasoner": "inherit",
        "default_worker": "gpt-5.6-terra",
        "task_worker": "gpt-5.6-luna",
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
        data = os.path.expanduser("~/.claude/plugins/data/haejwo")
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


def save_state(data_dir, session_id, state):
    try:
        path = state_file(data_dir, session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        state["updated_at"] = time.time()
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except Exception:
        pass  # fail open


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
    caller then proceeds UNLOCKED). Telemetry must never block a decision
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


def is_code_file(path, cfg, cwd=""):
    if not path:
        return False
    cpath = canonical(path, cwd)
    # temp locations: exempt by resolved PREFIX only (not substring)
    probe = cpath.rstrip("/") + "/"
    for prefix in _temp_prefixes():
        if probe.startswith(prefix):
            return False
    # metadata dirs: exempt by exact path component
    parts = set(cpath.split("/"))
    if parts & set(cfg["exempt_dir_components"]):
        return False
    return code_ext(cpath, cfg) is not None


class state_lock:
    """Advisory per-session lock so concurrent hook processes can't race the
    read-check-write of the counter (undercount). Fail-open on any error."""

    def __init__(self, data_dir, session_id):
        self.path = state_file(data_dir, session_id) + ".lock"
        self.fh = None

    def __enter__(self):
        try:
            if fcntl:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                self.fh = open(self.path, "w")
                fcntl.flock(self.fh, fcntl.LOCK_EX)
        except Exception:
            self.fh = None
        return self

    def __exit__(self, *exc):
        try:
            if self.fh:
                fcntl.flock(self.fh, fcntl.LOCK_UN)
                self.fh.close()
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
