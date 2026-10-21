"""Field round 2, worker C: reviewer-runner disclosure tokens and observation
retention (three prior generations). Wired into tests/test_hooks.py main();
standalone: cd tests && python3 -c "import r2_c as m; m.run_section()"

Origins: project M 2026-10-20 (reviewer runs from another repo; the header did not
say what change detection covered) and a demo app 2026-10-20 (reviews with
0 shell commands went undisclosed); 2026-10-19 observation records were gone
with one prior generation.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time

import test_hooks as T

TOKEN = "file access unverified (0 shell commands)"
SCOPE_NONE = "scope=none (change detection off)"

STUB = r'''#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then echo "stub-version 0.0.0"; exit 0; fi
cat > /dev/null
out=""; json=0; prev=""
for a in "$@"; do
  case "$prev" in -o) out="$a" ;; esac
  [ "$a" = "--json" ] && json=1
  prev="$a"
done
if [ "$json" = 1 ]; then
  if [ -n "${STUB_EVENTS_FILE:-}" ]; then cat "$STUB_EVENTS_FILE"; else
    echo '{"type":"thread.started","thread_id":"stub"}'
    echo '{"type":"turn.started"}'
    echo '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}'
    echo '{"type":"turn.completed"}'
  fi
fi
if [ -n "$out" ]; then echo "STUB-REPLY" > "$out"; else echo "STUB-REPLY"; fi
exit 0
'''

ONE_CMD_EVENTS = [
    '{"type":"thread.started","thread_id":"stub"}',
    '{"type":"turn.started"}',
    '{"type":"item.started","item":{"id":"c1","type":"command_execution"}}',
    '{"type":"item.completed","item":{"id":"c1","type":"command_execution","exit_code":0}}',
    '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}',
    '{"type":"turn.completed"}',
]


def _runner_tests(tmp):
    check = T.check
    bin_dir = os.path.join(tmp, "bin")
    os.makedirs(bin_dir)
    for name in ("codex", "claude"):
        p = os.path.join(bin_dir, name)
        with open(p, "w") as f:
            f.write(STUB)
        os.chmod(p, 0o755)
    repo = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(repo, "sub"))
    subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
    top = os.path.realpath(repo)
    nongit = os.path.join(tmp, "nongit")
    os.makedirs(nongit)
    outside = os.path.join(tmp, "outside")
    os.makedirs(outside)
    codex = os.path.join(T.SCRIPTS, "codex_consult.sh")
    claude = os.path.join(T.SCRIPTS, "claude_consult.sh")

    def run(script, label, cwd, extra=None):
        brief = os.path.join(outside, f"{label}.md")
        with open(brief, "w") as f:
            f.write("Test brief body.\n")
        env = dict(os.environ)
        for var in ("CODEX_MODEL", "CODEX_EFFORT", "CODEX_SANDBOX", "CLAUDE_MODEL",
                    "CODEX_TIMEOUT", "CLAUDE_TIMEOUT", "CLAUDE_CODE_SESSION_ID",
                    "HJW_CLAUDE_EFFORT", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            env.pop(var, None)
        data = os.path.join(tempfile.mkdtemp(dir=tmp, prefix="data-"), "haejwo-haejwo")
        env["CLAUDE_PLUGIN_DATA"] = data
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        env.update(extra or {})
        p = subprocess.run(["bash", script, brief], capture_output=True, text=True,
                           timeout=60, cwd=cwd, env=env)
        log_path = os.path.join(outside, f"{label}.reply.log")
        log = open(log_path, encoding="utf-8", errors="replace").read() \
            if os.path.isfile(log_path) else ""
        return p.returncode, p.stdout, p.stderr, log

    def result_line(out):
        return next((l for l in out.splitlines() if l.startswith("=== Codex reply")), "")

    # (1) 0 completed commands: the token is on the result line and the log's shell line.
    rc, out, err, log = run(codex, "zero", repo)
    check("r2c runner: 0 completed commands -> the result line carries the unverified token",
          rc == 0 and TOKEN in result_line(out), f"rc={rc} out={out[:300]} err={err[-300:]}")
    check("r2c runner: the shell count stays on the result line next to the token",
          "| shell: 0 completed commands" in result_line(out), result_line(out))
    check("r2c runner: the log's shell line carries the same token",
          f"# ---- shell: 0 completed commands | {TOKEN} ----" in log, log[-500:])
    head = err.splitlines()[0] if err else ""
    check("r2c runner: codex progress header names scope=<git toplevel>, one line",
          head.startswith("→ Codex (") and f"scope={top})" in head, err[:400])
    check("r2c runner: codex log header (line 1) names scope=<git toplevel>",
          bool(log) and f"scope={top} " in log.splitlines()[0], log[:300])

    # (2) >=1 completed command: no token anywhere.
    ev = os.path.join(outside, "one-cmd.jsonl")
    with open(ev, "w") as f:
        f.write("".join(l + "\n" for l in ONE_CMD_EVENTS))
    rc, out, err, log = run(codex, "one", repo, {"STUB_EVENTS_FILE": ev})
    check("r2c runner: 1 completed command -> no unverified token (result line or log)",
          rc == 0 and "| shell: 1 completed commands" in result_line(out)
          and TOKEN not in out and TOKEN not in log, f"rc={rc} out={out[:300]}")

    # (3) run from a subdirectory: scope is the toplevel change detection covers, not cwd.
    rc, out, err, log = run(codex, "subdir", os.path.join(repo, "sub"))
    check("r2c runner: from a subdirectory, scope= is still the git toplevel",
          rc == 0 and f"scope={top})" in err and f"scope={top}/sub" not in err, err[:400])

    # (4) non-git read-only codex run: scope=none, change detection off.
    rc, out, err, log = run(codex, "nongit", nongit)
    check("r2c runner: non-git read-only codex run -> header says scope=none (change detection off)",
          rc == 0 and f"{SCOPE_NONE})" in err and bool(log)
          and SCOPE_NONE in log.splitlines()[0], f"rc={rc} err={err[:400]} log={log[:300]}")

    # (5) claude runner: scope on both headers in a repo; the refused non-git run still logs scope=none.
    rc, out, err, log = run(claude, "claude", repo)
    check("r2c runner: claude progress and log headers name scope=<git toplevel>",
          rc == 0 and f"scope={top})" in err and bool(log)
          and f"scope={top} " in log.splitlines()[0], f"rc={rc} err={err[:400]} log={log[:300]}")
    check("r2c runner: claude has no shell-count token (codex event stream only)",
          TOKEN not in out, out[:300])
    rc, out, err, log = run(claude, "claude-nongit", nongit)
    check("r2c runner: claude outside git refuses (exit 2) and its log header says scope=none",
          rc == 2 and bool(log) and SCOPE_NONE in log.splitlines()[0], f"rc={rc} log={log[:300]}")


def _retention_tests(tmp):
    check = T.check
    data = os.path.join(tmp, "data")
    sdir = os.path.join(data, "state")
    os.makedirs(sdir)
    cur = os.path.join(sdir, "observations.jsonl")

    def names():
        return sorted(n for n in os.listdir(sdir) if n.startswith("observations.jsonl"))

    def fill(tag):
        # A >200KB current file whose first record names its generation.
        with open(cur, "w") as f:
            f.write(json.dumps({"gen": tag}) + "\n" + ("x" * 210_000) + "\n")

    def first_gen(name):
        with open(os.path.join(sdir, name)) as f:
            return json.loads(f.readline()).get("gen")

    fill("g1")
    T.observe(data, {"hook": "gate", "n": 1})
    check("r2c retention: an oversized current file rotates to .1",
          names() == ["observations.jsonl", "observations.jsonl.1"]
          and first_gen("observations.jsonl.1") == "g1", str(names()))
    fill("g2")
    T.observe(data, {"hook": "gate", "n": 2})
    check("r2c retention: second rotation shifts .1 -> .2",
          names() == ["observations.jsonl", "observations.jsonl.1", "observations.jsonl.2"]
          and first_gen("observations.jsonl.2") == "g1"
          and first_gen("observations.jsonl.1") == "g2", str(names()))
    fill("g3")
    T.observe(data, {"hook": "gate", "n": 3})
    check("r2c retention: third rotation fills .3 (three prior generations)",
          len(names()) == 4 and first_gen("observations.jsonl.3") == "g1"
          and first_gen("observations.jsonl.2") == "g2"
          and first_gen("observations.jsonl.1") == "g3", str(names()))
    fill("g4")
    T.observe(data, {"hook": "gate", "n": 4})
    check("r2c retention: a fourth rotation drops the oldest; still at most 4 files",
          len(names()) == 4 and first_gen("observations.jsonl.3") == "g2"
          and first_gen("observations.jsonl.1") == "g4", str(names()))
    with open(cur) as f:
        last = [json.loads(l) for l in f if l.strip()]
    check("r2c retention: the new record lands in a fresh current file",
          len(last) == 1 and last[0].get("n") == 4, str(last)[:200])

    # prune_state keeps all four despite stale mtimes; other stale files still go.
    stale = time.time() - 30 * 86400
    junk = os.path.join(sdir, "old-session.json")
    open(junk, "w").close()
    for n in names() + ["old-session.json"]:
        os.utime(os.path.join(sdir, n), (stale, stale))
    T.prune_state(data)
    check("r2c retention: prune_state keeps all four observation files (stale mtime)",
          len(names()) == 4 and not os.path.exists(junk), str(os.listdir(sdir)))

    # load_observations reads .3, .2, .1, current in that order.
    import status_collect as sc
    ldata = os.path.join(tmp, "ldata")
    os.makedirs(os.path.join(ldata, "state"))
    for suffix, tag in ((".3", "a"), (".2", "b"), (".1", "c"), ("", "d")):
        with open(os.path.join(ldata, "state", "observations.jsonl" + suffix), "w") as f:
            f.write(json.dumps({"tag": tag}) + "\n")
    recs, bad, notes = sc.load_observations(ldata)
    check("r2c status: load_observations reads .3, .2, .1, current in that order",
          [r.get("tag") for r in recs] == ["a", "b", "c", "d"] and bad == 0 and not notes,
          f"{recs} {bad} {notes}")
    lines, _ = sc.observation_lines(recs, bad, "")
    check("r2c status: the machine-wide line says 4 files",
          lines and lines[0].startswith("observations (machine-wide, 4 files): records=4"),
          str(lines[:1]))
    check("r2c status: KNOWN_KEYS knows report_check{via}; no resolved_from (resolver withdrawn)",
          "via" in sc.KNOWN_KEYS.get("report_check", set())
          and "resolved_from" not in sc.KNOWN_KEYS["bash_guard"], str(sc.KNOWN_KEYS)[:300])


def run_section():
    print("== field round 2 C: runner disclosure tokens, observation retention ==")
    tmp = tempfile.mkdtemp(prefix="hjw-r2c-")
    try:
        _runner_tests(tmp)
        _retention_tests(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
