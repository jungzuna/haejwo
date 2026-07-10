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
        "deep_reasoner": "opus",
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


def load_config(data_dir):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        # utf-8-sig: tolerate a BOM from Windows/editor-saved config
        # (origin: recurring real-world BOM corruption incidents).
        with open(os.path.join(data_dir, "config.json"), encoding="utf-8-sig") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except Exception:
        pass
    return cfg


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
            p = os.path.join(sdir, name)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.unlink(p)
    except Exception:
        pass


def is_subagent(payload):
    """Documented: agent_id/agent_type are populated only inside subagents."""
    return bool(payload.get("agent_id") or payload.get("agent_type"))


def observe(data_dir, record):
    """Best-effort empirical log (e.g. to answer: do hooks fire in subagents?)."""
    try:
        path = os.path.join(data_dir, "state", "observations.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > 200_000:
            os.unlink(path)
        record["ts"] = round(time.time(), 1)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
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
