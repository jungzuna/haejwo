#!/usr/bin/env python3
"""Synthetic hook-contract tests for haejwo gate scripts.

Pipes realistic hook JSON payloads into the actual scripts (subprocess, the
real CLI contract) and asserts allow/deny/reset behavior. No Claude Code
required. Run: python3 tests/test_hooks.py
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "haejwo")
SCRIPTS = os.path.join(PLUGIN, "scripts")
sys.path.insert(0, SCRIPTS)
from hjw_common import DEFAULT_CONFIG, observe, prune_state  # noqa: E402
from session_brief import CORE_BODY, EMERGENCY_CORE, MAX_LEN, UNCONFIGURED_CORE  # noqa: E402
from delegation_gate import CLAUDE_TIER_ONLY, CODEX_TIER_ONLY  # noqa: E402

PASS, FAIL = 0, []


def run(script, payload, data_dir, env_extra=None, root=None):
    env = dict(os.environ)
    env.pop("HAEJWO_GATE", None)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        ["python3", os.path.join(SCRIPTS, script), PLUGIN if root is None else root, data_dir],
        input=json.dumps(payload) if isinstance(payload, dict) else payload,
        capture_output=True, text=True, timeout=15, env=env,
    )
    out = {}
    if p.stdout.strip():
        try:
            out = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            out = {"_raw": p.stdout}
    return p.returncode, out


def decision(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision")


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL {name}  {detail}")


def edit_payload(path, sid="sess-A", pid="p1", agent=None, tool="Edit"):
    d = {
        "session_id": sid, "prompt_id": pid, "hook_event_name": "PreToolUse",
        "tool_name": tool, "cwd": "/repo",
        "tool_input": {"file_path": path},
    }
    if agent:
        d["agent_type"] = agent
        d["agent_id"] = "agent-123"
    return d


def bash_payload(cmd, sid="sess-B", agent=None):
    d = {
        "session_id": sid, "prompt_id": "p1", "hook_event_name": "PreToolUse",
        "tool_name": "Bash", "cwd": "/repo", "tool_input": {"command": cmd},
    }
    if agent:
        d["agent_type"] = agent
    return d


def patch_payload(ops, sid="sess-CX", turn_id="t1"):
    cmd = "*** Begin Patch\n" + "".join(f"*** {op} File: {p}\n+x\n" for op, p in ops) + "*** End Patch"
    return {"session_id": sid, "turn_id": turn_id, "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch", "cwd": "/repo", "tool_input": {"command": cmd}}


def task_payload(subagent_type, model=None, sid="sess-T", agent=None, tool="Task",
                  prompt=None):
    d = {
        "session_id": sid, "prompt_id": "p1", "hook_event_name": "PreToolUse",
        "tool_name": tool, "cwd": "/repo",
        "tool_input": {"subagent_type": subagent_type},
    }
    if model:
        d["tool_input"]["model"] = model
    if prompt is not None:
        d["tool_input"]["prompt"] = prompt
    if agent:
        d["agent_type"] = agent
        d["agent_id"] = "agent-123"
    return d


def main():
    data = tempfile.mkdtemp(prefix="hjw-test-")
    try:
        print("== gate.py ==")
        rc, out = run("gate.py", edit_payload("/repo/src/a.py"), data)
        check("1st distinct code file -> allow", rc == 0 and decision(out) != "deny")

        rc, out = run("gate.py", edit_payload("/repo/src/a.py"), data)
        check("same file again -> allow (free)", rc == 0 and decision(out) != "deny")

        rc, out = run("gate.py", edit_payload("/repo/src/b.py"), data)
        ctx = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("2nd distinct -> allow + budget-full warning",
              rc == 0 and decision(out) == "allow" and "budget" in ctx.lower())

        rc, out = run("gate.py", edit_payload("/repo/src/c.py"), data)
        reason = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
        check("3rd distinct -> DENY", decision(out) == "deny", str(out))
        check("deny reason instructs delegation",
              "default-worker" in reason and "haejwo" in reason)
        check("deny reason nudges plan-first (backup channel)",
              "/haejwo:plan" in reason)

        rc, out = run("gate.py", edit_payload("/repo/notes.md"), data)
        check("non-code (.md) -> allow, uncounted", decision(out) != "deny")

        rc, out = run("gate.py", edit_payload("/tmp/x/scratch.py"), data)
        check("exempt path (/tmp) -> allow", decision(out) != "deny")

        rc, out = run("gate.py", edit_payload("/repo/src/d.py", agent="default-worker"), data)
        check("SUBAGENT 3rd+ file -> allow (exempt)", decision(out) != "deny")

        rc, out = run("gate.py", edit_payload("/repo/src/e.py", pid="p2"), data)
        check("new prompt_id -> lazy reset -> allow", decision(out) != "deny")

        rc, out = run("gate.py", edit_payload("/repo/src/f.py", pid="p2"), data)
        rc, out = run("gate.py", edit_payload("/repo/src/g.py", pid="p2"), data)
        check("3rd in new turn -> deny again", decision(out) == "deny")

        rc, out = run("gate.py", edit_payload("/repo/src/h.py", pid="p2"), data,
                      env_extra={"HAEJWO_GATE": "off"})
        check("env HAEJWO_GATE=off -> allow", decision(out) != "deny")

        rc, out = run("gate.py", "not-json{{{", data)
        check("malformed stdin -> fail open (rc0)", rc == 0)

        nb = edit_payload("/repo/nb/train.ipynb", pid="p3", tool="NotebookEdit")
        nb["tool_input"] = {"notebook_path": "/repo/nb/train.ipynb"}
        rc, out = run("gate.py", nb, data)
        check("NotebookEdit notebook_path counted", decision(out) != "deny")

        # observation audit records the touched file path (audit trail of hook activity)
        obs_file = os.path.join(data, "state", "observations.jsonl")
        obs_recs = [json.loads(l) for l in open(obs_file)]
        check("observations record file path (audit)",
              any(str(r.get("path", "")).endswith("/repo/src/a.py") for r in obs_recs))

        print("== rule canary (load-bearing phrases) ==")
        # Whitespace-normalized so a line wrap inside a phrase can't false-negative.
        rules_path = os.path.join(PLUGIN, "rules", "orchestration.md")
        rules_text = " ".join(open(rules_path, encoding="utf-8-sig").read().split())

        # Selection rule (2.9.0 rules diet): canaries pin only enforced
        # contracts and acceptance invariants; style/ceremony phrases are NOT
        # pinned (they may evolve with model generations). A tuple entry is
        # two fragments that must BOTH appear (used when the source phrase
        # crosses a line wrap in the diet file).
        CANARIES = [
            "does JUDGMENT",                        # 1. judgment/execution split
            "NEVER require plugin commands",        # 2. zero-command concept
            "no behavior/risk/API/data-shape judgment",  # 3. task-worker litmus
            "NOT independent authority",            # 4. deep-reasoner is not independent authority
            "independent review BEFORE",            # 5. review-timing invariant
            "xhigh ONLY",                           # 6. stakes-scaled effort ceiling
            "never --resume",                       # 7. escalation = new session
            "No plan because",                      # 8. plan linkage escape
            "no acceptance",                        # 9. acceptance evidence split
            "Judgment calls:",                      # 10. discretion disclosure
            ("workers NEVER", "push or deploy"),    # 11. outward-action ban
            "delegation signal",                    # 12. bash-write rule
            "never grind",                          # 13. retry stop-condition
        ]
        for phrase in CANARIES:
            if isinstance(phrase, tuple):
                name = " AND ".join(repr(p) for p in phrase)
                cond = all(p in rules_text for p in phrase)
            else:
                name = repr(phrase)
                cond = phrase in rules_text
            check(f"canary: {name}", cond)

        # diet budget — target 3000, actual 3190 at 2.10.0 (task-worker litmus
        # relaxation); consensus-kept host contracts cost the overage;
        # re-audit at next rules change
        rules_size = os.path.getsize(rules_path)
        check("rules file size within diet budget (<=3300 bytes)",
              rules_size <= 3300, f"size={rules_size}")

        print("== turn_reset.py ==")
        rc, _ = run("turn_reset.py",
                    {"session_id": "sess-A", "prompt_id": "p9",
                     "hook_event_name": "UserPromptSubmit"}, data)
        rc, out = run("gate.py", edit_payload("/repo/src/z1.py", pid="p9"), data)
        check("after reset -> counting starts fresh", decision(out) != "deny")

        print("== codex-finding regressions ==")
        # tmpdir prefix exemption is path-anchored: a repo's own tmp/ subdir is still real code
        for i, p in enumerate(["/repo/tmp/a.py", "/repo/tmp/b.py"]):
            run("gate.py", edit_payload(p, sid="sess-C"), data)
        rc, out = run("gate.py", edit_payload("/repo/tmp/c.py", sid="sess-C"), data)
        check("repo-local tmp/ dir IS counted (deny at 3rd)", decision(out) == "deny")

        # Canonical path dedup: a "./"-spelled path resolves to the same physical file
        run("gate.py", edit_payload("/repo/src/./a.py", sid="sess-D"), data)
        rc, out = run("gate.py", edit_payload("/repo/src/a.py", sid="sess-D"), data)
        check("canonical dedup: ./a.py == a.py (free)", decision(out) != "deny")
        rc, out = run("gate.py", edit_payload("/repo/src/b.py", sid="sess-D"), data)
        check("dedup didn't inflate count (2nd allowed)", decision(out) != "deny")
        rc, out = run("gate.py", edit_payload("/repo/src/c.py", sid="sess-D"), data)
        check("deny lands at true 3rd file", decision(out) == "deny")

        # Stale state (both turn signals lost) auto-resets after 2h
        run("gate.py", edit_payload("/repo/s/x1.py", sid="sess-E"), data)
        run("gate.py", edit_payload("/repo/s/x2.py", sid="sess-E"), data)
        sf = os.path.join(data, "state", "sess-E.json")
        st = json.load(open(sf))
        st["updated_at"] = st["updated_at"] - 8000
        json.dump(st, open(sf, "w"))
        rc, out = run("gate.py", edit_payload("/repo/s/x3.py", sid="sess-E"), data)
        check("stale (>2h) state auto-resets -> allow", decision(out) != "deny")

        # Concurrent edits can't slip under the budget (flock serializes).
        # REAL concurrency: the four children are spawned first and their
        # stdins are released together by a Barrier, so they actually race the
        # read-check-write. (The previous version wrote+communicated one child
        # at a time, which serialized the race away and proved nothing.)
        procs = []
        for i in range(4):
            p = subprocess.Popen(
                ["python3", os.path.join(SCRIPTS, "gate.py"), PLUGIN, data],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            procs.append((p, json.dumps(edit_payload(f"/repo/r/r{i}.py", sid="sess-R"))))

        barrier = threading.Barrier(len(procs))

        def feed(proc, payload_s):
            try:
                barrier.wait(timeout=10)
            except Exception:
                pass
            try:
                proc.stdin.write(payload_s)
                proc.stdin.flush()
            except Exception:
                pass
            finally:
                try:
                    proc.stdin.close()  # promptly: the hook reads to EOF
                    # so the later communicate() doesn't flush a closed pipe
                    proc.stdin = None
                except Exception:
                    pass

        feeders = [threading.Thread(target=feed, args=(p, s_), daemon=True)
                   for p, s_ in procs]
        for t in feeders:
            t.start()
        for t in feeders:
            t.join(timeout=20)
        outs, rcs = [], []
        for proc, _ in procs:
            try:
                stdout, _unused = proc.communicate(timeout=20)
            except Exception:
                stdout = ""
                proc.kill()  # stdin is already closed: kill+wait, never communicate
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
            rcs.append(proc.returncode)
            try:
                outs.append(json.loads(stdout.strip().splitlines()[-1]))
            except Exception:
                outs.append({})
        denies = sum(1 for o in outs if decision(o) == "deny")
        check("4 CONCURRENT edits -> exactly 2 denied (no race undercount)",
              denies == 2, f"denies={denies}")
        check("4 concurrent edits -> every hook exits 0 (never bricks)",
              rcs == [0, 0, 0, 0], f"rcs={rcs}")
        # The invariant that actually matters: what the hooks TOLD the host it
        # could edit is exactly what the counter recorded — no allowed path
        # goes uncounted, no denied path gets counted.
        allowed_paths = {os.path.realpath(f"/repo/r/r{i}.py")
                         for i, o in enumerate(outs) if decision(o) != "deny"}
        race_state = json.load(open(os.path.join(data, "state", "sess-R.json")))
        check("4 concurrent edits -> allowed responses == paths in state",
              allowed_paths == set(race_state.get("files", []))
              and len(allowed_paths) == 2,
              f"allowed={sorted(allowed_paths)} state={race_state}")
        print("  note: the barrier makes overlap LIKELY, not certain — "
              "critical-section overlap here is inferred, not proven "
              "(the held-lock fixture below proves the lock itself binds)")

        # The lock is load-bearing, not decorative: hold it from the TEST and
        # prove the child blocks (no state written) until it is released.
        hl_sid = "sess-HL"
        hl_lock = os.path.join(data, "state", hl_sid + ".json.lock")
        hl_state = os.path.join(data, "state", hl_sid + ".json")
        os.makedirs(os.path.dirname(hl_lock), exist_ok=True)
        holder = open(hl_lock, "w")
        child = None
        try:
            fcntl.flock(holder, fcntl.LOCK_EX)
            child = subprocess.Popen(
                ["python3", os.path.join(SCRIPTS, "gate.py"), PLUGIN, data],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            child.stdin.write(json.dumps(edit_payload("/repo/hl/a.py", sid=hl_sid)))
            child.stdin.flush()
            child.stdin.close()
            fd_dir = "/proc/%d/fd" % child.pid
            if not os.path.isdir(fd_dir):
                print("  skipped (no /proc) held-lock fixture")
            else:
                real_lock = os.path.realpath(hl_lock)
                deadline = time.time() + 10  # slow/loaded CI: readiness, not timing
                opened = False
                while time.time() < deadline and not opened:
                    try:
                        for fd in os.listdir(fd_dir):
                            if os.path.realpath(os.path.join(fd_dir, fd)) == real_lock:
                                opened = True
                                break
                    except Exception:
                        pass
                    if not opened:
                        time.sleep(0.05)
                check("held lock: child opened the session lock file (waiting on it)",
                      opened, fd_dir)
                blocked_until = time.time() + 0.5
                blocked = True
                while time.time() < blocked_until:
                    if os.path.exists(hl_state):
                        blocked = False
                        break
                    time.sleep(0.05)
                check("held lock: no state written while another holder has the lock",
                      blocked)
                fcntl.flock(holder, fcntl.LOCK_UN)
                try:
                    hl_rc = child.wait(timeout=5)
                except Exception:
                    hl_rc = None
                check("held lock: child completes once released (exit 0)", hl_rc == 0,
                      f"rc={hl_rc}")
                check("held lock: state written after release", os.path.exists(hl_state))
        finally:
            try:
                fcntl.flock(holder, fcntl.LOCK_UN)
            except Exception:
                pass
            holder.close()
            if child is not None:
                if child.poll() is None:
                    child.kill()
                try:
                    child.wait(timeout=5)  # stdin closed: never communicate()
                except Exception:
                    pass

        print("== bash_guard.py ==")
        cases_deny = [
            ("sed -i 's/a/b/' src/app.py", "sed -i on code"),
            ("echo 'x' >> src/app.py", "append redirect to code"),
            ("cat <<EOF > src/new.py\nhi\nEOF", "heredoc redirect to code"),
            ("something | tee src/app.py", "tee to code"),
            ("perl -pi -e 's/a/b/' lib/x.rb", "perl -i on code"),
            ("ls; echo hack > b.py", "relative code path redirect"),
            ("find src -name '*.py' -exec sed -i 's/a/b/' {} +", "find -exec sed -i"),
            ("git ls-files | xargs sed -i 's/old/new/'", "xargs sed -i"),
        ]
        for cmd, name in cases_deny:
            rc, out = run("bash_guard.py", bash_payload(cmd), data)
            check(f"deny: {name}", decision(out) == "deny", str(out))

        cases_allow = [
            ("cat src/app.py", "plain read"),
            ("grep -rn foo src/ > /tmp/out.txt", "redirect to /tmp txt"),
            ("pytest -x > /tmp/log.txt 2>&1", "test output redirect"),
            ("sed -i 's/a/b/' notes.md", "sed on non-code"),
            ("echo done", "no file at all"),
            ("git diff > /dev/null", "dev null"),
        ]
        for cmd, name in cases_allow:
            rc, out = run("bash_guard.py", bash_payload(cmd), data)
            check(f"allow: {name}", decision(out) != "deny", str(out))

        rc, out = run("bash_guard.py",
                      bash_payload("sed -i 's/a/b/' src/app.py", agent="task-worker"), data)
        check("SUBAGENT bash write -> allow (exempt)", decision(out) != "deny")

        print("== bash_guard.py unresolved targets (A9) ==")
        # A write target the hook cannot resolve (shell expansion / backtick)
        # is exempted PER TOKEN — the hook has no shell env to expand it, and
        # field data showed those denies were scratch writes the Write tool
        # then performed freely anyway.
        a9_data = tempfile.mkdtemp(prefix="hjw-test-a9-")
        try:
            def a9_recs():
                return [json.loads(l) for l in
                        open(os.path.join(a9_data, "state", "observations.jsonl"))]

            def a9_last(sid):
                hits = [r for r in a9_recs() if r.get("sid") == sid]
                return hits[-1] if hits else None

            a9_cases = [
                ("cat > $SP/probe.py", "sess-A9a", "allow", "unresolved-target",
                 "$SP/probe.py", "redirect target with $VAR"),
                ("echo x > '$SP/a.py'", "sess-A9b", "allow", "unresolved-target",
                 "'$SP/a.py'", "single-quoted $VAR (documented broader exemption)"),
                ("cat > `mktemp`.py", "sess-A9c", "allow", "unresolved-target",
                 "`mktemp`.py", "backtick target"),
                ("sed -i s/a/b/ $SP/x.py", "sess-A9d", "allow", "unresolved-target",
                 "$SP/x.py", "in-place editor on an unresolved token"),
                ("printf x > $SP/a.py; echo y > src/real.py", "sess-A9e", "deny",
                 "redirect", "src/real.py", "literal target wins over unresolved"),
                # the only code-ish word here is the QUOTED glob, which is
                # classified raw (quoted literals are a kept gap), so the deny
                # comes from the fan-out branch
                ("find . -name '*.py' -exec sed -i s/a/b/ {} +", "sess-A9f", "deny",
                 "fanout", None, "fan-out in-place edit still denies"),
                ("git ls-files | xargs sed -i 's/old/new/'", "sess-A9g", "deny",
                 "fanout", None, "xargs fan-out with no visible token"),
                # provenance: classify the shell WORD, not a stripped token
                # whitespace INSIDE $( ) / backticks does not split the word,
                # so the whole target is recorded verbatim
                ('sed -i s/a/b/ "$(printf /repo)/x.py"', "sess-A9h", "allow",
                 "unresolved-target", '"$(printf /repo)/x.py"',
                 "command substitution inside quotes -> whole word recorded"),
                ("sed -i s/a/b/ $(printf /repo)/x.py", "sess-A9p", "allow",
                 "unresolved-target", "$(printf /repo)/x.py",
                 "bare command substitution -> whole word recorded"),
                ("echo x > `pwd`/a.py", "sess-A9q", "allow", "unresolved-target",
                 "`pwd`/a.py", "backtick redirect target"),
                ("echo x > `printf /repo`/a.py", "sess-A9r", "allow",
                 "unresolved-target", "`printf /repo`/a.py",
                 "backtick span with whitespace stays one word"),
                # L3: quoted literal targets keep their 2.10 behavior (allow) —
                # a known gap kept deliberately, no field evidence to tighten
                ("echo x > 'src/app.py'", "sess-A9s", "allow", "ok", None,
                 "quoted literal redirect target (kept gap)"),
                ("sed -i s/a/b/ 'src/app.py'", "sess-A9t", "allow", "ok", None,
                 "quoted literal in-place target (kept gap)"),
                ('echo x > "a b.py"', "sess-A9i", "allow", "unresolved-target",
                 '"a', "quote opened but not closed in this word"),
                ("echo x > src/real.py", "sess-A9j", "deny", "redirect",
                 "src/real.py", "literal redirect still denies"),
                ('sed -i s/a/b/ $SP/x.py src/real.py', "sess-A9k", "deny",
                 "inplace", "src/real.py",
                 "mixed unresolved + literal in one command -> deny"),
            ]
            for cmd, sid, want, via, target, name in a9_cases:
                rc, out = run("bash_guard.py", bash_payload(cmd, sid=sid), a9_data)
                got = "deny" if decision(out) == "deny" else "allow"
                check(f"A9 {want}: {name}", rc == 0 and got == want, str(out))
                rec = a9_last(sid)
                check(f"A9 record: {name} -> via {via}",
                      bool(rec) and rec.get("v") == 2 and rec.get("decision") == want
                      and rec.get("via") == via, str(rec))
                if target is not None:
                    check(f"A9 record: {name} -> raw target preserved",
                          bool(rec) and rec.get("target") == target, str(rec))
        finally:
            shutil.rmtree(a9_data, ignore_errors=True)

        print("== gate.py / bash_guard.py decision telemetry (A10) ==")
        a10_data = tempfile.mkdtemp(prefix="hjw-test-a10-")
        try:
            def a10_last(sid, hook):
                recs = [json.loads(l) for l in
                        open(os.path.join(a10_data, "state", "observations.jsonl"))]
                hits = [r for r in recs
                        if r.get("sid") == sid and r.get("hook") == hook]
                return hits[-1] if hits else None

            run("gate.py", edit_payload("/repo/t/a.py", sid="sess-TEL"), a10_data)
            r_ok = a10_last("sess-TEL", "gate")
            check("A10 gate: 1st file -> v2 allow via 'ok'",
                  bool(r_ok) and r_ok.get("v") == 2 and r_ok.get("decision") == "allow"
                  and r_ok.get("via") == "ok", str(r_ok))

            run("gate.py", edit_payload("/repo/t/b.py", sid="sess-TEL"), a10_data)
            r_full = a10_last("sess-TEL", "gate")
            check("A10 gate: budget-filling file -> via 'budget-full'",
                  bool(r_full) and r_full.get("via") == "budget-full", str(r_full))

            run("gate.py", edit_payload("/repo/t/a.py", sid="sess-TEL"), a10_data)
            r_free = a10_last("sess-TEL", "gate")
            check("A10 gate: re-edit -> via 'free-reedit'",
                  bool(r_free) and r_free.get("via") == "free-reedit", str(r_free))

            rc, out = run("gate.py", edit_payload("/repo/t/c.py", sid="sess-TEL"), a10_data)
            r_deny = a10_last("sess-TEL", "gate")
            check("A10 gate: over-budget file -> deny via 'budget' + offending list",
                  decision(out) == "deny" and bool(r_deny)
                  and r_deny.get("decision") == "deny" and r_deny.get("via") == "budget"
                  and r_deny.get("offending") == ["/repo/t/c.py"], str(r_deny))

            run("gate.py", edit_payload("/repo/t/notes.md", sid="sess-TEL"), a10_data)
            check("A10 gate: non-code file -> via 'non-code'",
                  (a10_last("sess-TEL", "gate") or {}).get("via") == "non-code")

            run("gate.py", edit_payload("/repo/t/d.py", sid="sess-TEX", agent="default-worker"),
                a10_data)
            check("A10 gate: subagent -> via 'subagent-exempt'",
                  (a10_last("sess-TEX", "gate") or {}).get("via") == "subagent-exempt")

            run("gate.py", edit_payload("/repo/t/e.py", sid="sess-TEV"), a10_data,
                env_extra={"HAEJWO_GATE": "off"})
            check("A10 gate: HAEJWO_GATE=off -> via 'env-off'",
                  (a10_last("sess-TEV", "gate") or {}).get("via") == "env-off")

            with open(os.path.join(a10_data, "config.json"), "w") as f:
                json.dump({"gate": {"enabled": False}}, f)
            run("gate.py", edit_payload("/repo/t/f.py", sid="sess-TEG"), a10_data)
            check("A10 gate: gate disabled in config -> via 'gate-off'",
                  (a10_last("sess-TEG", "gate") or {}).get("via") == "gate-off")
            run("bash_guard.py", bash_payload("sed -i 's/a/b/' src/app.py", sid="sess-TBG"),
                a10_data)
            check("A10 bash_guard: gate disabled in config -> via 'gate-off'",
                  (a10_last("sess-TBG", "bash_guard") or {}).get("via") == "gate-off")
            os.remove(os.path.join(a10_data, "config.json"))

            rc, out = run("bash_guard.py",
                          bash_payload("sed -i 's/a/b/' src/app.py", sid="sess-TB1"), a10_data)
            r_bd = a10_last("sess-TB1", "bash_guard")
            check("A10 bash_guard: in-place deny -> v2 + via 'inplace' + target",
                  decision(out) == "deny" and bool(r_bd) and r_bd.get("v") == 2
                  and r_bd.get("decision") == "deny" and r_bd.get("via") == "inplace"
                  and r_bd.get("target") == "src/app.py", str(r_bd))

            rc, out = run("bash_guard.py",
                          bash_payload("echo x >> src/app.py", sid="sess-TB2"), a10_data)
            r_br = a10_last("sess-TB2", "bash_guard")
            check("A10 bash_guard: redirect deny -> via 'redirect' + target",
                  decision(out) == "deny" and bool(r_br)
                  and r_br.get("via") == "redirect"
                  and r_br.get("target") == "src/app.py", str(r_br))

            rc, out = run("bash_guard.py", bash_payload("cat src/app.py", sid="sess-TB3"),
                          a10_data)
            check("A10 bash_guard: plain read -> allow via 'ok'",
                  decision(out) != "deny"
                  and (a10_last("sess-TB3", "bash_guard") or {}).get("via") == "ok")

            def a10_count(sid, hook):
                recs = [json.loads(l) for l in
                        open(os.path.join(a10_data, "state", "observations.jsonl"))]
                return sum(1 for r in recs
                           if r.get("sid") == sid and r.get("hook") == hook)

            run("gate.py", edit_payload("/repo/t/once.py", sid="sess-ONE1"), a10_data)
            check("A10 gate: exactly ONE observation record per invocation",
                  a10_count("sess-ONE1", "gate") == 1, a10_count("sess-ONE1", "gate"))
            run("bash_guard.py", bash_payload("echo hi", sid="sess-ONE2"), a10_data)
            check("A10 bash_guard: exactly ONE observation record per invocation",
                  a10_count("sess-ONE2", "bash_guard") == 1,
                  a10_count("sess-ONE2", "bash_guard"))
            rc, out = run("delegation_gate.py",
                          task_payload("haejwo:default-worker", sid="sess-ONE3"), a10_data)
            check("A10 delegation: exactly ONE observation record per invocation",
                  a10_count("sess-ONE3", "delegation") == 1,
                  a10_count("sess-ONE3", "delegation"))
        finally:
            shutil.rmtree(a10_data, ignore_errors=True)

        # K5: telemetry is never load-bearing — a HELD observations lock must
        # not make the host wait for a gate decision.
        obs_lock_data = tempfile.mkdtemp(prefix="hjw-test-obslock-")
        try:
            obs_sdir = os.path.join(obs_lock_data, "state")
            os.makedirs(obs_sdir, exist_ok=True)
            obs_lock_path = os.path.join(obs_sdir, "__observations__.json.lock")
            obs_holder = open(obs_lock_path, "w")
            try:
                fcntl.flock(obs_holder, fcntl.LOCK_EX)
                t0 = time.time()
                rc, out = run("gate.py", edit_payload("/repo/ol/a.py", sid="sess-OL"),
                              obs_lock_data)
                elapsed = time.time() - t0
                check("A10 held observations lock: decision still emitted, fast",
                      rc == 0 and decision(out) != "deny" and elapsed < 1.5,
                      f"rc={rc} elapsed={elapsed:.2f}s")
                obs_path = os.path.join(obs_sdir, "observations.jsonl")
                wrote = False
                if os.path.isfile(obs_path):
                    wrote = any(json.loads(l).get("sid") == "sess-OL"
                                for l in open(obs_path) if l.strip())
                check("A10 held observations lock: record still appended (unlocked "
                      "best-effort)", wrote, obs_path)
            finally:
                try:
                    fcntl.flock(obs_holder, fcntl.LOCK_UN)
                except Exception:
                    pass
                obs_holder.close()
        finally:
            shutil.rmtree(obs_lock_data, ignore_errors=True)

        # Observation failure must never change the decision: make
        # observations.jsonl a DIRECTORY so every observe() write raises.
        obs_fail_data = tempfile.mkdtemp(prefix="hjw-test-obsfail-")
        try:
            os.makedirs(os.path.join(obs_fail_data, "state", "observations.jsonl"),
                        exist_ok=True)
            for i, path_ in enumerate(["/repo/of/a.py", "/repo/of/b.py"]):
                rc, out = run("gate.py", edit_payload(path_, sid="sess-OF"), obs_fail_data)
                check(f"A10 obs-failure: gate file {i + 1} still allowed", rc == 0
                      and decision(out) != "deny", str(out))
            rc, out = run("gate.py", edit_payload("/repo/of/c.py", sid="sess-OF"),
                          obs_fail_data)
            check("A10 obs-failure: gate still DENIES over budget (decision unaffected)",
                  rc == 0 and decision(out) == "deny", str(out))
            rc, out = run("bash_guard.py",
                          bash_payload("sed -i 's/a/b/' src/app.py", sid="sess-OF"),
                          obs_fail_data)
            check("A10 obs-failure: bash_guard still DENIES (decision unaffected)",
                  rc == 0 and decision(out) == "deny", str(out))

            mal = edit_payload("/repo/of/d.py", sid="sess-MAL")
            mal["tool_input"] = "not-a-dict"
            rc, out = run("gate.py", mal, obs_fail_data)
            check("A10 malformed tool_input -> allow rc0 (fail open)",
                  rc == 0 and decision(out) != "deny", str(out))
        finally:
            shutil.rmtree(obs_fail_data, ignore_errors=True)

        print("== malformed config.json fails OPEN (A2, P4) ==")
        # A config.json that exists but cannot be parsed is not a rule set:
        # enforcing DEFAULTS against it would apply limits the user never
        # chose, on a file they believe is live. Every gate allows, the
        # telemetry says why, and gate.py says it ONCE per session.
        cm_data = tempfile.mkdtemp(prefix="hjw-test-cfgmal-")
        try:
            def cm_last(sid, hook):
                recs = [json.loads(l) for l in
                        open(os.path.join(cm_data, "state", "observations.jsonl"))]
                hits = [r for r in recs
                        if r.get("sid") == sid and r.get("hook") == hook]
                return hits[-1] if hits else None

            def cm_ctx(out_):
                return (out_.get("hookSpecificOutput") or {}).get("additionalContext", "")

            NOTE = ("[haejwo] config.json is unreadable (malformed JSON) — "
                    "enforcement is disabled (fail-open) until it is repaired; "
                    "run /haejwo:setup or fix the file")

            with open(os.path.join(cm_data, "config.json"), "w") as f:
                f.write("{ \"gate\": { \"max_files_per_turn\": 2,,, ")

            ctxs = []
            for i, name in enumerate(("a", "b", "c")):
                rc, out = run("gate.py", edit_payload(f"/repo/cm/{name}.py", sid="sess-CM1"),
                              cm_data)
                ctxs.append(cm_ctx(out))
                check(f"A2 gate: malformed config, file {i + 1} allowed",
                      rc == 0 and decision(out) != "deny", str(out))
            check("A2 gate: 3rd distinct file allowed via 'config-malformed'",
                  (cm_last("sess-CM1", "gate") or {}).get("via") == "config-malformed",
                  str(cm_last("sess-CM1", "gate")))
            check("A2 gate: the fail-open note is emitted exactly once per session",
                  ctxs[0] == NOTE and ctxs[1:] == ["", ""], str(ctxs))

            rc, _ = run("turn_reset.py",
                        {"session_id": "sess-CM1", "prompt_id": "p2",
                         "hook_event_name": "UserPromptSubmit"}, cm_data)
            rc, out = run("gate.py", edit_payload("/repo/cm/d.py", sid="sess-CM1", pid="p2"),
                          cm_data)
            check("A2 gate: the note does not repeat after a turn reset",
                  rc == 0 and cm_ctx(out) == "", cm_ctx(out))

            rc, out = run("bash_guard.py",
                          bash_payload("echo x >> src/app.py", sid="sess-CM2"), cm_data)
            check("A2 bash_guard: redirect into code allowed via 'config-malformed'",
                  rc == 0 and decision(out) != "deny"
                  and (cm_last("sess-CM2", "bash_guard") or {}).get("via")
                  == "config-malformed", str(cm_last("sess-CM2", "bash_guard")))

            rc, out = run("delegation_gate.py",
                          task_payload("general-purpose", sid="sess-CM3"), cm_data)
            rec = cm_last("sess-CM3", "delegation") or {}
            check("A2 delegation: generic agent without model allowed on a malformed "
                  "config (the deny would steer with pins we could not read)",
                  rc == 0 and decision(out) != "deny"
                  and rec.get("decision") == "allow"
                  and rec.get("tier_pin_check") == "skip:config-malformed", str(rec))

            # F2 (2026-09-21 review): the note is SHARED across the three
            # enforcement hooks, not gate.py's alone. A session whose only
            # tool call is a Bash write or a delegation must still learn
            # enforcement is off — and once told, the other two stay silent.
            rc, out = run("bash_guard.py",
                          bash_payload("echo x >> src/app.py", sid="sess-CM4"), cm_data)
            first = cm_ctx(out)
            rc, out = run("delegation_gate.py",
                          task_payload("general-purpose", sid="sess-CM4"), cm_data)
            second = cm_ctx(out)
            rc, out = run("gate.py", edit_payload("/repo/cm/e.py", sid="sess-CM4"),
                          cm_data)
            third = cm_ctx(out)
            check("F2 shared note: bash_guard fires first -> it emits, gate and "
                  "delegation stay silent that session",
                  first == NOTE and second == "" and third == "",
                  str([first, second, third]))

            rc, out = run("delegation_gate.py",
                          task_payload("general-purpose", sid="sess-CM5"), cm_data)
            first = cm_ctx(out)
            rc, out = run("gate.py", edit_payload("/repo/cm/f.py", sid="sess-CM5"),
                          cm_data)
            second = cm_ctx(out)
            rc, out = run("bash_guard.py",
                          bash_payload("echo x >> src/app.py", sid="sess-CM5"), cm_data)
            third = cm_ctx(out)
            check("F2 shared note: delegation fires first -> it emits, the other "
                  "two stay silent",
                  first == NOTE and second == "" and third == "",
                  str([first, second, third]))

            # turn_reset preserves the SESSION-scoped flag for the other
            # hooks too, not just for gate.py (checked above for sess-CM1).
            rc, _ = run("turn_reset.py",
                        {"session_id": "sess-CM4", "prompt_id": "p2",
                         "hook_event_name": "UserPromptSubmit"}, cm_data)
            rc, out = run("bash_guard.py",
                          bash_payload("echo x >> src/app.py", sid="sess-CM4"), cm_data)
            check("F2 shared note: a turn reset preserves the flag for bash_guard",
                  rc == 0 and cm_ctx(out) == "", cm_ctx(out))
        finally:
            shutil.rmtree(cm_data, ignore_errors=True)

        # F2: an UNWRITABLE session state means the "already told them" flag
        # never sticks — the note then REPEATS. Fail loud, never silent: a
        # repeated line costs context; suppressing it would hide that every
        # gate is failing open. (State file made a DIRECTORY so save_state's
        # os.replace always raises, exactly as the obs-failure fixture does.)
        cm_unw = tempfile.mkdtemp(prefix="hjw-test-cfgmal-unw-")
        try:
            NOTE_U = ("[haejwo] config.json is unreadable (malformed JSON) — "
                      "enforcement is disabled (fail-open) until it is repaired; "
                      "run /haejwo:setup or fix the file")
            with open(os.path.join(cm_unw, "config.json"), "w") as f:
                f.write("{ \"gate\": { ,,,")
            os.makedirs(os.path.join(cm_unw, "state", "sess-UNW.json"), exist_ok=True)
            ctxs_u = []
            for tag in ("a", "b"):
                rc, out = run("gate.py", edit_payload(f"/repo/unw/{tag}.py",
                                                      sid="sess-UNW"), cm_unw)
                ctxs_u.append((out.get("hookSpecificOutput") or {})
                              .get("additionalContext", ""))
            rc, out = run("bash_guard.py",
                          bash_payload("echo x >> src/app.py", sid="sess-UNW"), cm_unw)
            ctxs_u.append((out.get("hookSpecificOutput") or {})
                          .get("additionalContext", ""))
            check("F2 unwritable state -> the fail-open note REPEATS (never silent)",
                  ctxs_u == [NOTE_U, NOTE_U, NOTE_U], str(ctxs_u))
        finally:
            shutil.rmtree(cm_unw, ignore_errors=True)

        # An ABSENT config is not a broken one: the shipped defaults still
        # bind, exactly as before.
        cm_absent = tempfile.mkdtemp(prefix="hjw-test-cfgabs-")
        try:
            for name in ("a", "b"):
                run("gate.py", edit_payload(f"/repo/ca/{name}.py", sid="sess-CA1"), cm_absent)
            rc, out = run("gate.py", edit_payload("/repo/ca/c.py", sid="sess-CA1"), cm_absent)
            check("A2 absent config: 3rd distinct file still DENIES",
                  decision(out) == "deny", str(out))
            rc, out = run("bash_guard.py",
                          bash_payload("echo x >> src/app.py", sid="sess-CA2"), cm_absent)
            check("A2 absent config: bash redirect into code still DENIES",
                  decision(out) == "deny", str(out))
            rc, out = run("delegation_gate.py",
                          task_payload("general-purpose", sid="sess-CA3"), cm_absent)
            check("A2 absent config: generic agent without model still DENIES",
                  decision(out) == "deny", str(out))
        finally:
            shutil.rmtree(cm_absent, ignore_errors=True)

        print("== codex host adapter (apply_patch) ==")
        # a. single Add counts like Claude's file_path; budget applies the same way
        rc, out = run("gate.py", patch_payload([("Add", "/repo/cx/a.py")], sid="CX1"), data)
        check("apply_patch single Add -> allow", rc == 0 and decision(out) != "deny")

        rc, out = run("gate.py", patch_payload([("Add", "/repo/cx/b.py")], sid="CX1"), data)
        ctx = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("apply_patch 2nd distinct -> allow + budget warning",
              rc == 0 and decision(out) == "allow" and "budget" in ctx.lower())

        rc, out = run("gate.py", patch_payload([("Add", "/repo/cx/c.py")], sid="CX1"), data)
        check("apply_patch 3rd distinct -> DENY", decision(out) == "deny", str(out))

        # b. whole-patch is atomic: an over-budget multi-file patch mutates NO state
        rc, out = run("gate.py", patch_payload(
            [("Add", "/repo/cx/p1.py"), ("Add", "/repo/cx/p2.py"), ("Add", "/repo/cx/p3.py")],
            sid="CX2"), data)
        reason = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
        check("apply_patch whole-patch over budget -> DENY", decision(out) == "deny", str(out))
        check("deny reason names budget + offending file",
              "budget exceeded" in reason and "p1.py" in reason)

        rc, out = run("gate.py", patch_payload([("Add", "/repo/cx/p1.py")], sid="CX2"), data)
        check("denied patch consumed no budget (retry allowed)", decision(out) != "deny", str(out))

        # c. Delete counts as a touched file too (destructive)
        rc, out = run("gate.py", patch_payload([("Delete", "/repo/cx/z.py")], sid="CX3"), data)
        check("apply_patch Delete counts (1st) -> allow", decision(out) != "deny")

        rc, out = run("gate.py", patch_payload([("Add", "/repo/cx/y.py")], sid="CX3"), data)
        check("apply_patch 2nd distinct after Delete -> allow", decision(out) != "deny")

        rc, out = run("gate.py", patch_payload([("Add", "/repo/cx/x.py")], sid="CX3"), data)
        check("apply_patch 3rd distinct after Delete -> deny", decision(out) == "deny")

        # d. re-editing a file already counted this turn (scenario a's CX1) stays free
        rc, out = run("gate.py", patch_payload([("Update", "/repo/cx/a.py")], sid="CX1"), data)
        check("apply_patch re-edit already-touched file -> allow (free)", decision(out) != "deny")

        # e. turn_id (Codex's turn boundary) lazily resets the counter
        rc, out = run("gate.py",
                      patch_payload([("Add", "/repo/cx/d1.py")], sid="CX4", turn_id="t1"), data)
        check("apply_patch turn t1 1st add -> allow", decision(out) != "deny")

        rc, out = run("gate.py",
                      patch_payload([("Add", "/repo/cx/d2.py")], sid="CX4", turn_id="t1"), data)
        check("apply_patch turn t1 2nd add -> allow (full)", decision(out) != "deny")

        rc, out = run("gate.py",
                      patch_payload([("Add", "/repo/cx/d3.py")], sid="CX4", turn_id="t2"), data)
        check("apply_patch new turn_id -> lazy reset -> allow", decision(out) != "deny")

        # f. non-code files inside a patch don't occupy a budget slot
        rc, out = run("gate.py", patch_payload(
            [("Add", "/repo/notes.md"), ("Add", "/repo/cx/real.py")], sid="CX5"), data)
        check("apply_patch mixed non-code+code -> allow (md ignored)", decision(out) != "deny")

        rc, out = run("gate.py", patch_payload([("Add", "/repo/cx/real2.py")], sid="CX5"), data)
        check("apply_patch 2nd distinct code file -> allow", decision(out) != "deny")

        rc, out = run("gate.py", patch_payload([("Add", "/repo/cx/real3.py")], sid="CX5"), data)
        reason = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
        check("apply_patch 3rd distinct code file -> deny (md didn't count)", decision(out) == "deny")

        # g. deny reason mirrors gate.py's delegation nudge (reusing the deny above)
        check("apply_patch deny mentions delegation target", "default-worker" in reason)

        print("== dual-host manifests & mirrors ==")
        claude_pj = json.load(open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json")))
        codex_pj = json.load(open(os.path.join(PLUGIN, ".codex-plugin", "plugin.json")))
        check("plugin.json versions in sync (claude == codex)",
              claude_pj["version"] == codex_pj["version"],
              f'claude={claude_pj["version"]} codex={codex_pj["version"]}')

        frontmatter_re = re.compile(r"^---\n.*?\n---\n", re.S)
        commands_dir = os.path.join(PLUGIN, "commands")
        for fn in sorted(os.listdir(commands_dir)):
            if not fn.endswith(".md"):
                continue
            name = fn[:-3]
            cmd_text = open(os.path.join(commands_dir, fn), encoding="utf-8").read()
            skill_path = os.path.join(PLUGIN, "codex-skills", f"haejwo-{name}", "SKILL.md")
            skill_text = open(skill_path, encoding="utf-8").read()
            cmd_body = " ".join(frontmatter_re.sub("", cmd_text, count=1).split())
            skill_body = " ".join(frontmatter_re.sub("", skill_text, count=1).split())
            check(f"mirror drift: commands/{fn} content present in codex-skills/haejwo-{name}/SKILL.md",
                  cmd_body in skill_body)

        print("== session_brief.py ==")
        rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, data)
        ctx = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("unconfigured -> setup nudge", "setup" in ctx and "NOT configured" in ctx)
        # COLD-START REPAIR: an unconfigured session now gets the FULL rules
        # text (the defaults are enforced from turn 1, so the rules that
        # explain them ship from turn 1) plus a defaults summary. The 4-line
        # UNCONFIGURED_CORE survives only on the degraded path below.
        check("unconfigured -> full rules injected (not just the minimal core)",
              "does JUDGMENT" in ctx and "delegation signal" in ctx
              and UNCONFIGURED_CORE not in ctx, ctx[:200])
        check("unconfigured -> defaults summary labelled as defaults",
              "[haejwo config] defaults — not configured:" in ctx, ctx[-400:])
        check("unconfigured -> defaults summary carries gate/tiers/reviewer",
              "gate=ON budget=2 files/turn bash_guard=ON" in ctx
              and "models: deep-reasoner=session model, default-worker=opus, "
                  "task-worker=opus (low effort)" in ctx
              and ctx.rstrip().endswith("codex reviewer: disabled (fallback: deep-reasoner)"),
              ctx[-400:])
        check("unconfigured -> rules + nudge + summary stay under MAX_LEN",
              len(ctx) < MAX_LEN, f"len={len(ctx)}")
        check("unconfigured -> ${CLAUDE_PLUGIN_ROOT} resolved in the injected rules",
              "${CLAUDE_PLUGIN_ROOT}" not in ctx
              and f"{PLUGIN.rstrip('/')}/scripts/" in ctx, ctx[:200])
        check("unconfigured -> does NOT claim the rules file is unreadable "
              "(that's the degrade cause, not this one)",
              "unreadable" not in ctx, ctx)

        with open(os.path.join(data, "config.json"), "w") as f:
            json.dump({"configured": True,
                       "gate": {"enabled": True, "max_files_per_turn": 2, "bash_guard": True}}, f)
        rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, data)
        ctx = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("configured -> rules + config summary",
              "haejwo config" in ctx and "delegate" in ctx.lower())
        check("claude host summary has no codex-tiers leakage",
              "codex tiers" not in ctx and "models:" in ctx and "codex reviewer" in ctx)
        check("claude host: default deep-reasoner='inherit' renders as inherit(session) + clarifier",
              "deep-reasoner=inherit(session)" in ctx
              and "(inherit = omit the model override)" in ctx, ctx)

        check("${CLAUDE_PLUGIN_ROOT} resolved: no literal placeholder leaks into injected rules",
              "${CLAUDE_PLUGIN_ROOT}" not in ctx)
        check("${CLAUDE_PLUGIN_ROOT} resolved: consult path resolved to the real plugin root",
              f"{PLUGIN.rstrip('/')}/scripts/" in ctx, ctx[:200])

        # codex-host branch: host is path-sniffed off the DATA argv (root|data
        # containing "/.codex/"), so a data dir alone is enough to flip it.
        codex_data = os.path.join(data, ".codex", "plugins", "data", "haejwo")
        os.makedirs(codex_data, exist_ok=True)
        with open(os.path.join(codex_data, "config.json"), "w") as f:
            json.dump({"configured": True}, f)
        rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, codex_data)
        ctx = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("codex host -> codex tiers + claude reviewer + spawn_agent summary",
              "codex tiers" in ctx and "claude reviewer" in ctx
              and "spawn_agent" in ctx
              and "task-worker=inherit/low" in ctx, ctx)

        print("== session_brief.py: ${CLAUDE_PLUGIN_ROOT} resolution edge cases ==")

        # (b1) relative root: rules load succeeds (open() resolves relative to
        # CWD), but substitution requires an ABSOLUTE root -> placeholder survives.
        rel_root = os.path.relpath(PLUGIN, os.getcwd())
        rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, data, root=rel_root)
        ctx_rel = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("relative root -> placeholder preserved (not absolute, no substitution), exit 0",
              rc == 0 and "${CLAUDE_PLUGIN_ROOT}" in ctx_rel, ctx_rel[:200])

        # (b2) nonexistent absolute root: rules file can't even be opened ->
        # emergency-core fallback (which has no placeholder to begin with);
        # must still exit 0 and never crash.
        rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, data,
                      root="/nonexistent/haejwo/root/xyz-does-not-exist")
        ctx_missing = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("nonexistent absolute root -> emergency-core fallback, exit 0, no crash",
              rc == 0 and "emergency core" in ctx_missing and "${CLAUDE_PLUGIN_ROOT}" not in ctx_missing,
              ctx_missing[:200])

        # (c) non-truncation: a realistically long absolute root + long model
        # names must not push the config-summary tail (reviewer segment) past
        # the 5000-char cap (session_brief.py:15,83).
        long_root_base = tempfile.mkdtemp(prefix="hjw-test-longroot-")
        long_root = os.path.join(long_root_base, "home", "user", ".claude", "plugins",
                                  "marketplaces", "jungzuna-haejwo", "haejwo")
        try:
            os.makedirs(os.path.join(long_root, "rules"), exist_ok=True)
            real_rules = open(os.path.join(PLUGIN, "rules", "orchestration.md"),
                              encoding="utf-8-sig").read()
            with open(os.path.join(long_root, "rules", "orchestration.md"),
                      "w", encoding="utf-8") as f:
                f.write(real_rules)

            long_data = tempfile.mkdtemp(prefix="hjw-test-longdata-")
            try:
                long_model = "claude-sonnet-4-5-20250929"
                with open(os.path.join(long_data, "config.json"), "w") as f:
                    json.dump({
                        "configured": True,
                        "gate": {"enabled": True, "max_files_per_turn": 2, "bash_guard": True},
                        "models": {"deep_reasoner": long_model, "default_worker": long_model,
                                   "task_worker": long_model},
                    }, f)
                rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, long_data,
                              root=long_root)
                ctx_long = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
                check("long root + long model names: substitution still applied",
                      "${CLAUDE_PLUGIN_ROOT}" not in ctx_long)
                check("long root + long model names: config-summary tail (reviewer segment) "
                      "survives — not truncated away",
                      ctx_long.rstrip().endswith(
                          "codex reviewer: disabled (fallback: deep-reasoner)"),
                      ctx_long[-200:])
            finally:
                shutil.rmtree(long_data, ignore_errors=True)
        finally:
            shutil.rmtree(long_root_base, ignore_errors=True)

        # (d) codex-host branch: root ITSELF (not just data) containing
        # "/.codex/" must also get substitution.
        codex_root_base = tempfile.mkdtemp(prefix="hjw-test-codexroot-")
        try:
            codex_root = os.path.join(codex_root_base, ".codex", "plugins", "haejwo")
            os.makedirs(os.path.join(codex_root, "rules"), exist_ok=True)
            with open(os.path.join(codex_root, "rules", "orchestration.md"),
                      "w", encoding="utf-8") as f:
                f.write(open(os.path.join(PLUGIN, "rules", "orchestration.md"),
                             encoding="utf-8-sig").read())
            rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, codex_data,
                          root=codex_root)
            ctx_cx = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
            check("codex-host root (contains /.codex/) still gets ${CLAUDE_PLUGIN_ROOT} substitution",
                  "${CLAUDE_PLUGIN_ROOT}" not in ctx_cx
                  and f"{codex_root.rstrip('/')}/scripts/" in ctx_cx
                  and "codex tiers" in ctx_cx, ctx_cx[:200])
        finally:
            shutil.rmtree(codex_root_base, ignore_errors=True)

        # (e) oversized rules file: explicit degrade instead of a silent
        # mid-text truncation. Swap in the trusted EMERGENCY_CORE but keep
        # the FULL config summary — never cut the summary either.
        oversized_root_base = tempfile.mkdtemp(prefix="hjw-test-oversized-")
        try:
            oversized_root = os.path.join(oversized_root_base, "haejwo")
            os.makedirs(os.path.join(oversized_root, "rules"), exist_ok=True)
            with open(os.path.join(oversized_root, "rules", "orchestration.md"),
                      "w", encoding="utf-8") as f:
                f.write("x" * (MAX_LEN + 1000))
            rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"},
                          data, root=oversized_root)
            ctx_over = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
            check("oversized rules file -> exit 0, no crash", rc == 0)
            check("oversized rules file -> degrades to EMERGENCY_CORE (no mid-text cut)",
                  ctx_over.startswith(EMERGENCY_CORE), ctx_over[:200])
            check("oversized rules file -> full config summary preserved at the tail",
                  ctx_over.rstrip().endswith(
                      "codex reviewer: disabled (fallback: deep-reasoner)"),
                  ctx_over[-200:])
            check("oversized rules file -> degraded output well under MAX_LEN",
                  len(ctx_over) < MAX_LEN, f"len={len(ctx_over)}")
        finally:
            shutil.rmtree(oversized_root_base, ignore_errors=True)

        # normal (non-oversized) path stays unaffected: the real rules file
        # degrades neither via truncation nor EMERGENCY_CORE.
        rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, data)
        ctx_normal = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("normal path: real rules file does NOT trigger the degrade",
              rc == 0 and not ctx_normal.startswith(EMERGENCY_CORE), ctx_normal[:80])

        # (f) UNCONFIGURED branch degrades the same way: the rules text it now
        # injects can be unreadable or over budget too. EMERGENCY_CORE names
        # that cause; UNCONFIGURED_CORE keeps naming this branch's own.
        unconf_data = tempfile.mkdtemp(prefix="hjw-test-unconf-")
        over_base = tempfile.mkdtemp(prefix="hjw-test-unconf-over-")
        try:
            over_root = os.path.join(over_base, "haejwo")
            os.makedirs(os.path.join(over_root, "rules"), exist_ok=True)
            with open(os.path.join(over_root, "rules", "orchestration.md"),
                      "w", encoding="utf-8") as f:
                f.write("x" * (MAX_LEN + 1000))
            rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"},
                          unconf_data, root=over_root)
            ctx_uo = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
            check("unconfigured + oversized rules -> EMERGENCY_CORE degrade, exit 0",
                  rc == 0 and ctx_uo.startswith(EMERGENCY_CORE), ctx_uo[:200])
            check("unconfigured + oversized rules -> nudge, UNCONFIGURED_CORE and "
                  "defaults summary all preserved",
                  "NOT configured" in ctx_uo and UNCONFIGURED_CORE in ctx_uo
                  and CORE_BODY in ctx_uo
                  and "[haejwo config] defaults — not configured:" in ctx_uo, ctx_uo)
            check("unconfigured + oversized rules -> degraded output under MAX_LEN",
                  len(ctx_uo) < MAX_LEN, f"len={len(ctx_uo)}")

            rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"},
                          unconf_data, root="/nonexistent/haejwo/root/xyz")
            ctx_um = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
            check("unconfigured + unreadable rules -> same explicit degrade, exit 0",
                  rc == 0 and ctx_um.startswith(EMERGENCY_CORE)
                  and UNCONFIGURED_CORE in ctx_um
                  and "[haejwo config] defaults — not configured:" in ctx_um,
                  ctx_um[:200])
        finally:
            shutil.rmtree(over_base, ignore_errors=True)
            shutil.rmtree(unconf_data, ignore_errors=True)

        # (g) malformed config.json: the summary must say so instead of
        # presenting fail-open defaults as a settled configuration (A2).
        mal_data = tempfile.mkdtemp(prefix="hjw-test-sbmal-")
        try:
            with open(os.path.join(mal_data, "config.json"), "w") as f:
                f.write("{ not valid json !!!")
            rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, mal_data)
            ctx_mal = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
            check("malformed config -> summary says unreadable + fail-open defaults",
                  rc == 0
                  and "[haejwo config] config.json unreadable — fail-open defaults "
                      "active" in ctx_mal
                  and "defaults — not configured:" not in ctx_mal, ctx_mal[-300:])
            check("malformed config -> rules still injected (session stays operable)",
                  "does JUDGMENT" in ctx_mal, ctx_mal[:200])
            # F2: the "not configured yet (first use) ... gate ON, bash-guard ON"
            # nudge is NOT a description of a malformed-config session — it
            # advertises enforcement this very status has switched off.
            check("F2 malformed config -> says enforcement is DISABLED",
                  "[haejwo] config.json is unreadable (malformed JSON): enforcement "
                  "is DISABLED — every gate fails open until the file is repaired. "
                  "Run /haejwo:setup to rewrite it, or fix the JSON by hand."
                  in ctx_mal, ctx_mal[-600:])
            check("F2 malformed config -> no setup nudge (no 'first use', no 'gate ON')",
                  "first use" not in ctx_mal and "gate ON" not in ctx_mal, ctx_mal[-600:])
        finally:
            shutil.rmtree(mal_data, ignore_errors=True)

        print("== session_brief.py host-correct nudge + inherit rendering (A6/E8) ==")
        # The unconfigured nudge names the DEFAULT tiers of the host it is
        # actually running on: a Codex session cannot pass Claude aliases.
        a6_claude = tempfile.mkdtemp(prefix="hjw-test-a6c-")
        a6_codex_base = tempfile.mkdtemp(prefix="hjw-test-a6x-")
        try:
            rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, a6_claude)
            ctx_c = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
            check("A6 Claude nudge names the shipped defaults (session model + opus)",
                  rc == 0 and "NOT configured" in ctx_c
                  and "haejwo:deep-reasoner (session model), haejwo:default-worker "
                      "(opus), haejwo:task-worker (opus)." in ctx_c, ctx_c)

            a6_codex = os.path.join(a6_codex_base, ".codex", "plugins", "data", "haejwo")
            os.makedirs(a6_codex, exist_ok=True)
            rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, a6_codex)
            ctx_x = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
            check("A6 Codex nudge names the codex tiers (host model, all inherit)",
                  rc == 0 and "NOT configured" in ctx_x
                  and "haejwo:deep-reasoner (host model), haejwo:default-worker "
                      "(host model), haejwo:task-worker (host model)." in ctx_x, ctx_x)
            check("A6 Codex defaults summary: host model + per-role efforts",
                  "codex tiers: deep-reasoner=host model/high, default-worker=host "
                  "model/medium, task-worker=host model/low (pass reasoning_effort "
                  "on spawn_agent; omit model to inherit; effort overrides need a "
                  "fresh or partial context fork (fork_turns), never a full-history "
                  "fork)" in ctx_x, ctx_x[-400:])
            check("A6 Codex unconfigured: no Claude aliases leak",
                  "sonnet" not in ctx_x and "haiku" not in ctx_x
                  and "opus" not in ctx_x, ctx_x)

            # E8a: on Claude a worker tier's "inherit" means the AGENT FILE's
            # default, not the session model — render it as such and say so.
            with open(os.path.join(a6_claude, "config.json"), "w") as f:
                json.dump({"configured": True,
                           "gate": {"enabled": True, "max_files_per_turn": 2,
                                    "bash_guard": True},
                           "models": {"deep_reasoner": "opus",
                                      "default_worker": "inherit",
                                      "task_worker": "haiku"}}, f)
            rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, a6_claude)
            ctx_i = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
            check("E8a worker 'inherit' renders as 'agent-file default'",
                  "default-worker=agent-file default" in ctx_i, ctx_i)
            check("E8a worker 'inherit' adds the explanatory sentence",
                  "On Claude, omitting the model override uses each agent file's "
                  "default; pass an explicit model to override it." in ctx_i, ctx_i)
            check("E8a deep_reasoner explicit -> no '(inherit = omit...)' clarifier",
                  "(inherit = omit the model override)" not in ctx_i, ctx_i)
            check("E8a inherit rendering stays under MAX_LEN (reviewer tail survives)",
                  len(ctx_i) < MAX_LEN and ctx_i.rstrip().endswith(
                      "codex reviewer: disabled (fallback: deep-reasoner)"),
                  f"len={len(ctx_i)} tail={ctx_i[-120:]}")

            with open(os.path.join(a6_claude, "config.json"), "w") as f:
                json.dump({"configured": True,
                           "gate": {"enabled": True, "max_files_per_turn": 2,
                                    "bash_guard": True},
                           "models": {"deep_reasoner": "opus",
                                      "default_worker": "sonnet",
                                      "task_worker": "haiku"}}, f)
            rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, a6_claude)
            ctx_e = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
            check("E8a all tiers explicit -> neither 'agent-file default' nor the sentence",
                  "agent-file default" not in ctx_e
                  and "On Claude, omitting the model override" not in ctx_e
                  and "(inherit = omit the model override)" not in ctx_e, ctx_e)
        finally:
            shutil.rmtree(a6_claude, ignore_errors=True)
            shutil.rmtree(a6_codex_base, ignore_errors=True)

        print("== hjw_common.DEFAULT_CONFIG ==")
        # Owner policy (2.12.0): public defaults are Opus for execution and
        # the session model for judgment; the roles differ by reasoning
        # effort, not by model family.
        check("models defaults: judgment inherits, execution is opus",
              DEFAULT_CONFIG["models"] == {
                  "deep_reasoner": "inherit",
                  "default_worker": "opus",
                  "task_worker": "opus",
              }, str(DEFAULT_CONFIG["models"]))
        check("models_codex defaults: all inherit (host model, effort-only tiers)",
              DEFAULT_CONFIG["models_codex"] == {
                  "deep_reasoner": "inherit",
                  "default_worker": "inherit",
                  "task_worker": "inherit",
              }, str(DEFAULT_CONFIG["models_codex"]))
        check("gate.delegation_guard defaults True",
              DEFAULT_CONFIG["gate"]["delegation_guard"] is True)

        print("== hjw_common.observe() 1-generation rotation ==")
        rot_data = tempfile.mkdtemp(prefix="hjw-test-rotate-")
        try:
            sdir = os.path.join(rot_data, "state")
            os.makedirs(sdir, exist_ok=True)
            obs_path = os.path.join(sdir, "observations.jsonl")
            old1_path = obs_path + ".1"
            # a stale prior .1 must be OVERWRITTEN by the next rotation, not appended to
            with open(old1_path, "w") as f:
                f.write('{"marker": "stale-generation"}\n')
            with open(obs_path, "w") as f:
                f.write("z" * 200_001 + "\n")
            observe(rot_data, {"hook": "test", "marker": "after-rotate"})
            check("rotation: .1 archive created at threshold", os.path.isfile(old1_path))
            check("rotation: .1 overwrites any previous .1 (old content gone)",
                  "stale-generation" not in open(old1_path).read())
            check("rotation: .1 archive holds the rotated-out oversized content",
                  os.path.getsize(old1_path) > 200_000)
            check("rotation: current file restarts small",
                  os.path.getsize(obs_path) < 1000)
            check("rotation: current record still appended after rotate",
                  "after-rotate" in open(obs_path).read())
        finally:
            shutil.rmtree(rot_data, ignore_errors=True)

        rot_fail_data = tempfile.mkdtemp(prefix="hjw-test-rotate-fail-")
        try:
            sdir = os.path.join(rot_fail_data, "state")
            os.makedirs(sdir, exist_ok=True)
            obs_path = os.path.join(sdir, "observations.jsonl")
            with open(obs_path, "w") as f:
                f.write("z" * 200_001 + "\n")
            # force os.replace(...) to fail: the rotation target is an existing
            # directory, so os.replace(file, dir) raises -> rotation must be
            # swallowed separately and the append must still be attempted.
            os.makedirs(obs_path + ".1", exist_ok=True)
            observe(rot_fail_data, {"hook": "test", "marker": "rotate-failed-but-appended"})
            check("rotation failure path: record still appended (fail-open)",
                  "rotate-failed-but-appended" in open(obs_path).read())
        finally:
            shutil.rmtree(rot_fail_data, ignore_errors=True)

        print("== hjw_common.prune_state() observation exemption ==")
        prune_data = tempfile.mkdtemp(prefix="hjw-test-prune-")
        try:
            sdir = os.path.join(prune_data, "state")
            os.makedirs(sdir, exist_ok=True)
            obs_path = os.path.join(sdir, "observations.jsonl")
            obs1_path = obs_path + ".1"
            stale_path = os.path.join(sdir, "stale-session.json")
            fresh_path = os.path.join(sdir, "fresh-session.json")
            for p in (obs_path, obs1_path, stale_path, fresh_path):
                with open(p, "w") as f:
                    f.write("{}")
            old_time = time.time() - 8 * 86400  # 8 days: past the 7-day cutoff
            os.utime(obs_path, (old_time, old_time))
            os.utime(obs1_path, (old_time, old_time))
            os.utime(stale_path, (old_time, old_time))
            # fresh_path keeps its just-created mtime
            prune_state(prune_data)
            check("prune_state: observations.jsonl survives despite stale mtime (size-bounded, not age-bounded)",
                  os.path.isfile(obs_path))
            check("prune_state: observations.jsonl.1 survives despite stale mtime (P13 audit evidence)",
                  os.path.isfile(obs1_path))
            check("prune_state: stale session state file IS pruned",
                  not os.path.isfile(stale_path))
            check("prune_state: fresh session state file survives",
                  os.path.isfile(fresh_path))
        finally:
            shutil.rmtree(prune_data, ignore_errors=True)

        print("== delegation_gate.py ==")
        rc, out = run("delegation_gate.py", task_payload("general-purpose"), data)
        reason = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
        check("deny: general-purpose without model", decision(out) == "deny", str(out))
        check("deny reason instructs default-worker + INHERIT",
              "haejwo:default-worker" in reason and "INHERIT" in reason)

        rc, out = run("delegation_gate.py",
                      task_payload("general-purpose", model="haiku"), data)
        check("allow: general-purpose WITH explicit model", decision(out) != "deny", str(out))

        rc, out = run("delegation_gate.py",
                      task_payload("general-purpose", model="inherit"), data)
        check("deny: general-purpose with model='inherit' (non-explicit)",
              decision(out) == "deny", str(out))

        rc, out = run("delegation_gate.py",
                      task_payload("general-purpose", model=" "), data)
        check("deny: general-purpose with whitespace-only model (non-explicit)",
              decision(out) == "deny", str(out))

        with open(os.path.join(data, "config.json"), "w") as f:
            json.dump({"gate": {"delegation_guard": False}}, f)
        rc, out = run("delegation_gate.py", task_payload("Explore"), data)
        check("allow: Explore without model but delegation_guard disabled",
              decision(out) != "deny", str(out))
        os.remove(os.path.join(data, "config.json"))

        rc, out = run("delegation_gate.py", task_payload("haejwo:default-worker"), data)
        check("allow: haejwo:* tiered worker without model",
              decision(out) != "deny", str(out))

        rc, out = run("delegation_gate.py",
                      task_payload("general-purpose", agent="task-worker"), data)
        check("SUBAGENT delegation -> allow (exempt)", decision(out) != "deny", str(out))

        rc, out = run("delegation_gate.py", "not-json{{{", data)
        check("fail-open: garbage stdin -> allow (rc0)", rc == 0 and decision(out) != "deny")

        rc, out = run("delegation_gate.py", "", data)
        check("fail-open: empty stdin -> allow (rc0)", rc == 0 and decision(out) != "deny")

        print("== delegation_gate.py host-aware deny wording (codex vs Claude) ==")
        # codex-host branch: path-sniffed off root|data containing "/.codex/"
        # (same heuristic session_brief.py:87 uses), so a data dir alone
        # under a ".codex/" component is enough to flip it.
        codex_data = os.path.join(data, ".codex", "plugins", "data", "haejwo")
        os.makedirs(codex_data, exist_ok=True)

        # Full expected deny text (Claude-host wording) — byte-identical
        # comparison, not a substring check, so a future contract regression
        # (wording drift, punctuation, ordering) is caught even if the model
        # fragments themselves survive unchanged. With the 2.12.0 defaults
        # both worker tiers sit on opus, so this is the identical-tier
        # collapse (named once, no fake choice).
        CLAUDE_HOST_DENY_TEXT = (
            "[haejwo gate] Delegation to generic agent 'general-purpose' without an "
            "explicit model — it would INHERIT the session model instead of a "
            "configured tier. Pass model: 'opus', or delegate to "
            "haejwo:default-worker / haejwo:task-worker instead. Emergency override: "
            "/haejwo:gate off."
        )

        # 1. Claude host (existing fixtures/data dir): wording byte-identical
        #    to the pre-existing assertions above — nothing changes here.
        rc, out = run("delegation_gate.py",
                      task_payload("general-purpose", sid="sess-HOST1"), data)
        reason1 = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
        check("Claude host: deny -> full wording byte-identical to expected contract text",
              decision(out) == "deny" and reason1 == CLAUDE_HOST_DENY_TEXT,
              reason1)

        # 2. Codex host + BOTH models_codex tiers explicit (custom pins):
        #    deny names both the configured models AND the haejwo tiers.
        with open(os.path.join(codex_data, "config.json"), "w") as f:
            json.dump({"models_codex": {"task_worker": "gpt-5.6-luna",
                                         "default_worker": "gpt-5.6-terra"}}, f)
        rc, out = run("delegation_gate.py",
                      task_payload("general-purpose", sid="sess-HOST2"), codex_data)
        reason2 = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
        check("Codex host + explicit pins: still denies, rc0",
              rc == 0 and decision(out) == "deny", str(out))
        check("Codex host + explicit pins: names configured models",
              "'gpt-5.6-luna' (locate)" in reason2
              and "'gpt-5.6-terra' (read/summarize)" in reason2, reason2)
        check("Codex host + explicit pins: also names haejwo tiers",
              "haejwo:default-worker" in reason2 and "haejwo:task-worker" in reason2, reason2)
        check("Codex host + explicit pins: no Claude-alias leakage",
              "'haiku'" not in reason2 and "'sonnet'" not in reason2, reason2)

        # 3. Codex host + models_codex all "inherit": no model:'inherit'
        #    recommendation, but both haejwo tiers still named.
        with open(os.path.join(codex_data, "config.json"), "w") as f:
            json.dump({"models_codex": {"task_worker": "inherit",
                                         "default_worker": "inherit"}}, f)
        rc, out = run("delegation_gate.py",
                      task_payload("general-purpose", sid="sess-HOST3"), codex_data)
        reason3 = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
        check("Codex host + inherit tiers: still denies, rc0",
              rc == 0 and decision(out) == "deny", str(out))
        check("Codex host + inherit tiers: no 'Pass model:' recommendation",
              "Pass model:" not in reason3, reason3)
        check("Codex host + inherit tiers: names both haejwo tiers",
              "haejwo:default-worker" in reason3 and "haejwo:task-worker" in reason3, reason3)

        # 4. Codex host + malformed models_codex (a string, not a dict):
        #    falls back to the CODEX tier-only wording (2.11.0: it used to
        #    fall back to the Claude wording, which recommended Claude
        #    aliases a Codex host cannot pass); still denies; exit unchanged.
        with open(os.path.join(codex_data, "config.json"), "w") as f:
            json.dump({"models_codex": "not-a-dict"}, f)
        rc, out = run("delegation_gate.py",
                      task_payload("general-purpose", sid="sess-HOST4"), codex_data)
        reason4 = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
        check("Codex host + malformed models_codex: still denies, rc0 (exit unchanged)",
              rc == 0 and decision(out) == "deny", str(out))
        check("Codex host + malformed models_codex: falls back to CODEX tier-only wording",
              CODEX_TIER_ONLY in reason4 and "'haiku'" not in reason4
              and "'sonnet'" not in reason4, reason4)

        os.remove(os.path.join(codex_data, "config.json"))

        print("== delegation_gate.py steering from configured tiers (A12) ==")
        # The deny's next-action must name the models the USER configured,
        # not hard-coded aliases. Default config == the long-tested wording
        # (asserted byte-identically as case 1 above).
        a12_data = tempfile.mkdtemp(prefix="hjw-test-a12-")
        try:
            def a12(models, sid):
                with open(os.path.join(a12_data, "config.json"), "w") as f:
                    json.dump({"models": models}, f)
                rc_, out_ = run("delegation_gate.py",
                                task_payload("general-purpose", sid=sid), a12_data)
                return rc_, decision(out_), (out_.get("hookSpecificOutput") or {}).get(
                    "permissionDecisionReason", "")

            rc, dec, r = a12({}, "sess-A12a")
            check("A12 Claude default config: deny wording byte-identical to contract",
                  dec == "deny" and r == CLAUDE_HOST_DENY_TEXT, r)

            rc, dec, r = a12({"default_worker": "opus", "task_worker": "sonnet"},
                             "sess-A12b")
            check("A12 both tiers explicit: names both configured models",
                  dec == "deny"
                  and "Pass model: 'sonnet' (locate) or 'opus' (read/summarize)" in r, r)

            rc, dec, r = a12({"default_worker": "opus", "task_worker": "opus"}, "sess-A12g")
            check("A12 both tiers on the SAME model: named once, no fake choice",
                  dec == "deny"
                  and "Pass model: 'opus', or delegate to haejwo:default-worker / "
                      "haejwo:task-worker instead." in r
                  and "(locate)" not in r and "(read/summarize)" not in r, r)

            rc, dec, r = a12({"default_worker": "opus", "task_worker": "inherit"}, "sess-A12c")
            check("A12 only default_worker explicit: names just that one, for its role",
                  dec == "deny"
                  and "Pass model: 'opus' (read/summarize), or delegate to "
                      "haejwo:default-worker / haejwo:task-worker instead." in r
                  and "(locate)" not in r, r)

            rc, dec, r = a12({"default_worker": "inherit", "task_worker": "opus"}, "sess-A12d")
            check("A12 only task_worker explicit: names just that one, for its role",
                  dec == "deny"
                  and "Pass model: 'opus' (locate), or delegate to "
                      "haejwo:default-worker / haejwo:task-worker instead." in r
                  and "(read/summarize)" not in r, r)

            rc, dec, r = a12({"default_worker": "inherit", "task_worker": "inherit"},
                             "sess-A12e")
            check("A12 neither explicit: tier-only wording, no 'Pass model:'",
                  dec == "deny" and CLAUDE_TIER_ONLY in r and "Pass model:" not in r, r)

            rc, dec, r = a12("not-a-dict", "sess-A12f")
            check("A12 malformed models on Claude: still denies with Claude tier-only wording",
                  rc == 0 and dec == "deny" and CLAUDE_TIER_ONLY in r, r)
        finally:
            shutil.rmtree(a12_data, ignore_errors=True)

        print("== delegation_gate.py envelope v2 (plan_marker_kind / prompt_bytes) ==")
        rc, out = run("delegation_gate.py", task_payload(
            "haejwo:default-worker", sid="sess-PM1",
            prompt="Implement the thing.\nPlan: per the user-approved decision above — do X."),
            data)
        check("allow: plan-marker prompt", decision(out) != "deny", str(out))

        rc, out = run("delegation_gate.py", task_payload(
            "haejwo:default-worker", sid="sess-PM2",
            prompt="Tiny rename.\nNo plan because: mechanical, single symbol."), data)
        check("allow: no-plan-marker prompt", decision(out) != "deny", str(out))

        rc, out = run("delegation_gate.py", task_payload(
            "haejwo:default-worker", sid="sess-PM3", prompt="Just fix the typo."), data)
        check("allow: bare prompt (no marker)", decision(out) != "deny", str(out))

        obs_file = os.path.join(data, "state", "observations.jsonl")
        obs_recs = [json.loads(l) for l in open(obs_file)]

        def _last(pred):
            hits = [r for r in obs_recs if pred(r)]
            return hits[-1] if hits else None

        r_deny = _last(lambda r: r.get("hook") == "delegation" and r.get("decision") == "deny"
                       and r.get("subagent_type") == "general-purpose"
                       and r.get("requested_model") is None)
        check("envelope v2: deny case records decision 'deny'",
              bool(r_deny) and r_deny.get("v") == 2, str(r_deny))

        r_allow = _last(lambda r: r.get("hook") == "delegation"
                        and r.get("subagent_type") == "general-purpose"
                        and r.get("requested_model") == "haiku")
        check("envelope v2: allow case records decision 'allow'",
              bool(r_allow) and r_allow.get("decision") == "allow", str(r_allow))

        r_plan = _last(lambda r: r.get("sid") == "sess-PM1")
        check("envelope v2: 'Plan:' prompt -> plan_marker_kind 'plan'",
              bool(r_plan) and r_plan.get("plan_marker_kind") == "plan", str(r_plan))
        check("envelope v2: prompt_bytes > 0 recorded",
              bool(r_plan) and r_plan.get("prompt_bytes", 0) > 0, str(r_plan))

        r_noplan = _last(lambda r: r.get("sid") == "sess-PM2")
        check("envelope v2: 'No plan because' prompt -> plan_marker_kind 'no_plan'",
              bool(r_noplan) and r_noplan.get("plan_marker_kind") == "no_plan", str(r_noplan))

        r_none = _last(lambda r: r.get("sid") == "sess-PM3")
        check("envelope v2: bare prompt -> plan_marker_kind 'none'",
              bool(r_none) and r_none.get("plan_marker_kind") == "none", str(r_none))

        print("== delegation_gate.py fail-open edge cases ==")
        fo_data = tempfile.mkdtemp(prefix="hjw-test-failopen-")
        try:
            null_ti = task_payload("general-purpose")
            null_ti["tool_input"] = None
            rc, out = run("delegation_gate.py", null_ti, fo_data)
            check("fail-open: tool_input null -> allow rc0",
                  rc == 0 and decision(out) != "deny", str(out))

            rc, out = run("delegation_gate.py", task_payload(5), fo_data)
            check("fail-open: subagent_type non-string (5) -> allow rc0",
                  rc == 0 and decision(out) != "deny", str(out))

            with open(os.path.join(fo_data, "config.json"), "w") as f:
                f.write("{ not valid json !!! ### garbage")
            rc, out = run("delegation_gate.py",
                          task_payload("haejwo:default-worker"), fo_data)
            check("fail-open: malformed config.json -> allow rc0",
                  rc == 0 and decision(out) != "deny", str(out))
        finally:
            shutil.rmtree(fo_data, ignore_errors=True)

        print("== delegation_gate.py adversarial-review fixes (fail-open envelope, audit/decision parity) ==")

        def _last_fresh(pred):
            recs = [json.loads(l) for l in open(obs_file)]
            hits = [r for r in recs if pred(r)]
            return hits[-1] if hits else None

        # (a) both markers present -> "Plan:" wins (documented precedence)
        rc, out = run("delegation_gate.py", task_payload(
            "haejwo:default-worker", sid="sess-PM4",
            prompt="No plan because: quick. Plan: per approved decision — do X."), data)
        check("allow: both plan markers present", decision(out) != "deny", str(out))
        r_both = _last_fresh(lambda r: r.get("sid") == "sess-PM4")
        check("envelope: both markers present -> plan_marker_kind 'plan' (precedence)",
              bool(r_both) and r_both.get("plan_marker_kind") == "plan", str(r_both))

        # (b) non-string prompt (dict) -> no crash, allow, prompt_bytes == 0
        rc, out = run("delegation_gate.py", task_payload(
            "haejwo:default-worker", sid="sess-PM5", prompt={"x": 1}), data)
        check("allow: non-string (dict) prompt -> no crash", rc == 0 and decision(out) != "deny", str(out))
        r_dict = _last_fresh(lambda r: r.get("sid") == "sess-PM5")
        check("envelope: non-string prompt -> prompt_bytes == 0",
              bool(r_dict) and r_dict.get("prompt_bytes") == 0, str(r_dict))

        # (c) lone surrogate in prompt -> no crash, decision recorded
        rc, out = run("delegation_gate.py", task_payload(
            "haejwo:default-worker", sid="sess-PM6", prompt="bad\ud800"), data)
        check("allow: prompt with lone surrogate -> no crash", rc == 0 and decision(out) != "deny", str(out))
        r_surr = _last_fresh(lambda r: r.get("sid") == "sess-PM6")
        check("envelope: lone-surrogate prompt -> decision recorded",
              bool(r_surr) and r_surr.get("decision") == "allow", str(r_surr))

        # (d) gate disabled via config -> envelope still written with decision "allow"
        with open(os.path.join(data, "config.json"), "w") as f:
            json.dump({"gate": {"delegation_guard": False}}, f)
        rc, out = run("delegation_gate.py", task_payload("Explore", sid="sess-PM7"), data)
        check("allow: gate disabled via config -> allow", rc == 0 and decision(out) != "deny", str(out))
        r_disabled = _last_fresh(lambda r: r.get("sid") == "sess-PM7")
        check("envelope: gate disabled -> record written with decision 'allow'",
              bool(r_disabled) and r_disabled.get("decision") == "allow", str(r_disabled))
        os.remove(os.path.join(data, "config.json"))

        # (e) subagent-exempt payload -> envelope decision "allow"
        rc, out = run("delegation_gate.py", task_payload(
            "general-purpose", agent="task-worker", sid="sess-PM8"), data)
        check("allow: subagent-exempt payload", decision(out) != "deny", str(out))
        r_subagent = _last_fresh(lambda r: r.get("sid") == "sess-PM8")
        check("envelope: subagent-exempt payload -> decision 'allow'",
              bool(r_subagent) and r_subagent.get("decision") == "allow", str(r_subagent))

        # (f) generic + model="inherit" -> envelope requested_model is None (matches decision)
        rc, out = run("delegation_gate.py", task_payload(
            "general-purpose", model="inherit", sid="sess-PM9"), data)
        check("deny: generic + model='inherit'", decision(out) == "deny", str(out))
        r_inherit = _last_fresh(lambda r: r.get("sid") == "sess-PM9")
        check("envelope: model='inherit' -> requested_model None, decision 'deny' (record matches decision)",
              bool(r_inherit) and r_inherit.get("requested_model") is None
              and r_inherit.get("decision") == "deny", str(r_inherit))

        print("== delegation_gate.py tier pin check (B1) ==")
        # Omitting the model on a tier worker runs the AGENT FILE's default,
        # silently ignoring a config pin that says otherwise (origin
        # 2026-08-21 silent-downgrade). Deny only on that exact mismatch.
        pin_data = tempfile.mkdtemp(prefix="hjw-test-pin-")
        try:
            def pin_cfg(models, raw=None):
                with open(os.path.join(pin_data, "config.json"), "w") as f:
                    if raw is not None:
                        f.write(raw)
                    else:
                        json.dump({"configured": True, "models": models}, f)

            def pin_run(subagent, sid, model=None, data_dir=None, root=None):
                rc_, out_ = run("delegation_gate.py",
                                task_payload(subagent, model=model, sid=sid),
                                data_dir or pin_data, root=root)
                return rc_, decision(out_), (out_.get("hookSpecificOutput") or {}).get(
                    "permissionDecisionReason", "")

            def pin_rec(sid, data_dir=None):
                path_ = os.path.join(data_dir or pin_data, "state", "observations.jsonl")
                try:
                    recs = [json.loads(l) for l in open(path_)]
                except Exception:
                    return None
                hits = [r for r in recs if r.get("sid") == sid]
                return hits[-1] if hits else None

            # 2.12.0: the agent files pin opus for BOTH worker tiers, so a
            # deny case needs a config pin that DIFFERS from that default.
            PIN_DENY_TEXT = (
                "[haejwo gate] Delegation to 'haejwo:default-worker' without a model "
                "override — the agent file defaults to 'opus' but your config pins "
                "'sonnet' for this tier (omission would not honor the pin). Pass "
                "model: 'sonnet', or another explicit model if you intend to override "
                "the pin, or run /haejwo:setup to change it. Emergency override: "
                "/haejwo:gate off."
            )

            pin_cfg({"default_worker": "sonnet"})
            rc, dec, r = pin_run("haejwo:default-worker", "sess-PIN1")
            check("B1 pin differs from the agent-file default -> DENY", dec == "deny", r)
            check("B1 deny text byte-identical to the contract", r == PIN_DENY_TEXT, r)
            check("B1 deny text carries no incident origin (it belongs in the code)",
                  "origin" not in r and "silent-downgrade" not in r, r)
            check("B1 record: tier_pin_check 'deny'",
                  (pin_rec("sess-PIN1") or {}).get("tier_pin_check") == "deny",
                  str(pin_rec("sess-PIN1")))

            rc, dec, r = pin_run("haejwo:default-worker", "sess-PIN2", model="sonnet")
            check("B1 explicit model -> allow (the host chose deliberately)",
                  rc == 0 and dec != "deny", r)
            check("B1 record: tier_pin_check 'pass:explicit-model'",
                  (pin_rec("sess-PIN2") or {}).get("tier_pin_check") == "pass:explicit-model",
                  str(pin_rec("sess-PIN2")))

            pin_cfg({"default_worker": "inherit"})
            rc, dec, r = pin_run("haejwo:default-worker", "sess-PIN3")
            check("B1 pin 'inherit' -> allow (no pin to honor)", rc == 0 and dec != "deny", r)
            check("B1 record: tier_pin_check 'pass:pin-inherit'",
                  (pin_rec("sess-PIN3") or {}).get("tier_pin_check") == "pass:pin-inherit",
                  str(pin_rec("sess-PIN3")))

            pin_cfg({"default_worker": "opus"})
            rc, dec, r = pin_run("haejwo:default-worker", "sess-PIN4")
            check("B1 pin == agent-file default -> allow (omission honors it)",
                  rc == 0 and dec != "deny", r)
            check("B1 record: tier_pin_check 'pass:pin-matches-default'",
                  (pin_rec("sess-PIN4") or {}).get("tier_pin_check")
                  == "pass:pin-matches-default", str(pin_rec("sess-PIN4")))

            # An ALL-OPUS config still denies a deep-reasoner omission: that
            # agent file declares no model (it inherits the session model), so
            # omitting the override would not honor an explicit opus pin.
            pin_cfg({"deep_reasoner": "opus", "default_worker": "opus",
                     "task_worker": "opus"})
            rc, dec, r = pin_run("haejwo:deep-reasoner", "sess-PIN5")
            check("B1 all-opus config: deep-reasoner omission still DENIES "
                  "(file inherits, pin opus)",
                  dec == "deny" and "defaults to 'inherit'" in r
                  and "pins 'opus'" in r, r)
            rc, dec, r = pin_run("haejwo:default-worker", "sess-PIN5b")
            check("B1 all-opus config: worker omission allowed (pin == file default)",
                  rc == 0 and dec != "deny", r)

            pin_cfg({"deep_reasoner": "inherit"})
            rc, dec, r = pin_run("haejwo:deep-reasoner", "sess-PIN6")
            check("B1 deep-reasoner + pin 'inherit' -> allow", rc == 0 and dec != "deny", r)

            pin_cfg({"task_worker": "sonnet"})
            rc, dec, r = pin_run("task-worker", "sess-PIN7")
            check("B1 BARE tier name 'task-worker' + differing pin -> DENY",
                  dec == "deny" and "'task-worker'" in r and "'opus'" in r, r)

            pin_cfg(None, raw="{ not valid json !!! ### garbage")
            rc, dec, r = pin_run("haejwo:default-worker", "sess-PIN8")
            check("B1 malformed config.json -> allow (never deny on a config we can't read)",
                  rc == 0 and dec != "deny", r)
            check("B1 record: tier_pin_check 'skip:config-malformed'",
                  (pin_rec("sess-PIN8") or {}).get("tier_pin_check")
                  == "skip:config-malformed", str(pin_rec("sess-PIN8")))

            pin_cfg({"default_worker": "opus"})
            rc, dec, r = pin_run("haejwo:default-worker", "sess-PIN9",
                                 root=os.path.join(pin_data, "no-such-plugin-root"))
            check("B1 agent file missing (agents/ absent) -> allow",
                  rc == 0 and dec != "deny", r)
            check("B1 record: tier_pin_check 'skip:frontmatter-unreadable'",
                  (pin_rec("sess-PIN9") or {}).get("tier_pin_check")
                  == "skip:frontmatter-unreadable", str(pin_rec("sess-PIN9")))

            fm_root = tempfile.mkdtemp(prefix="hjw-test-fmroot-")
            try:
                os.makedirs(os.path.join(fm_root, "agents"), exist_ok=True)
                # (a) frontmatter never closed -> unparseable -> skip
                with open(os.path.join(fm_root, "agents", "default-worker.md"), "w") as f:
                    f.write("---\nname: default-worker\nmodel: sonnet\nbody with no close\n")
                rc, dec, r = pin_run("haejwo:default-worker", "sess-PINA", root=fm_root)
                check("B1 frontmatter with no closing '---' -> allow (skip)",
                      rc == 0 and dec != "deny", r)
                check("B1 record: unclosed frontmatter -> 'skip:frontmatter-unreadable'",
                      (pin_rec("sess-PINA") or {}).get("tier_pin_check")
                      == "skip:frontmatter-unreadable", str(pin_rec("sess-PINA")))

                # (b) valid frontmatter, no model key -> default is "inherit"
                with open(os.path.join(fm_root, "agents", "default-worker.md"), "w") as f:
                    f.write("---\nname: default-worker\ndescription: x\n---\nbody\n")
                rc, dec, r = pin_run("haejwo:default-worker", "sess-PINB", root=fm_root)
                check("B1 frontmatter without model: + pin opus -> DENY (inherit != opus)",
                      dec == "deny" and "defaults to 'inherit'" in r, r)

                # K1: certainty rules — only a column-0 `---` delimits, only a
                # column-0 `model:` counts, and any uncertainty fails OPEN.
                def fm_write(body):
                    with open(os.path.join(fm_root, "agents", "default-worker.md"),
                              "w") as f:
                        f.write(body)

                pin_cfg({"default_worker": "sonnet"})
                fm_write('---\nname: default-worker\nmodel: "sonnet"\n---\nbody\n')
                rc, dec, r = pin_run("haejwo:default-worker", "sess-PINF", root=fm_root)
                check("K1 quoted model value compares equal to the pin -> allow",
                      rc == 0 and dec != "deny", r)
                check("K1 record: quoted value -> 'pass:pin-matches-default'",
                      (pin_rec("sess-PINF") or {}).get("tier_pin_check")
                      == "pass:pin-matches-default", str(pin_rec("sess-PINF")))

                fm_write("---\nname: default-worker\nmodel: sonnet # default\n---\nbody\n")
                rc, dec, r = pin_run("haejwo:default-worker", "sess-PING", root=fm_root)
                check("K1 inline-comment model value compares equal to the pin -> allow",
                      rc == 0 and dec != "deny", r)
                check("K1 record: inline comment -> 'pass:pin-matches-default'",
                      (pin_rec("sess-PING") or {}).get("tier_pin_check")
                      == "pass:pin-matches-default", str(pin_rec("sess-PING")))

                # an INDENTED --- inside a description must not close the block
                pin_cfg({"default_worker": "opus"})
                fm_write("---\nname: default-worker\ndescription: writes\n"
                         "  ---\n  more prose\nmodel: sonnet\n---\nbody\n")
                rc, dec, r = pin_run("haejwo:default-worker", "sess-PINH", root=fm_root)
                check("K1 indented '---' does not close the block (model still read)",
                      dec == "deny" and "defaults to 'sonnet'" in r, r)

                fm_write("---\nname: [broken\nmodel: sonnet\n---\nbody\n")
                rc, dec, r = pin_run("haejwo:default-worker", "sess-PINI", root=fm_root)
                check("K1 unparseable value ('[') anywhere in the block -> allow (skip)",
                      rc == 0 and dec != "deny", r)
                check("K1 record: '[' value -> 'skip:frontmatter-unreadable'",
                      (pin_rec("sess-PINI") or {}).get("tier_pin_check")
                      == "skip:frontmatter-unreadable", str(pin_rec("sess-PINI")))

                fm_write("---\nname: default-worker\nmodel: sonnet | opus\n---\nbody\n")
                rc, dec, r = pin_run("haejwo:default-worker", "sess-PINJ", root=fm_root)
                check("K1 model value outside [A-Za-z0-9._-] -> allow (skip)",
                      rc == 0 and dec != "deny", r)

                # L1: forms whose effective default would be a GUESS -> skip.
                # Pin 'sonnet' equals the real agent-file default, so a skip
                # and a correct read both allow; the record tells them apart.
                pin_cfg({"default_worker": "sonnet"})
                for body, sid, name in (
                    ("---\nname: default-worker\nmodel:\n  sonnet\n---\nbody\n",
                     "sess-PINP", "model: value continues on an indented line"),
                    ("---\nname: default-worker\nmodel:sonnet\n---\nbody\n",
                     "sess-PINQ", "no space after the colon"),
                    ("---\nname: default-worker\nmodel: sonnet\nmodel: opus\n---\nbody\n",
                     "sess-PINR", "model: declared twice"),
                ):
                    fm_write(body)
                    rc, dec, r = pin_run("haejwo:default-worker", sid, root=fm_root)
                    check(f"L1 {name} -> allow", rc == 0 and dec != "deny", r)
                    check(f"L1 record: {name} -> 'skip:frontmatter-unreadable'",
                          (pin_rec(sid) or {}).get("tier_pin_check")
                          == "skip:frontmatter-unreadable", str(pin_rec(sid)))

                # M1: a YAML comment is a comment, not uncertainty — the block
                # still parses and the pin check still binds (origin: the
                # 2026-09-21 `effort: low` note in agents/task-worker.md).
                pin_cfg({"default_worker": "opus"})
                for body, sid, name in (
                    ("---\nname: default-worker\n# origin note\nmodel: haiku\n"
                     "effort: low\n---\nbody\n",
                     "sess-PINS", "column-0 '# comment' line"),
                    ("---\nname: default-worker\n\nmodel: haiku\n---\nbody\n",
                     "sess-PINT", "blank line inside the block"),
                    ("---\nname: default-worker\n  # indented note\n"
                     "model: haiku\n---\nbody\n",
                     "sess-PINU", "indented '# comment' line"),
                ):
                    fm_write(body)
                    rc, dec, r = pin_run("haejwo:default-worker", sid, root=fm_root)
                    check(f"M1 {name} -> still parsed, pin check DENIES",
                          dec == "deny" and "defaults to 'haiku'" in r, r)
                    check(f"M1 record: {name} -> tier_pin_check 'deny'",
                          (pin_rec(sid) or {}).get("tier_pin_check") == "deny",
                          str(pin_rec(sid)))

                # N1: a comment between an empty `model:` and its indented
                # continuation must NOT hide the continuation — still a guess,
                # still fail open. Pin == the continuation value, so only the
                # record distinguishes a skip from a (wrong) read.
                pin_cfg({"default_worker": "haiku"})
                fm_write("---\nname: default-worker\nmodel:\n# comment\n"
                         "  haiku\n---\nbody\n")
                rc, dec, r = pin_run("haejwo:default-worker", "sess-PINV", root=fm_root)
                check("N1 comment-separated continuation -> allow (fail open)",
                      rc == 0 and dec != "deny", r)
                check("N1 record: comment-separated continuation -> "
                      "'skip:frontmatter-unreadable'",
                      (pin_rec("sess-PINV") or {}).get("tier_pin_check")
                      == "skip:frontmatter-unreadable", str(pin_rec("sess-PINV")))
            finally:
                shutil.rmtree(fm_root, ignore_errors=True)

            rc, out = run("delegation_gate.py", task_payload(5, sid="sess-PINC"), pin_data)
            check("B1 non-string subagent_type -> allow (no check)",
                  rc == 0 and decision(out) != "deny", str(out))
            check("B1 record: non-string subagent_type -> 'pass:not-a-tier'",
                  (pin_rec("sess-PINC") or {}).get("tier_pin_check") == "pass:not-a-tier",
                  str(pin_rec("sess-PINC")))

            # K3: a non-string model means we cannot tell what the host asked
            # for at all -> fail open (the generic check keeps its own contract).
            pin_cfg({"default_worker": "opus"})
            for raw_model, sid in ((42, "sess-PINK"), ({}, "sess-PINL")):
                pl = task_payload("haejwo:default-worker", sid=sid)
                pl["tool_input"]["model"] = raw_model
                rc, out = run("delegation_gate.py", pl, pin_data)
                check(f"K3 non-string model ({raw_model!r}) -> allow rc0",
                      rc == 0 and decision(out) != "deny", str(out))
                check(f"K3 record: non-string model ({raw_model!r}) -> 'skip:fail-open'",
                      (pin_rec(sid) or {}).get("tier_pin_check") == "skip:fail-open",
                      str(pin_rec(sid)))

            # K4: valid JSON that is not an object is an unusable config
            for raw_cfg, sid in (("[]", "sess-PINM"), ("null", "sess-PINN"),
                                 ("42", "sess-PINO")):
                pin_cfg(None, raw=raw_cfg)
                rc, dec, r = pin_run("haejwo:default-worker", sid)
                check(f"K4 config.json {raw_cfg} -> allow (unusable config)",
                      rc == 0 and dec != "deny", r)
                check(f"K4 record: config.json {raw_cfg} -> 'skip:config-malformed'",
                      (pin_rec(sid) or {}).get("tier_pin_check")
                      == "skip:config-malformed", str(pin_rec(sid)))
            pin_cfg({"default_worker": "opus"})

            rc, dec, r = pin_run("general-purpose", "sess-PIND")
            check("B1 generic-agent deny still takes precedence (not the pin wording)",
                  dec == "deny" and "generic agent" in r and "silent-downgrade" not in r, r)

            # Codex host: spawn_agent never hits this hook's Task|Agent matcher,
            # so the tier check must never deny there. Recorded as
            # 'pass:not-a-tier' (the documented "check did not apply" bucket).
            pin_codex = os.path.join(pin_data, ".codex", "plugins", "data", "haejwo")
            os.makedirs(pin_codex, exist_ok=True)
            with open(os.path.join(pin_codex, "config.json"), "w") as f:
                json.dump({"configured": True, "models": {"default_worker": "opus"}}, f)
            rc, dec, r = pin_run("haejwo:default-worker", "sess-PINE", data_dir=pin_codex)
            check("B1 codex host: tier check never denies", rc == 0 and dec != "deny", r)
            check("B1 codex host record: 'pass:not-a-tier'",
                  (pin_rec("sess-PINE", data_dir=pin_codex) or {}).get("tier_pin_check")
                  == "pass:not-a-tier", str(pin_rec("sess-PINE", data_dir=pin_codex)))
        finally:
            shutil.rmtree(pin_data, ignore_errors=True)

        print("== hooks.json hook-target existence ==")
        hooks_path = os.path.join(PLUGIN, "hooks", "hooks.json")
        hooks_text = open(hooks_path, encoding="utf-8").read()
        script_names = sorted(set(re.findall(
            r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([^\"\s]+\.py)", hooks_text)))
        check("hooks.json references at least one script", len(script_names) > 0)
        for name in script_names:
            check(f"hook target exists: scripts/{name}",
                  os.path.isfile(os.path.join(SCRIPTS, name)))

        print("== runner stub tests (codex_consult.sh / claude_consult.sh) ==")
        runner_tmp = tempfile.mkdtemp(prefix="hjw-test-runners-")
        try:
            def make_repo(name):
                d = os.path.join(runner_tmp, name)
                os.makedirs(d, exist_ok=True)
                subprocess.run(["git", "init", "-q", d], check=True, capture_output=True)
                for k, v in (("user.email", "stub@example.invalid"), ("user.name", "stub")):
                    subprocess.run(["git", "-C", d, "config", k, v],
                                   check=True, capture_output=True)
                return d

            repo_dir = make_repo("repo")

            codex_script = os.path.join(SCRIPTS, "codex_consult.sh")
            claude_script = os.path.join(SCRIPTS, "claude_consult.sh")
            CONTRACT_HEAD = "REVIEWER CONTRACT: analyze and reply only."

            # Stub codex/claude executable. Captures argv (one arg per line) +
            # full stdin per invocation; the `--version` probe is NOT captured
            # (the runners probe it before the real call). With `--json` in
            # argv it prints a JSONL event stream to stdout and honors
            # `-o <file>` for the reply (real codex contract); without it the
            # reply goes to stdout (claude). All behavior is scripted through
            # env vars, globally (STUB_RC) or per call index (STUB_RC_2) — the
            # per-call form is what makes the model-fallback and
            # second-attempt-fails fixtures expressible.
            STUB_BODY = r'''#!/usr/bin/env bash
set +u
if [ "${1:-}" = "--version" ]; then
  echo "stub-version 0.0.0"
  exit 0
fi
CAP="__CAP__"
mkdir -p "$CAP"
idx_file="$CAP/_idx"
if [ -f "$idx_file" ]; then idx=$(( $(cat "$idx_file") + 1 )); else idx=1; fi
echo "$idx" > "$idx_file"
printf '%s\n' "$@" > "$CAP/call_${idx}.argv"
cat > "$CAP/call_${idx}.stdin"
cd_dir=""
out=""; model=""; json=0; prev=""
for a in "$@"; do
  case "$prev" in
    -o|--output-last-message) out="$a" ;;
    -m|--model) model="$a" ;;
    --cd) cd_dir="$a" ;;
  esac
  if [ "$a" = "--json" ]; then json=1; fi
  prev="$a"
done
# real codex chdirs into --cd; the stub must too, or the recorded cwd (and any
# relative STUB_TOUCH_FILE) would describe the caller instead of the reviewer.
if [ -n "$cd_dir" ]; then cd "$cd_dir" || exit 97; fi
pwd -P > "$CAP/call_${idx}.cwd"
pick() {
  local per="${1}_${idx}"
  local v="${!per}"
  if [ -z "$v" ]; then local g="$1"; v="${!g}"; fi
  printf '%s' "$v"
}
ev="$(pick STUB_EVENTS_FILE)"
se="$(pick STUB_STDERR_FILE)"
rc="$(pick STUB_RC)"; [ -z "$rc" ] && rc=0
noout="$(pick STUB_NO_OUT)"
touchf="$(pick STUB_TOUCH_FILE)"
gitc="$(pick STUB_GIT_COMMIT)"
# STUB_MANIFEST records what the reviewer ACTUALLY sees in its own cwd — the
# only way to assert on a snapshot that is deleted before the runner returns.
mf="$(pick STUB_MANIFEST)"
if [ -n "$mf" ]; then
  : > "$mf"
  for p in $(pick STUB_MANIFEST_PATHS); do
    if [ -L "$p" ]; then printf 'link %s %s\n' "$p" "$(readlink "$p")" >> "$mf"
    elif [ -f "$p" ]; then printf 'file %s %s\n' "$p" \
      "$(python3 -c 'import hashlib,sys;print(hashlib.sha1(open(sys.argv[1],"rb").read()).hexdigest())' "$p")" >> "$mf"
    elif [ -e "$p" ]; then printf 'other %s -\n' "$p" >> "$mf"
    else printf 'absent %s -\n' "$p" >> "$mf"; fi
  done
fi
if [ -n "$se" ] && [ -f "$se" ]; then cat "$se" >&2; fi
if [ -n "$touchf" ]; then printf 'mutated by stub call %s\n' "$idx" > "$touchf"; fi
if [ -n "$gitc" ]; then git commit --allow-empty -q -m "stub commit $idx" >/dev/null 2>&1; fi
# STUB_RELEASE_FILE: written the moment the reviewer starts, so a test can
# wait for "capture is over, the run has begun" instead of racing a sleep.
rel="$(pick STUB_RELEASE_FILE)"
if [ -n "$rel" ]; then printf 'released\n' > "$rel"; fi
spawn="$(pick STUB_SPAWN_PIDFILE)"
if [ -n "$spawn" ]; then
  python3 -c 'import time; time.sleep(60)' &
  echo "$!" > "$spawn"
fi
slp="$(pick STUB_SLEEP)"
if [ -n "$slp" ]; then python3 -c 'import sys,time; time.sleep(float(sys.argv[1]))' "$slp"; fi
reply="STUB-REPLY-OK-${idx}"
if [ "$json" = 1 ]; then
  if [ -n "$ev" ] && [ -f "$ev" ]; then
    cat "$ev"
  else
    echo '{"type":"thread.started","thread_id":"stub-thread"}'
    echo '{"type":"turn.started"}'
    echo '{"type":"item.started","item":{"type":"agent_message"}}'
    printf '{"type":"item.completed","item":{"type":"agent_message","text":"%s"}}\n' "$reply"
    echo '{"type":"turn.completed"}'
  fi
fi
if [ -z "$noout" ]; then
  if [ -n "$out" ]; then
    printf '%s\n' "$reply" > "$out"
  else
    printf '%s\n' "$reply"
  fi
fi
# Wall-clock end of this invocation: lets a test prove an event in the host
# happened WHILE the reviewer was still running, not after it returned.
python3 -c 'import sys,time;open(sys.argv[1],"w").write(repr(time.time()))' "$CAP/call_${idx}.end"
exit "$rc"
'''

            def make_stub(bin_dir, name, capture_dir):
                os.makedirs(bin_dir, exist_ok=True)
                path = os.path.join(bin_dir, name)
                body = STUB_BODY.replace("__CAP__", capture_dir)
                with open(path, "w") as f:
                    f.write(body)
                os.chmod(path, 0o755)
                return path

            def make_git_stub(bin_dir, fail_from_call=1):
                """git that fails `status` from the Nth call on (everything
                else passes through to the real git) — the only honest way to
                exercise 'change detection unavailable' without a real
                broken repo."""
                os.makedirs(bin_dir, exist_ok=True)
                real_git = shutil.which("git")
                counter = os.path.join(bin_dir, "_gitcount")
                path = os.path.join(bin_dir, "git")
                with open(path, "w") as f:
                    f.write(
                        "#!/usr/bin/env bash\n"
                        "for a in \"$@\"; do\n"
                        "  if [ \"$a\" = \"status\" ]; then\n"
                        f"    c=1; if [ -f '{counter}' ]; then c=$(( $(cat '{counter}') + 1 )); fi\n"
                        f"    echo \"$c\" > '{counter}'\n"
                        f"    if [ \"$c\" -ge {fail_from_call} ]; then\n"
                        "      echo \"fatal: stubbed git status failure\" >&2\n"
                        "      exit 1\n"
                        "    fi\n"
                        "  fi\n"
                        "done\n"
                        f"exec {real_git} \"$@\"\n")
                os.chmod(path, 0o755)
                return path

            def run_script(script, args, extra_env, stdin_data="", cwd=None):
                """Runner invocation with a HERMETIC env: every runner-read
                env var is popped unless the fixture sets it, and
                CLAUDE_PLUGIN_DATA points at a fresh empty dir so the
                derived (~/.claude/...) config path is never consulted by
                accident. Pass CLAUDE_PLUGIN_DATA=None to opt out (the
                derived-path fixtures need the real resolution)."""
                env = dict(os.environ)
                for var in ("CODEX_MODEL", "CODEX_EFFORT", "CODEX_SANDBOX",
                            "CLAUDE_MODEL", "CODEX_ALLOW_MARKERS",
                            "CODEX_TIMEOUT", "CLAUDE_TIMEOUT",
                            "CLAUDE_PLUGIN_DATA"):
                    env.pop(var, None)
                if "CLAUDE_PLUGIN_DATA" not in extra_env:
                    env["CLAUDE_PLUGIN_DATA"] = tempfile.mkdtemp(dir=runner_tmp, prefix="nocfg-")
                env.update({k: v for k, v in extra_env.items() if v is not None})
                p = subprocess.run(
                    ["bash", script] + args, input=stdin_data,
                    capture_output=True, text=True, timeout=60,
                    cwd=cwd or repo_dir, env=env,
                )
                return p.returncode, p.stdout, p.stderr

            def read_calls(capture_dir):
                calls = []
                i = 1
                while os.path.isfile(os.path.join(capture_dir, f"call_{i}.stdin")):
                    argv_path = os.path.join(capture_dir, f"call_{i}.argv")
                    argv_lines = (open(argv_path).read().splitlines()
                                  if os.path.isfile(argv_path) else [])
                    stdin_text = open(os.path.join(capture_dir, f"call_{i}.stdin")).read()
                    calls.append((argv_lines, stdin_text))
                    i += 1
                return calls

            def write_file(path, text):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(text)
                return path

            def events_file(name, lines):
                return write_file(os.path.join(runner_tmp, name),
                                  "".join(l + "\n" for l in lines))

            def brief_file(name, text="Test brief body.\n"):
                return write_file(os.path.join(runner_tmp, name), text)

            def cfg_dir_with(name, payload):
                d = os.path.join(runner_tmp, name)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, "config.json"), "w") as f:
                    json.dump(payload, f)
                return d

            OK_EVENTS = [
                '{"type":"thread.started","thread_id":"stub"}',
                '{"type":"turn.started"}',
                '{"type":"item.started","item":{"type":"agent_message"}}',
                '{"type":"item.completed","item":{"type":"agent_message","text":"fine"}}',
                '{"type":"turn.completed"}',
            ]

            def codex_run(label, env_extra=None, args=None, cwd=None, stub_repo=None):
                """One hermetic codex run: fresh stub, fresh capture dir."""
                bin_dir = os.path.join(runner_tmp, f"bin-{label}")
                cap = os.path.join(runner_tmp, f"cap-{label}")
                make_stub(bin_dir, "codex", cap)
                env = {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}
                env.update(env_extra or {})
                brief = brief_file(f"brief-{label}.md")
                rc, out, err = run_script(codex_script, (args or []) + [brief], env, cwd=cwd)
                return rc, out, err, read_calls(cap)

            def check_resume_removed(slug, title, script, cli, args, cwd=None):
                """--resume was REMOVED in 2.13 (implicit latest-thread
                selection misroutes under concurrent sessions). Parsing
                refuses it: exit 2 with the removal message, no CLI call, and
                not one artifact on disk — the refusal happens before any
                snapshot capture or preflight work, so there is nothing to
                clean up."""
                bin_dir = os.path.join(runner_tmp, f"bin-{slug}")
                cap = os.path.join(runner_tmp, f"cap-{slug}")
                make_stub(bin_dir, cli, cap)
                brief_dir = os.path.join(runner_tmp, f"briefdir-{slug}")
                brief = brief_file(f"briefdir-{slug}/brief.md")
                rc, out, err = run_script(
                    script, list(args) + [brief],
                    {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")},
                    cwd=cwd)
                combined = out + err
                check(f"{title}: exit 2", rc == 2, f"rc={rc} out={out} err={err}")
                check(f"{title}: message names the 2.13 removal and a NEW session",
                      "removed in 2.13" in combined
                      and "start a NEW session with a self-contained brief" in combined,
                      combined)
                check(f"{title}: the CLI is never invoked",
                      len(read_calls(cap)) == 0, read_calls(cap))
                left = sorted(os.listdir(brief_dir))
                check(f"{title}: no reply/log/events artifact is written",
                      left == ["brief.md"], left)

            # ---- (p) existing runner contracts: contract prepend on top
            # of captured stdin — file brief, stdin brief (both runners).
            # Sandbox precedence, --mode implement removal and
            # --disallowedTools continue further down under the same label. ----
            for label, script in (("codex", codex_script), ("claude", claude_script)):
                bin_dir = os.path.join(runner_tmp, f"bin-{label}-contract")
                os.makedirs(bin_dir, exist_ok=True)
                env = {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}

                brief_path = brief_file(f"{label}-brief-file.md")

                cap = os.path.join(runner_tmp, f"cap-{label}-file")
                make_stub(bin_dir, label, cap)
                rc, out, err = run_script(script, [brief_path], env)
                calls = read_calls(cap)
                check(f"{label} file-brief: run succeeds", rc == 0, f"rc={rc} out={out} err={err}")
                check(f"{label} file-brief: contract at top of captured stdin",
                      bool(calls) and calls[0][1].startswith(CONTRACT_HEAD), calls[:1])

                cap2 = os.path.join(runner_tmp, f"cap-{label}-stdin")
                make_stub(bin_dir, label, cap2)
                rc, out, err = run_script(script, ["-"], env, stdin_data="Stdin brief body.\n")
                calls2 = read_calls(cap2)
                check(f"{label} stdin-brief: run succeeds", rc == 0, f"rc={rc} out={out} err={err}")
                check(f"{label} stdin-brief: contract at top of captured stdin",
                      bool(calls2) and calls2[0][1].startswith(CONTRACT_HEAD), calls2[:1])

                check_resume_removed(f"{label}-resume", f"{label} --resume",
                                     script, label, ["--resume"])

            # ---- (a/b) nested content is NEVER inspected: a reply that
            # QUOTES an error string, or a command whose aggregated output
            # contains a tracing line, must not fail its own run ----
            ev = events_file("ev-quoted-marker.jsonl", [
                '{"type":"thread.started","thread_id":"stub"}',
                '{"type":"item.completed","item":{"type":"agent_message",'
                '"text":"The sandbox helper failed and ERROR codex_core appeared in their log."}}',
                '{"type":"turn.completed"}',
            ])
            rc, out, err, calls = codex_run("quoted-marker", {"STUB_EVENTS_FILE": ev})
            check("events: agent_message quoting an error marker -> success",
                  rc == 0, f"rc={rc} err={err}")

            ev = events_file("ev-nested-trace.jsonl", [
                '{"type":"thread.started","thread_id":"stub"}',
                '{"type":"item.completed","item":{"type":"command_execution",'
                '"aggregated_output":"2026-01-02T03:04:05.123456Z  ERROR codex_core::exec: boom"}}',
                '{"type":"turn.completed"}',
            ])
            rc, out, err, calls = codex_run("nested-trace", {"STUB_EVENTS_FILE": ev})
            check("events: command_execution aggregated_output with a tracing line -> success",
                  rc == 0, f"rc={rc} err={err}")

            # ---- (c/d) TOP-LEVEL failure events fail the run even at rc=0
            # with a non-empty reply (the silent-failure case) ----
            ev = events_file("ev-turn-failed.jsonl", [
                '{"type":"thread.started","thread_id":"stub"}',
                '{"type":"turn.failed","error":{"message":"stream disconnected before completion"}}',
            ])
            rc, out, err, calls = codex_run("turn-failed", {"STUB_EVENTS_FILE": ev})
            check("events: top-level turn.failed (rc=0, reply present) -> failure",
                  rc != 0, f"rc={rc} out={out}")
            check("events: turn.failed failure names type and message",
                  "codex reported turn.failed" in err
                  and "stream disconnected before completion" in err, err)

            ev = events_file("ev-error-event.jsonl", [
                '{"type":"thread.started","thread_id":"stub"}',
                '{"type":"error","message":"usage limit reached"}',
            ])
            rc, out, err, calls = codex_run("error-event", {"STUB_EVENTS_FILE": ev})
            check("events: top-level error event -> failure naming it",
                  rc != 0 and "codex reported error" in err
                  and "usage limit reached" in err, f"rc={rc} err={err}")

            # ---- (e/f) stderr tracing scan is ANCHORED at column 0 ----
            trace_stderr = write_file(
                os.path.join(runner_tmp, "stderr-anchored.txt"),
                "2026-01-02T03:04:05.123456Z  ERROR codex_core::exec: sandbox helper failed\n")
            ev = events_file("ev-ok.jsonl", OK_EVENTS)
            rc, out, err, calls = codex_run("trace-anchored", {
                "STUB_EVENTS_FILE": ev, "STUB_STDERR_FILE": trace_stderr})
            check("stderr scan: anchored tracing line -> failure printing the line",
                  rc != 0 and "codex tracing error:" in err
                  and "codex_core::exec" in err, f"rc={rc} err={err}")

            rc, out, err, calls = codex_run("trace-allow-markers", {
                "STUB_EVENTS_FILE": ev, "STUB_STDERR_FILE": trace_stderr,
                "CODEX_ALLOW_MARKERS": "1"})
            check("stderr scan: CODEX_ALLOW_MARKERS=1 disables only this scan -> success",
                  rc == 0, f"rc={rc} err={err}")

            # K8: an anchored tracing line that reports a HOOK BLOCK of a
            # reviewer command is the gate working on the reviewer's side —
            # keep the reply, note it once (observed live 2026-09-14).
            hook_block_stderr = write_file(
                os.path.join(runner_tmp, "stderr-hook-block.txt"),
                "2026-01-02T03:04:05.123456Z  ERROR codex_core::exec: "
                "Command blocked by PreToolUse hook: [haejwo gate] ...\n")
            rc, out, err, calls = codex_run("trace-hook-block", {
                "STUB_EVENTS_FILE": ev, "STUB_STDERR_FILE": hook_block_stderr})
            check("stderr scan: hook-blocked reviewer command -> success + one note",
                  rc == 0
                  and "note: a reviewer command was blocked by a hook (see log)" in out,
                  f"rc={rc} out={out} err={err}")

            mixed_stderr = write_file(
                os.path.join(runner_tmp, "stderr-hook-block-mixed.txt"),
                "2026-01-02T03:04:05.123456Z  ERROR codex_core::exec: "
                "Command blocked by PreToolUse hook: [haejwo gate] ...\n"
                "2026-01-02T03:04:06.123456Z  ERROR codex_core::exec: sandbox helper failed\n")
            rc, out, err, calls = codex_run("trace-hook-block-mixed", {
                "STUB_EVENTS_FILE": ev, "STUB_STDERR_FILE": mixed_stderr})
            # the failure must name the REAL error, not the hook block (the
            # hook-block line legitimately appears in the printed log tail)
            fail_line = next((l for l in err.splitlines()
                              if "codex tracing error:" in l), "")
            check("stderr scan: a REAL tracing error alongside a hook block still fails",
                  rc != 0 and "sandbox helper failed" in fail_line
                  and "PreToolUse hook" not in fail_line, f"rc={rc} err={err}")

            prose_stderr = write_file(
                os.path.join(runner_tmp, "stderr-prose.txt"),
                "    2026-01-02T03:04:05.123456Z  ERROR codex_core::exec: indented, not a trace\n"
                "the reviewer wrote: 2026-01-02T03:04:05Z  ERROR codex_core happened yesterday\n")
            rc, out, err, calls = codex_run("trace-prose", {
                "STUB_EVENTS_FILE": ev, "STUB_STDERR_FILE": prose_stderr})
            check("stderr scan: indented / in-prose tracing text -> success (anchor holds)",
                  rc == 0, f"rc={rc} err={err}")

            # ---- (g) malformed event lines are counted and ignored; zero
            # valid events at rc=0 is an unverifiable run ----
            ev = events_file("ev-malformed-mixed.jsonl", [
                'not json at all',
                '[1, 2, 3]',
                '{"type":"thread.started","thread_id":"stub"}',
                '{"type":"item.completed","item":{"type":"agent_message","text":"fine"}}',
                '{"type":"turn.completed"}',
                '{"broken": ',
            ])
            rc, out, err, calls = codex_run("malformed-mixed", {"STUB_EVENTS_FILE": ev})
            check("events: malformed lines mixed with valid events -> success",
                  rc == 0, f"rc={rc} err={err}")

            ev = events_file("ev-all-malformed.jsonl", ['not json', 'still not json'])
            rc, out, err, calls = codex_run("no-events", {"STUB_EVENTS_FILE": ev})
            check("events: rc=0 with zero valid top-level events -> 'no event stream' failure",
                  rc != 0 and "no event stream" in err, f"rc={rc} err={err}")

            ev = events_file("ev-anonymous-objects.jsonl", ['{}', '{}', '{}'])
            rc, out, err, calls = codex_run("anonymous-events", {"STUB_EVENTS_FILE": ev})
            check("events: a stream of anonymous {} objects counts as ABSENT",
                  rc != 0 and "no event stream" in err, f"rc={rc} err={err}")

            # ---- (h) model/effort: env > config > runner default ----
            model_cfg = cfg_dir_with("plugin-data-model", {"codex": {"model": "cfg-model"}})

            def argv_value(argv, flag):
                for i, a in enumerate(argv):
                    if a == flag and i + 1 < len(argv):
                        return argv[i + 1]
                return None

            rc, out, err, calls = codex_run("model-env-wins", {
                "CLAUDE_PLUGIN_DATA": model_cfg, "CODEX_MODEL": "env-model"})
            check("model precedence: env CODEX_MODEL wins over config codex.model",
                  bool(calls) and argv_value(calls[0][0], "-m") == "env-model", calls[:1])
            check("model disclosure: env source labeled on the result line",
                  "model=env-model (env)" in out, out)

            rc, out, err, calls = codex_run("model-config", {"CLAUDE_PLUGIN_DATA": model_cfg})
            check("model precedence: config codex.model used when env unset",
                  bool(calls) and argv_value(calls[0][0], "-m") == "cfg-model", calls[:1])
            check("model disclosure: (config) source on the result line",
                  "model=cfg-model (config)" in out, out)

            rc, out, err, calls = codex_run("model-env-empty", {
                "CLAUDE_PLUGIN_DATA": model_cfg, "CODEX_MODEL": ""})
            check("model precedence: empty CODEX_MODEL counts as UNSET (config wins)",
                  bool(calls) and argv_value(calls[0][0], "-m") == "cfg-model", calls[:1])

            rc, out, err, calls = codex_run("model-none", {})
            check("model disclosure: nothing selected -> cli-default (identity unverified)",
                  "model=cli-default (identity unverified)" in out and "-m" not in (calls[0][0] if calls else []),
                  f"out={out} argv={calls[:1]}")

            effort_cfg = cfg_dir_with("plugin-data-effort", {"codex": {"effort": "medium"}})
            rc, out, err, calls = codex_run("effort-config", {"CLAUDE_PLUGIN_DATA": effort_cfg})
            check("effort precedence: config codex.effort used when env unset",
                  'model_reasoning_effort="medium"' in (calls[0][0] if calls else []), calls[:1])
            check("effort disclosure: (config) source on the result line",
                  "effort=medium (config)" in out, out)

            bad_effort_cfg = cfg_dir_with("plugin-data-effort-bad",
                                          {"codex": {"effort": "insane"}})
            rc, out, err, calls = codex_run("effort-config-invalid",
                                            {"CLAUDE_PLUGIN_DATA": bad_effort_cfg})
            check("effort: invalid CONFIG value -> one note + runner-default high, run continues",
                  rc == 0 and "note: config codex.effort 'insane' invalid; using runner-default high" in err
                  and 'model_reasoning_effort="high"' in (calls[0][0] if calls else [])
                  and "effort=high (runner-default)" in out,
                  f"rc={rc} err={err} out={out}")

            rc, out, err, calls = codex_run("effort-env-invalid", {"CODEX_EFFORT": "insane"})
            check("effort: invalid ENV value -> exit 2 naming all four valid values",
                  rc == 2 and all(v in (out + err) for v in ("low", "medium", "high", "xhigh")),
                  f"rc={rc} err={err}")
            check("effort: invalid ENV value -> codex never invoked", len(calls) == 0, calls)

            # ---- (F8) non-string config values are noted, never used ----
            junk_cfg = cfg_dir_with("plugin-data-junk", {"codex": {
                "model": 123, "effort": [], "fallback_model": None}})
            rc, out, err, calls = codex_run("config-nonstring", {"CLAUDE_PLUGIN_DATA": junk_cfg})
            check("config: non-string values -> one note per key, defaults used",
                  rc == 0
                  and "note: config codex.model ignored (not a string)" in err
                  and "note: config codex.effort ignored (not a string)" in err
                  and "note: config codex.fallback_model ignored (not a string)" in err
                  and "model=cli-default (identity unverified)" in out
                  and "effort=high (runner-default)" in out,
                  f"rc={rc} err={err} out={out}")

            # ---- (F9) the `codex` config block describes the HOST's reviewer:
            # on a codex host that reviewer is Claude, so the codex runner must
            # ignore model/effort/fallback_model there. ----
            codex_host_cfg = cfg_dir_with(os.path.join(".codex", "plugins", "data", "haejwo-haejwo"),
                                          {"codex": {"model": "claude-reviewer-model"}})
            rc, out, err, calls = codex_run("model-codex-host", {"CLAUDE_PLUGIN_DATA": codex_host_cfg})
            check("host-relative config: codex runner ignores codex.model under a /.codex/ path",
                  "model=cli-default (identity unverified)" in out
                  and argv_value(calls[0][0] if calls else [], "-m") is None,
                  f"out={out} argv={calls[:1]}")

            # ---- (i) model-unavailable fallback: pre-execution only, once ----
            fb_cfg = cfg_dir_with("plugin-data-fallback",
                                  {"codex": {"fallback_model": "fb-model"}})
            pre_exec_fail = events_file("ev-unknown-model-preexec.jsonl", [
                '{"type":"thread.started","thread_id":"stub"}',
                '{"type":"turn.failed","error":{"message":"unknown model: totally-fake-model"}}',
            ])
            bin_dir = os.path.join(runner_tmp, "bin-fallback")
            cap = os.path.join(runner_tmp, "cap-fallback")
            make_stub(bin_dir, "codex", cap)
            fb_out = os.path.join(runner_tmp, "fallback-reply.md")
            rc, out, err = run_script(codex_script, ["-o", fb_out, brief_file("fb-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "CLAUDE_PLUGIN_DATA": fb_cfg,
                "CODEX_MODEL": "totally-fake-model",
                "STUB_EVENTS_FILE_1": pre_exec_fail,
                "STUB_RC_1": "1", "STUB_NO_OUT_1": "1",
            })
            calls = read_calls(cap)
            check("fallback: exactly two codex invocations (fail then retry)",
                  len(calls) == 2, calls)
            check("fallback: run eventually succeeds", rc == 0, f"rc={rc} err={err}")
            if len(calls) == 2:
                check("fallback: retry uses config codex.fallback_model",
                      argv_value(calls[1][0], "-m") == "fb-model", calls[1][0])
                check("fallback: both calls carry the reviewer contract",
                      calls[0][1].startswith(CONTRACT_HEAD)
                      and calls[1][1].startswith(CONTRACT_HEAD), calls)
            note = "note: requested model 'totally-fake-model' unavailable; reviewed by 'fb-model' (config fallback)"
            check("fallback: reply note names BOTH requested and effective model (stdout)",
                  note in out, out)
            check("fallback: the same note is persisted into the reply FILE",
                  os.path.isfile(fb_out) and note in open(fb_out).read(),
                  open(fb_out).read() if os.path.isfile(fb_out) else "missing")

            post_exec_fail = events_file("ev-unknown-model-postexec.jsonl", [
                '{"type":"thread.started","thread_id":"stub"}',
                '{"type":"item.started","item":{"type":"command_execution"}}',
                '{"type":"turn.failed","error":{"message":"model not available: totally-fake-model"}}',
            ])
            bin_dir = os.path.join(runner_tmp, "bin-fallback-postexec")
            cap = os.path.join(runner_tmp, "cap-fallback-postexec")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("fb-post-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "CLAUDE_PLUGIN_DATA": fb_cfg,
                "CODEX_MODEL": "totally-fake-model",
                "STUB_EVENTS_FILE": post_exec_fail,
                "STUB_RC": "1", "STUB_NO_OUT": "1",
            })
            calls = read_calls(cap)
            check("fallback: unknown-model AFTER item.started -> no retry (execution had begun)",
                  len(calls) == 1, calls)
            check("fallback: post-execution unknown-model failure is reported, not retried",
                  rc != 0, f"rc={rc} err={err}")

            bin_dir = os.path.join(runner_tmp, "bin-fallback-twice")
            cap = os.path.join(runner_tmp, "cap-fallback-twice")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("fb-twice-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "CLAUDE_PLUGIN_DATA": fb_cfg,
                "CODEX_MODEL": "totally-fake-model",
                "STUB_EVENTS_FILE": pre_exec_fail,
                "STUB_RC": "1", "STUB_NO_OUT": "1",
            })
            calls = read_calls(cap)
            check("fallback: retry also fails -> failure, still exactly two invocations",
                  rc != 0 and len(calls) == 2, f"rc={rc} calls={len(calls)}")

            contaminated = events_file("ev-unknown-model-contaminated.jsonl", [
                '{"type":"thread.started","thread_id":"stub"}',
                'not json — a dropped line could have carried item.started',
                '{"type":"turn.failed","error":{"message":"unknown model: totally-fake-model"}}',
            ])
            bin_dir = os.path.join(runner_tmp, "bin-fallback-contaminated")
            cap = os.path.join(runner_tmp, "cap-fallback-contaminated")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("fb-contaminated-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "CLAUDE_PLUGIN_DATA": fb_cfg,
                "CODEX_MODEL": "totally-fake-model",
                "STUB_EVENTS_FILE": contaminated,
                "STUB_RC": "1", "STUB_NO_OUT": "1",
            })
            check("fallback: a malformed line BEFORE the failure event inhibits the retry",
                  rc != 0 and len(read_calls(cap)) == 1, f"rc={rc} calls={len(read_calls(cap))}")

            # the failed FIRST attempt's events and stderr traces must never
            # fail a successful retry (each attempt is classified on its own).
            bin_dir = os.path.join(runner_tmp, "bin-fallback-clean-retry")
            cap = os.path.join(runner_tmp, "cap-fallback-clean-retry")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("fb-clean-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "CLAUDE_PLUGIN_DATA": fb_cfg,
                "CODEX_MODEL": "totally-fake-model",
                "STUB_EVENTS_FILE_1": pre_exec_fail,
                "STUB_STDERR_FILE_1": trace_stderr,
                "STUB_RC_1": "1", "STUB_NO_OUT_1": "1",
            })
            check("fallback: attempt 1 events/traces do not fail a clean retry",
                  rc == 0 and len(read_calls(cap)) == 2, f"rc={rc} err={err}")

            # ---- (k/l/m/n) change detection ----
            head_repo = make_repo("repo-head")
            bin_dir = os.path.join(runner_tmp, "bin-head")
            cap = os.path.join(runner_tmp, "cap-head")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("head-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "STUB_GIT_COMMIT": "1"}, cwd=head_repo)
            check("change detection: HEAD moved during the run -> failure listing HEAD",
                  rc != 0 and "repository changed during the run" in err and "HEAD" in err,
                  f"rc={rc} err={err}")
            check("change detection: failure states attribution is unknown, never auto-revert",
                  "attribution unknown" in err and "revert" not in err.lower(), err)

            untracked_repo = make_repo("repo-untracked")
            spaced = os.path.join(untracked_repo, "note file.txt")
            write_file(spaced, "original\n")
            bin_dir = os.path.join(runner_tmp, "bin-untracked")
            cap = os.path.join(runner_tmp, "cap-untracked")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("untracked-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "STUB_TOUCH_FILE": spaced}, cwd=untracked_repo)
            check("change detection: untracked file CONTENT change -> failure",
                  rc != 0 and "repository changed during the run" in err, f"rc={rc} err={err}")
            check("change detection: a path containing a space is named intact",
                  "note file.txt" in err, err)

            artifact_repo = make_repo("repo-artifacts")
            bin_dir = os.path.join(runner_tmp, "bin-artifacts")
            cap = os.path.join(runner_tmp, "cap-artifacts")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(
                codex_script,
                ["-o", os.path.join(artifact_repo, "reply.md"), brief_file("artifact-brief.md")],
                {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")},
                cwd=artifact_repo)
            check("change detection: runner-owned artifacts inside the repo do NOT trigger it",
                  rc == 0, f"rc={rc} err={err}")

            big_repo = make_repo("repo-big")
            for i in range(2001):
                write_file(os.path.join(big_repo, "untracked", f"f{i:05d}.txt"), "x\n")
            bin_dir = os.path.join(runner_tmp, "bin-big")
            cap = os.path.join(runner_tmp, "cap-big")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("big-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}, cwd=big_repo)
            check("change detection: >2000 untracked files -> success with partial coverage noted",
                  rc == 0 and "(untracked coverage partial: >2000 files)" in out,
                  f"rc={rc} out={out} err={err}")

            # ---- (F2) NUL-safe serialization: filenames may contain newlines ----
            nl_repo = make_repo("repo-newline-names")
            first = os.path.join(nl_repo, "a\nsame")
            second = os.path.join(nl_repo, "b\nsame")
            write_file(first, "one\n")
            write_file(second, "two\n")
            bin_dir = os.path.join(runner_tmp, "bin-newline")
            cap = os.path.join(runner_tmp, "cap-newline")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("newline-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "STUB_TOUCH_FILE": first}, cwd=nl_repo)
            check("change detection: a newline in a filename is detected, not conflated",
                  rc != 0 and repr("a\nsame")[1:-1] in err
                  and repr("b\nsame")[1:-1] not in err, f"rc={rc} err={err}")

            # ---- (F4) artifact exclusion completeness ----
            tracked_out_repo = make_repo("repo-tracked-out")
            tracked_out = os.path.join(tracked_out_repo, "tracked-out.md")
            write_file(tracked_out, "committed content\n")
            subprocess.run(["git", "-C", tracked_out_repo, "add", "tracked-out.md"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", tracked_out_repo, "commit", "-q", "-m", "seed"],
                           check=True, capture_output=True)
            bin_dir = os.path.join(runner_tmp, "bin-tracked-out")
            cap = os.path.join(runner_tmp, "cap-tracked-out")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, ["-o", tracked_out,
                                                     brief_file("tracked-out-brief.md")],
                                      {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")},
                                      cwd=tracked_out_repo)
            check("change detection: -o over a TRACKED file does not self-trip the gate",
                  rc == 0, f"rc={rc} err={err}")

            newdir_repo = make_repo("repo-newdir")
            newdir = os.path.join(newdir_repo, "fresh")
            os.makedirs(newdir, exist_ok=True)  # empty: invisible to git
            bin_dir = os.path.join(runner_tmp, "bin-newdir")
            cap = os.path.join(runner_tmp, "cap-newdir")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, ["-o", os.path.join(newdir, "reply.md"),
                                                     brief_file("newdir-brief.md")],
                                      {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")},
                                      cwd=newdir_repo)
            check("change detection: artifacts in a new directory are excluded (-uall, not a collapsed dir)",
                  rc == 0, f"rc={rc} err={err}")

            # ---- (F5) per-path fingerprints: an already-dirty tracked file
            # edited AGAIN keeps its status but must still be named ----
            dirty_repo = make_repo("repo-dirty")
            dirty = os.path.join(dirty_repo, "tracked-dirty.md")
            write_file(dirty, "committed\n")
            subprocess.run(["git", "-C", dirty_repo, "add", "tracked-dirty.md"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", dirty_repo, "commit", "-q", "-m", "seed"],
                           check=True, capture_output=True)
            write_file(dirty, "dirty before the run\n")
            bin_dir = os.path.join(runner_tmp, "bin-dirty")
            cap = os.path.join(runner_tmp, "cap-dirty")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("dirty-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "STUB_TOUCH_FILE": dirty}, cwd=dirty_repo)
            check("change detection: pre-existing dirt edited again -> the PATH is named",
                  rc != 0 and "tracked-dirty.md" in err, f"rc={rc} err={err}")

            # ---- (F6/F1) a snapshot that cannot be taken or read is NEVER
            # 'no change': before-failure costs no reviewer run, after-failure
            # fails the run ----
            fail_before_repo = make_repo("repo-detect-before")
            bin_dir = os.path.join(runner_tmp, "bin-detect-before")
            cap = os.path.join(runner_tmp, "cap-detect-before")
            make_stub(bin_dir, "codex", cap)
            make_git_stub(bin_dir, fail_from_call=1)
            rc, out, err = run_script(codex_script, [brief_file("detect-before-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}, cwd=fail_before_repo)
            check("change detection: BEFORE-snapshot failure -> run fails unavailable",
                  rc != 0 and "change detection unavailable" in err, f"rc={rc} err={err}")
            check("change detection: BEFORE-snapshot failure -> codex was NEVER invoked (no paid run)",
                  len(read_calls(cap)) == 0, read_calls(cap))
            preflight_log = os.path.join(runner_tmp, "detect-before-brief.reply.log")
            log_text = open(preflight_log).read() if os.path.isfile(preflight_log) else ""
            check("change detection: preflight failure is persisted into $LOG, not just stderr",
                  "stubbed git status failure" in log_text and "stubbed git status failure" in err,
                  f"log={log_text!r}")

            fail_after_repo = make_repo("repo-detect-after")
            bin_dir = os.path.join(runner_tmp, "bin-detect-after")
            cap = os.path.join(runner_tmp, "cap-detect-after")
            make_stub(bin_dir, "codex", cap)
            make_git_stub(bin_dir, fail_from_call=2)
            rc, out, err = run_script(codex_script, [brief_file("detect-after-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}, cwd=fail_after_repo)
            check("change detection: AFTER-snapshot failure -> failure, never 'no change'",
                  rc != 0 and "change detection unavailable" in err
                  and len(read_calls(cap)) == 1, f"rc={rc} calls={len(read_calls(cap))} err={err}")

            # ---- claude-runner parity for both detection signals ----
            claude_head_repo = make_repo("repo-claude-head")
            bin_dir = os.path.join(runner_tmp, "bin-claude-head")
            cap = os.path.join(runner_tmp, "cap-claude-head")
            make_stub(bin_dir, "claude", cap)
            rc, out, err = run_script(claude_script, [brief_file("claude-head-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "STUB_GIT_COMMIT": "1"}, cwd=claude_head_repo)
            check("claude change detection: HEAD moved during the run -> failure listing HEAD",
                  rc != 0 and "repository changed during the run" in err and "HEAD" in err,
                  f"rc={rc} err={err}")

            claude_unt_repo = make_repo("repo-claude-untracked")
            claude_spaced = os.path.join(claude_unt_repo, "note file.txt")
            write_file(claude_spaced, "original\n")
            bin_dir = os.path.join(runner_tmp, "bin-claude-untracked")
            cap = os.path.join(runner_tmp, "cap-claude-untracked")
            make_stub(bin_dir, "claude", cap)
            rc, out, err = run_script(claude_script, [brief_file("claude-untracked-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "STUB_TOUCH_FILE": claude_spaced}, cwd=claude_unt_repo)
            check("claude change detection: untracked content change -> failure naming the path",
                  rc != 0 and "note file.txt" in err, f"rc={rc} err={err}")

            # ---- (G5a) the wall clock must hold with NO `timeout` binary ----
            def make_minimal_bin(name, capture_dir, stub_name="codex"):
                """A PATH with everything the runner needs EXCEPT timeout(1)."""
                d = os.path.join(runner_tmp, name)
                os.makedirs(d, exist_ok=True)
                for tool in ("python3", "git", "bash", "sh", "sed", "awk", "grep",
                             "cat", "head", "tail", "rm", "mv", "mkdir", "mktemp",
                             "date", "realpath", "chmod", "env"):
                    src = shutil.which(tool)
                    dst = os.path.join(d, tool)
                    if src and not os.path.exists(dst):
                        os.symlink(src, dst)
                make_stub(d, stub_name, capture_dir)
                return d

            notimeout_repo = make_repo("repo-no-timeout")
            cap = os.path.join(runner_tmp, "cap-no-timeout")
            min_bin = make_minimal_bin("bin-no-timeout", cap)
            check("no-timeout PATH: the `timeout` binary really is absent",
                  shutil.which("timeout", path=min_bin) is None, min_bin)
            rc, out, err = run_script(codex_script, [brief_file("no-timeout-brief.md")],
                                      {"PATH": min_bin}, cwd=notimeout_repo)
            check("no-timeout PATH: a clean consult still succeeds",
                  rc == 0, f"rc={rc} err={err}")

            cap = os.path.join(runner_tmp, "cap-no-timeout-slow")
            min_bin2 = make_minimal_bin("bin-no-timeout-slow", cap)
            pidfile = os.path.join(runner_tmp, "slow-descendant.pid")
            t0 = time.time()
            rc, out, err = run_script(codex_script, [brief_file("no-timeout-slow-brief.md")],
                                      {"PATH": min_bin2, "CODEX_TIMEOUT": "2",
                                       "STUB_SLEEP": "5",
                                       "STUB_SPAWN_PIDFILE": pidfile}, cwd=notimeout_repo)
            elapsed = time.time() - t0
            check("no-timeout PATH: an over-running reviewer is cut off at rc=124, on time",
                  rc == 124 and "timed out" in err and elapsed < 2 + 3,
                  f"rc={rc} elapsed={elapsed:.1f}s err={err}")

            # the killed reviewer must not leave descendants RUNNING. A
            # zombie counts as dead: the process is killed, its reaper (PID 1
            # in a container) may simply not have collected it.
            def pid_running(pid):
                try:
                    with open("/proc/%d/stat" % pid) as f:
                        state = f.read().rsplit(")", 1)[1].split()[0]
                    return state != "Z"
                except FileNotFoundError:
                    pass
                except Exception:
                    return False
                try:
                    os.kill(pid, 0)
                    return True
                except Exception:
                    return False

            # the fixture only PROVES anything if the stub actually spawned a
            # descendant and recorded its pid — assert that before polling.
            leaked = None
            check("no-timeout PATH: stub recorded a descendant pid file",
                  os.path.isfile(pidfile), pidfile)
            if os.path.isfile(pidfile):
                raw_pid = open(pidfile).read().strip()
                try:
                    leaked = int(raw_pid or 0)
                except Exception:
                    leaked = 0
                check("no-timeout PATH: descendant pid file holds a positive integer",
                      leaked > 0, repr(raw_pid))
                if leaked <= 0:
                    leaked = None  # never poll pid 0 (that signals the group)
            if leaked is not None:
                deadline = time.time() + 3
                while time.time() < deadline:
                    if not pid_running(leaked):
                        leaked = None
                        break
                    time.sleep(0.1)
            check("timeout kills the whole process group (no surviving descendant)",
                  leaked is None, f"pid still running: {leaked}")

            # ---- (G5b) an unwritable TMPDIR must fail closed, unpaid ----
            ro_tmp = os.path.join(runner_tmp, "readonly-tmp")
            os.makedirs(ro_tmp, exist_ok=True)
            os.chmod(ro_tmp, 0o555)
            try:
                probe = os.path.join(ro_tmp, ".probe")
                with open(probe, "w"):
                    pass
                os.remove(probe)
                ro_tmp_enforced = False
            except Exception:
                ro_tmp_enforced = True
            if not ro_tmp_enforced:
                # running as root: mode bits do not bind. An absent TMPDIR
                # fails mktemp for every user, so the same fail-closed path is
                # still exercised.
                print("  note: mode-555 TMPDIR stayed writable (root) — using an absent TMPDIR instead")
                ro_tmp = os.path.join(runner_tmp, "tmpdir-that-does-not-exist")
            bin_dir = os.path.join(runner_tmp, "bin-ro-tmp")
            cap = os.path.join(runner_tmp, "cap-ro-tmp")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("ro-tmp-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "TMPDIR": ro_tmp})
            check("unusable TMPDIR: exits 4 (temp-file guard) and codex is never invoked",
                  rc == 4 and len(read_calls(cap)) == 0,
                  f"rc={rc} calls={len(read_calls(cap))} err={err}")

            # ---- (G5d/G3) a repo directory name ending in a space ----
            space_repo = make_repo("repo-trailing-space ")
            spaced_file = os.path.join(space_repo, "watched.txt")
            write_file(spaced_file, "original\n")
            bin_dir = os.path.join(runner_tmp, "bin-trailing-space")
            cap = os.path.join(runner_tmp, "cap-trailing-space")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("trailing-space-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "STUB_TOUCH_FILE": spaced_file}, cwd=space_repo)
            check("repo root ending in a space: the change is still detected with the right path",
                  rc != 0 and "repository changed during the run" in err
                  and "watched.txt" in err, f"rc={rc} err={err}")

            # ---- (G5e/G4) files this gate cannot read are counted, not skipped ----
            unreadable_repo = make_repo("repo-unreadable")
            secret = os.path.join(unreadable_repo, "secret.txt")
            write_file(secret, "unreadable\n")
            os.chmod(secret, 0o000)
            try:
                with open(secret, "rb"):
                    pass
                still_readable = True
            except Exception:
                still_readable = False
            if still_readable:
                print("  skipped (root) unreadable-file fixture: mode 000 stayed readable")
            else:
                bin_dir = os.path.join(runner_tmp, "bin-unreadable")
                cap = os.path.join(runner_tmp, "cap-unreadable")
                make_stub(bin_dir, "codex", cap)
                rc, out, err = run_script(codex_script, [brief_file("unreadable-brief.md")], {
                    "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")},
                    cwd=unreadable_repo)
                check("unreadable file: run succeeds with the count disclosed on the result line",
                      rc == 0 and "(some files unreadable: 1)" in out,
                      f"rc={rc} out={out} err={err}")
            os.chmod(secret, 0o644)

            # ---- (o) claude runner reads codex.model as its default ----
            bin_dir = os.path.join(runner_tmp, "bin-claude-model")
            cap = os.path.join(runner_tmp, "cap-claude-model")
            make_stub(bin_dir, "claude", cap)
            claude_host_cfg = cfg_dir_with(
                os.path.join(".codex", "plugins", "data", "haejwo-haejwo-claude"),
                {"codex": {"model": "cfg-model"}})
            rc, out, err = run_script(claude_script, [brief_file("claude-model-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "CLAUDE_PLUGIN_DATA": claude_host_cfg})
            calls = read_calls(cap)
            check("claude: config codex.model used when CLAUDE_MODEL unset (codex-host config)",
                  bool(calls) and argv_value(calls[0][0], "--model") == "cfg-model", calls[:1])
            check("claude: (config) source disclosed on the result line",
                  rc == 0 and "model=cfg-model (config)" in out, f"rc={rc} out={out}")

            # live-smoke regression: a CLAUDE-host config's codex.model names
            # the OTHER vendor's reviewer — claude must never be launched with it.
            bin_dir = os.path.join(runner_tmp, "bin-claude-wrong-host")
            cap = os.path.join(runner_tmp, "cap-claude-wrong-host")
            make_stub(bin_dir, "claude", cap)
            astra_cfg = cfg_dir_with("plugin-data-claude-host", {"codex": {"model": "gpt-6-astra"}})
            rc, out, err = run_script(claude_script, [brief_file("claude-wrong-host-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "CLAUDE_PLUGIN_DATA": astra_cfg})
            calls = read_calls(cap)
            check("claude: a non-/.codex/ config's codex.model is IGNORED (no --model passed)",
                  rc == 0 and bool(calls) and "--model" not in calls[0][0]
                  and "model=cli-default (identity unverified)" in out,
                  f"rc={rc} argv={calls[:1]} out={out}")

            # ---- --mode implement removed (both runners, both flag forms) ----
            for label, script in (("codex", codex_script), ("claude", claude_script)):
                for flag_args in (["--mode", "implement"], ["--mode=implement"]):
                    rc, out, err = run_script(script, flag_args, {})
                    combined = out + err
                    check(f"{label} {' '.join(flag_args)}: exit 2", rc == 2, f"rc={rc}")
                    check(f"{label} {' '.join(flag_args)}: dedicated removal message",
                          "removed in 2.10" in combined, combined)

            # ---- codex sandbox precedence: env > config consult_sandbox > read-only ----
            sandbox_bin_dir = os.path.join(runner_tmp, "bin-codex-sandbox")
            os.makedirs(sandbox_bin_dir, exist_ok=True)

            def sandbox_used(env_extra, label):
                cap = os.path.join(runner_tmp, f"cap-sandbox-{label}")
                make_stub(sandbox_bin_dir, "codex", cap)
                env = {"PATH": sandbox_bin_dir + os.pathsep + os.environ.get("PATH", "")}
                env.update(env_extra)
                brief_path = brief_file(f"sandbox-brief-{label}.md",
                                        "Sandbox precedence test brief.\n")
                run_script(codex_script, [brief_path], env)
                calls = read_calls(cap)
                argv = calls[0][0] if calls else []
                return argv_value(argv, "-s")

            sbx = sandbox_used({"CODEX_SANDBOX": "workspace-write"}, "env-wins")
            check("sandbox precedence: env CODEX_SANDBOX wins", sbx == "workspace-write", sbx)

            cfg_dir = cfg_dir_with("plugin-data-danger",
                                   {"codex": {"consult_sandbox": "danger-full-access"}})
            sbx = sandbox_used({"CLAUDE_PLUGIN_DATA": cfg_dir}, "config-danger")
            check("sandbox precedence: config consult_sandbox used when env unset",
                  sbx == "danger-full-access", sbx)

            cfg_dir2 = cfg_dir_with("plugin-data-invalid",
                                    {"codex": {"consult_sandbox": "yolo"}})
            sbx = sandbox_used({"CLAUDE_PLUGIN_DATA": cfg_dir2}, "config-invalid")
            check("sandbox precedence: invalid config value falls back to read-only",
                  sbx == "read-only", sbx)

            cfg_dir3 = os.path.join(runner_tmp, "plugin-data-malformed")
            os.makedirs(cfg_dir3, exist_ok=True)
            with open(os.path.join(cfg_dir3, "config.json"), "w") as f:
                f.write("{ not valid json !!!")
            sbx = sandbox_used({"CLAUDE_PLUGIN_DATA": cfg_dir3}, "config-malformed")
            check("sandbox precedence: malformed config JSON falls back to read-only",
                  sbx == "read-only", sbx)

            # malformed SHAPE (codex is not an object) is "no config" too —
            # model/effort/fallback_model must not crash or leak a value.
            cfg_dir4 = cfg_dir_with("plugin-data-shape", {"codex": "not-an-object"})
            sbx = sandbox_used({"CLAUDE_PLUGIN_DATA": cfg_dir4}, "config-shape")
            check("config shape: codex not an object -> read-only, no crash",
                  sbx == "read-only", sbx)

            # regression: CLAUDE_PLUGIN_DATA set (non-empty) but its
            # config.json is MISSING -> must NOT fall back to the derived
            # (~/.claude/.../config.json) path, even when that derived path
            # holds a dangerous value (a stale danger-full-access setting
            # elsewhere must never get resurrected).
            stale_home = os.path.join(runner_tmp, "stale-home")
            stale_cfg_dir = os.path.join(stale_home, ".claude", "plugins", "data", "haejwo-haejwo")
            os.makedirs(stale_cfg_dir, exist_ok=True)
            with open(os.path.join(stale_cfg_dir, "config.json"), "w") as f:
                json.dump({"codex": {"consult_sandbox": "danger-full-access"}}, f)
            empty_data_dir = os.path.join(runner_tmp, "plugin-data-empty")
            os.makedirs(empty_data_dir, exist_ok=True)  # no config.json inside
            sbx = sandbox_used({"HOME": stale_home, "CLAUDE_PLUGIN_DATA": empty_data_dir},
                                "env-set-file-missing")
            check("sandbox precedence: CLAUDE_PLUGIN_DATA set but config.json missing -> "
                  "read-only (NEVER falls back to the derived path / a stale config there)",
                  sbx == "read-only", sbx)

            # empty $HOME in the derived-path branch (CLAUDE_PLUGIN_DATA unset
            # — passed as None so the hermetic default does not mask it) must
            # not crash under `set -u` and must resolve to "no config".
            sbx = sandbox_used({"HOME": "", "CLAUDE_PLUGIN_DATA": None}, "home-empty")
            check("sandbox precedence: empty $HOME in derived-path branch -> read-only, no crash",
                  sbx == "read-only", sbx)

            # explicit CODEX_SANDBOX allowlist: invalid ENV value is caller
            # input and deserves a loud error, not a silent downgrade.
            bad_env_bin_dir = os.path.join(runner_tmp, "bin-codex-badenv")
            os.makedirs(bad_env_bin_dir, exist_ok=True)
            cap = os.path.join(runner_tmp, "cap-codex-badenv")
            make_stub(bad_env_bin_dir, "codex", cap)
            env = {"PATH": bad_env_bin_dir + os.pathsep + os.environ.get("PATH", ""),
                   "CODEX_SANDBOX": "yolo"}
            rc, out, err = run_script(codex_script, [brief_file("badenv-brief.md")], env)
            combined = out + err
            check("invalid CODEX_SANDBOX env: exit 2 (loud error, not silent downgrade)",
                  rc == 2, f"rc={rc}")
            check("invalid CODEX_SANDBOX env: message names all three valid values",
                  "read-only" in combined and "workspace-write" in combined
                  and "danger-full-access" in combined, combined)
            check("invalid CODEX_SANDBOX env: codex never invoked (fails before the call)",
                  len(read_calls(cap)) == 0, read_calls(cap))

            # ---- claude runner: --disallowedTools present on every run ----
            disallow_bin_dir = os.path.join(runner_tmp, "bin-claude-disallow")
            os.makedirs(disallow_bin_dir, exist_ok=True)
            env = {"PATH": disallow_bin_dir + os.pathsep + os.environ.get("PATH", "")}
            brief_path = brief_file("claude-disallow-brief.md")

            cap = os.path.join(runner_tmp, "cap-claude-disallow-normal")
            make_stub(disallow_bin_dir, "claude", cap)
            rc, out, err = run_script(claude_script, [brief_path], env)
            calls = read_calls(cap)
            argv = calls[0][0] if calls else []
            check("claude normal run: --disallowedTools flag present",
                  "--disallowedTools" in argv, argv)
            check("claude normal run: Edit,Write,NotebookEdit value present",
                  "Edit,Write,NotebookEdit" in argv, argv)

            # ---- (B8) --snapshot: the reviewer reads a detached worktree
            # snapshot instead of the live working copy. Every fixture below
            # is hermetic (stub CLIs, stub git/mktemp where a failure must be
            # forced) and asserts on what the reviewer ACTUALLY saw — the
            # snapshot itself is gone by the time the runner returns. The
            # whole matrix runs against BOTH runners: the capture code is
            # duplicated by design, so it must be proven twice. ----
            def worktree_count(repo):
                """-1 when git could not answer: an unreadable listing must
                FAIL a 'nothing leaked' check, never silently satisfy it."""
                p = subprocess.run(["git", "-C", repo, "worktree", "list"],
                                   capture_output=True, text=True)
                if p.returncode != 0:
                    return -1
                return len([l for l in p.stdout.splitlines() if l.strip()])

            def read_cwd(capture_dir, n=1):
                p = os.path.join(capture_dir, f"call_{n}.cwd")
                return open(p).read().strip() if os.path.isfile(p) else ""

            def read_end(capture_dir, n=1):
                p = os.path.join(capture_dir, f"call_{n}.end")
                try:
                    return float(open(p).read().strip())
                except Exception:
                    return None

            def snap_log_path(log_path):
                """The runner records the snapshot it built into $LOG."""
                head = "# ---- snapshot: "
                for line in (open(log_path).read().splitlines()
                             if os.path.isfile(log_path) else []):
                    if line.startswith(head) and line.endswith(" ----"):
                        return line[len(head):-len(" ----")]
                return ""

            def sha1_text(text):
                return hashlib.sha1(text.encode()).hexdigest()

            def hermetic_env(extra_env):
                """Same env discipline as run_script, for the fixtures that
                need their own process handling (combined stream, signals)."""
                env = dict(os.environ)
                for var in ("CODEX_MODEL", "CODEX_EFFORT", "CODEX_SANDBOX",
                            "CLAUDE_MODEL", "CODEX_ALLOW_MARKERS",
                            "CODEX_TIMEOUT", "CLAUDE_TIMEOUT",
                            "CLAUDE_PLUGIN_DATA"):
                    env.pop(var, None)
                if "CLAUDE_PLUGIN_DATA" not in extra_env:
                    env["CLAUDE_PLUGIN_DATA"] = tempfile.mkdtemp(dir=runner_tmp,
                                                                 prefix="nocfg-")
                env.update({k: v for k, v in extra_env.items() if v is not None})
                return env

            def make_repo_committed(name):
                """A repo with one commit — `make_repo` leaves HEAD unborn,
                which --snapshot refuses by design."""
                d = make_repo(name)
                write_file(os.path.join(d, "seed.txt"), "seed\n")
                subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
                subprocess.run(["git", "-C", d, "commit", "-q", "-m", "seed"],
                               check=True, capture_output=True)
                return d

            def dirty_snap_repo(name):
                """Every working-tree shape the snapshot must reproduce —
                staged, unstaged, binary, deletion, untracked, symlink — plus
                an ignored file it must NOT carry."""
                d = make_repo(name)
                write_file(os.path.join(d, "staged.txt"), "committed\n")
                write_file(os.path.join(d, "unstaged.txt"), "committed\n")
                with open(os.path.join(d, "binary.dat"), "wb") as f:
                    f.write(b"\x00\x01committed\x02")
                write_file(os.path.join(d, "deleted.txt"), "committed\n")
                write_file(os.path.join(d, ".gitignore"), "ignored.txt\n")
                subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
                subprocess.run(["git", "-C", d, "commit", "-q", "-m", "seed"],
                               check=True, capture_output=True)
                write_file(os.path.join(d, "staged.txt"), "staged change\n")
                subprocess.run(["git", "-C", d, "add", "staged.txt"], check=True,
                               capture_output=True)
                write_file(os.path.join(d, "unstaged.txt"), "unstaged change\n")
                with open(os.path.join(d, "binary.dat"), "wb") as f:
                    f.write(b"\x00\x01changed\x02\x03")
                os.remove(os.path.join(d, "deleted.txt"))
                write_file(os.path.join(d, "untracked.txt"), "untracked\n")
                os.symlink("unstaged.txt", os.path.join(d, "link.txt"))
                write_file(os.path.join(d, "ignored.txt"), "ignored\n")
                return d

            def gitlink_repo(name):
                d = make_repo_committed(name)
                sha = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"],
                                     capture_output=True, text=True).stdout.strip()
                subprocess.run(["git", "-C", d, "update-index", "--add",
                                "--cacheinfo", f"160000,{sha},nested"],
                               check=True, capture_output=True)
                return d

            def embedded_repo(name):
                d = make_repo_committed(name)
                subprocess.run(["git", "init", "-q", os.path.join(d, "vendor")],
                               check=True, capture_output=True)
                return d

            def conflict_repo(name):
                d = make_repo_committed(name)
                base = subprocess.run(["git", "-C", d, "rev-parse", "--abbrev-ref", "HEAD"],
                                      capture_output=True, text=True).stdout.strip()
                write_file(os.path.join(d, "c.txt"), "base\n")
                subprocess.run(["git", "-C", d, "add", "c.txt"], check=True, capture_output=True)
                subprocess.run(["git", "-C", d, "commit", "-q", "-m", "base"],
                               check=True, capture_output=True)
                subprocess.run(["git", "-C", d, "checkout", "-q", "-b", "side"],
                               check=True, capture_output=True)
                write_file(os.path.join(d, "c.txt"), "side\n")
                subprocess.run(["git", "-C", d, "commit", "-q", "-am", "side"],
                               check=True, capture_output=True)
                subprocess.run(["git", "-C", d, "checkout", "-q", base],
                               check=True, capture_output=True)
                write_file(os.path.join(d, "c.txt"), "main\n")
                subprocess.run(["git", "-C", d, "commit", "-q", "-am", "main"],
                               check=True, capture_output=True)
                subprocess.run(["git", "-C", d, "merge", "side"], capture_output=True)
                return d

            def big_untracked_repo(name):
                d = make_repo_committed(name)
                for i in range(2001):
                    write_file(os.path.join(d, "untracked", f"f{i:05d}.txt"), "x\n")
                return d

            def make_snapshot_git_stub(bin_dir, mode, nth=1, touch=""):
                """git wrapper for the capture fixtures: fail `apply`, fail
                `worktree remove`, mutate a tracked file just before the Nth
                tracked-patch diff, or mutate an untracked file just before
                the Nth untracked listing — everything else passes through to
                the real git."""
                os.makedirs(bin_dir, exist_ok=True)
                real_git = shutil.which("git")
                counter = os.path.join(bin_dir, "_snapcount")
                path = os.path.join(bin_dir, "git")
                body = ["#!/usr/bin/env bash"]
                if mode == "apply":
                    body += ['for a in "$@"; do',
                             '  if [ "$a" = "apply" ]; then',
                             '    echo "fatal: stubbed git apply failure" >&2; exit 1',
                             '  fi',
                             'done']
                elif mode == "worktree-list":
                    # Only the LISTING fails: cleanup must then assume the
                    # snapshot is registered and still remove it through git.
                    body += ['wt=0; lst=0',
                             'for a in "$@"; do',
                             '  [ "$a" = "worktree" ] && wt=1',
                             '  [ "$a" = "list" ] && lst=1',
                             'done',
                             'if [ "$wt" = 1 ] && [ "$lst" = 1 ]; then',
                             '  echo "fatal: stubbed worktree list failure" >&2; exit 1',
                             'fi']
                elif mode == "worktree-remove":
                    # `worktree list` still passes through: cleanup must be
                    # able to see that the snapshot IS registered.
                    body += ['wt=0; rmv=0',
                             'for a in "$@"; do',
                             '  [ "$a" = "worktree" ] && wt=1',
                             '  [ "$a" = "remove" ] && rmv=1',
                             'done',
                             'if [ "$wt" = 1 ] && [ "$rmv" = 1 ]; then',
                             '  echo "fatal: stubbed worktree remove failure" >&2; exit 1',
                             'fi']
                elif mode in ("drift", "drift-untracked"):
                    if mode == "drift":
                        # --binary AND --no-ext-diff identify the tracked-patch
                        # call only (the `git apply` replay carries --binary too).
                        cond = ('b=0; e=0\n'
                                'for a in "$@"; do\n'
                                '  [ "$a" = "--binary" ] && b=1\n'
                                '  [ "$a" = "--no-ext-diff" ] && e=1\n'
                                'done\n'
                                'hit=0; [ "$b" = 1 ] && [ "$e" = 1 ] && hit=1')
                    else:
                        # --others without --ignored = the untracked listing
                        # (preflight, capture, drift — in that order).
                        cond = ('o=0; g=0\n'
                                'for a in "$@"; do\n'
                                '  [ "$a" = "--others" ] && o=1\n'
                                '  [ "$a" = "--ignored" ] && g=1\n'
                                'done\n'
                                'hit=0; [ "$o" = 1 ] && [ "$g" = 0 ] && hit=1')
                    body += cond.split("\n")
                    body += ['if [ "$hit" = 1 ]; then',
                             f"  c=1; if [ -f '{counter}' ]; then c=$(( $(cat '{counter}') + 1 )); fi",
                             f"  echo \"$c\" > '{counter}'",
                             f"  if [ \"$c\" -eq {nth} ]; then",
                             f"    printf 'torn by a concurrent writer\\n' > '{touch}'",
                             '  fi',
                             'fi']
                body.append(f'exec {real_git} "$@"')
                with open(path, "w") as f:
                    f.write("\n".join(body) + "\n")
                os.chmod(path, 0o755)
                return path

            def make_mktemp_stub(bin_dir, rules):
                """mktemp wrapper: each (template-suffix, path) rule returns a
                FIXED directory for that template, everything else passes
                through. Pinning the snapshot path is the only way to reach
                the containment guard; pinning the EFFECTIVE-BRIEF path to a
                directory is the only way to make its write fail."""
                os.makedirs(bin_dir, exist_ok=True)
                real = shutil.which("mktemp")
                path = os.path.join(bin_dir, "mktemp")
                body = ["#!/usr/bin/env bash", 'for a in "$@"; do', '  case "$a" in']
                for suffix, fixed in rules:
                    body += [f'    *{suffix})',
                             f'      mkdir -p "{fixed}" || exit 1',
                             f'      printf "%s\\n" "{fixed}"; exit 0 ;;']
                body += ['  esac', 'done', f'exec {real} "$@"']
                with open(path, "w") as f:
                    f.write("\n".join(body) + "\n")
                os.chmod(path, 0o755)
                return path

            def extract_capture_helper(script_path):
                """The runner embeds its capture helper as a python heredoc.
                Pulling that exact source out lets a fixture run it in
                isolation and drive a race the shell cannot reach."""
                text = open(script_path, encoding="utf-8").read()
                m = re.search(r"snapshot_build\(\) \{.*?<<'PY'\n(.*?)\nPY\n\}",
                              text, re.S)
                return m.group(1) if m else ""

            RACE_DRIVER = '''import runpy, shutil, sys

helper, args = sys.argv[1], sys.argv[2:]
_real = shutil.copy2


def racing_copy2(src, dst, follow_symlinks=True):
    # A writer wins the race with the reader: the file changes BEFORE its
    # bytes are copied, so the destination holds content the pre-copy source
    # hash never covered. That is exactly the window the source/destination
    # comparison exists to close.
    with open(src, "a") as fh:
        fh.write("racing writer\\n")
    return _real(src, dst, follow_symlinks=follow_symlinks)


shutil.copy2 = racing_copy2
sys.argv = [helper] + args
runpy.run_path(helper, run_name="__main__")
'''

            # Both runners, same capture contract, so both get the same matrix.
            SNAP_RUNNERS = (("codex", codex_script, "codex", "=== Codex reply"),
                            ("claude", claude_script, "claude", "=== Claude reply"))

            for who, snap_script, snap_cli, reply_head in SNAP_RUNNERS:
                def snap_run(label, repo, args=None, env_extra=None, bin_dir=None,
                             _s=snap_script, _c=snap_cli):
                    """One hermetic --snapshot run: fresh stub, fresh capture
                    dir, explicit -o so the fixture knows where $LOG lands."""
                    bin_dir = bin_dir or os.path.join(runner_tmp, f"bin-{label}")
                    cap = os.path.join(runner_tmp, f"cap-{label}")
                    make_stub(bin_dir, _c, cap)
                    out_path = os.path.join(runner_tmp, f"{label}.reply.md")
                    env = {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}
                    env.update(env_extra or {})
                    brief = brief_file(f"brief-{label}.md")
                    rc, out, err = run_script(
                        _s, ["--snapshot", "-o", out_path] + (args or []) + [brief],
                        env, cwd=repo)
                    log_path = os.path.join(runner_tmp, f"{label}.reply.log")
                    return {"rc": rc, "out": out, "err": err, "cap": cap,
                            "bin": bin_dir, "calls": read_calls(cap),
                            "log": log_path, "snap": snap_log_path(log_path)}

                # (i) every working-tree shape reaches the snapshot; the
                # ignored file does not; the reviewer's cwd IS the snapshot.
                shapes_repo = dirty_snap_repo(f"repo-snap-shapes-{who}")
                shapes_wt = worktree_count(shapes_repo)
                shapes_manifest = os.path.join(runner_tmp, f"snap-shapes-{who}.manifest")
                r = snap_run(f"snap-shapes-{who}", shapes_repo, env_extra={
                    "STUB_MANIFEST": shapes_manifest,
                    "STUB_MANIFEST_PATHS": ("staged.txt unstaged.txt binary.dat deleted.txt "
                                            "untracked.txt link.txt ignored.txt")})
                check(f"{who} snapshot: a dirty repository is captured and the run succeeds",
                      r["rc"] == 0, f"rc={r['rc']} err={r['err']}")
                seen = {}
                for line in (open(shapes_manifest).read().splitlines()
                             if os.path.isfile(shapes_manifest) else []):
                    kind, name, rest = (line.split(" ", 2) + ["", ""])[:3]
                    seen[name] = (kind, rest)
                cwd_seen = read_cwd(r["cap"])
                check(f"{who} snapshot: the reviewer's cwd is the snapshot, never the original",
                      bool(cwd_seen) and bool(r["snap"])
                      and os.path.realpath(cwd_seen) == os.path.realpath(r["snap"])
                      and os.path.realpath(cwd_seen) != os.path.realpath(shapes_repo),
                      f"cwd={cwd_seen} snap={r['snap']} orig={shapes_repo}")
                check(f"{who} snapshot: a STAGED change is present in the snapshot",
                      seen.get("staged.txt") == ("file", sha1_text("staged change\n")),
                      seen.get("staged.txt"))
                check(f"{who} snapshot: an UNSTAGED change is present in the snapshot",
                      seen.get("unstaged.txt") == ("file", sha1_text("unstaged change\n")),
                      seen.get("unstaged.txt"))
                check(f"{who} snapshot: a BINARY change is present in the snapshot",
                      seen.get("binary.dat") == (
                          "file", hashlib.sha1(b"\x00\x01changed\x02\x03").hexdigest()),
                      seen.get("binary.dat"))
                check(f"{who} snapshot: a DELETED file is absent in the snapshot",
                      seen.get("deleted.txt", ("", ""))[0] == "absent", seen.get("deleted.txt"))
                check(f"{who} snapshot: an UNTRACKED file is copied into the snapshot",
                      seen.get("untracked.txt") == ("file", sha1_text("untracked\n")),
                      seen.get("untracked.txt"))
                check(f"{who} snapshot: a symlink is preserved AS A LINK, never followed",
                      seen.get("link.txt") == ("link", "unstaged.txt"), seen.get("link.txt"))
                check(f"{who} snapshot: an IGNORED file is omitted from the snapshot",
                      seen.get("ignored.txt", ("", ""))[0] == "absent", seen.get("ignored.txt"))
                check(f"{who} snapshot: start and result lines disclose sha7 + dirty counts",
                      "snapshot=" in r["err"] and "+dirty(4,2)" in r["err"]
                      and "+dirty(4,2)" in r["out"], f"out={r['out']} err={r['err']}")
                check(f"{who} snapshot: the log records SNAP, HEAD, interval, patch, digest",
                      all(s in open(r["log"]).read() for s in (
                          "# snapshot HEAD: ", "# snapshot capture: ", "# snapshot patch: ",
                          "# snapshot untracked: ", "# snapshot digest: sha256 ",
                          "# snapshot omissions: ignored_entries=1")), open(r["log"]).read())
                check(f"{who} snapshot: the effective brief carries contract THEN the note",
                      bool(r["calls"]) and r["calls"][0][1].startswith(CONTRACT_HEAD)
                      and "SNAPSHOT: this run executes in a detached snapshot of" in r["calls"][0][1]
                      and "read the snapshot, never the original" in r["calls"][0][1]
                      and "Do not write." in r["calls"][0][1], r["calls"][:1])
                check(f"{who} snapshot: the worktree list is restored after a SUCCESSFUL run",
                      shapes_wt > 0 and worktree_count(shapes_repo) == shapes_wt,
                      f"{worktree_count(shapes_repo)} != {shapes_wt}")

                # (ii) the host keeps working in the ORIGINAL while the
                # reviewer runs — the whole point of the snapshot. The stub
                # releases a marker when it starts and stamps its end time, so
                # the write is PROVEN to land mid-run, not after it.
                conc_repo = dirty_snap_repo(f"repo-snap-concurrent-{who}")
                conc_release = os.path.join(runner_tmp, f"snap-release-{who}")
                conc_target = os.path.join(conc_repo, "unstaged.txt")
                conc_written = []
                conc_observed = []

                def host_writes(_rel=conc_release, _t=conc_target, _w=conc_written,
                                _o=conc_observed):
                    # A timeout must NOT fall through into the write: if the
                    # marker never appeared the fixture proved nothing, so it
                    # leaves _o empty and the check below fails.
                    deadline = time.time() + 30
                    while time.time() < deadline and not os.path.isfile(_rel):
                        time.sleep(0.02)
                    if not os.path.isfile(_rel):
                        return
                    _o.append(True)
                    write_file(_t, "the host kept working\n")
                    _w.append(time.time())

                writer = threading.Thread(target=host_writes)
                writer.start()
                r = snap_run(f"snap-concurrent-{who}", conc_repo, env_extra={
                    "STUB_RELEASE_FILE": conc_release, "STUB_SLEEP": "1.5"})
                writer.join()
                stub_end = read_end(r["cap"])
                check(f"{who} snapshot: a host write into the ORIGINAL mid-run does not fail it",
                      r["rc"] == 0, f"rc={r['rc']} err={r['err']}")
                check(f"{who} snapshot: that write really landed in the original, mid-run",
                      bool(conc_observed) and bool(conc_written) and stub_end is not None
                      and stub_end > conc_written[0]
                      and open(conc_target).read() == "the host kept working\n",
                      f"stub_end={stub_end} wrote_at={conc_written} "
                      f"content={open(conc_target).read()!r}")

                # (iii) a write INSIDE the snapshot still trips the no-edit
                # gate, and the original never sees the file.
                wrote_repo = dirty_snap_repo(f"repo-snap-wrote-{who}")
                wrote_wt = worktree_count(wrote_repo)
                r = snap_run(f"snap-wrote-{who}", wrote_repo,
                             env_extra={"STUB_TOUCH_FILE": "reviewer-wrote-this.txt"})
                check(f"{who} snapshot: a reviewer write inside the snapshot fails by path",
                      r["rc"] != 0 and "repository changed during the run" in r["err"]
                      and "reviewer-wrote-this.txt" in r["err"],
                      f"rc={r['rc']} err={r['err']}")
                check(f"{who} snapshot: the ORIGINAL never receives the reviewer's file",
                      not os.path.exists(os.path.join(wrote_repo, "reviewer-wrote-this.txt")),
                      wrote_repo)
                check(f"{who} snapshot: the FAILURE line still discloses the snapshot",
                      "snapshot=" in r["err"], r["err"])
                check(f"{who} snapshot: the worktree list is restored after a FAILED run",
                      wrote_wt > 0 and worktree_count(wrote_repo) == wrote_wt,
                      f"{worktree_count(wrote_repo)} != {wrote_wt}")

                # (v) --snapshot --resume: --resume is rejected FIRST, with
                # the same 2.13 removal message — no combined-refusal wording
                # survives, and the snapshot is never captured.
                resume_repo = make_repo_committed(f"repo-snap-resume-{who}")
                resume_wt = worktree_count(resume_repo)
                check_resume_removed(f"snap-resume-{who}",
                                     f"{who} --snapshot --resume",
                                     snap_script, snap_cli,
                                     ["--snapshot", "--resume"], cwd=resume_repo)
                check(f"{who} --snapshot --resume: no snapshot worktree is created",
                      resume_wt > 0 and worktree_count(resume_repo) == resume_wt,
                      f"{worktree_count(resume_repo)} != {resume_wt}")

                # (vi) shapes a worktree snapshot cannot reproduce honestly
                # are refused up front, unpaid, each with ITS OWN reason.
                for kind, builder, reason in (
                        ("unborn HEAD", make_repo,
                         "snapshot unavailable: unborn HEAD — there is no commit to snapshot"),
                        ("gitlink", gitlink_repo,
                         "snapshot unavailable: gitlink (submodule) entry present"),
                        ("embedded repo", embedded_repo,
                         "snapshot unavailable: embedded untracked repository: vendor"),
                        ("merge conflict", conflict_repo,
                         "snapshot unavailable: unresolved merge conflicts in the working tree")):
                    slug = kind.split()[0]
                    r = snap_run(f"snap-refuse-{slug}-{who}", builder(f"repo-snap-{slug}-{who}"))
                    check(f"{who} snapshot: {kind} -> exit 2 with its own reason, zero calls",
                          r["rc"] == 2 and reason in r["err"] and len(r["calls"]) == 0,
                          f"rc={r['rc']} err={r['err']}")

                # (vii) a patch that will not replay is a HOLE in the
                # snapshot, not a degraded run: refuse before the paid call.
                apply_repo = dirty_snap_repo(f"repo-snap-apply-{who}")
                apply_wt = worktree_count(apply_repo)
                apply_bin = os.path.join(runner_tmp, f"bin-snap-apply-{who}")
                make_snapshot_git_stub(apply_bin, "apply")
                r = snap_run(f"snap-apply-{who}", apply_repo, bin_dir=apply_bin)
                check(f"{who} snapshot: a patch that cannot be replayed -> exit 2, zero calls",
                      r["rc"] == 2
                      and "snapshot unavailable: cannot replay the working-tree patch" in r["err"]
                      and len(r["calls"]) == 0, f"rc={r['rc']} err={r['err']}")
                check(f"{who} snapshot: a refusal after `worktree add` leaves no worktree behind",
                      apply_wt > 0 and worktree_count(apply_repo) == apply_wt,
                      f"{worktree_count(apply_repo)} != {apply_wt}")

                # (viii) untracked coverage above the cap is PARTIAL, and that
                # word reaches the reviewer's brief as well as the result line.
                big_repo = big_untracked_repo(f"repo-snap-big-{who}")
                r = snap_run(f"snap-big-{who}", big_repo)
                check(f"{who} snapshot: >2000 untracked files -> success, partial coverage",
                      r["rc"] == 0 and "+dirty(0,2000,partial)" in r["out"],
                      f"rc={r['rc']} out={r['out']} err={r['err']}")
                check(f"{who} snapshot: partial coverage is stated in the reviewer's own brief",
                      bool(r["calls"])
                      and "Untracked coverage is partial (first 2000 of 2001 eligible files)."
                      in r["calls"][0][1], r["calls"][:1])

                # (ix) capture is NOT atomic — a writer that tears it must
                # refuse, never ship a state that never existed. Both halves
                # of the drift check get their own fixture.
                drift_repo = dirty_snap_repo(f"repo-snap-drift-{who}")
                drift_bin = os.path.join(runner_tmp, f"bin-snap-drift-{who}")
                make_snapshot_git_stub(drift_bin, "drift", nth=2,
                                       touch=os.path.join(drift_repo, "unstaged.txt"))
                r = snap_run(f"snap-drift-{who}", drift_repo, bin_dir=drift_bin)
                check(f"{who} snapshot: a TRACKED file torn during capture -> refuse, zero calls",
                      r["rc"] == 2
                      and "snapshot unavailable: original changed during capture" in r["err"]
                      and len(r["calls"]) == 0, f"rc={r['rc']} err={r['err']}")

                udrift_repo = dirty_snap_repo(f"repo-snap-udrift-{who}")
                udrift_bin = os.path.join(runner_tmp, f"bin-snap-udrift-{who}")
                # 3rd untracked listing = the drift re-check (preflight,
                # capture, drift); the stub rewrites a COPIED untracked file
                # just before it, so the recomputed source fingerprint differs
                # from the one taken before the copy.
                make_snapshot_git_stub(udrift_bin, "drift-untracked", nth=3,
                                       touch=os.path.join(udrift_repo, "untracked.txt"))
                r = snap_run(f"snap-udrift-{who}", udrift_repo, bin_dir=udrift_bin)
                check(f"{who} snapshot: an UNTRACKED file torn during capture -> refuse",
                      r["rc"] == 2
                      and "snapshot unavailable: original changed during capture" in r["err"]
                      and len(r["calls"]) == 0, f"rc={r['rc']} err={r['err']}")

                # (x) a leftover worktree is never swallowed by a successful
                # review. ONE combined stream proves the ORDER: reply first,
                # cleanup failure after.
                leak_repo = dirty_snap_repo(f"repo-snap-leak-{who}")
                leak_bin = os.path.join(runner_tmp, f"bin-snap-leak-{who}")
                leak_cap = os.path.join(runner_tmp, f"cap-snap-leak-{who}")
                make_snapshot_git_stub(leak_bin, "worktree-remove")
                make_stub(leak_bin, snap_cli, leak_cap)
                leak_out = os.path.join(runner_tmp, f"snap-leak-{who}.reply.md")
                leak_stream = os.path.join(runner_tmp, f"snap-leak-{who}.combined")
                with open(leak_stream, "w") as fh:
                    lp = subprocess.run(
                        ["bash", snap_script, "--snapshot", "-o", leak_out,
                         brief_file(f"snap-leak-{who}-brief.md")],
                        input="", stdout=fh, stderr=subprocess.STDOUT, text=True,
                        timeout=60, cwd=leak_repo,
                        env=hermetic_env({"PATH": leak_bin + os.pathsep
                                          + os.environ.get("PATH", "")}))
                combined = open(leak_stream).read()
                check(f"{who} snapshot: cleanup failure -> non-zero exit though the review passed",
                      lp.returncode != 0, f"rc={lp.returncode} stream={combined}")
                check(f"{who} snapshot: the reply is printed BEFORE the cleanup failure",
                      reply_head in combined and "snapshot cleanup failed: " in combined
                      and combined.index(reply_head) < combined.index("snapshot cleanup failed: ")
                      and "STUB-REPLY-OK-1" in combined, combined)
                leaked_snap = ""
                for line in combined.splitlines():
                    if line.startswith("snapshot cleanup failed: "):
                        leaked_snap = line[len("snapshot cleanup failed: "):].strip()
                check(f"{who} snapshot: the leftover snapshot is named and still on disk",
                      bool(leaked_snap) and os.path.isdir(leaked_snap), leaked_snap)
                if leaked_snap and os.path.isdir(leaked_snap):
                    shutil.rmtree(leaked_snap, ignore_errors=True)

                # (xi) a caller artifact inside the snapshot would be deleted
                # with it — refuse. Unreachable through mktemp's randomness,
                # so the fixture pins the snapshot path with a stub.
                inside_repo = dirty_snap_repo(f"repo-snap-inside-{who}")
                inside_bin = os.path.join(runner_tmp, f"bin-snap-inside-{who}")
                pinned_snap = os.path.join(runner_tmp, f"pinned-snapshot-{who}")
                make_mktemp_stub(inside_bin, [("hjw_snap.XXXXXX", pinned_snap)])
                r = snap_run(f"snap-inside-{who}", inside_repo, bin_dir=inside_bin,
                             args=["-o", os.path.join(pinned_snap, "reply.md")])
                check(f"{who} snapshot: an output path inside the snapshot -> exit 2, zero calls",
                      r["rc"] == 2
                      and "snapshot unavailable: a caller path lies inside the snapshot"
                      in r["err"] and len(r["calls"]) == 0, f"rc={r['rc']} err={r['err']}")

                # (xii) an interrupted run must not leave a worktree behind
                # either: SIGTERM while the reviewer is mid-flight.
                term_repo = dirty_snap_repo(f"repo-snap-term-{who}")
                term_wt = worktree_count(term_repo)
                term_bin = os.path.join(runner_tmp, f"bin-snap-term-{who}")
                term_cap = os.path.join(runner_tmp, f"cap-snap-term-{who}")
                make_stub(term_bin, snap_cli, term_cap)
                term_release = os.path.join(runner_tmp, f"snap-term-release-{who}")
                term_proc = subprocess.Popen(
                    ["bash", snap_script, "--snapshot", "-o",
                     os.path.join(runner_tmp, f"snap-term-{who}.reply.md"),
                     brief_file(f"snap-term-{who}-brief.md")],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, cwd=term_repo,
                    env=hermetic_env({"PATH": term_bin + os.pathsep
                                      + os.environ.get("PATH", ""),
                                      "STUB_RELEASE_FILE": term_release,
                                      "STUB_SLEEP": "2"}))
                deadline = time.time() + 30
                while time.time() < deadline and not os.path.isfile(term_release):
                    time.sleep(0.02)
                released = os.path.isfile(term_release)
                term_proc.terminate()
                try:
                    term_proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    term_proc.kill()
                    term_proc.communicate()
                check(f"{who} snapshot: the SIGTERM fixture really interrupted a live run",
                      released, term_release)
                check(f"{who} snapshot: SIGTERM mid-run -> non-zero exit",
                      term_proc.returncode not in (0, None), term_proc.returncode)
                check(f"{who} snapshot: SIGTERM mid-run leaves NO worktree behind",
                      term_wt > 0 and worktree_count(term_repo) == term_wt,
                      f"{worktree_count(term_repo)} != {term_wt}")

                # (Z1a) a temp root carrying a TAB and a space: the porcelain
                # listing must still identify the snapshot, and git — not
                # rm -rf — must be what removes it.
                odd_tmp = os.path.join(runner_tmp, f"tmp dir\twith ws {who}")
                os.makedirs(odd_tmp, exist_ok=True)
                odd_repo = dirty_snap_repo(f"repo-snap-oddtmp-{who}")
                odd_wt = worktree_count(odd_repo)
                r = snap_run(f"snap-oddtmp-{who}", odd_repo, env_extra={"TMPDIR": odd_tmp})
                check(f"{who} snapshot: a TMPDIR with a tab and a space still runs",
                      r["rc"] == 0, f"rc={r['rc']} err={r['err']}")
                check(f"{who} snapshot: and `git worktree list` ends clean afterwards",
                      odd_wt > 0 and worktree_count(odd_repo) == odd_wt
                      and not [n for n in os.listdir(odd_tmp) if n.startswith("hjw_snap.")],
                      f"{worktree_count(odd_repo)} != {odd_wt} left={os.listdir(odd_tmp)}")

                # (Z1b) a listing we cannot read is NOT a licence to rm -rf: the
                # removal still goes through git, and the worktree really goes.
                wl_repo = dirty_snap_repo(f"repo-snap-wtlist-{who}")
                wl_wt = worktree_count(wl_repo)
                wl_bin = os.path.join(runner_tmp, f"bin-snap-wtlist-{who}")
                make_snapshot_git_stub(wl_bin, "worktree-list")
                r = snap_run(f"snap-wtlist-{who}", wl_repo, bin_dir=wl_bin)
                check(f"{who} snapshot: an unreadable worktree listing still succeeds",
                      r["rc"] == 0 and "snapshot cleanup failed" not in r["err"],
                      f"rc={r['rc']} err={r['err']}")
                check(f"{who} snapshot: and the snapshot is still removed THROUGH git",
                      wl_wt > 0 and worktree_count(wl_repo) == wl_wt,
                      f"{worktree_count(wl_repo)} != {wl_wt}")

                # (Z2) an effective brief that cannot be written refuses before
                # the paid call — a reviewer must never get the contract
                # without the snapshot note.
                eb_repo = dirty_snap_repo(f"repo-snap-brief-{who}")
                eb_bin = os.path.join(runner_tmp, f"bin-snap-brief-{who}")
                eb_blocker = os.path.join(runner_tmp, f"effective-is-a-dir-{who}")
                make_mktemp_stub(eb_bin, [("_effective.XXXXXX.md", eb_blocker)])
                r = snap_run(f"snap-brief-{who}", eb_repo, bin_dir=eb_bin)
                check(f"{who} snapshot: an unwritable effective brief -> exit 2, zero calls",
                      r["rc"] == 2
                      and "snapshot unavailable: cannot write the effective brief" in r["err"]
                      and len(r["calls"]) == 0, f"rc={r['rc']} err={r['err']}")

                # (Z3a) a brief whose FILE NAME ends in a newline must resolve
                # to itself — $(cat)/$() would have eaten the newline.
                nl_brief = os.path.join(runner_tmp, f"nl-brief-{who}.md\n")
                write_file(nl_brief, "Newline-named brief body.\n")
                nlb_repo = dirty_snap_repo(f"repo-snap-nlbrief-{who}")
                nlb_bin = os.path.join(runner_tmp, f"bin-snap-nlbrief-{who}")
                nlb_cap = os.path.join(runner_tmp, f"cap-snap-nlbrief-{who}")
                make_stub(nlb_bin, snap_cli, nlb_cap)
                rc, out, err = run_script(
                    snap_script,
                    ["--snapshot", "-o", os.path.join(runner_tmp, f"nl-brief-{who}.reply.md"),
                     nl_brief],
                    {"PATH": nlb_bin + os.pathsep + os.environ.get("PATH", "")},
                    cwd=nlb_repo)
                nlb_calls = read_calls(nlb_cap)
                check(f"{who} snapshot: a brief NAMED with a trailing newline is read losslessly",
                      rc == 0 and bool(nlb_calls)
                      and "Newline-named brief body." in nlb_calls[0][1],
                      f"rc={rc} err={err} calls={nlb_calls[:1]}")

                # (Z3b) a repository root that ENDS in a newline survives
                # preflight AND cleanup (the cleanup runs `git -C ORIG`).
                nlw_repo = dirty_snap_repo(f"repo-snap-nlwd-{who}\n")
                nlw_wt = worktree_count(nlw_repo)
                r = snap_run(f"snap-nlwd-{who}", nlw_repo)
                check(f"{who} snapshot: a working directory ending in a newline captures",
                      r["rc"] == 0 and "+dirty(4,2)" in r["out"],
                      f"rc={r['rc']} out={r['out']} err={r['err']}")
                check(f"{who} snapshot: and its snapshot is removed through that same root",
                      nlw_wt > 0 and worktree_count(nlw_repo) == nlw_wt,
                      f"{worktree_count(nlw_repo)} != {nlw_wt}")

                # (Z4) -o x.log: the derived log must not devour the reply.
                alias_repo = dirty_snap_repo(f"repo-snap-alias-{who}")
                alias_out = os.path.join(runner_tmp, f"alias-{who}.log")
                write_file(alias_out, "PREVIOUS REPLY\n")
                alias_bin = os.path.join(runner_tmp, f"bin-snap-alias-{who}")
                alias_cap = os.path.join(runner_tmp, f"cap-snap-alias-{who}")
                make_snapshot_git_stub(alias_bin, "apply")
                make_stub(alias_bin, snap_cli, alias_cap)
                rc, out, err = run_script(
                    snap_script, ["--snapshot", "-o", alias_out,
                                  brief_file(f"snap-alias-{who}-brief.md")],
                    {"PATH": alias_bin + os.pathsep + os.environ.get("PATH", "")},
                    cwd=alias_repo)
                check(f"{who} -o x.log: a capture refusal leaves the previous reply intact",
                      rc == 2 and open(alias_out).read() == "PREVIOUS REPLY\n",
                      f"rc={rc} content={open(alias_out).read()!r} err={err}")
                alias_bin2 = os.path.join(runner_tmp, f"bin-snap-alias2-{who}")
                alias_cap2 = os.path.join(runner_tmp, f"cap-snap-alias2-{who}")
                make_stub(alias_bin2, snap_cli, alias_cap2)
                rc, out, err = run_script(
                    snap_script, ["--snapshot", "-o", alias_out,
                                  brief_file(f"snap-alias2-{who}-brief.md")],
                    {"PATH": alias_bin2 + os.pathsep + os.environ.get("PATH", "")},
                    cwd=alias_repo)
                check(f"{who} -o x.log: the reply lands in x.log and the log in x.log.log",
                      rc == 0 and "STUB-REPLY-OK-1" in open(alias_out).read()
                      and os.path.isfile(alias_out + ".log")
                      and "# snapshot HEAD: " in open(alias_out + ".log").read(),
                      f"rc={rc} reply={open(alias_out).read()!r} err={err}")

                # (Z6) the copy/hash race itself. The shell cannot reach the
                # window between shutil.copy2 and the destination hash, so the
                # fixture extracts the runner's OWN capture helper and runs it
                # with a copy that loses the race.
                helper_src = extract_capture_helper(snap_script)
                check(f"{who} snapshot: the capture helper source is extractable",
                      bool(helper_src) and "untracked file changed during capture" in helper_src,
                      len(helper_src))
                if helper_src:
                    race_repo = dirty_snap_repo(f"repo-snap-race-{who}")
                    race_sha = subprocess.run(["git", "-C", race_repo, "rev-parse", "HEAD"],
                                              capture_output=True, text=True).stdout.strip()
                    helper_path = write_file(os.path.join(runner_tmp, f"capture-{who}.py"),
                                             helper_src + "\n")
                    driver_path = write_file(os.path.join(runner_tmp, f"race-driver-{who}.py"),
                                             RACE_DRIVER)
                    race_meta = tempfile.mkdtemp(dir=runner_tmp, prefix=f"race-meta-{who}-")
                    race_snap = tempfile.mkdtemp(dir=runner_tmp, prefix=f"race-snap-{who}-")
                    rp = subprocess.run(
                        [sys.executable, driver_path, helper_path, race_repo, race_sha,
                         race_snap, race_meta],
                        capture_output=True, text=True, timeout=120)
                    race_refuse = os.path.join(race_meta, "refuse")
                    race_reason = open(race_refuse).read() if os.path.isfile(race_refuse) else ""
                    check(f"{who} snapshot: a file that changes between hash and copy is refused",
                          rp.returncode == 3
                          and "untracked file changed during capture" in race_reason,
                          f"rc={rp.returncode} reason={race_reason!r} stderr={rp.stderr}")
                    subprocess.run(["git", "-C", race_repo, "worktree", "remove",
                                    "--force", race_snap], capture_output=True)

            # ---- (A3) non-git precondition: refuse BEFORE the paid call ----
            # A consult whose no-edit contract can never be verified used to
            # run first and fail afterwards — a reviewer call spent on a
            # result that was then discarded. Now it exits 2 with ZERO calls.
            nongit_dir = os.path.join(runner_tmp, "not-a-repo")
            os.makedirs(nongit_dir, exist_ok=True)

            for who, script, cli, expect in (
                ("codex", codex_script, "codex",
                 "consult outside a git repo with sandbox=danger-full-access "
                 "(not read-only) — cannot verify the no-edit contract."),
                ("claude", claude_script, "claude",
                 "consult outside a git repo — cannot verify the no-edit contract "
                 "(claude -p is unsandboxed)."),
            ):
                ng_bin = os.path.join(runner_tmp, f"bin-nongit-{who}")
                ng_cap = os.path.join(runner_tmp, f"cap-nongit-{who}")
                make_stub(ng_bin, cli, ng_cap)
                env = {"PATH": ng_bin + os.pathsep + os.environ.get("PATH", "")}
                if who == "codex":
                    env["CODEX_SANDBOX"] = "danger-full-access"
                rc, out, err = run_script(
                    script, [brief_file(f"nongit-{who}-brief.md")], env, cwd=nongit_dir)
                check(f"{who} non-git: refused with exit 2 before the call",
                      rc == 2, f"rc={rc} err={err}")
                check(f"{who} non-git: the refusal names the unverifiable contract",
                      expect in err, err)
                check(f"{who} non-git: ZERO reviewer calls were made",
                      read_calls(ng_cap) == [], read_calls(ng_cap))

            # The read-only sandbox IS the enforcement: codex outside a repo
            # still runs there. (claude -p has no sandbox, so it has no
            # equivalent path — it always refuses.)
            ro_bin = os.path.join(runner_tmp, "bin-nongit-ro")
            ro_cap = os.path.join(runner_tmp, "cap-nongit-ro")
            make_stub(ro_bin, "codex", ro_cap)
            rc, out, err = run_script(
                codex_script, [brief_file("nongit-ro-brief.md")],
                {"PATH": ro_bin + os.pathsep + os.environ.get("PATH", ""),
                 "CODEX_SANDBOX": "read-only"}, cwd=nongit_dir)
            check("codex non-git + read-only sandbox: still runs and succeeds",
                  rc == 0 and "STUB-REPLY-OK-1" in out, f"rc={rc} out={out} err={err}")
            check("codex non-git + read-only sandbox: the reviewer was called once",
                  len(read_calls(ro_cap)) == 1, read_calls(ro_cap))
        finally:
            shutil.rmtree(runner_tmp, ignore_errors=True)

        print(f"\n{PASS} passed, {len(FAIL)} failed")
        if FAIL:
            print("FAILED:", *FAIL, sep="\n  - ")
            sys.exit(1)
    finally:
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    main()
