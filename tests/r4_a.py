"""2.35.0 setup helper (plan D, ledger objections 3, 7-11, 13): the
deterministic half of /haejwo:setup — scripts/setup_probe.py preflight /
write / verify / keep-check — against stub CLIs on PATH and a stub runner
directory (--plugin-root). These tests NEVER call the real codex or claude;
they prove the script's logic, not the CLIs' behaviour (the one real run is
recorded in the K5 ledger). Wired into tests/test_hooks.py main();
standalone: cd tests && python3 -c "import r4_a as m; m.run_section()"
"""
import ast
import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import test_hooks as T

PROBE = os.path.join(T.SCRIPTS, "setup_probe.py")
BWRAP = "bwrap: Failed to make / slave: Permission denied"
DISCLOSURE = (
    "Future Codex reviewers run with your permissions, including possible network "
    "access. The REVIEWER CONTRACT and git change detection are not a security "
    "boundary: package installs, MCP/user config changes, ignored/out-of-repo files, "
    "and edit-then-restore changes can escape detection. Briefs/repository content "
    "go to OpenAI. Consent is remembered until revoked.")
# A runner shaped like the real one: bash, the CLI under the REAL lib/bounded.py
# (its own session), stdout/stderr redirected away. The "CLI" records its pid and
# hangs. STUB_ESCAPE=1 also leaves a setsid'd descendant holding the pipes.
HANG_STUB = r"""#!/bin/bash
out=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done
cat > /dev/null
pwd > "$STUB_DIR/cwd"
python3 -c 'import json,os,sys; c=json.load(open(sys.argv[1])); json.dump(c.get("codex"), open(sys.argv[2],"w"))' \
  "$CLAUDE_PLUGIN_DATA/config.json" "$STUB_DIR/cfg_during.json"
echo "# stub config=$CLAUDE_PLUGIN_DATA/config.json (env) config_status=ok" > "${out%.*}.log"
if [ -n "${STUB_ESCAPE:-}" ]; then
  python3 -c 'import os,sys,time; os.setsid(); open(sys.argv[1],"w").write(str(os.getpid())); time.sleep(300)' "$STUB_DIR/esc.pid" &
fi
trap 'exit 143' TERM
python3 "$STUB_BOUNDED" 600 bash -c 'echo $$ > "$STUB_DIR/gc.pid"; exec sleep 300' > /dev/null 2>&1
"""

CODEX_STUB = r"""#!/bin/bash
case "$1" in
  --version) echo "codex-cli ${STUB_VER:-0.1.0}" ;;
  login) [ -n "${STUB_LOGIN_OUT:-}" ] && echo "$STUB_LOGIN_OUT"; exit "${STUB_LOGIN_RC:-0}" ;;
  sandbox) [ -n "${STUB_SB_SLEEP:-}" ] && sleep "$STUB_SB_SLEEP"
           [ -n "${STUB_SB_ERR:-}" ] && echo "$STUB_SB_ERR" >&2; exit "${STUB_SB_RC:-0}" ;;
esac
"""
CLAUDE_STUB = '#!/bin/bash\necho "${STUB_VER:-2.1.0} (Claude Code)"\n'
# The stub runner records what it saw (env, brief, cwd, the repo file) and
# answers per STUB_SMOKE / STUB_READ; STUB_HDR overrides the header's path.
RUNNER_STUB = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["STUB_DIR"]
out = sys.argv[sys.argv.index("-o") + 1]
brief = sys.stdin.read()
stage = "smoke" if "HAEJWO-OK" in brief else "read"
cfgp = os.path.join(os.environ["CLAUDE_PLUGIN_DATA"], "config.json")
with open(os.path.join(d, stage + ".json"), "w") as f:
    json.dump({"env": dict(os.environ), "brief": brief, "cwd": os.getcwd(),
               "argv": sys.argv[1:], "codex_cfg": json.load(open(cfgp)).get("codex")}, f)
if os.path.exists("weather_notes.txt"):
    with open(os.path.join(d, "nonce"), "w") as f:
        f.write(open("weather_notes.txt").read().strip())
cfg = os.environ.get("STUB_HDR") or os.environ["CLAUDE_PLUGIN_DATA"] + "/config.json"
with open(os.path.splitext(out)[0] + ".log", "w") as f:
    f.write("# stub_consult mode=consult config=%s (env) config_status=ok\n" % cfg)
mode = os.environ.get("STUB_SMOKE" if stage == "smoke" else "STUB_READ", "ok")
if mode == "rc":
    sys.stderr.write("  - codex exit code 1\n")
    sys.exit(1)
if mode == "leak":  # the nonce in the reply's last line and in the failure line
    nonce = open("weather_notes.txt").read().strip()
    with open(out, "w") as f:
        f.write(nonce + "\n")
    sys.stderr.write("  - repository changed: %s\n" % nonce)
    sys.exit(1)
if stage == "smoke":
    reply = "HAEJWO-OK\n" if mode == "ok" else "something else\n"
else:
    path = brief.split("File (absolute path): ", 1)[1].strip()
    reply = open(path).read() if mode == "ok" else "Unable to read the file.\n"
with open(out, "w") as f:
    f.write(reply + "Closing: read 1 file; no verification; no omissions.\n")
print(reply)
'''


def run_section():
    print("== 2.35.0 setup helper (setup_probe.py) ==")
    check = T.check
    tmp = tempfile.mkdtemp(prefix="hjw-r4a-")
    stub_bin = os.path.join(tmp, "bin")
    plug = os.path.join(tmp, "plug")
    hang = os.path.join(tmp, "hang")
    os.makedirs(os.path.join(hang, "scripts"))
    os.makedirs(stub_bin)
    tmpdir, work = os.path.join(tmp, "t"), os.path.join(tmp, "w")
    os.makedirs(tmpdir)
    os.makedirs(work)
    os.makedirs(os.path.join(plug, "scripts"))
    for path, body in ((os.path.join(stub_bin, "codex"), CODEX_STUB),
                       (os.path.join(stub_bin, "claude"), CLAUDE_STUB),
                       (os.path.join(plug, "scripts", "codex_consult.sh"), RUNNER_STUB),
                       (os.path.join(plug, "scripts", "claude_consult.sh"), RUNNER_STUB),
                       (os.path.join(hang, "scripts", "codex_consult.sh"), HANG_STUB)):
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)
    # PATH without any real codex/claude: the stubs, or nothing (not installed).
    clean = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and not any(
        os.path.exists(os.path.join(p, n)) for n in ("codex", "claude"))]
    n = [0]

    def cfg_path(seed=None, raw=None):
        n[0] += 1
        d = os.path.join(tmp, f"c{n[0]}", "haejwo-haejwo")
        os.makedirs(d)
        p = os.path.join(d, "config.json")
        if seed is not None or raw is not None:
            with open(p, "w") as f:
                f.write(raw if raw is not None else json.dumps(seed))
        return p

    def alive(pidfile):
        try:
            pid = int(open(pidfile).read())
        except (OSError, ValueError):
            return None
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        st = open(f"/proc/{pid}/stat").read().split() if os.path.exists(f"/proc/{pid}") else []
        return not (len(st) > 2 and st[2] == "Z")

    def probe(*args, stubs=True, cwd=None, **stub_env):
        sd = os.path.join(tmp, "stubdata")
        shutil.rmtree(sd, ignore_errors=True)
        os.makedirs(sd)
        env = {k: v for k, v in os.environ.items() if not k.startswith("STUB_")}
        env.update(PATH=os.pathsep.join(([stub_bin] if stubs else []) + clean),
                   STUB_DIR=sd, CODEX_MODEL="leak-model", CLAUDE_MODEL="leak-model",
                   CODEX_SANDBOX="workspace-write", CODEX_EFFORT="xhigh",
                   HJW_CLAUDE_EFFORT="xhigh", TMPDIR=tmpdir)
        env.update({k: str(v) for k, v in stub_env.items()})
        argv = [sys.executable, PROBE] + list(args)
        if args[0] == "verify":
            argv += ["--plugin-root", plug]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=env,
                           cwd=cwd or work)
        try:
            out = json.loads(p.stdout)
        except ValueError:
            out = {"_raw": p.stdout, "_err": p.stderr[-300:]}
        seen = {}
        for name in ("smoke", "read"):
            f = os.path.join(sd, name + ".json")
            if os.path.exists(f):
                seen[name] = json.load(open(f))
        nf = os.path.join(sd, "nonce")
        seen["nonce"] = open(nf).read() if os.path.exists(nf) else None
        seen["text"] = p.stdout + p.stderr
        return p.returncode, out, seen

    def load(p):
        return json.load(open(p))

    try:
        # --- canaries ------------------------------------------------------
        for host in (".claude-plugin", ".codex-plugin"):
            v = json.load(open(os.path.join(T.PLUGIN, host, "plugin.json")))["version"]
            check(f"2.35 version {host} == 2.35.1", v == "2.35.1", v)
        tree = ast.parse(open(PROBE).read())
        mods = {a.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import) for a in node.names}
        mods |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
        foreign = sorted(m for m in mods
                         if m not in sys.stdlib_module_names and m != "hjw_common")
        check("2.35 setup_probe.py imports only stdlib + hjw_common", not foreign, foreign)
        p = subprocess.run([sys.executable, "-I", PROBE, "--help"], capture_output=True,
                           timeout=30, cwd=tmp)
        check("2.35 python3 -I setup_probe.py --help exits 0", p.returncode == 0)

        # --- preflight -----------------------------------------------------
        c = cfg_path()
        rc, out, _ = probe("preflight", "--config", c)
        check("2.35 preflight: authenticated + sandbox exit 0 -> ok, never writes",
              rc == 0 and out["cli"] == {"installed": True, "version": "codex-cli 0.1.0",
                                         "auth": "authenticated"}
              and out["sandbox_readonly"] == "ok" and not os.path.exists(c), out)
        rc, out, _ = probe("preflight", "--config", c, STUB_LOGIN_RC=1,
                           STUB_LOGIN_OUT="Not logged in")
        check("2.35 preflight: not logged in", out["cli"]["auth"] == "not-logged-in", out)
        rc, out, _ = probe("preflight", "--config", c, STUB_LOGIN_RC=1,
                           STUB_LOGIN_OUT="network error")
        check("2.35 preflight: other login failure -> auth unknown",
              out["cli"]["auth"] == "unknown", out)
        rc, out, _ = probe("preflight", "--config", c, stubs=False)
        check("2.35 preflight: not installed",
              rc == 0 and out["cli"] == {"installed": False, "version": None, "auth": "unknown"}
              and out["sandbox_readonly"] == "unknown", out)
        rc, out, _ = probe("preflight", "--config", c, STUB_SB_RC=1, STUB_SB_ERR=BWRAP)
        check("2.35 preflight: bwrap permission line -> broken + reason",
              out["sandbox_readonly"] == "broken" and out["reason"] == BWRAP, out)
        rc, out, _ = probe("preflight", "--config", c, STUB_SB_RC=1,
                           STUB_SB_ERR="Landlock: ruleset unsupported by kernel")
        check("2.35 preflight: landlock line -> broken", out["sandbox_readonly"] == "broken", out)
        for err, want in (
                ("error: failed to read user configuration: Operation not permitted", "unknown"),
                ("Operation not permitted", "unknown"),
                ("note: retried after bwrap: Permission denied", "unknown"),
                ("error: failed to read user configuration /tmp/sandbox/config.toml: "
                 "Operation not permitted", "unknown"),
                ("/tmp/landlock/config.toml: Permission denied", "unknown"),
                ("open /var/sandbox/x: Permission denied", "unknown"),
                ("error: failed to read sandbox configuration: Permission denied", "unknown"),
                ('error: open "/tmp/my sandbox project/input.txt": Permission denied',
                 "unknown"),
                ("error: bwrap: Permission denied", "unknown"),
                ("bwrap: something harmless", "unknown"),
                ("error: Landlock ruleset unsupported by kernel", "unknown"),
                ("error: sandbox unsupported on this platform", "unknown"),
                ("bwrap: execvp true: Permission denied", "unknown"),
                ("bwrap: execvp /bin/true: No such file or directory", "unknown"),
                (BWRAP, "broken"),
                ("bwrap: Creating new namespace failed, likely because the kernel does not "
                 "support user namespaces.", "broken"),
                ("bwrap: No permissions to create new namespace, likely because the kernel "
                 "does not allow non-privileged user namespaces.", "broken"),
                ("bwrap: setting up uid map: Permission denied", "broken"),
                ("BWRAP: Failed to make / slave: permission denied", "broken"),
                ("landlock: Operation not permitted", "broken"),
                ("sandbox: unsupported kernel", "broken"),
                ("codex-linux-sandbox: landlock not permitted", "broken"),
                ("bwrap: Can't mount proc: Operation not permitted", "broken")):
            rc, out, _ = probe("preflight", "--config", c, STUB_SB_RC=1, STUB_SB_ERR=err)
            check(f"2.35 preflight: {err!r} -> {want} (launcher lines only)",
                  out["sandbox_readonly"] == want and out["reason"] == err, out)
        rc, out, _ = probe("preflight", "--config", c, STUB_SB_RC=1,
                           STUB_SB_ERR="warning: retrying the launcher\n" + BWRAP)
        check("2.35 preflight: multi-line stderr, the bwrap line second -> broken "
              "(reason = first line)",
              out["sandbox_readonly"] == "broken"
              and out["reason"] == "warning: retrying the launcher", out)
        rc, out, _ = probe("preflight", "--config", c, STUB_SB_RC=2,
                           STUB_SB_ERR="error: unrecognized subcommand 'sandbox'")
        check("2.35 preflight: other non-zero -> unknown (+reason)",
              out["sandbox_readonly"] == "unknown" and "unrecognized" in out["reason"], out)
        t0 = time.time()
        rc, out, _ = probe("preflight", "--config", c, STUB_SB_SLEEP=20)
        check("2.35 preflight: sandbox timeout (5 s bound) -> unknown",
              out["sandbox_readonly"] == "unknown" and time.time() - t0 < 15, out)
        rc, out, _ = probe("preflight", "--config", c, "--host", "codex")
        check("2.35 preflight on the Codex host: n/a, auth unknown, claude version",
              out["sandbox_readonly"] == "n/a" and out["cli"]["auth"] == "unknown"
              and out["cli"]["version"] == "2.1.0 (Claude Code)", out)
        fp = [probe("preflight", "--config", c, STUB_SB_RC=1, STUB_SB_ERR=BWRAP, **kw)[1]
              ["fingerprint"] for kw in ({}, {}, {"STUB_VER": "0.2.0"})]
        check("2.35 preflight: fingerprint stable, changes with the CLI version",
              fp[0] == fp[1] != fp[2] and len(fp[0]) == 12, fp)

        # --- write ---------------------------------------------------------
        c = cfg_path()
        rc, out, _ = probe("write", "--config", c, "--gate", "on", "--budget", "2",
                           "--bash-guard", "on", "--preset", "standard")
        cfg = load(c) if os.path.exists(c) else {}
        check("2.35 write: absent config -> created, configured true, keys given only",
              rc == 0 and cfg == {"gate": {"enabled": True, "max_files_per_turn": 2,
                                           "bash_guard": True},
                                  "models": {"deep_reasoner": "inherit",
                                             "default_worker": "opus", "task_worker": "opus"},
                                  "configured": True}, cfg)
        seed = {"zzz": {"a": [1, 2]}, "version": 1, "gate": {"turn_checklist": True},
                "codex": {"enabled": True, "custom": {"x": 1}, "model": "m1",
                          "consult_sandbox": "danger-full-access",
                          "danger_full_access_consented_at": 1700000000,
                          "verify_fingerprint": "abc", "verified_at": 1700000000}}
        c = cfg_path(seed)
        rc, out, _ = probe("write", "--config", c, "--preset", "budget", "--model-default")
        cfg = load(c)
        check("2.35 write: unknown keys preserved (top level and nested in codex)",
              cfg["zzz"] == seed["zzz"] and cfg["gate"] == {"turn_checklist": True}
              and cfg["codex"]["custom"] == {"x": 1} and cfg["codex"]["enabled"] is True, cfg)
        check("2.35 write: --model-default removes codex.model; budget tiers",
              "model" not in cfg["codex"] and cfg["models"] == {
                  "deep_reasoner": "inherit", "default_worker": "sonnet",
                  "task_worker": "haiku"}, cfg)
        rc, out, _ = probe("write", "--config", c, "--reviewer", "off")
        cfg = load(c)
        check("2.35 write: --reviewer off -> enabled false, the three keys cleared",
              cfg["codex"]["enabled"] is False and not any(
                  k in cfg["codex"] for k in ("consult_sandbox", "verify_fingerprint",
                                              "danger_full_access_consented_at")), cfg)
        c = cfg_path()
        rc, out, _ = probe("write", "--config", c, "--host", "codex", "--preset", "budget")
        cfg = load(c)
        check("2.35 write: Codex host preset -> models_codex",
              cfg.get("models_codex") == {"deep_reasoner": "inherit",
                                          "default_worker": "gpt-5.6-terra",
                                          "task_worker": "gpt-5.6-luna"}
              and "models" not in cfg, cfg)
        rc, out, _ = probe("write", "--config", c, "--preset", "custom",
                           "--default-worker", "sonnet", "--model", "gpt-x", "--effort", "high")
        cfg = load(c)
        check("2.35 write: custom writes the given roles only; model/effort",
              cfg["models"] == {"default_worker": "sonnet"}
              and cfg["codex"] == {"model": "gpt-x", "effort": "high"}, cfg)
        for bad in (["--default-worker", "sonnet"], ["--preset", "custom"], ["--budget", "0"],
                    ["--model", "a", "--model-default"], ["--gate", "maybe"]):
            before = open(c, "rb").read()
            rc, out, _ = probe("write", "--config", c, *bad)
            check(f"2.35 write usage error {bad} -> exit 2, JSON, untouched",
                  rc == 2 and out.get("error") == "usage" and open(c, "rb").read() == before,
                  (rc, out))
        for raw in ('{"gate": {', "[1, 2]", '{"gate": 3}'):
            c = cfg_path(raw=raw)
            rc, out, _ = probe("write", "--config", c, "--gate", "on")
            check(f"2.35 write: malformed {raw!r} -> exit 3, bytes identical",
                  rc == 3 and out.get("error") == "malformed"
                  and open(c).read() == raw, (rc, out))
        c = cfg_path({"keep": 1})
        before = open(c, "rb").read()
        sys.path.insert(0, T.SCRIPTS)
        import setup_probe as SP
        real_replace = os.replace

        def boom(*_a, **_k):
            raise OSError("injected")
        os.replace = boom
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = SP.main(["write", "--config", c, "--gate", "off"])
        finally:
            os.replace = real_replace
        check("2.35 write atomicity: failed replace -> exit 3, file intact, no temp left",
              rc == 3 and json.loads(buf.getvalue())["error"] == "unwritable"
              and open(c, "rb").read() == before
              and os.listdir(os.path.dirname(c)) == ["config.json"], buf.getvalue())

        # --- verify --------------------------------------------------------
        def armed(**codex):
            base = {"configured": True, "other": {"k": 1},
                    "codex": {"enabled": True, "consult_sandbox": "danger-full-access",
                              "danger_full_access_consented_at": 1700000000,
                              "verify_fingerprint": "old", "verified_at": 1700000000}}
            base["codex"].update(codex)
            return cfg_path(base)

        def gone(seen):
            cwds = [seen[s]["cwd"] for s in ("smoke", "read") if s in seen]
            return bool(cwds) and not any(os.path.exists(x) for x in cwds)

        def disabled(c):
            cx = load(c)["codex"]
            return cx["enabled"] is False and not any(
                k in cx for k in ("consult_sandbox", "danger_full_access_consented_at",
                                  "verify_fingerprint"))

        c = armed()
        rc, out, seen = probe("verify", "--config", c, "--sandbox", "read-only",
                              STUB_SMOKE="rc")
        check("2.35 verify: smoke failure -> exit 4 stage smoke, disabled, keys absent",
              rc == 4 and out.get("stage") == "smoke" and "exit 1" in out.get("cause", "")
              and disabled(c) and "read" not in seen and load(c)["other"] == {"k": 1}, out)
        check("2.35 verify: temp repo deleted (smoke failure)", gone(seen), seen.get("smoke"))
        check("2.35 verify: config read WHILE the runner runs = disabled + pending",
              (seen.get("smoke") or {}).get("codex_cfg", {}).get("enabled") is False
              and seen["smoke"]["codex_cfg"].get("verification_pending") is True
              and "consult_sandbox" not in seen["smoke"]["codex_cfg"], seen.get("smoke"))
        rc, out, _ = probe("keep-check", "--config", c)
        check("2.35 failed verification: pending stays, keep-check false (not an opt-out)",
              load(c)["codex"].get("verification_pending") is True and out.get("keep") is False
              and "pending" in out.get("why", ""), out)
        probe("write", "--config", c, "--reviewer", "off")
        rc, out, _ = probe("keep-check", "--config", c)
        check("2.35 write --reviewer off clears pending -> keep true, reviewer off",
              "verification_pending" not in load(c)["codex"] and out.get("keep") is True
              and "reviewer off" in out.get("why", ""), out)
        c = armed()
        rc, out, seen = probe("verify", "--config", c, "--sandbox", "read-only",
                              STUB_SMOKE="bad")
        check("2.35 verify: smoke reply without HAEJWO-OK -> exit 4",
              rc == 4 and out.get("stage") == "smoke" and disabled(c), out)
        c = armed()
        rc, out, seen = probe("verify", "--config", c, "--sandbox", "read-only",
                              STUB_READ="miss")
        check("2.35 verify: nonce mismatch with rc=0 -> exit 4 stage read, disabled",
              rc == 4 and out.get("stage") == "read" and disabled(c)
              and out.get("closing", "").startswith("Closing:"), out)
        check("2.35 verify: temp repo deleted (read failure)", gone(seen))
        check("2.35 verify: no nonce in output (failure)",
              seen["nonce"] and seen["nonce"] not in seen["text"])
        c = armed()
        rc, out, seen = probe("verify", "--config", c, "--sandbox", "read-only",
                              "--consented", STUB_SB_RC=0)
        cx = load(c)["codex"]
        fp_now = probe("preflight", "--config", c)[1]["fingerprint"]
        check("2.35 verify: nonce match -> enabled, verified_at, read-only, fingerprint",
              rc == 0 and out.get("verified") is True and cx["enabled"] is True
              and isinstance(cx["verified_at"], int) and abs(cx["verified_at"] - time.time()) < 60
              and cx["consult_sandbox"] == "read-only" and cx["verify_fingerprint"] == fp_now
              and set(out["durations"]) == {"smoke_s", "read_s"}, (out, cx))
        check("2.35 verify: read-only never writes a consent stamp (even with --consented)",
              "danger_full_access_consented_at" not in cx and "verification_pending" not in cx, cx)
        k1 = probe("keep-check", "--config", c)[1].get("keep")
        outw = probe("write", "--config", c, "--effort", "high")[1]
        outk = probe("keep-check", "--config", c)[1]
        check("2.35 write --effort after verification drops the fingerprint -> keep false",
              k1 is True and "codex.verify_fingerprint" in outw.get("removed", [])
              and load(c)["codex"]["enabled"] is True and "verified_at" in load(c)["codex"]
              and outk.get("keep") is False and "settings changed" in outk.get("why", ""),
              (k1, outw, outk))
        check("2.35 verify: temp repo deleted (success); nonce not in output or brief",
              gone(seen) and seen["nonce"] not in seen["text"]
              and len(seen["nonce"]) == 16
              and all(seen["nonce"] not in seen[s]["brief"] for s in ("smoke", "read")))
        art = os.path.join(os.path.dirname(c), "setup")
        check("2.35 verify: artefacts under <config dir>/setup/ (0700), -o per stage",
              all(os.path.dirname(p) == art for s in ("smoke", "read")
                  for p in out["artefacts"][s])
              and oct(os.stat(art).st_mode & 0o777) == "0o700"
              and seen["read"]["argv"][:3] == ["--mode", "consult", "-o"], out)
        e_s, e_r = seen["smoke"]["env"], seen["read"]["env"]
        check("2.35 verify env: smoke = HJW_PROBE=1 CODEX_EFFORT=low CODEX_TIMEOUT=90, "
              "no CODEX_MODEL / CODEX_SANDBOX",
              e_s.get("HJW_PROBE") == "1" and e_s.get("CODEX_EFFORT") == "low"
              and e_s.get("CODEX_TIMEOUT") == "90" and "CODEX_MODEL" not in e_s
              and "CODEX_SANDBOX" not in e_s, e_s)
        check("2.35 verify env: read = HJW_PROBE=1, explicit CODEX_SANDBOX exactly once, "
              "no CODEX_EFFORT / CODEX_MODEL, CODEX_TIMEOUT=180",
              e_r.get("HJW_PROBE") == "1" and e_r.get("CODEX_SANDBOX") == "read-only"
              and sum(k == "CODEX_SANDBOX" for k in e_r) == 1
              and "CODEX_EFFORT" not in e_r and "CODEX_MODEL" not in e_r
              and e_r.get("CODEX_TIMEOUT") == "180"
              and e_r.get("CLAUDE_PLUGIN_DATA") == os.path.dirname(c), e_r)
        c = armed()
        rc, out, seen = probe("verify", "--config", c, "--sandbox", "danger-full-access",
                              "--consented")
        cx = load(c)["codex"]
        check("2.35 verify: danger-full-access --consented -> stamp now",
              rc == 0 and cx.get("consult_sandbox") == "danger-full-access"
              and abs(cx.get("danger_full_access_consented_at", 0) - time.time()) < 60, cx)
        c = armed()
        rc, out, seen = probe("verify", "--config", c, "--sandbox", "danger-full-access",
                              "--consented-at", "1761500000")
        cx = load(c)["codex"]
        check("2.35 verify: --consented-at preserved verbatim",
              rc == 0 and cx.get("danger_full_access_consented_at") == 1761500000
              and seen["read"]["env"].get("CODEX_SANDBOX") == "danger-full-access", cx)
        c = armed()
        before = open(c, "rb").read()
        rc, out, seen = probe("verify", "--config", c, "--sandbox", "danger-full-access")
        check("2.35 verify: danger-full-access without consent -> exit 2, nothing run/written",
              rc == 2 and open(c, "rb").read() == before and "smoke" not in seen, out)
        c = armed()
        rc, out, seen = probe("verify", "--config", c, "--sandbox", "read-only",
                              STUB_HDR="/elsewhere/haejwo-haejwo/config.json")
        check("2.35 verify: config-path header mismatch -> exit 4, disabled",
              rc == 4 and out.get("stage") == "config-path" and disabled(c)
              and "read" not in seen, out)
        c = armed()
        rc, out, seen = probe("verify", "--config", c, "--sandbox", "read-only",
                              STUB_READ="leak")
        check("2.35 verify: a failure echoing the nonce is redacted (closing, cause)",
              rc == 4 and out.get("stage") == "read" and seen["nonce"]
              and seen["nonce"] not in seen["text"] and out.get("closing") == "<redacted>"
              and "<redacted>" in out.get("cause", ""), out)
        c3 = cfg_path({"configured": True})
        rc, out, seen = probe("verify", "--config", c3, "--host", "codex")
        cx = load(c3)["codex"]
        e_s, e_r = seen["smoke"]["env"], seen["read"]["env"]
        check("2.35 verify on the Codex host: claude runner, no CODEX_SANDBOX, no sandbox key",
              rc == 0 and cx["enabled"] is True and "consult_sandbox" not in cx
              and "CODEX_SANDBOX" not in e_s and "CODEX_SANDBOX" not in e_r
              and e_s.get("HJW_CLAUDE_EFFORT") == "low" and e_s.get("CLAUDE_TIMEOUT") == "90"
              and "HJW_CLAUDE_EFFORT" not in e_r and e_r.get("CLAUDE_TIMEOUT") == "180"
              and "CLAUDE_MODEL" not in e_s and "CLAUDE_MODEL" not in e_r, (out, cx))
        rc, out, _ = probe("verify", "--config", c3, "--host", "codex", "--sandbox", "read-only")
        check("2.35 verify on the Codex host: --sandbox is a usage error", rc == 2, out)
        c = armed()
        rc, out, seen = probe("verify", "--config", c, "--sandbox", "read-only", cwd=tmp)
        check("2.35 verify: a temp dir under the cwd -> exit 4 temp-repo, no probe, disabled",
              rc == 4 and out.get("stage") == "temp-repo" and "smoke" not in seen
              and disabled(c) and os.listdir(tmpdir) == [], out)
        # --- termination: timeout, escaped descendants, cancel, temp-repo failure
        sys.path.insert(0, T.SCRIPTS)
        import setup_probe as SP

        def inproc(argv, **env):
            sd = os.path.join(tmp, "stubdata")
            shutil.rmtree(sd, ignore_errors=True)
            os.makedirs(sd)
            saved_env = dict(os.environ)
            saved_sig = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
            saved_lim = (dict(SP.LIMITS), SP.MARGIN, SP.TERM_GRACE)
            os.environ.update(STUB_DIR=sd, STUB_BOUNDED=os.path.join(T.SCRIPTS, "lib",
                                                                     "bounded.py"), **env)
            SP.LIMITS.update(smoke=2, read=2)
            SP.MARGIN, SP.TERM_GRACE = 1, 4
            buf = io.StringIO()
            t0 = time.time()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = SP.main(argv)
            finally:
                os.environ.clear()
                os.environ.update(saved_env)
                for s_, h in saved_sig.items():
                    signal.signal(s_, h)
                SP.LIMITS.update(saved_lim[0])
                SP.MARGIN, SP.TERM_GRACE = saved_lim[1], saved_lim[2]
                SP._REDACT.clear()
            return rc, json.loads(buf.getvalue() or "{}"), sd, time.time() - t0

        c = armed()
        rc, out, sd, dt = inproc(["verify", "--config", c, "--sandbox", "read-only",
                                  "--plugin-root", hang])
        repo_dir = open(os.path.join(sd, "cwd")).read().strip()
        check("2.35 verify timeout: exit 4 within the bound; bounded.py's own-session child "
              "is gone; temp repo gone; disabled + pending",
              rc == 4 and out.get("stage") == "smoke" and "timed out" in out.get("cause", "")
              and dt < 15 and alive(os.path.join(sd, "gc.pid")) is False
              and not os.path.exists(repo_dir) and disabled(c)
              and load(c)["codex"].get("verification_pending") is True, (rc, out, dt))
        c = armed()
        rc, out, sd, dt = inproc(["verify", "--config", c, "--sandbox", "read-only",
                                  "--plugin-root", hang], STUB_ESCAPE="1")
        esc = alive(os.path.join(sd, "esc.pid"))
        check("2.35 verify timeout with an escaped pipe-holding descendant: exit 4 within the "
              "bound, reported (not verified terminated), never blocks",
              rc == 4 and dt < 20 and "not verified terminated" in out.get("cause", "")
              and alive(os.path.join(sd, "gc.pid")) is False, (rc, out, dt, esc))
        with contextlib.suppress(Exception):
            os.kill(int(open(os.path.join(sd, "esc.pid")).read()), signal.SIGKILL)
        # cancel: SIGTERM to the helper while the (hanging) runner runs
        c = armed()
        sd = os.path.join(tmp, "stubdata")
        shutil.rmtree(sd, ignore_errors=True)
        os.makedirs(sd)
        env = dict(os.environ, STUB_DIR=sd, TMPDIR=tmpdir,
                   STUB_BOUNDED=os.path.join(T.SCRIPTS, "lib", "bounded.py"))
        hp = subprocess.Popen([sys.executable, PROBE, "verify", "--config", c, "--sandbox",
                               "read-only", "--plugin-root", hang], cwd=work, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 20
        while time.time() < deadline and not os.path.exists(os.path.join(sd, "gc.pid")):
            time.sleep(0.1)
        t0 = time.time()
        hp.send_signal(signal.SIGTERM)
        try:
            o, _e = hp.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            hp.kill()
            o, _e = hp.communicate()
        dt = time.time() - t0
        try:
            out = json.loads(o)
        except ValueError:
            out = {"_raw": o}
        repo_dir = open(os.path.join(sd, "cwd")).read().strip() if os.path.exists(
            os.path.join(sd, "cwd")) else ""
        check("2.35 verify cancel (SIGTERM): exit 4 stage cancelled, children reaped, temp "
              "repo gone, config disabled + pending",
              hp.returncode == 4 and out.get("stage") == "cancelled" and dt < 15
              and alive(os.path.join(sd, "gc.pid")) is False and repo_dir
              and not os.path.exists(repo_dir) and disabled(c)
              and load(c)["codex"].get("verification_pending") is True, (hp.returncode, out, dt))
        # nonce-file creation failure: the mkdtemp directory never survives
        c = armed()
        tdir = tempfile.gettempdir()
        before_dirs = {x for x in os.listdir(tdir) if x.startswith("hjw-setup-")}
        real_open = open

        def bad_open(path, *a, **k):
            if str(path).endswith("weather_notes.txt"):
                raise OSError("injected")
            return real_open(path, *a, **k)
        SP.open = bad_open
        try:
            rc, out, sd, dt = inproc(["verify", "--config", c, "--sandbox", "read-only",
                                      "--plugin-root", plug])
        finally:
            del SP.open
        after_dirs = {x for x in os.listdir(tdir) if x.startswith("hjw-setup-")}
        check("2.35 verify: nonce-file write failure -> exit 4 temp-repo, directory removed",
              rc == 4 and out.get("stage") == "temp-repo" and "injected" in out.get("cause", "")
              and after_dirs <= before_dirs and disabled(c), (rc, out))

        # a cancel landing while the fresh temp dir is resolved: removed, reported as cancel
        c = armed()
        before_dirs = {x for x in os.listdir(tdir) if x.startswith("hjw-setup-")}
        real_realpath = os.path.realpath
        hit = []

        def cancel_realpath(path, *a, **k):
            if os.path.basename(str(path)).startswith("hjw-setup-") and not hit:
                hit.append(path)
                raise SP.Cancelled("SIGTERM")
            return real_realpath(path, *a, **k)
        os.path.realpath = cancel_realpath
        try:
            rc, out, sd, dt = inproc(["verify", "--config", c, "--sandbox", "read-only",
                                      "--plugin-root", plug])
        finally:
            os.path.realpath = real_realpath
        after_dirs = {x for x in os.listdir(tdir) if x.startswith("hjw-setup-")}
        check("2.35 verify: cancel while resolving the temp dir -> stage cancelled, dir removed",
              rc == 4 and out.get("stage") == "cancelled" and hit and not os.path.exists(hit[0])
              and after_dirs <= before_dirs and disabled(c), (rc, out, hit))

        c = cfg_path(raw="{bad")
        rc, out, seen = probe("verify", "--config", c, "--sandbox", "read-only")
        check("2.35 verify: malformed config -> exit 3, no probe, untouched",
              rc == 3 and "smoke" not in seen and open(c).read() == "{bad", out)

        # --- keep-check ----------------------------------------------------
        bw = {"STUB_SB_RC": 1, "STUB_SB_ERR": BWRAP}
        now = int(time.time())

        good = {"enabled": True, "verified_at": now, "verify_fingerprint": None,
                "consult_sandbox": "danger-full-access",
                "danger_full_access_consented_at": now}
        cases = [
            ("fresh dfa + consent", dict(good), {}, True),
            ("stale (> STALE_DAYS)", dict(good, verified_at=now - 31 * 86400), {}, False),
            ("fingerprint mismatch (CLI version)", dict(good), dict(bw, STUB_VER="9.9"), False),
            ("read-only stored, sandbox broken", dict(good, consult_sandbox="read-only"), {},
             False),
            ("dfa without consent stamp",
             {k: v for k, v in good.items() if k != "danger_full_access_consented_at"}, {},
             False),
            ("reviewer deliberately off", {"enabled": False}, {}, True),
            ("disabled but sandbox key left", {"enabled": False,
                                               "consult_sandbox": "read-only"}, {}, False),
        ]
        for name, codex, kw, want in cases:
            p = cfg_path({"configured": True, "codex": codex})  # fingerprint: path + settings
            if "verify_fingerprint" in codex:  # as verify stored it (bwrap broken, 0.1.0)
                codex["verify_fingerprint"] = probe("preflight", "--config", p,
                                                    **bw)[1]["fingerprint"]
            with open(p, "w") as f:
                json.dump({"configured": True, "codex": codex}, f)
            rc, out, _ = probe("keep-check", "--config", p, **(kw or bw))
            check(f"2.35 keep-check: {name} -> keep={want}",
                  rc == 0 and out.get("keep") is want and out.get("why"), out)
        p = cfg_path({"configured": False, "codex": {"enabled": False}})
        rc, out, _ = probe("keep-check", "--config", p, **bw)
        check("2.35 keep-check: not configured -> keep false", out.get("keep") is False, out)
        p = cfg_path({"configured": True, "codex": {"enabled": True, "verified_at": now,
                                                    "verify_fingerprint": "x",
                                                    "consult_sandbox": "read-only"}})
        fp_ok = probe("preflight", "--config", p)[1]["fingerprint"]
        with open(p, "w") as f:
            json.dump({"configured": True, "codex": {"enabled": True, "verified_at": now,
                                                     "verify_fingerprint": fp_ok,
                                                     "consult_sandbox": "read-only"}}, f)
        before = open(p, "rb").read()
        rc, out, _ = probe("keep-check", "--config", p)
        check("2.35 keep-check: read-only stored, sandbox ok -> keep true, never writes",
              out.get("keep") is True and out["stored"]["consult_sandbox"] == "read-only"
              and open(p, "rb").read() == before, out)
        md = " ".join(open(os.path.join(T.PLUGIN, "commands", "setup.md"),
                           encoding="utf-8").read().split())
        check("2.35 setup.md carries the ledger's danger-full-access disclosure verbatim",
              DISCLOSURE in md)
        c = cfg_path(raw="nope")
        rc, out, _ = probe("keep-check", "--config", c)
        check("2.35 keep-check: malformed -> exit 3", rc == 3, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
