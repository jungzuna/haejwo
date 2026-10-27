#!/usr/bin/env python3
"""setup_probe.py — the deterministic half of /haejwo:setup (2.35.0).

  preflight   reviewer CLI state + read-only sandbox capability; never writes
  write       one read-modify-write of config.json (only the keys given)
  verify      disable first, then smoke + nonce repo-read probe; enable on success
  keep-check  is the stored setup still valid on this machine? never writes

Every mode prints ONE JSON object on stdout. Exit: 0 ok, 2 usage, 3 malformed or
unwritable config, 4 failed probe. Every subprocess is bounded; the nonce never
appears in the output. The questions, the disclosure and the consent stay with
the host model (commands/setup.md); this script never infers consent.
`codex sandbox -- true` predicts the read-only sandbox only conditionally: the
nonce probe always runs in the chosen sandbox. Not a security boundary (P4).
*[origin: plan D, 2026-10-27 — setup took 4-5 min of model turns for ~30 s of probes]*
"""
import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops it
from hjw_common import STALE_DAYS  # noqa: E402

EXIT_USAGE, EXIT_CONFIG, EXIT_PROBE = 2, 3, 4
DFA = "danger-full-access"
# Tier presets as commands/setup.md documents them (Standard == DEFAULT_CONFIG).
PRESETS = {
    "claude": {"standard": ("inherit", "opus", "opus"),
               "budget": ("inherit", "sonnet", "haiku")},
    "codex": {"standard": ("inherit", "inherit", "inherit"),
              "budget": ("inherit", "gpt-5.6-terra", "gpt-5.6-luna")},
}
ROLES = ("deep_reasoner", "default_worker", "task_worker")
PROBE_ENV_UNSET = ("CODEX_MODEL", "CLAUDE_MODEL", "CODEX_SANDBOX", "CODEX_EFFORT",
                   "HJW_CLAUDE_EFFORT", "CODEX_TIMEOUT", "CLAUDE_TIMEOUT")
REVIEWER_KEYS = ("consult_sandbox", "danger_full_access_consented_at",
                 "verify_fingerprint", "verification_pending")
# A sandbox-INITIALIZATION refusal, by structure: a stderr line that STARTS with
# the launcher's own prefix and names a setup stage. A payload exec failure
# (`bwrap: execvp true: …`), file-access and config-read errors are not; any
# other non-zero exit is `unknown`.
# *[origin: 2.35.0 diff review, blocker 3 — keywords matched paths and payload errors]*
LAUNCHER_RE = re.compile(r"^(bwrap|landlock|sandbox|codex-linux-sandbox):\s(.*)$", re.I)
INIT_RE = {
    "bwrap": r"failed to make|namespace|mount|uid map|gid map|pivot_root|unshare|clone"
             r"|setns|seccomp|no permissions to create",
    "landlock": r"denied|unsupported|not permitted|ruleset|\babi\b",
    "sandbox": r"unsupported|namespace|landlock|seccomp|bwrap|not permitted|permission denied",
}
PAYLOAD_RE = re.compile(r"^(execvp|exec )|\btrue\b", re.I)
LIMITS = {"smoke": 90, "read": 180}  # the runner's own bound per stage (CODEX_TIMEOUT)
MARGIN = 30      # the helper's backstop beyond the runner's bound
TERM_GRACE = 5   # SIGTERM -> exit: lets lib/bounded.py reap the CLI in ITS session
SMOKE_BRIEF = "MODE: consult\nReply with exactly: HAEJWO-OK\n"
READ_BRIEF = ("MODE: consult\nReturn the file's exact content, nothing else. "
              "File (absolute path): {}\n")
_REDACT = []  # the nonce, once drawn: scrubbed from every printed string


class Cancelled(Exception):
    """SIGTERM/SIGINT: raised in the main thread so every cleanup path runs."""


def on_signal(signum, _frame):
    raise Cancelled(signal.Signals(signum).name)


class Fail(Exception):
    def __init__(self, code, obj):
        super().__init__(code)
        self.code, self.obj = code, obj


def usage(detail):
    return Fail(EXIT_USAGE, {"error": "usage", "detail": detail})


def slurp(path, first=False):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readline() if first else f.read()
    except OSError:
        return ""


def emit(obj, code=0):
    text = json.dumps(obj, sort_keys=True)
    for s in _REDACT:
        text = text.replace(s, "<redacted>")
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return code


def run(argv, timeout, env=None, cwd=None, stdin_text=None):
    """(rc, stdout, stderr); rc None on timeout, 127 when not runnable. On a
    timeout or a cancel the child's group is stopped (stop) before returning
    or re-raising."""
    try:
        p = subprocess.Popen(argv, cwd=cwd, env=env, text=True, errors="replace",
                             stdin=subprocess.PIPE if stdin_text is not None
                             else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
    except OSError as e:
        return 127, "", str(e)
    try:
        out, err = p.communicate(stdin_text, timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        out, err, held = stop(p)
        return None, out, err + ("\n- a descendant outside the runner's process group "
                                 "still held its output; not verified terminated" if held else "")
    except BaseException:
        stop(p)
        raise


def stop(p):
    """Cooperative first: SIGTERM to the runner's group reaches lib/bounded.py,
    which reaps the CLI it started in its OWN session (a group SIGKILL would
    orphan it); then SIGKILL. Every drain is bounded: an escaped descendant
    may hold the pipes. Returns (out, err, pipes_still_held). Signals are
    ignored during the sweep."""
    saved = [(s, signal.signal(s, signal.SIG_IGN)) for s in (signal.SIGTERM, signal.SIGINT)]
    try:
        for sig, grace in ((signal.SIGTERM, TERM_GRACE), (signal.SIGKILL, 2)):
            with contextlib.suppress(OSError):
                os.killpg(p.pid, sig)
            try:
                out, err = p.communicate(timeout=grace)
                return out or "", err or "", False
            except Exception:
                continue
        for pipe in (p.stdin, p.stdout, p.stderr):
            with contextlib.suppress(Exception):
                pipe and pipe.close()
        with contextlib.suppress(Exception):
            p.wait(timeout=1)
        return "", "", True
    finally:
        for s, h in saved:
            signal.signal(s, h)


def lines(text):
    return [l.strip()[:200] for l in (text or "").splitlines() if l.strip()] or [None]


# ---- preflight ---------------------------------------------------------------
def launcher_failure(line):
    m = LAUNCHER_RE.match(line.strip())
    if not m or PAYLOAD_RE.search(m.group(2)):
        return False
    kind = m.group(1).lower()
    pattern = INIT_RE["sandbox" if kind == "codex-linux-sandbox" else kind]
    return re.search(pattern, m.group(2), re.I) is not None


def identity(codex):
    """The verified reviewer settings: model, effort, sandbox."""
    return (str(codex.get("model") or "cli-default"), str(codex.get("effort") or "runner-default"),
            str(codex.get("consult_sandbox") or "n/a"))


def stored_codex(path):
    try:
        cfg = read_config(path)
    except Fail:
        return {}
    return cfg.get("codex") if isinstance(cfg.get("codex"), dict) else {}


def preflight(host, config, codex=None):
    cli_name = "codex" if host == "claude" else "claude"
    cli = {"installed": shutil.which(cli_name) is not None, "version": None,
           "auth": "unknown"}
    sandbox, reason = ("n/a", None) if host == "codex" else ("unknown", None)
    if cli["installed"]:
        rc, out, err = run([cli_name, "--version"], 10)
        if rc == 0:
            cli["version"] = lines(out or err)[0]
        if host == "claude":
            rc, out, err = run(["codex", "login", "status"], 10)
            if rc == 0:
                cli["auth"] = "authenticated"
            elif rc is not None and re.search(r"not logged in", out + err, re.I):
                cli["auth"] = "not-logged-in"
            rc, out, err = run(["codex", "sandbox", "--", "true"], 5)
            if rc == 0:
                sandbox = "ok"
            elif rc is None:
                reason = "timed out after 5s"
            else:
                reason = lines(err)[0] or lines(out)[0]
                if any(launcher_failure(line) for line in (err or "").splitlines()):
                    sandbox = "broken"
    elif host == "claude":
        reason = "codex not installed"
    codex = stored_codex(config) if codex is None else codex
    fp_src = "|".join((host, config, cli["version"] or "", sandbox) + identity(codex))
    return {"cli": cli, "sandbox_readonly": sandbox, "reason": reason,
            "fingerprint": hashlib.sha256(fp_src.encode()).hexdigest()[:12]}


# ---- config I/O --------------------------------------------------------------
def read_config(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        cfg = None
    if not isinstance(cfg, dict):
        raise Fail(EXIT_CONFIG, {"error": "malformed", "path": path})
    return cfg


def section(cfg, name, path):
    sec = cfg.setdefault(name, {})
    if not isinstance(sec, dict):  # never repaired without the host's disclosure
        raise Fail(EXIT_CONFIG, {"error": "malformed", "path": path,
                                 "detail": f"{name} is not an object"})
    return sec


def write_config(path, cfg):
    d = os.path.dirname(path) or "."
    tmp = None
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as e:
        if tmp:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        if isinstance(e, Cancelled):
            raise
        raise Fail(EXIT_CONFIG, {"error": "unwritable", "path": path,
                                 "detail": type(e).__name__})


def disable_reviewer(codex):
    codex["enabled"] = False
    for k in REVIEWER_KEYS:
        codex.pop(k, None)


# ---- write -------------------------------------------------------------------
def cmd_write(a):
    roles = {r: getattr(a, r) for r in ROLES if getattr(a, r) is not None}
    if bool(roles) != (a.preset == "custom"):
        raise usage("--preset custom takes one or more role options; only it does")
    for v in list(roles.values()) + ([a.model] if a.model is not None else []):
        if not v or len(v) > 100 or any(c.isspace() or ord(c) < 32 for c in v):
            raise usage("invalid model value")
    cfg = read_config(a.config)
    written, removed = [], []

    def put(sec, key, val):  # a section is created only when a key goes into it
        section(cfg, sec, a.config)[key] = val
        written.append(f"{sec}.{key}")

    for key, val in (("enabled", a.gate), ("max_files_per_turn", a.budget),
                     ("bash_guard", a.bash_guard)):
        if val is not None:
            put("gate", key, val == "on" if isinstance(val, str) else val)
    if a.preset:
        values = roles if a.preset == "custom" else dict(zip(ROLES, PRESETS[a.host][a.preset]))
        for role, v in values.items():
            put("models" if a.host == "claude" else "models_codex", role, v)
    if a.reviewer == "off":  # "on" enables nothing: only verify enables
        disable_reviewer(section(cfg, "codex", a.config))
        written.append("codex.enabled")
        removed.extend("codex." + k for k in REVIEWER_KEYS)
    before = identity(cfg.get("codex")) if isinstance(cfg.get("codex"), dict) else None
    if a.model is not None:
        put("codex", "model", a.model)
    if a.model_default:
        section(cfg, "codex", a.config).pop("model", None)
        removed.append("codex.model")
    if a.effort:
        put("codex", "effort", a.effort)
    codex = cfg.get("codex")
    if (before is not None and identity(codex) != before
            and codex.pop("verify_fingerprint", None) is not None):
        removed.append("codex.verify_fingerprint")  # settings changed: re-verify
    cfg["configured"] = True
    written.append("configured")
    write_config(a.config, cfg)
    return emit({"written": written, "removed": removed, "path": a.config})


# ---- verify ------------------------------------------------------------------
def esc_display(path):
    """The runner's header spelling of a path (hjw_esc_display)."""
    path = path.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return re.sub(r"[\x00-\x1f\x7f]", "?", path)


def runner_call(a, stage, brief, repo, art, sandbox):
    runner = os.path.join(a.plugin_root, "scripts",
                          "codex_consult.sh" if a.host == "claude" else "claude_consult.sh")
    out = os.path.join(art, f"setup_{stage}.md")
    env = {k: v for k, v in os.environ.items() if k not in PROBE_ENV_UNSET}
    limit = LIMITS[stage]
    v = "CODEX" if a.host == "claude" else "CLAUDE"
    env.update({"HJW_PROBE": "1", "CLAUDE_PLUGIN_DATA": os.path.dirname(a.config),
                f"{v}_TIMEOUT": str(limit)})
    if stage == "smoke":
        env["CODEX_EFFORT" if a.host == "claude" else "HJW_CLAUDE_EFFORT"] = "low"
    elif a.host == "claude":
        env["CODEX_SANDBOX"] = sandbox  # explicit; the Codex host has no sandbox
    t0 = time.time()
    rc, _out, err = run([runner, "--mode", "consult", "-o", out, "-"], limit + MARGIN,
                        env=env, cwd=repo, stdin_text=brief)
    dur = round(time.time() - t0, 1)
    log = os.path.splitext(out)[0] + ".log"
    reply, header, cause = slurp(out), slurp(log, first=True), None
    if rc != 0:
        fails = [l.strip() for l in err.splitlines() if l.strip().startswith("- ")]
        detail = fails[0] if fails else lines(err)[-1]
        cause = ("runner timed out" if rc is None else f"runner exit {rc}") + (
            f": {detail[:200]}" if detail else "")
    return {"rc": rc, "reply": reply, "header": header, "dur": dur, "cause": cause,
            "artefacts": [out, log, os.path.splitext(out)[0] + ".events.jsonl"]}


def make_repo(nonce):
    """The temp repo; on ANY failure here it is removed before the error leaves
    (ownership passes to the caller only on return)."""
    def failed(cause):
        return Fail(EXIT_PROBE, {"stage": "temp-repo", "cause": cause[:200]})
    # A cancel cannot land between allocation and the guard: TERM/INT stay
    # blocked until the first statement inside it (a pending one fires there).
    sigs = (signal.SIGTERM, signal.SIGINT)
    signal.pthread_sigmask(signal.SIG_BLOCK, sigs)
    try:
        raw = tempfile.mkdtemp(prefix="hjw-setup-")
    except Exception as e:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, sigs)
        raise failed(f"mkdtemp: {type(e).__name__}")
    try:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, sigs)
        repo = os.path.realpath(raw)
        cwd = os.path.realpath(os.getcwd())
        if repo == cwd or repo.startswith(cwd.rstrip("/") + "/"):
            raise failed("temp dir is under the cwd")
        with open(os.path.join(repo, "weather_notes.txt"), "w") as f:
            f.write(nonce + "\n")
        git = ["git", "-c", "user.name=haejwo-setup", "-c", "user.email=setup@haejwo.invalid",
               "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
        for argv in (git + ["init", "-q"], git + ["add", "weather_notes.txt"],
                     git + ["commit", "-q", "-m", "setup probe"]):
            rc, _o, err = run(argv, 30, cwd=repo)
            if rc != 0:
                raise failed(lines(err)[0] or "git failed")
        return repo
    except BaseException as e:
        shutil.rmtree(raw, ignore_errors=True)  # a cancel propagates as itself
        if isinstance(e, Exception) and not isinstance(e, (Fail, Cancelled)):
            raise failed(f"{type(e).__name__}: {e}")
        raise


def cmd_verify(a):
    if (a.host == "claude") != bool(a.sandbox):
        raise usage("--sandbox is required on the Claude host and invalid on the Codex host")
    consent = None
    if a.consented_at is not None:
        try:
            consent = json.loads(a.consented_at)
        except ValueError:
            consent = None
        if isinstance(consent, bool) or not isinstance(consent, (int, float)) or consent <= 0:
            raise usage("--consented-at needs a unix timestamp")
    elif a.consented:
        consent = int(time.time())
    if a.sandbox == DFA and consent is None:
        raise usage(f"{DFA} needs --consented or --consented-at")
    # Step 0: no earlier enablement survives an interruption; `pending` tells a
    # failed or interrupted verification apart from a deliberate opt-out.
    cfg = read_config(a.config)
    codex = section(cfg, "codex", a.config)
    disable_reviewer(codex)
    codex["verification_pending"] = True
    if a.effort:
        codex["effort"] = a.effort
    write_config(a.config, cfg)
    art = os.path.join(os.path.dirname(a.config), "setup")
    os.makedirs(art, mode=0o700, exist_ok=True)
    nonce = secrets.token_hex(8)
    _REDACT.append(nonce)
    durations, artefacts, repo = {}, {}, None
    want_header = f"config={esc_display(a.config)} ("
    try:
        repo = make_repo(nonce)
        for stage, brief in (("smoke", SMOKE_BRIEF),
                             ("read", READ_BRIEF.format(os.path.join(repo, "weather_notes.txt")))):
            probe_ts = int(time.time())
            r = runner_call(a, stage, brief, repo, art, a.sandbox)
            durations[f"{stage}_s"] = r["dur"]
            artefacts[stage] = r["artefacts"]
            base = {"durations": durations, "artefacts": artefacts,
                    "closing": lines(r["reply"])[-1]}
            if r["header"] and want_header not in r["header"]:
                raise Fail(EXIT_PROBE, dict(base, stage="config-path",
                                            cause="runner log header names another config"))
            token = "HAEJWO-OK" if stage == "smoke" else nonce
            if r["cause"] or token not in r["reply"]:
                cause = r["cause"] or ("reply lacks HAEJWO-OK" if stage == "smoke"
                                       else "reply lacks the file content")
                raise Fail(EXIT_PROBE, dict(base, stage=stage, cause=cause))
            if not r["header"]:
                raise Fail(EXIT_PROBE, dict(base, stage="config-path",
                                            cause="runner log header missing"))
    finally:
        if repo:
            shutil.rmtree(repo, ignore_errors=True)
    cfg = read_config(a.config)
    codex = section(cfg, "codex", a.config)
    disable_reviewer(codex)
    codex.update(enabled=True, verified_at=probe_ts)
    if a.host == "claude":
        codex["consult_sandbox"] = a.sandbox
        if a.sandbox == DFA:
            codex["danger_full_access_consented_at"] = consent
    codex["verify_fingerprint"] = preflight(a.host, a.config, codex)["fingerprint"]
    write_config(a.config, cfg)
    return emit({"verified": True, "sandbox": a.sandbox if a.host == "claude" else "n/a",
                 "verified_at": probe_ts, "durations": durations,
                 "artefacts": artefacts, "path": a.config})


# ---- keep-check --------------------------------------------------------------
def keep_rule(cfg, pf, host):
    if not cfg:
        return False, "not configured (no config)"
    if cfg.get("configured") is not True:
        return False, "configured is not true"
    codex = cfg.get("codex") if isinstance(cfg.get("codex"), dict) else {}
    if codex.get("verification_pending"):
        return False, "reviewer verification pending or failed"
    if codex.get("enabled") is not True:
        if any(k in codex for k in REVIEWER_KEYS[:2]):
            return False, "reviewer disabled but sandbox/consent keys remain"
        return True, "configured; reviewer off"
    ts = codex.get("verified_at")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return False, "reviewer enabled without verified_at"
    if time.time() - ts > STALE_DAYS * 86400:
        return False, f"reviewer verification older than {STALE_DAYS} days"
    if not codex.get("verify_fingerprint"):
        return False, "reviewer settings changed; re-verify"
    if codex.get("verify_fingerprint") != pf["fingerprint"]:
        return False, "machine, reviewer CLI or reviewer settings changed since verification"
    if pf["cli"]["auth"] == "not-logged-in":
        return False, "reviewer CLI not logged in"
    if host == "claude":
        sb = codex.get("consult_sandbox")
        if sb == "read-only" and pf["sandbox_readonly"] != "ok":
            return False, "read-only stored but the sandbox check is not ok"
        if sb == DFA and not isinstance(codex.get("danger_full_access_consented_at"), (int, float)):
            return False, f"{DFA} stored without a consent stamp"
        if sb not in ("read-only", DFA):
            return False, "reviewer enabled without a verified sandbox"
    return True, "configured; reviewer verified"


def cmd_keep_check(a):
    cfg = read_config(a.config)
    pf = preflight(a.host, a.config)
    keep, why = keep_rule(cfg, pf, a.host)
    codex = cfg.get("codex") if isinstance(cfg.get("codex"), dict) else {}
    stored = {k: codex.get(k) for k in ("enabled", "verified_at", "consult_sandbox",
                                        "danger_full_access_consented_at")}
    return emit({"keep": keep, "why": why, "stored": stored, "preflight": pf})


# ---- CLI ---------------------------------------------------------------------
class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise usage(message[:300])


def parse(argv):
    p = Parser(prog="setup_probe.py", description=__doc__.split("\n\n")[0],
               formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True, parser_class=Parser)
    common = Parser(add_help=False)
    common.add_argument("--config", required=True)
    common.add_argument("--host", choices=("claude", "codex"), default="claude")
    common.add_argument("--plugin-root",
                        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    onoff = ("on", "off")
    sub.add_parser("preflight", parents=[common])
    sub.add_parser("keep-check", parents=[common])
    w = sub.add_parser("write", parents=[common])
    w.add_argument("--gate", choices=onoff)
    w.add_argument("--budget", type=int)
    w.add_argument("--bash-guard", choices=onoff)
    w.add_argument("--preset", choices=("standard", "budget", "custom"))
    for r in ROLES:
        w.add_argument("--" + r.replace("_", "-"), dest=r)
    w.add_argument("--reviewer", choices=onoff)
    m = w.add_mutually_exclusive_group()
    m.add_argument("--model")
    m.add_argument("--model-default", action="store_true")
    w.add_argument("--effort", choices=("medium", "high", "xhigh"))
    v = sub.add_parser("verify", parents=[common])
    v.add_argument("--sandbox", choices=("read-only", DFA))
    c = v.add_mutually_exclusive_group()
    c.add_argument("--consented", action="store_true")
    c.add_argument("--consented-at")
    v.add_argument("--effort", choices=("medium", "high", "xhigh"))
    a = p.parse_args(argv)
    a.config = os.path.abspath(a.config)
    if getattr(a, "budget", None) is not None and a.budget < 1:
        p.error("--budget must be >= 1")
    return a


def main(argv=None):
    os.umask(0o077)
    for sig in (signal.SIGTERM, signal.SIGINT):  # cleanup (temp repo, children) still runs
        signal.signal(sig, on_signal)
    try:
        a = parse(sys.argv[1:] if argv is None else argv)
        if a.mode == "preflight":
            return emit(preflight(a.host, a.config))
        return {"write": cmd_write, "verify": cmd_verify,
                "keep-check": cmd_keep_check}[a.mode](a)
    except Fail as f:
        return emit(f.obj, f.code)
    except Cancelled as c:  # the config stays disabled + pending (verify step 0)
        return emit({"stage": "cancelled", "cause": str(c)}, EXIT_PROBE)
    except Exception as e:  # never raise (P4)
        return emit({"error": "internal", "detail": type(e).__name__}, EXIT_PROBE)


if __name__ == "__main__":
    sys.exit(main())
