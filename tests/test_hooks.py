#!/usr/bin/env python3
"""Synthetic hook-contract tests for haejwo gate scripts.

Pipes realistic hook JSON payloads into the actual scripts (subprocess, the
real CLI contract) and asserts allow/deny/reset behavior. No Claude Code
required. Run: python3 tests/test_hooks.py
"""
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "haejwo")
SCRIPTS = os.path.join(PLUGIN, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)
from hjw_common import DEFAULT_CONFIG, observe, prune_state  # noqa: E402
from session_brief import CORE_BODY, EMERGENCY_CORE, MAX_LEN, UNCONFIGURED_CORE  # noqa: E402
from delegation_gate import CLAUDE_TIER_ONLY, CODEX_TIER_ONLY, _agent_file_default  # noqa: E402

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


def ctx_of(out):
    return (out.get("hookSpecificOutput") or {}).get("additionalContext", "") or ""


def reason_of(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "") or ""


def classification_state_tests():
    """2.25.0 (cold-loop cycle 1): temp exemption scoped to OUTSIDE the
    project (D1), bounded state lock (D10), persistence failure said every
    call (D15), apply_patch Move-to (B1), stored gate values before setup
    (B3), Codex-host denial wording (D7)."""
    print("== temp exemption is scoped to outside the project (D1) ==")
    base = tempfile.mkdtemp(prefix="hjw-test-d1-")
    d1_data = tempfile.mkdtemp(prefix="hjw-test-d1data-")
    try:
        base = os.path.realpath(base)
        proj = os.path.join(base, "repo")
        nogit = os.path.join(base, "plain")
        scratch = os.path.join(base, "scratchpad")  # stands in for the session scratchpad
        for d_ in (os.path.join(proj, "sub"), nogit, scratch):
            os.makedirs(d_, exist_ok=True)
        tmp_root = os.path.realpath(tempfile.gettempdir()).rstrip("/") + "/"
        has_git = shutil.which("git") is not None and subprocess.run(
            ["git", "init", "-q", proj], capture_output=True).returncode == 0
        nogit_is_bare = subprocess.run(
            ["git", "-C", nogit, "rev-parse", "--show-toplevel"],
            capture_output=True).returncode != 0 if has_git else True
        check("D1 fixture: the temp project lives under the system temp dir",
              (base + "/").startswith(tmp_root), base)

        def ep(path, sid, cwd):
            d_ = edit_payload(path, sid=sid)
            d_["cwd"] = cwd
            return d_

        if not has_git:
            print("  skipped (no git) D1 git-toplevel fixtures")
        else:
            outs = [run("gate.py", ep(os.path.join(proj, n), "sess-D1G", proj), d1_data)[1]
                    for n in ("a.py", "b.py")]
            check("D1 git repo under temp: files 1-2 allowed (counted)",
                  all(decision(o) != "deny" for o in outs)
                  and "budget" in ctx_of(outs[1]).lower(), str(outs))
            rc, out = run("gate.py", ep(os.path.join(scratch, "x.py"), "sess-D1G", proj),
                          d1_data)
            check("D1 scratchpad OUTSIDE the project stays exempt (allowed at a full "
                  "budget)", rc == 0 and decision(out) != "deny", str(out))
            rc, out = run("gate.py", ep(os.path.join(proj, "c.py"), "sess-D1G", proj),
                          d1_data)
            check("D1 git repo under temp: 3rd code file DENIED", decision(out) == "deny",
                  str(out))
            rc, out = run("gate.py", ep("rel.py", "sess-D1G", proj), d1_data)
            check("D1 git repo under temp: a RELATIVE path resolves into the project "
                  "and is gated", decision(out) == "deny", str(out))
            # cwd is a subdirectory: the git TOPLEVEL bounds the project, so a
            # file above cwd but inside the repo is still gated.
            sub = os.path.join(proj, "sub")
            for n in ("s1.py", "s2.py"):
                run("gate.py", ep(os.path.join(sub, n), "sess-D1S", sub), d1_data)
            rc, out = run("gate.py", ep(os.path.join(proj, "top.py"), "sess-D1S", sub),
                          d1_data)
            check("D1 cwd in a repo subdir: a file above cwd inside the git toplevel "
                  "is gated", decision(out) == "deny", str(out))
            # The shared Edit/Bash classification: bash_guard sees the same line.
            bp = bash_payload(f"echo x > {os.path.join(proj, 'z.py')}", sid="sess-D1B")
            bp["cwd"] = proj
            rc, out = run("bash_guard.py", bp, d1_data)
            check("D1 bash_guard: redirect into a code file of a temp-dir repo DENIED",
                  decision(out) == "deny", str(out))
            bp = bash_payload(f"echo x > {os.path.join(scratch, 'z.py')}", sid="sess-D1B")
            bp["cwd"] = proj
            rc, out = run("bash_guard.py", bp, d1_data)
            check("D1 bash_guard: redirect into the scratchpad stays allowed",
                  rc == 0 and decision(out) != "deny", str(out))

        if not nogit_is_bare:
            print("  skipped (temp dir is inside a git repo) D1 no-git fixtures")
        else:
            for n in ("a.py", "b.py"):
                run("gate.py", ep(os.path.join(nogit, n), "sess-D1N", nogit), d1_data)
            rc, out = run("gate.py", ep(os.path.join(scratch, "y.py"), "sess-D1N", nogit),
                          d1_data)
            check("D1 cwd under temp without git: a file OUTSIDE cwd stays exempt",
                  rc == 0 and decision(out) != "deny", str(out))
            rc, out = run("gate.py", ep(os.path.join(nogit, "c.py"), "sess-D1N", nogit),
                          d1_data)
            check("D1 cwd under temp without git: the 3rd file UNDER cwd is DENIED",
                  decision(out) == "deny", str(out))

        # Uncertainty keeps today's exemption (P4): no cwd at all.
        nocwd = edit_payload(os.path.join(scratch, "n.py"), sid="sess-D1X")
        nocwd.pop("cwd", None)
        for n in ("n1.py", "n2.py", "n3.py"):
            nocwd["tool_input"] = {"file_path": os.path.join(proj, n)}
            rc, out = run("gate.py", nocwd, d1_data)
        check("D1 no cwd in the payload: temp paths keep the exemption (fail open)",
              rc == 0 and decision(out) != "deny", str(out))
    finally:
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(d1_data, ignore_errors=True)

    print("== bounded state lock: allow WITHOUT mutation (D10) ==")
    lk_data = tempfile.mkdtemp(prefix="hjw-test-d10-")
    try:
        sdir = os.path.join(lk_data, "state")
        os.makedirs(sdir, exist_ok=True)
        lk_state = os.path.join(sdir, "sess-LK.json")
        with open(lk_state, "w") as f:
            json.dump({"prompt_id": "p1", "files": ["/repo/lk/a.py", "/repo/lk/b.py"],
                       "updated_at": time.time()}, f)
        before = open(lk_state, "rb").read()
        holder = open(lk_state + ".lock", "w")
        try:
            fcntl.flock(holder, fcntl.LOCK_EX)
            t0 = time.time()
            rc, out = run("gate.py", edit_payload("/repo/lk/c.py", sid="sess-LK"), lk_data)
            elapsed = time.time() - t0
            check("D10 held lock: an over-budget edit is ALLOWED (no unlocked check)",
                  rc == 0 and decision(out) != "deny", str(out))
            check("D10 held lock: the hook waits about 2 s, not until released",
                  1.5 <= elapsed < 5, f"elapsed={elapsed:.2f}s")
            check("D10 held lock: the state file is byte-identical",
                  open(lk_state, "rb").read() == before)
            check("D10 held lock: the output says the budget did not count",
                  ctx_of(out) == ("[haejwo gate] state not persisted (lock busy); "
                                  "the edit budget is not being counted"), ctx_of(out))
            obs = [json.loads(l) for l in open(os.path.join(sdir, "observations.jsonl"))
                   if l.strip()]
            check("D10 held lock: observed via 'lock-unavailable'",
                  any(r.get("sid") == "sess-LK" and r.get("via") == "lock-unavailable"
                      for r in obs), str(obs[-1:]))
            # 2.25 fix: a lock MISS skips the prune too (no bookkeeping write).
            lk_stale = os.path.join(sdir, "sess-STALE.json")
            with open(lk_stale, "w") as f:
                f.write("{}")
            old_t = time.time() - 30 * 86400
            os.utime(lk_stale, (old_t, old_t))
            t0 = time.time()
            rc, _ = run("turn_reset.py", {"session_id": "sess-LK", "prompt_id": "p2",
                                          "hook_event_name": "UserPromptSubmit"}, lk_data)
            elapsed = time.time() - t0
            check("D10 held lock: turn_reset exits 0 within the bound, state untouched",
                  rc == 0 and elapsed < 5 and open(lk_state, "rb").read() == before,
                  f"rc={rc} elapsed={elapsed:.2f}s")
            check("D10 held lock: turn_reset does not prune stale session state after the miss",
                  os.path.isfile(lk_stale), sorted(os.listdir(sdir)))
        finally:
            try:
                fcntl.flock(holder, fcntl.LOCK_UN)
            except Exception:
                pass
            holder.close()
    finally:
        shutil.rmtree(lk_data, ignore_errors=True)

    print("== persistence failure is said on EVERY call (D15) ==")
    pf_base = tempfile.mkdtemp(prefix="hjw-test-d15-")
    try:
        # (a) the data dir path cannot exist (a regular file sits where its
        # parent should be) — unwritable even for root.
        blocker = os.path.join(pf_base, "blocker")
        open(blocker, "w").close()
        dead_data = os.path.join(blocker, "data")
        msgs = []
        for n in ("a", "b", "c"):
            rc, out = run("gate.py", edit_payload(f"/repo/pf/{n}.py", sid="sess-PF"),
                          dead_data)
            msgs.append((rc, decision(out), ctx_of(out)))
        check("D15 unwritable data dir: every call allowed and says state is not "
              "persisted",
              all(rc_ == 0 and d_ != "deny"
                  and "[haejwo gate] state not persisted (ENOTDIR); the edit budget is "
                      "not being counted" in c_ for rc_, d_, c_ in msgs), str(msgs))
        # (b) the lock works but the state file cannot be replaced: the save
        # itself fails, so nothing is counted and every call says so.
        pf_data = os.path.join(pf_base, "data")
        os.makedirs(os.path.join(pf_data, "state", "sess-PF2.json"), exist_ok=True)
        ctxs = []
        for n in ("a", "b"):
            rc, out = run("gate.py", edit_payload(f"/repo/pf2/{n}.py", sid="sess-PF2"),
                          pf_data)
            ctxs.append(ctx_of(out))
        note = ("[haejwo gate] state not persisted (EISDIR); the edit budget is not "
                "being counted")
        check("D15 failed save: the note on EVERY call (not a once-note)",
              ctxs == [note, note], str(ctxs))
        check("D15 writable state: no note on a normal save",
              "not persisted" not in ctx_of(run("gate.py", edit_payload(
                  "/repo/pf3/a.py", sid="sess-PF3"), pf_data)[1]))
    finally:
        shutil.rmtree(pf_base, ignore_errors=True)

    print("== apply_patch Move to: + Codex-host denial wording (B1, D7) ==")
    mv_data = tempfile.mkdtemp(prefix="hjw-test-b1-")
    try:
        cmd = ("*** Begin Patch\n*** Update File: /repo/mv/a.py\n"
               "*** Move to: /repo/mv/b.py\n@@\n-x\n+y\n*** End Patch")
        mv = {"session_id": "sess-MV", "turn_id": "t1", "hook_event_name": "PreToolUse",
              "tool_name": "apply_patch", "cwd": "/repo", "tool_input": {"command": cmd}}
        rc, out = run("gate.py", mv, mv_data)
        st = json.load(open(os.path.join(mv_data, "state", "sess-MV.json")))
        check("B1 Update+Move: source AND destination counted (budget full)",
              decision(out) != "deny" and "budget now full" in ctx_of(out)
              and st.get("files") == ["/repo/mv/a.py", "/repo/mv/b.py"], str(st))
        rc, out = run("gate.py", patch_payload([("Add", "/repo/mv/c.py")], sid="sess-MV"),
                      mv_data)
        r = reason_of(out)
        check("B1 a further file after a Move fills the budget -> DENY",
              decision(out) == "deny", str(out))
        check("D7 Codex (apply_patch) denial names spawn_agent, never the Agent tool",
              "delegate via spawn_agent: 'haejwo:default-worker'" in r
              and "Agent tool" not in r, r)

        for n in ("a", "b"):
            run("gate.py", edit_payload(f"/repo/d7/{n}.py", sid="sess-D7C"), mv_data)
        rc, out = run("gate.py", edit_payload("/repo/d7/c.py", sid="sess-D7C"), mv_data)
        check("D7 Claude denial text is unchanged (pinned in full)",
              reason_of(out) == (
                  "[haejwo gate] Per-turn code-edit budget exceeded: this change adds "
                  "1 new file(s) (/repo/d7/c.py) on top of 2/2 already touched "
                  "(/repo/d7/a.py, /repo/d7/b.py). Do NOT edit more code files directly "
                  "— split the change or delegate via the Agent tool: "
                  "'haejwo:default-worker' (implementation), 'haejwo:task-worker' "
                  "(mechanical chores), 'haejwo:deep-reasoner' (hard design/analysis). "
                  "Re-editing the files already touched this turn is still allowed. "
                  "If this is unplanned feature-scale work, run /haejwo:plan first "
                  "(backup nudge — plan-first is the norm for delegate-tier work). "
                  "Emergency override: /haejwo:gate off."), reason_of(out))
        cx_root = "/home/u/.codex/plugins/cache/haejwo/haejwo"
        for n in ("a", "b"):
            run("gate.py", edit_payload(f"/repo/d7x/{n}.py", sid="sess-D7X"), mv_data,
                root=cx_root)
        rc, out = run("gate.py", edit_payload("/repo/d7x/c.py", sid="sess-D7X"), mv_data,
                      root=cx_root)
        check("D7 Codex install (plugin root under /.codex/) also names spawn_agent",
              decision(out) == "deny" and "via spawn_agent:" in reason_of(out),
              reason_of(out))
    finally:
        shutil.rmtree(mv_data, ignore_errors=True)

    print("== stored gate values before setup (B3) ==")
    b3_data = tempfile.mkdtemp(prefix="hjw-test-b3-")
    try:
        with open(os.path.join(b3_data, "config.json"), "w") as f:
            json.dump({"gate": {"enabled": False, "max_files_per_turn": 5}}, f)
        rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, b3_data)
        c_ = ctx_of(out)
        check("B3 gate off before setup: the summary reports the STORED values",
              "[haejwo config] stored gate settings — not configured: "
              "gate=OFF budget=5 files/turn bash_guard=OFF delegation_guard=OFF" in c_,
              c_[-400:])
        check("B3 gate off before setup: the nudge does not claim the gate is ON",
              "NOT configured" in c_ and "gate OFF, max 5 distinct code files" in c_
              and "safe defaults are ACTIVE" not in c_ and "gate=ON" not in c_, c_[-900:])
        with open(os.path.join(b3_data, "config.json"), "w") as f:
            json.dump({"gate": {"enabled": True}}, f)
        rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, b3_data)
        c_ = ctx_of(out)
        check("B3 stored values equal to the defaults: wording unchanged",
              "[haejwo config] defaults — not configured: gate=ON budget=2" in c_
              and "safe defaults are ACTIVE" in c_, c_[-400:])
    finally:
        shutil.rmtree(b3_data, ignore_errors=True)


def config_host_summary_tests():
    """2.26.0 (cold-loop cycle 2): typed config validation with the once-note
    (B2), Codex command names in denials (F11), the effective state in the
    config summary (F12/D6), a bounded summary, per-user file modes (F15),
    one host-detection helper (F16)."""
    from hjw_common import load_config_with_status, on_codex_host  # noqa: E402
    cx_root = "/home/u/.codex/plugins/cache/haejwo/haejwo"
    SB = {"hook_event_name": "SessionStart"}

    def write_cfg(d_, cfg):
        with open(os.path.join(d_, "config.json"), "w") as f:
            json.dump(cfg, f)

    def summary_line(c_):
        return next((ln for ln in c_.splitlines()
                     if ln.startswith("[haejwo config]")), "")

    print("== typed config validation + once-note (B2) ==")
    b2 = tempfile.mkdtemp(prefix="hjw-test-b2-")
    try:
        write_cfg(b2, {"gate": {"max_files_per_turn": "2"}})
        cfg_, st_ = load_config_with_status(b2)
        check("B2 load: a string budget becomes the default 2, status ok, reason kept",
              st_ == "ok" and cfg_["gate"]["max_files_per_turn"] == 2
              and cfg_.get("_ignored") == ["gate.max_files_per_turn must be an integer >= 1"],
              str((st_, cfg_.get("gate"), cfg_.get("_ignored"))))
        note = ("[haejwo] config value ignored: gate.max_files_per_turn must be an "
                "integer >= 1 — the default applies until config.json is fixed")
        rc, out = run("gate.py", edit_payload("/repo/b2/a.py", sid="sess-B2"), b2)
        check("B2 string budget: 1st code file allowed WITH the note (was: silent fail-open)",
              decision(out) == "allow" and ctx_of(out) == note, str(out))
        rc, out = run("gate.py", edit_payload("/repo/b2/b.py", sid="sess-B2"), b2)
        check("B2 string budget: the default 2 is used (2nd file fills the budget), note not repeated",
              decision(out) == "allow" and "Edit budget now full (2/2" in ctx_of(out)
              and "config value ignored" not in ctx_of(out), ctx_of(out))
        rc, out = run("gate.py", edit_payload("/repo/b2/c.py", sid="sess-B2"), b2)
        check("B2 string budget: the 3rd file is DENIED, no note in the reason",
              decision(out) == "deny" and "config value ignored" not in reason_of(out),
              str(out))
        rc, out = run("bash_guard.py", bash_payload("ls", sid="sess-B2"), b2)
        check("B2 the note is shared across hooks: bash_guard stays silent after gate said it",
              ctx_of(out) == "", str(out))
        run("turn_reset.py", {"session_id": "sess-B2", "prompt_id": "p2",
                              "hook_event_name": "UserPromptSubmit"}, b2)
        rc, out = run("gate.py", edit_payload("/repo/b2/d.py", sid="sess-B2", pid="p2"), b2)
        with open(os.path.join(b2, "state", "sess-B2.json")) as f:
            b2_state = json.load(f)
        check("B2 the once-flag survives a turn reset",
              rc == 0 and decision(out) != "deny" and ctx_of(out) == ""
              and b2_state.get("prompt_id") == "p2"
              and b2_state.get("cfg_ignored_noted") is True, str((out, b2_state)))
        rc, out = run("bash_guard.py", bash_payload("ls", sid="sess-B2b"), b2)
        check("B2 a new session hears the note once (bash_guard fires first)",
              ctx_of(out) == note, str(out))

        write_cfg(b2, {"gate": {"enabled": "false"}})
        firsts = [run("gate.py", edit_payload(f"/repo/b2e/{n}.py", sid="sess-B2E"), b2)[1]
                  for n in ("a", "b")]
        rc, out = run("gate.py", edit_payload("/repo/b2e/c.py", sid="sess-B2E"), b2)
        check("B2 the string \"false\" never disables the gate (default ON), noted once",
              decision(out) == "deny" and ctx_of(firsts[0]) == (
                  "[haejwo] config value ignored: gate.enabled must be true or "
                  "false — the default applies until config.json is fixed")
              and "config value ignored" not in reason_of(out), str(out))

        write_cfg(b2, {"gate": "off", "models": {"default_worker": 5},
                       "codex": {"enabled": "yes"}})
        cfg_, st_ = load_config_with_status(b2)
        check("B2 load: a non-object section and wrong-typed leaves all fall back, each named",
              st_ == "ok" and cfg_["gate"] == DEFAULT_CONFIG["gate"]
              and cfg_["models"]["default_worker"] == "opus"
              and cfg_["codex"]["enabled"] is False
              and cfg_.get("_ignored") == ["gate must be an object",
                                           "codex.enabled must be true or false",
                                           "models.default_worker must be a string"],
              str(cfg_.get("_ignored")))
        rc, out = run("delegation_gate.py", task_payload("general-purpose", sid="sess-B2D"), b2)
        check("B2 a deny carries the note on its reason (a deny has no context)",
              decision(out) == "deny" and reason_of(out).startswith(
                  "[haejwo gate] Delegation to generic agent")
              and reason_of(out).endswith(
                  "Emergency override: /haejwo:gate off.\n[haejwo] config value "
                  "ignored: gate must be an object; codex.enabled must be true or "
                  "false; models.default_worker must be a string — the default "
                  "applies until config.json is fixed"), reason_of(out))
        write_cfg(b2, {"gate": {"max_files_per_turn": 3, "enabled": True}})
        cfg_, _ = load_config_with_status(b2)
        check("B2 a valid config records nothing ignored", "_ignored" not in cfg_, str(cfg_))
    finally:
        shutil.rmtree(b2, ignore_errors=True)

    print("== Codex command names in denials (F11) ==")
    f11 = tempfile.mkdtemp(prefix="hjw-test-f11-")
    try:
        for n in ("a", "b"):
            run("gate.py", edit_payload(f"/repo/f11/{n}.py", sid="sess-F11"), f11, root=cx_root)
        rc, out = run("gate.py", edit_payload("/repo/f11/c.py", sid="sess-F11"), f11, root=cx_root)
        r = reason_of(out)
        check("F11 gate on Codex: @haejwo-plan and @haejwo-gate off, no Claude command",
              decision(out) == "deny" and "run @haejwo-plan first (backup nudge" in r
              and r.endswith("Emergency override: @haejwo-gate off.") and "/haejwo:" not in r, r)
        rc, out = run("bash_guard.py", bash_payload("echo x >> src/app.py"), f11, root=cx_root)
        check("F11 bash_guard redirect deny on Codex, exact",
              reason_of(out) == (
                  "[haejwo gate] Bash output redirect writes to a code file (src/app.py). "
                  "The main agent must not modify code via Bash — use Edit/Write within "
                  "the turn budget, or delegate to 'haejwo:default-worker'. "
                  "Emergency override: @haejwo-gate off."), reason_of(out))
        rc, out = run("bash_guard.py", bash_payload("sed -i 's/a/b/' src/app.py"), f11,
                      root=cx_root)
        check("F11 bash_guard in-place deny on Codex names @haejwo-gate off",
              decision(out) == "deny"
              and reason_of(out).endswith("Emergency override: @haejwo-gate off."),
              reason_of(out))
        rc, out = run("delegation_gate.py", task_payload("general-purpose", sid="sess-F11D"),
                      f11, root=cx_root)
        check("F11 delegation deny on Codex names @haejwo-gate off",
              decision(out) == "deny"
              and reason_of(out).endswith("Emergency override: @haejwo-gate off.")
              and "/haejwo:" not in reason_of(out), reason_of(out))
        check("F16 on_codex_host: root or data under /.codex/, else Claude",
              on_codex_host(cx_root, "") and on_codex_host("", "/h/.codex/data")
              and not on_codex_host(PLUGIN, f11) and not on_codex_host(None, None))
    finally:
        shutil.rmtree(f11, ignore_errors=True)

    print("== config summary: effective state, delegation_guard, bound (F12/D6) ==")
    f12 = tempfile.mkdtemp(prefix="hjw-test-f12-")
    try:
        write_cfg(f12, {"configured": True})
        rc, out = run("session_brief.py", SB, f12, env_extra={"HAEJWO_GATE": "off"})
        check("F12 HAEJWO_GATE=off: the configured summary says OFF (env) for all three",
              summary_line(ctx_of(out)).startswith(
                  "[haejwo config] gate=OFF (env) budget=2 files/turn "
                  "bash_guard=OFF (env) delegation_guard=OFF (env) | "), ctx_of(out)[-700:])
        rc, out = run("session_brief.py", SB, f12)
        check("F12 without the env override: the stored ON",
              summary_line(ctx_of(out)).startswith(
                  "[haejwo config] gate=ON budget=2 files/turn bash_guard=ON "
                  "delegation_guard=ON | "), ctx_of(out)[-700:])
        write_cfg(f12, {})
        rc, out = run("session_brief.py", SB, f12, env_extra={"HAEJWO_GATE": "off"})
        check("F12 not configured + env off: the defaults summary says OFF (env) too",
              "[haejwo config] defaults — not configured: gate=OFF (env) budget=2 "
              "files/turn bash_guard=OFF (env) delegation_guard=OFF (env) | "
              in ctx_of(out), ctx_of(out)[-700:])
        # (2.26 review e) both guards run only while the gate is on: a stored
        # gate OFF shows them OFF too, untagged (the cause is config, not env).
        write_cfg(f12, {"configured": True, "gate": {"enabled": False}})
        rc, out = run("session_brief.py", SB, f12)
        check("F12 stored gate OFF: bash_guard and delegation_guard are effectively OFF",
              summary_line(ctx_of(out)).startswith(
                  "[haejwo config] gate=OFF budget=2 files/turn bash_guard=OFF "
                  "delegation_guard=OFF | "), ctx_of(out)[-700:])
        write_cfg(f12, {"configured": True, "gate": {"delegation_guard": False}})
        rc, out = run("session_brief.py", SB, f12)
        check("D6 delegation_guard=false is visible in the summary",
              summary_line(ctx_of(out)).startswith(
                  "[haejwo config] gate=ON budget=2 files/turn bash_guard=ON "
                  "delegation_guard=OFF | "), ctx_of(out)[-700:])
        write_cfg(f12, {"gate": {"delegation_guard": False}})
        rc, out = run("session_brief.py", SB, f12)
        check("D6 delegation_guard=false before setup is reported as STORED, not defaults",
              "[haejwo config] stored gate settings — not configured: gate=ON budget=2 "
              "files/turn bash_guard=ON delegation_guard=OFF | " in ctx_of(out),
              ctx_of(out)[-700:])
        long_id = "claude-" + "x" * 400
        write_cfg(f12, {"configured": True,
                        "models": {"deep_reasoner": long_id, "default_worker": long_id,
                                   "task_worker": long_id}})
        rc, out = run("session_brief.py", SB, f12)
        line = summary_line(ctx_of(out))
        check("summary bound: long model ids are cut to <= 600 chars at a word, with …",
              line.startswith("[haejwo config] gate=ON") and len(line) <= 600
              and line.endswith("…") and not line[:-1].endswith(" ")
              and len(ctx_of(out)) <= MAX_LEN, f"len={len(line)} {line[-80:]!r}")
    finally:
        shutil.rmtree(f12, ignore_errors=True)

    print("== setup nudge: env override + Codex names (F12/F11) ==")
    nd = tempfile.mkdtemp(prefix="hjw-test-nudge-")
    cx_data = os.path.join(nd, ".codex", "data")
    os.makedirs(cx_data)
    try:
        rc, out = run("session_brief.py", SB, nd)
        check("nudge Claude text byte-identical (both hosts named, safe defaults ON)",
              "(the setup procedure — /haejwo:setup in Claude Code, the @haejwo-setup "
              "skill in Codex; the user only answers 4 quick choices and never needs "
              "to type a command). Until then safe defaults are ACTIVE: gate ON, max 2 "
              "distinct code files per turn for the main agent, bash-guard ON, "
              "subagents exempt. Delegation targets: " in ctx_of(out), ctx_of(out)[-900:])
        rc, out = run("session_brief.py", SB, nd, env_extra={"HAEJWO_GATE": "off"})
        c_ = ctx_of(out)
        check("F12 nudge with HAEJWO_GATE=off: says OFF (env), never 'gate ON'",
              "environment overrides them: gate OFF (env), bash-guard OFF (env). " in c_
              and "gate ON" not in c_ and "safe defaults are ACTIVE" not in c_, c_[-900:])
        write_cfg(nd, {"gate": {"max_files_per_turn": 5}})
        rc, out = run("session_brief.py", SB, nd, env_extra={"HAEJWO_GATE": "off"})
        c_ = ctx_of(out)
        check("F12 stored-settings nudge with HAEJWO_GATE=off: OFF (env) for both",
              "STORED gate settings are ACTIVE: gate OFF (env), max 5 distinct code "
              "files per turn for the main agent, bash-guard OFF (env)," in c_
              and "gate ON" not in c_, c_[-900:])
        rc, out = run("session_brief.py", SB, cx_data)
        c_ = ctx_of(out)
        check("F11 Codex nudge names only the @haejwo-setup skill",
              "(the setup procedure — the @haejwo-setup skill; the user only answers"
              in c_ and "/haejwo:" not in c_, c_[-900:])
        with open(os.path.join(cx_data, "config.json"), "w") as f:
            f.write("{not json")
        rc, out = run("session_brief.py", SB, cx_data)
        c_ = ctx_of(out)
        check("F11 Codex malformed notice names @haejwo-setup",
              "Run @haejwo-setup to rewrite it, or fix the JSON by hand." in c_
              and "/haejwo:" not in c_, c_[-600:])
        cx_note = ("[haejwo] config.json is unreadable (malformed JSON) — enforcement "
                   "is disabled (fail-open) until it is repaired; run @haejwo-setup "
                   "or fix the file")
        rc, out = run("gate.py", edit_payload("/repo/n/a.py", sid="sess-NCX"), cx_data)
        check("F11 Codex malformed-config hook note, exact", ctx_of(out) == cx_note,
              ctx_of(out))
        rc, out = run("bash_guard.py", bash_payload("ls", sid="sess-NCX2"), cx_data)
        check("F11 Codex malformed-config note from bash_guard too", ctx_of(out) == cx_note,
              ctx_of(out))
        from delegation_gate import _pin_not_passable_note_once  # noqa: E402
        n_cx = _pin_not_passable_note_once(nd, "sess-PNCX", "default_worker",
                                           "claude-x", "opus", True)
        n_cl = _pin_not_passable_note_once(nd, "sess-PNCL", "default_worker",
                                           "claude-x", "opus")
        check("F11 pin note: Codex names @haejwo-setup, Claude keeps /haejwo:setup",
              "set an alias in @haejwo-setup, or pass" in (n_cx or "")
              and "/haejwo:" not in (n_cx or "")
              and "set an alias in /haejwo:setup, or pass" in (n_cl or ""),
              f"{n_cx!r} {n_cl!r}")
    finally:
        shutil.rmtree(nd, ignore_errors=True)

    print("== hook files are per-user (F15) ==")
    f15 = tempfile.mkdtemp(prefix="hjw-test-f15-")
    old_mask = os.umask(0o022)
    try:
        run("gate.py", edit_payload("/repo/f15/a.py", sid="sess-F15"), f15)
        obs_ = os.path.join(f15, "state", "observations.jsonl")
        st_f = os.path.join(f15, "state", "sess-F15.json")
        modes = {os.path.basename(p_): oct(os.stat(p_).st_mode & 0o777)
                 for p_ in (obs_, st_f) if os.path.exists(p_)}
        check("F15 under a 022 caller umask, a fresh observations.jsonl and state file are 0600",
              modes == {"observations.jsonl": "0o600", "sess-F15.json": "0o600"}, str(modes))
        check("F15 the state dir is 0700",
              oct(os.stat(os.path.join(f15, "state")).st_mode & 0o777) == "0o700",
              oct(os.stat(os.path.join(f15, "state")).st_mode & 0o777))
    finally:
        os.umask(old_mask)
        shutil.rmtree(f15, ignore_errors=True)

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

        # The deny TEXT is a tested contract (PROMPTS.md): what was blocked ->
        # why -> the EXACT next action -> the escape hatch, in that order. Both
        # bash_guard strings are pinned in full, and the hatch is worded exactly
        # as gate.py and delegation_gate.py word it — a model that meets one
        # deny must not have to learn a second override.
        BASH_DENY_TEXT = (
            ("echo 'x' >> src/app.py",
             "[haejwo gate] Bash output redirect writes to a code file (src/app.py). "
             "The main agent must not modify code via Bash — use Edit/Write within "
             "the turn budget, or delegate to 'haejwo:default-worker'. "
             "Emergency override: /haejwo:gate off.", "redirect"),
            ("sed -i 's/a/b/' src/app.py",
             "[haejwo gate] Bash in-place edit (sed -i) targets ['src/app.py']. "
             "The main agent must not modify code via Bash — use Edit/Write within "
             "budget, or delegate to 'haejwo:default-worker'. "
             "Emergency override: /haejwo:gate off.", "in-place edit"),
        )
        for cmd, want, label in BASH_DENY_TEXT:
            rc, out = run("bash_guard.py", bash_payload(cmd), data)
            got = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason")
            check(f"deny text: the {label} reason is byte-exact", got == want,
                  f"got={got!r}")
            check(f"deny text: the {label} reason ENDS with the escape hatch",
                  bool(got) and got.endswith("Emergency override: /haejwo:gate off."),
                  f"got={got!r}")

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

        print("== bash_guard.py heredoc-data shape (S-F, 2.26) ==")
        # ONE shape (bash_guard.py header): the WHOLE command is a single
        # `cat > T <<'D'` (or `>>`) into a literal non-code T -> body is data.
        # Every other command keeps the 2.25.0 decision AND via (pinned here
        # per case). Signed stop condition: any reproduced allow outside the
        # shape withdraws the change — these denies are not to be relaxed.
        sf_data = tempfile.mkdtemp(prefix="hjw-test-sf-")
        try:
            def sf_last(sid):
                recs = [json.loads(l) for l in
                        open(os.path.join(sf_data, "state", "observations.jsonl"))]
                hits = [r for r in recs if r.get("sid") == sid]
                return hits[-1] if hits else None

            sf_cases = [
                # field case (cold-loop cycle 1 F#3 / cycle 2 E): a note whose
                # body QUOTES a redirect into a .py and an in-place edit
                ("cat > notes/x.md <<'EOF'\nrun: printf x > src/app.py\n"
                 "then: sed -i 's/a/b/' src/app.py\nEOF", "sess-SFa", "allow",
                 "heredoc-data", "field case: quoted heredoc note"),
                ('cat >> notes/x.md <<"EOF"\nprintf x > src/app.py\nEOF\n  \n',
                 "sess-SFb", "allow", "heredoc-data",
                 "double-quoted delimiter, append, trailing blank line"),
                ("cat <<'EOF' > notes/x.md\nsed -i s/a/b/ a.py\nEOF", "sess-SFc",
                 "allow", "heredoc-data", "heredoc before the redirect"),
                ("cat > notes/x.md <<-'EOF'\nsed -i s/a/b/ a.py\n\t\tEOF",
                 "sess-SFd", "allow", "heredoc-data",
                 "<<- closes on a tab-indented delimiter"),
                ("cat > notes/x.md <<'EOF'\nprintf x > a.py\n EOF\nEOF",
                 "sess-SFe", "allow", "heredoc-data",
                 "a space-indented line is body, as in bash"),
                # adversarial: each was denied by 2.25.0 and still is, same via
                ("cat > notes/x.md <<'EOF'; sed -i 's/a/b/' src/app.py\nhello\nEOF",
                 "sess-SF1", "deny", "inplace", "second command on the first line"),
                ("cat > notes/x.md <<'EOF'\nhello\nEOF; sed -i 's/a/b/' src/app.py",
                 "sess-SF2", "deny", "inplace",
                 "heredoc followed by `; <in-place edit of a .py>`"),
                ("cat > notes/x.md <<'EOF'\nprintf x > src/app.py\nEOF x",
                 "sess-SF3", "deny", "redirect", "delimiter line with trailing text"),
                ("cat > notes/x.md <<EOF\n$(echo x; printf x > src/app.py )\nEOF",
                 "sess-SF4", "deny", "redirect", "unquoted delimiter with $( ) body"),
                ("cat > src/new.py <<'EOF'\nhello\nEOF", "sess-SF5", "deny",
                 "redirect", "quoted heredoc into a .py target"),
                # `python3 -c` alone is the documented residual gap (allowed in
                # 2.25.0 too); what must hold is that a trailing command
                # leaves the shape, so the body is scanned as before
                ("cat > notes/x.md <<'EOF'\nsed -i 's/a/b/' src/app.py\nEOF\n"
                 "python3 -c \"open('src/app.py','w').write('x')\"", "sess-SF6",
                 "deny", "inplace", "a python3 -c line after the delimiter"),
                ("cat <<'EOF' | tee x.py\nhello\nEOF", "sess-SF7", "deny", "tee",
                 "cat heredoc piped to tee x.py"),
                ("cat f.py > notes/x.md <<'EOF'\nsed -i 's/a/b/' src/app.py\nEOF",
                 "sess-SF8", "deny", "inplace", "cat with a file operand"),
                ("cat > notes/x.md <<'EOF'\nEOF\nprintf x > src/app.py\nEOF",
                 "sess-SF9", "deny", "redirect",
                 "the FIRST delimiter line ends the body (a later EOF does not)"),
                ("cat > notes/x.md <<'EOF'\nprintf x > src/app.py", "sess-SF10",
                 "deny", "redirect", "no delimiter line"),
                ("cat\r > notes/x.md <<'EOF'\nprintf x > src/app.py\nEOF",
                 "sess-SF11", "deny", "redirect", "a carriage return is not a blank"),
                ("cat > notes/x.md 2>a.py <<'EOF'\nx\nEOF", "sess-SF12", "deny",
                 "redirect", "an extra redirect"),
            ]
            for cmd, sid, want, via, name in sf_cases:
                rc, out = run("bash_guard.py", bash_payload(cmd, sid=sid), sf_data)
                got = "deny" if decision(out) == "deny" else "allow"
                rec = sf_last(sid)
                check(f"S-F {want}: {name} (via {via})",
                      rc == 0 and got == want and bool(rec)
                      and rec.get("decision") == want and rec.get("via") == via,
                      f"out={out} rec={rec}")
            rec = sf_last("sess-SFa")
            check("S-F record: heredoc-data names the data target",
                  bool(rec) and rec.get("target") == "notes/x.md", str(rec))
        finally:
            shutil.rmtree(sf_data, ignore_errors=True)

        print("== gate.py / bash_guard.py decision observations (A10) ==")
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

        # K5: an observation is never load-bearing — a HELD observations lock must
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
        # the observation says why, and gate.py says it ONCE per session.
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

        # The canary and the generator share ONE comparison (tests/mirrors.py):
        # a mirror is exact, not "the body appears somewhere in it". Never
        # `--write` from here — a test that regenerates hides the drift.
        import mirrors
        mirror_problems = mirrors.drift()
        check("mirror drift: every codex-skills mirror matches its command exactly, none missing or orphaned "
              "(fix: python3 tests/mirrors.py --write)", mirror_problems == [], "; ".join(mirror_problems))
        mirror_tmp = tempfile.mkdtemp(prefix="hjw-test-mirrors-")
        try:
            m_cmds = os.path.join(mirror_tmp, "commands")
            m_skills = os.path.join(mirror_tmp, "codex-skills")
            shutil.copytree(os.path.join(PLUGIN, "commands"), m_cmds)
            shutil.copytree(os.path.join(PLUGIN, "codex-skills"), m_skills)
            check("mirror drift: a faithful temp copy has no drift",
                  mirrors.drift(m_cmds, m_skills) == [])
            # Bytes, not text: a CRLF-only rewrite of a mirror is drift.
            crlf_path = os.path.join(m_skills, "haejwo-status", "SKILL.md")
            crlf_orig = open(crlf_path, "rb").read()
            with open(crlf_path, "wb") as f:
                f.write(crlf_orig.replace(b"\n", b"\r\n"))
            check("mirror drift: a CRLF-only rewrite of a mirror is drift (compared as bytes)",
                  any("haejwo-status" in p and p.startswith("drift:")
                      for p in mirrors.drift(m_cmds, m_skills)))
            with open(crlf_path, "wb") as f:
                f.write(crlf_orig)
            # The canary and the generator share expected_mirror(), so pin it
            # against an INDEPENDENT golden pair: a frozen command and its
            # byte-exact expected mirror. A generator bug cannot validate its
            # own output here.
            fx_dir = os.path.join(HERE, "fixtures", "mirror-gate")
            fx_cmd = open(os.path.join(fx_dir, "command.md"), "rb").read()
            fx_want = open(os.path.join(fx_dir, "SKILL.md"), "rb").read()
            fx_got = mirrors.expected_mirror_bytes("gate", fx_cmd)
            check("mirror generator: expected_mirror() reproduces the golden fixture byte for byte "
                  "(tests/fixtures/mirror-gate)", fx_got == fx_want,
                  f"got {len(fx_got)} bytes, want {len(fx_want)}")
            os.makedirs(os.path.join(m_skills, "haejwo-retired"))
            with open(os.path.join(m_skills, "haejwo-retired", "SKILL.md"), "w") as f:
                f.write("---\nname: haejwo-retired\n---\n")
            check("mirror drift: an ORPHAN mirror (no command) is drift",
                  any("orphan mirror: codex-skills/haejwo-retired" in p
                      for p in mirrors.drift(m_cmds, m_skills)))
            shutil.rmtree(os.path.join(m_skills, "haejwo-retired"))
            with open(os.path.join(m_skills, "haejwo-gate", "SKILL.md"), "a") as f:
                f.write("hand edit\n")
            os.remove(os.path.join(m_skills, "haejwo-status", "SKILL.md"))
            got = mirrors.drift(m_cmds, m_skills)
            check("mirror drift: a hand-edited mirror and a missing mirror are both reported",
                  any("haejwo-gate" in p and p.startswith("drift:") for p in got)
                  and any("haejwo-status" in p and p.startswith("missing mirror:") for p in got), str(got))
        finally:
            shutil.rmtree(mirror_tmp, ignore_errors=True)

        print("== docs canaries (effort single-source, size ratchets, retired surfaces) ==")
        repo = os.path.dirname(HERE)

        # a. The reviewer-effort DEFAULT has exactly one home: the runner. The
        # rules a host reads must NAME that same word, or the operator is told
        # one effort and billed another.
        runner_src = open(os.path.join(SCRIPTS, "codex_consult.sh"), encoding="utf-8").read()
        runner_efforts = set(re.findall(r'EFFORT="(\w+)";\s*EFFORT_SRC="runner-default"', runner_src))
        rules_src = open(os.path.join(PLUGIN, "rules", "orchestration.md"),
                         encoding="utf-8-sig").read()
        # The rules name the codex runner's default inline (2.25.0 wording:
        # "medium (routine; codex default, claude: CLI default)").
        rules_efforts = set(re.findall(r"(\w+) \(routine; codex default", rules_src))
        check("reviewer effort default single-sourced (codex_consult.sh runner-default "
              "== rules/orchestration.md)",
              len(runner_efforts) == 1 and runner_efforts == rules_efforts,
              f"runner={sorted(runner_efforts)} rules={sorted(rules_efforts)}")

        # b. Size ratchets on the cold-start read path: a doc nobody finishes
        # reading is a doc that does not ship its rules. Each cap pins the
        # SHIPPED count rounded up to the next 10 — a ratchet, not a budget.
        for rel, cap in (("README.md", 740), ("README.ko.md", 650),
                         ("haejwo/README.md", 1470),
                         ("haejwo/commands/plan.md", 490),
                         ("haejwo/commands/setup.md", 900),
                         ("haejwo/commands/status.md", 310)):
            words = len(open(os.path.join(repo, rel), encoding="utf-8").read().split())
            check(f"word-count ratchet: {rel} <= {cap}", words <= cap,
                  f"words={words} cap={cap}")

        # b2. Runner-stack line ratchet (2.22): the eight files' total `wc -l`.
        # The list is explicit so a renamed or added file fails loudly here
        # instead of silently leaving the count.
        runner_stack = ("codex_consult.sh", "claude_consult.sh",
                        "lib/consult_common.sh", "lib/snapshot.py", "lib/detect.py",
                        "lib/forward.py", "lib/bounded.py", "lib/config.py")
        stack_missing = [f for f in runner_stack
                         if not os.path.isfile(os.path.join(SCRIPTS, f))]
        stack_lines = sum(open(os.path.join(SCRIPTS, f), "rb").read().count(b"\n")
                          for f in runner_stack if f not in stack_missing)
        stack_unlisted = sorted(
            {f for f in os.listdir(os.path.join(SCRIPTS, "lib"))
             if f.endswith((".sh", ".py"))}
            - {f[len("lib/"):] for f in runner_stack if f.startswith("lib/")})
        check("line ratchet: runner stack (8 files) <= 1800 lines",
              not stack_missing and not stack_unlisted and stack_lines <= 1800,
              f"lines={stack_lines} missing={stack_missing} unlisted_lib={stack_unlisted}")

        # c. Retired surfaces stay retired, across every tracked doc, script and
        # test. The tokens are assembled from FRAGMENTS on purpose: spelled out
        # as one literal, this file would be its own first offender.
        retired_re = re.compile("|".join([
            r"consults\.jsonl", r"telemetry\.py", "golden" + "_diff",
            "tests/" + "baseline", "Ultra" + "-fast", "HJW_" + "TEL_",
        ]))
        # The push-consent registry (retired 2.23.0). PHILOSOPHY.md is the ONE
        # exemption: its history and worked example name the retired command.
        retired_push_re = re.compile("|".join([
            "/haejwo:" + "push", "haejwo" + "-push", r"push\." + "auto_repos",
        ]))
        push_exempt = {"haejwo/PHILOSOPHY.md"}
        listed = subprocess.run(
            ["git", "-C", repo, "ls-files", "-z", "--",
             "README.md", "README.ko.md", "AGENTS.md", "haejwo", "tests"],
            capture_output=True, text=True, timeout=30)
        tracked_paths = [x for x in listed.stdout.split("\0") if x]
        offenders, push_offenders = [], []
        for rel in tracked_paths:
            full = os.path.join(repo, rel)
            # Tracked but absent from the worktree = a removal on its way into
            # the next commit, i.e. the retirement itself. Nothing to scan.
            if not os.path.isfile(full):
                continue
            text = open(full, encoding="utf-8", errors="replace").read()
            hits = sorted({m.group(0) for m in retired_re.finditer(text)})
            if hits:
                offenders.append(f"{rel} -> {','.join(hits)}")
            if rel not in push_exempt:
                push_hits = sorted({m.group(0) for m in retired_push_re.finditer(text)})
                if push_hits:
                    push_offenders.append(f"{rel} -> {','.join(push_hits)}")
        check("retired-surface guard: no live reference to the removed consult-telemetry "
              "or frozen-baseline surfaces",
              listed.returncode == 0 and bool(tracked_paths) and not offenders,
              f"rc={listed.returncode} scanned={len(tracked_paths)} offenders={offenders[:10]}")
        check("retired-surface guard: no live reference to the removed push-consent registry "
              "(only haejwo/PHILOSOPHY.md may name it)",
              listed.returncode == 0 and bool(tracked_paths) and not push_offenders,
              f"rc={listed.returncode} scanned={len(tracked_paths)} offenders={push_offenders[:10]}")

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
              and "models: deep-reasoner=session model, default-worker=opus (effort high), "
                  "task-worker=opus (effort low)" in ctx
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
        check("claude host: configured summary names the agent-file effort pins",
              "default-worker=opus (effort high), task-worker=opus (effort low) — "
              "pass as Agent-tool model override" in ctx, ctx[-600:])

        # F8 (2.20): the summary's effort words are the agent files' pins, and
        # the frontmatter parser still reads `model:` past the new comment +
        # `effort:` lines of the REAL shipped default-worker.md.
        def _fm_effort(name):
            text = open(os.path.join(PLUGIN, "agents", name + ".md"), encoding="utf-8").read()
            fm = re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1)
            m_ = re.search(r"^effort:\s*(\S+)", fm, re.M)
            return m_.group(1) if m_ else None
        check("F8 agent-file effort pins: default-worker high, task-worker low, "
              "deep-reasoner none (inherits the session)",
              (_fm_effort("default-worker"), _fm_effort("task-worker"),
               _fm_effort("deep-reasoner")) == ("high", "low", None),
              str((_fm_effort("default-worker"), _fm_effort("task-worker"),
                   _fm_effort("deep-reasoner"))))
        check("F8 delegation_gate parser reads model: 'opus' from the real "
              "default-worker.md (comment + effort lines present)",
              _agent_file_default(PLUGIN, "default-worker") == "opus",
              str(_agent_file_default(PLUGIN, "default-worker")))

        # L7: a full-id pin cannot be passed through the Agent tool — say so.
        with open(os.path.join(data, "config.json"), "w") as f:
            json.dump({"configured": True,
                       "gate": {"enabled": True, "max_files_per_turn": 2, "bash_guard": True},
                       "models": {"task_worker": "claude-sonnet-5-5",
                                  "default_worker": "sonnet"}}, f)
        rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, data)
        l7_ctx = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("L7 claude host: full-id pin carries the not-passable suffix; alias does not",
              "task-worker=claude-sonnet-5-5 (not passable via the Agent tool — set an alias)"
              in l7_ctx and "default-worker=sonnet (effort high)," in l7_ctx, l7_ctx[-600:])
        with open(os.path.join(data, "config.json"), "w") as f:
            json.dump({"configured": True,
                       "gate": {"enabled": True, "max_files_per_turn": 2, "bash_guard": True}}, f)

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
            check("A6 Codex defaults summary: host model, deep-reasoner at the host's "
                  "own effort, per-role efforts below it",
                  "codex tiers: deep-reasoner=host model/host effort (omit "
                  "reasoning_effort), default-worker=host "
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

        print("== session_brief.render_summary: one renderer, both states (B6) ==")
        # cold-loop B6: the configured and not-configured summaries used to be
        # rendered by two copies of the tier code; one function now renders
        # both, byte-identical to what the two copies produced.
        from session_brief import render_summary  # noqa: E402
        _g = DEFAULT_CONFIG["gate"]
        check("B6 not-configured Claude defaults summary, exact",
              render_summary(_g, DEFAULT_CONFIG["models"], False, False, False,
                             "defaults — not configured: ")
              == "[haejwo config] defaults — not configured: gate=ON budget=2 "
                 "files/turn bash_guard=ON delegation_guard=ON | models: "
                 "deep-reasoner=session model, "
                 "default-worker=opus (effort high), task-worker=opus (effort low) "
                 "| codex reviewer: disabled (fallback: deep-reasoner)")
        check("B6 configured Codex: missing keys fall back to the shipped defaults, exact",
              render_summary({"enabled": False, "max_files_per_turn": 4,
                              "bash_guard": False}, {}, True, True, True)
              == "[haejwo config] gate=OFF budget=4 files/turn bash_guard=OFF "
                 "delegation_guard=OFF | codex tiers (pass model + reasoning_effort on spawn_agent; "
                 "'inherit' = omit model; effort overrides need a fresh or partial "
                 "context fork (fork_turns), never a full-history fork): "
                 "deep-reasoner=inherit/host effort (omit reasoning_effort), "
                 "default-worker=inherit/medium, task-worker=inherit/low | "
                 "claude reviewer: enabled")
        check("B6 configured Claude: inherit + non-passable pin rendering, exact",
              render_summary(_g, {"deep_reasoner": "inherit",
                                  "default_worker": "claude-opus-5-5",
                                  "task_worker": "inherit"}, False, True, False)
              == "[haejwo config] gate=ON budget=2 files/turn bash_guard=ON "
                 "delegation_guard=ON | models: deep-reasoner=inherit(session), "
                 "default-worker="
                 "claude-opus-5-5 (not passable via the Agent tool — set an alias) "
                 "(effort high), task-worker=agent-file default (effort low) — pass "
                 "as Agent-tool model override if it differs from the agent default "
                 "(inherit = omit the model override) On Claude, omitting the model "
                 "override uses each agent file's default; pass an explicit model to "
                 "override it. | codex reviewer: disabled (fallback: deep-reasoner)")

        print("== delegation_gate docstring names every tier_pin_check value (D2) ==")
        # The envelope docstring was shrunk to one line per field (cold-loop
        # D2); this canary keeps every value the code can record documented.
        import delegation_gate as _dg  # noqa: E402
        with open(os.path.join(SCRIPTS, "delegation_gate.py"), encoding="utf-8") as f:
            _dg_src = f.read()
        _emitted = set(re.findall(r'"((?:pass|skip):[a-z-]+)"', _dg_src)) | {"deny"}
        _undoc = sorted(v for v in _emitted if f'"{v}"' not in _dg.__doc__)
        check("D2 every recorded tier_pin_check value is in the module docstring",
              len(_emitted) >= 9 and not _undoc, f"emitted={sorted(_emitted)} undoc={_undoc}")

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

        # The fallback data dir is the one Claude Code really creates
        # (`<marketplace>-<plugin>`), not a bare `haejwo` nobody writes to.
        import hjw_common as _hjw_common
        _saved_data = os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        try:
            _fallback = _hjw_common.paths(["hook"])[1]
        finally:
            if _saved_data is not None:
                os.environ["CLAUDE_PLUGIN_DATA"] = _saved_data
        check("paths(): no CLAUDE_PLUGIN_DATA and no argv -> ~/.claude/plugins/data/haejwo-haejwo",
              _fallback == os.path.expanduser("~/.claude/plugins/data/haejwo-haejwo"), _fallback)

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

            # 2.26 (cold-loop B2): a non-object `models` falls back to the
            # shipped defaults at load, so the deny names the default pin and
            # carries the once-note — still the Claude wording, never Codex's.
            rc, dec, r = a12("not-a-dict", "sess-A12f")
            check("A12 malformed models on Claude: denies with the default pins + the note",
                  rc == 0 and dec == "deny" and "Pass model: 'opus'" in r
                  and "config value ignored: models must be an object" in r
                  and CODEX_TIER_ONLY not in r, r)
        finally:
            shutil.rmtree(a12_data, ignore_errors=True)

        print("== delegation_gate.py envelope v2 (plan_marker_kind) ==")
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
        check("envelope v2: prompt_bytes is no longer recorded (removed in 2.22)",
              bool(r_plan) and "prompt_bytes" not in r_plan, str(r_plan))

        r_noplan = _last(lambda r: r.get("sid") == "sess-PM2")
        check("envelope v2: 'No plan because' prompt -> plan_marker_kind 'no_plan'",
              bool(r_noplan) and r_noplan.get("plan_marker_kind") == "no_plan", str(r_noplan))

        r_none = _last(lambda r: r.get("sid") == "sess-PM3")
        check("envelope v2: bare prompt -> plan_marker_kind 'none'",
              bool(r_none) and r_none.get("plan_marker_kind") == "none", str(r_none))

        # (P2, 2.17) the marker is a LABEL AT THE HEAD OF A LINE, however
        # markdown dresses it. Measured 2026-09-28: a delegation brief carried
        # the agreed plan under `## Plan (합의본 — …)` and the substring match
        # recorded plan_marker_kind=none, which status would have counted as
        # drift. Telemetry only — it records the marker, never a real consensus.
        from delegation_gate import _plan_marker_kind as marker_kind  # noqa: E402
        PLAN_FORMS = [
            ("Plan: per the approved decision — do X", "bare colon (legacy)"),
            ("**Plan**: do X", "bold label, colon outside"),
            ("**Plan:** do X", "bold label, colon inside"),
            ("## Plan (합의본 — host + reviewer)", "heading + parenthetical"),
            ("Plan — the agreed shape", "em dash"),
            ("Plan - the agreed shape", "hyphen"),
            ("### Plan", "heading, end of line"),
            ("  ### **Plan**", "indented heading + bold, end of line"),
            ("Context first.\n\n## Plan (x)\nbody", "heading on a later line"),
            ("## Plan\r\nbody", "CRLF heading (a brief written with CRLF)"),
            ("- **Plan**: do X", "list bullet + bold label"),
            ("* **Plan**: do X", "asterisk bullet + bold label"),
        ]
        for text, label in PLAN_FORMS:
            check(f"plan marker: {label} -> 'plan'", marker_kind(text) == "plan", repr(text))

        NO_PLAN_FORMS = [
            ("No plan because: mechanical rename", "bare colon (legacy)"),
            ("No plan because mechanical rename", "prose (legacy substring)"),
            ("**No plan because**: mechanical", "bold label, colon outside"),
            ("**No plan because:** mechanical", "bold label, colon inside"),
            ("## No plan because (mechanical)", "heading + parenthetical"),
            ("### No plan because", "heading, end of line"),
            ("- **No plan because**: mechanical", "list bullet + bold label"),
            ("## No plan because\r\nmechanical", "CRLF heading"),
        ]
        for text, label in NO_PLAN_FORMS:
            check(f"no-plan marker: {label} -> 'no_plan'",
                  marker_kind(text) == "no_plan", repr(text))

        # Negatives: prose that merely CONTAINS the word is not a marker. The
        # spacing classes are [ \t] on purpose — \s would have let a "Plan"
        # on one line pair with a separator on the next.
        NEGATIVE_FORMS = [
            ("Planning notes for the migration", "'Planning notes' prose"),
            ("Plan to investigate the flake first", "'Plan to investigate' prose"),
            ("The rename is already planned", "lowercase 'planned'"),
            ("Plan-driven development is not a marker", "'Plan-driven' compound"),
            ("Plan to investigate\n: the separator is on the NEXT line",
             "separator on the NEXT line"),
            ("Plan to investigate\r\n: the separator is on the NEXT line",
             "CRLF separator on the NEXT line"),
            ("- rename the files in place", "a bullet without the label"),
        ]
        for text, label in NEGATIVE_FORMS:
            check(f"plan marker negative: {label} -> 'none'",
                  marker_kind(text) == "none", repr(text))

        check("plan marker: precedence unchanged — a plan label wins over 'No plan because'",
              marker_kind("## No plan because (x)\n## Plan (y)") == "plan")
        check("plan marker: non-string input still short-circuits to 'none'",
              marker_kind(None) == "none" and marker_kind({"x": 1}) == "none")

        # ...and the heading form reaches the RECORD, not just the parser.
        rc, out = run("delegation_gate.py", task_payload(
            "haejwo:default-worker", sid="sess-PM6",
            prompt="Brief body.\n\n## Plan (합의본 — host + reviewer)\nDo X."), data)
        check("allow: heading-form plan marker", decision(out) != "deny", str(out))
        r_head = [json.loads(l) for l in open(obs_file)]
        r_head = [r for r in r_head if r.get("sid") == "sess-PM6"]
        check("envelope v2: '## Plan (…)' heading -> plan_marker_kind 'plan'",
              bool(r_head) and r_head[-1].get("plan_marker_kind") == "plan", str(r_head[-1:]))

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

        # (b) non-string prompt (dict) -> no crash, allow, plan_marker_kind 'none'
        rc, out = run("delegation_gate.py", task_payload(
            "haejwo:default-worker", sid="sess-PM5", prompt={"x": 1}), data)
        check("allow: non-string (dict) prompt -> no crash", rc == 0 and decision(out) != "deny", str(out))
        r_dict = _last_fresh(lambda r: r.get("sid") == "sess-PM5")
        check("envelope: non-string prompt -> plan_marker_kind 'none'",
              bool(r_dict) and r_dict.get("plan_marker_kind") == "none", str(r_dict))

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

            # L7 (2.19.0): the Claude Code Agent tool's `model` accepted only
            # the MEASURED aliases sonnet/opus/haiku (2026-10-02). Any other
            # pin (full id, unknown word, account-specific alias) can't be
            # relied on to pass, so denying would brick that tier with an
            # unfollowable steer. Allow, record 'skip:pin-not-passable', note
            # ONCE per session per role.
            def l7_note(pin):
                return (
                    f"[haejwo] task_worker: configured pin `{pin}` is not enforced; "
                    f"allowing the agent-file default `opus` (`{pin}` is outside the "
                    f"measured passable aliases sonnet/opus/haiku). To enforce a pin, "
                    f"set an alias in /haejwo:setup, or pass the model explicitly if "
                    f"your session's Agent tool offers it."
                )

            def l7_ctx(sid):
                rc_, out_ = run("delegation_gate.py",
                                task_payload("haejwo:task-worker", sid=sid), pin_data)
                return rc_, out_, ((out_.get("hookSpecificOutput") or {})
                                   .get("additionalContext") or "")

            for l7_pin, l7_sid, l7_label in (
                    ("claude-sonnet-5-5", "sess-PINX", "full-id"),
                    ("banana", "sess-PINBN", "unknown word"),
                    ("fable", "sess-PINFB", "account-specific alias")):
                pin_cfg({"task_worker": l7_pin})
                notes = []
                for _ in range(2):
                    rc, out, ctx_ = l7_ctx(l7_sid)
                    check(f"L7 {l7_label} pin `{l7_pin}`, no model -> allow",
                          rc == 0 and decision(out) != "deny", str(out))
                    notes.append(ctx_)
                    check(f"L7 {l7_label} record: tier_pin_check 'skip:pin-not-passable'",
                          (pin_rec(l7_sid) or {}).get("tier_pin_check")
                          == "skip:pin-not-passable", str(pin_rec(l7_sid)))
                check(f"L7 {l7_label}: note present exactly once across two calls",
                      notes[0] == l7_note(l7_pin) and notes[1] == "", str(notes))
                check(f"L7 {l7_label}: note claims no execution",
                      " ran" not in notes[0], notes[0])

            pin_cfg({"task_worker": "claude-sonnet-5-5"})
            run("turn_reset.py", {"session_id": "sess-PINX", "prompt_id": "p2"}, pin_data)
            rc, out, ctx_ = l7_ctx("sess-PINX")
            check("L7 note stays once per SESSION (survives turn_reset)", not ctx_, str(out))

            # F1: gate.py's lazy (prompt_id change) and stale resets rebuild
            # the session state — the once-note flags must survive BOTH.
            for f1_sid, f1_kind in (("sess-PINLZ", "lazy"), ("sess-PINST", "stale")):
                rc, out, ctx_ = l7_ctx(f1_sid)
                check(f"F1 {f1_kind}: first delegation emits the note",
                      ctx_ == l7_note("claude-sonnet-5-5"), ctx_)
                f1_state = os.path.join(pin_data, "state", f1_sid + ".json")
                with open(f1_state) as f:
                    st = json.load(f)
                if f1_kind == "lazy":
                    f1_pid = "p-next"
                else:
                    f1_pid = st.get("prompt_id") or "p1"
                    st["prompt_id"] = f1_pid
                    st["files"] = ["/repo/f1/old.py"]
                    st["updated_at"] = st.get("updated_at", time.time()) - 8000
                    with open(f1_state, "w") as f:
                        json.dump(st, f)
                rc, out = run("gate.py",
                              edit_payload(f"/repo/f1/{f1_kind}.py", sid=f1_sid, pid=f1_pid),
                              pin_data)
                with open(f1_state) as f:
                    st_after = json.load(f)
                check(f"F1 {f1_kind}: the Edit reset the turn counter",
                      decision(out) != "deny"
                      and st_after.get("files") == [f"/repo/f1/{f1_kind}.py"],
                      str(st_after))
                check(f"F1 {f1_kind}: the once-note flag survived the gate reset",
                      st_after.get("pin_unpassable_noted") == ["task_worker"],
                      str(st_after))
                rc, out, ctx_ = l7_ctx(f1_sid)
                check(f"F1 {f1_kind}: delegation after the reset -> NO second note",
                      not ctx_, ctx_)

            # F1: turn_reset holds the session state lock around its
            # read/reset/save — hold it from the TEST and prove the child
            # writes nothing until released.
            tr_sid = "sess-TRL"
            tr_lock = os.path.join(pin_data, "state", tr_sid + ".json.lock")
            tr_state = os.path.join(pin_data, "state", tr_sid + ".json")
            os.makedirs(os.path.dirname(tr_lock), exist_ok=True)
            tr_holder = open(tr_lock, "w")
            tr_child = None
            try:
                fcntl.flock(tr_holder, fcntl.LOCK_EX)
                tr_child = subprocess.Popen(
                    ["python3", os.path.join(SCRIPTS, "turn_reset.py"), PLUGIN, pin_data],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                tr_child.stdin.write(json.dumps({"session_id": tr_sid, "prompt_id": "p9"}))
                tr_child.stdin.close()
                tr_blocked_until = time.time() + 0.7
                tr_blocked = True
                while time.time() < tr_blocked_until:
                    if os.path.exists(tr_state) or tr_child.poll() is not None:
                        tr_blocked = False
                        break
                    time.sleep(0.05)
                check("F1 turn_reset: blocked on the held session lock (no state written)",
                      tr_blocked)
                fcntl.flock(tr_holder, fcntl.LOCK_UN)
                try:
                    tr_rc = tr_child.wait(timeout=5)
                except Exception:
                    tr_rc = None
                check("F1 turn_reset: completes once released, state written",
                      tr_rc == 0 and os.path.exists(tr_state), f"rc={tr_rc}")
            finally:
                try:
                    fcntl.flock(tr_holder, fcntl.LOCK_UN)
                except Exception:
                    pass
                tr_holder.close()
                if tr_child and tr_child.poll() is None:
                    tr_child.kill()

            pin_cfg({"task_worker": "sonnet"})
            rc, dec, r = pin_run("haejwo:task-worker", "sess-PINY")
            check("L7 alias pin that differs (sonnet vs opus) still DENIES",
                  dec == "deny" and "defaults to 'opus'" in r, r)
            check("L7 alias pin record: tier_pin_check 'deny'",
                  (pin_rec("sess-PINY") or {}).get("tier_pin_check") == "deny",
                  str(pin_rec("sess-PINY")))
        finally:
            shutil.rmtree(pin_data, ignore_errors=True)

        classification_state_tests()
        config_host_summary_tests()

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
            def make_repo(name, base=None):
                # `base` lets a fixture build its repos under its OWN root
                # instead of the shared one.
                d = os.path.join(base or runner_tmp, name)
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
            # env vars, globally (STUB_RC) or per call index (STUB_RC_2).
            STUB_BODY = r'''#!/usr/bin/env bash
set +u
CAP="__CAP__"
mkdir -p "$CAP"
if [ "${1:-}" = "--version" ]; then
  # The probe is still not a reviewer call — but it is COUNTED, in a file of
  # its own, so a differential can compare probes and calls separately
  # instead of conflating them.
  vf="$CAP/_version_calls"
  if [ -f "$vf" ]; then printf '%s\n' "$(( $(cat "$vf") + 1 ))" > "$vf"; else echo 1 > "$vf"; fi
  echo "stub-version 0.0.0"
  exit 0
fi
idx_file="$CAP/_idx"
if [ -f "$idx_file" ]; then idx=$(( $(cat "$idx_file") + 1 )); else idx=1; fi
echo "$idx" > "$idx_file"
printf '%s\n' "$@" > "$CAP/call_${idx}.argv"
# NUL-delimited argv alongside the newline form: an argument that CONTAINS a
# newline is indistinguishable from two arguments in the line-based file. The
# line form stays for the fixtures that already read it.
printf '%s\0' "$@" > "$CAP/call_${idx}.argv0"
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
# The exported environment the reviewer ACTUALLY receives and the umask it
# inherits are part of the contract too — a refactor that drops an export or
# changes the mask is invisible in stdout/stderr. `sort` may be absent from a
# minimal PATH fixture, so its absence must not break the dump (the reader
# sorts as well).
if command -v sort >/dev/null 2>&1; then
  env -0 | sort -z > "$CAP/call_${idx}.env0"
else
  env -0 > "$CAP/call_${idx}.env0"
fi
umask > "$CAP/call_${idx}.umask"
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
if [ -n "$se" ] && [ -f "$se" ]; then cat "$se" >&2; fi
if [ -n "$touchf" ]; then printf 'mutated by stub call %s\n' "$idx" > "$touchf"; fi
if [ -n "$gitc" ]; then git commit --allow-empty -q -m "stub commit $idx" >/dev/null 2>&1; fi
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

            def empty_data_dir_named(prefix):
                """A fresh EMPTY data dir NAMED `haejwo-haejwo`. Since 2.17 a
                CLAUDE_PLUGIN_DATA whose basename is anything else is foreign
                and IGNORED — a fixture dir named `nocfg-xxxx` would fall
                through to the developer's real ~/.claude config, which is the
                opposite of hermetic."""
                return os.path.join(
                    tempfile.mkdtemp(dir=runner_tmp, prefix=prefix), "haejwo-haejwo")

            def run_script(script, args, extra_env, stdin_data="", cwd=None):
                """Runner invocation with a HERMETIC env: every runner-read
                env var is popped unless the fixture sets it, and
                CLAUDE_PLUGIN_DATA points at a fresh empty dir NAMED
                `haejwo-haejwo` (the only name the runner accepts) so the
                derived (~/.claude/...) config path is never consulted by
                accident. Pass CLAUDE_PLUGIN_DATA=None to opt out (the
                derived-path fixtures need the real resolution)."""
                env = dict(os.environ)
                # Every env var a runner reads, popped so an ambient value can
                # never reach a fixture. CODEX_ALLOW_MARKERS is gone from this
                # list because 2.14 deleted it from the runner.
                for var in ("CODEX_MODEL", "CODEX_EFFORT", "CODEX_SANDBOX",
                            "CLAUDE_MODEL", "CODEX_TIMEOUT", "CLAUDE_TIMEOUT",
                            "CLAUDE_PLUGIN_DATA", "CLAUDE_CODE_SESSION_ID",
                            "HJW_CLAUDE_EFFORT"):
                    env.pop(var, None)
                if "CLAUDE_PLUGIN_DATA" not in extra_env:
                    env["CLAUDE_PLUGIN_DATA"] = empty_data_dir_named("nocfg-")
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
                """`name` is only the unique PARENT: the data dir itself is
                always named `haejwo-haejwo`, because that is the only
                basename a runner will accept from CLAUDE_PLUGIN_DATA (2.17
                ownership rule)."""
                d = os.path.join(runner_tmp, name, "haejwo-haejwo")
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
                preflight work, so there is nothing to clean up."""
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

            # B1/B2 (2.14): CODEX_ALLOW_MARKERS was DELETED — there is no way to
            # switch the anchored scan off any more. The variable is set here on
            # purpose: a stale value must not resurrect the opt-out. What the knob
            # used to be needed for is asserted instead — a failing run keeps the
            # reply it captured, byte for byte, so the operator can read it and
            # decide. (A hook-block line stays a note, not a failure: K8 below.)
            rc, out, err, calls = codex_run("trace-no-optout", {
                "STUB_EVENTS_FILE": ev, "STUB_STDERR_FILE": trace_stderr,
                "CODEX_ALLOW_MARKERS": "1"})
            no_optout_reply = os.path.join(runner_tmp, "brief-trace-no-optout.reply.md")
            reply_bytes = (open(no_optout_reply, "rb").read()
                           if os.path.isfile(no_optout_reply) else b"<absent>")
            check("stderr scan: no opt-out exists (CODEX_ALLOW_MARKERS deleted) -> an "
                  "anchored tracing line still FAILS the run",
                  rc != 0 and "codex tracing error:" in err
                  and "codex_core::exec" in err, f"rc={rc} err={err}")
            check("stderr scan: the failing run PRESERVES the captured reply intact",
                  reply_bytes == b"STUB-REPLY-OK-1\n", reply_bytes)

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
            check("effort: invalid CONFIG value -> one note + runner-default medium, run continues",
                  rc == 0 and "note: config codex.effort 'insane' invalid; using runner-default medium" in err
                  and 'model_reasoning_effort="medium"' in (calls[0][0] if calls else [])
                  and "effort=medium (runner-default)" in out,
                  f"rc={rc} err={err} out={out}")
            # 2.14: the wall clock is DECOUPLED from effort. The old table gave
            # medium 300s, so a default-effort change would silently have halved
            # every unconfigured run's budget — the default is 600s at every level.
            check("timeout: the runner default is 600s at the runner-default effort",
                  "effort=medium (runner-default)" in out and "timeout=600s" in err,
                  f"out={out} err={err}")
            rc2, out2, err2, calls2 = codex_run("timeout-effort-low", {"CODEX_EFFORT": "low"})
            check("timeout: 600s at effort=low too (no effort->timeout table left)",
                  rc2 == 0 and "effort=low (env)" in out2 and "timeout=600s" in err2,
                  f"rc={rc2} out={out2} err={err2}")
            rc2, out2, err2, calls2 = codex_run("timeout-env-wins",
                                                {"CODEX_TIMEOUT": "42"})
            check("timeout: CODEX_TIMEOUT still overrides the 600s default",
                  rc2 == 0 and "timeout=42s" in err2, f"rc={rc2} err={err2}")

            rc, out, err, calls = codex_run("effort-env-invalid", {"CODEX_EFFORT": "insane"})
            check("effort: invalid ENV value -> exit 2 naming all four valid values",
                  rc == 2 and all(v in (out + err) for v in ("low", "medium", "high", "xhigh")),
                  f"rc={rc} err={err}")
            check("effort: invalid ENV value -> codex never invoked", len(calls) == 0, calls)

            # ---- (F8) non-string config values are noted, never used ----
            junk_cfg = cfg_dir_with("plugin-data-junk", {"codex": {
                "model": 123, "effort": []}})
            rc, out, err, calls = codex_run("config-nonstring", {"CLAUDE_PLUGIN_DATA": junk_cfg})
            check("config: non-string values -> one note per key, defaults used",
                  rc == 0
                  and "note: config codex.model ignored (not a string)" in err
                  and "note: config codex.effort ignored (not a string)" in err
                  and "model=cli-default (identity unverified)" in out
                  and "effort=medium (runner-default)" in out,
                  f"rc={rc} err={err} out={out}")

            # ---- (F9) the `codex` config block describes the HOST's reviewer:
            # on a codex host that reviewer is Claude, so the codex runner must
            # ignore model/effort there. ----
            codex_host_cfg = cfg_dir_with(os.path.join("host-codex", ".codex", "plugins", "data"),
                                          {"codex": {"model": "claude-reviewer-model"}})
            rc, out, err, calls = codex_run("model-codex-host", {"CLAUDE_PLUGIN_DATA": codex_host_cfg})
            check("host-relative config: codex runner ignores codex.model under a /.codex/ path",
                  "model=cli-default (identity unverified)" in out
                  and argv_value(calls[0][0] if calls else [], "-m") is None,
                  f"out={out} argv={calls[:1]}")

            # ---- (i, 2.22) a model codex rejects PRE-EXECUTION is never
            # retried: the run fails with codex's own error plus one hint
            # naming CODEX_MODEL / codex.model. ----
            pre_exec_fail = events_file("ev-unknown-model-preexec.jsonl", [
                '{"type":"thread.started","thread_id":"stub"}',
                '{"type":"turn.failed","error":{"message":"unknown model: totally-fake-model"}}',
            ])
            bin_dir = os.path.join(runner_tmp, "bin-model-rejected")
            cap = os.path.join(runner_tmp, "cap-model-rejected")
            make_stub(bin_dir, "codex", cap)
            mr_out = os.path.join(runner_tmp, "model-rejected-reply.md")
            rc, out, err = run_script(codex_script, ["-o", mr_out, brief_file("mr-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "CODEX_MODEL": "totally-fake-model",
                "STUB_EVENTS_FILE": pre_exec_fail,
                "STUB_RC": "1", "STUB_NO_OUT": "1",
            })
            mr_hints = [l for l in err.splitlines() if l.strip().startswith("hint:")]
            check("model rejected pre-execution: exactly ONE codex invocation (no retry), run fails",
                  rc != 0 and len(read_calls(cap)) == 1, f"rc={rc} calls={len(read_calls(cap))}")
            check("model rejected pre-execution: codex's own error is reported",
                  "codex reported turn.failed: unknown model: totally-fake-model" in err, err)
            check("model rejected pre-execution: exactly one hint line naming CODEX_MODEL and codex.model",
                  len(mr_hints) == 1 and "CODEX_MODEL" in mr_hints[0]
                  and "codex.model" in mr_hints[0] and "totally-fake-model" in mr_hints[0],
                  f"hints={mr_hints} err={err}")
            check("model rejected pre-execution: no .events.2.jsonl or $OUT.tmp is written",
                  not os.path.exists(os.path.join(runner_tmp, "model-rejected-reply.events.2.jsonl"))
                  and not os.path.exists(mr_out + ".tmp"), mr_out)

            post_exec_fail = events_file("ev-unknown-model-postexec.jsonl", [
                '{"type":"thread.started","thread_id":"stub"}',
                '{"type":"item.started","item":{"type":"command_execution"}}',
                '{"type":"turn.failed","error":{"message":"model not available: totally-fake-model"}}',
            ])
            bin_dir = os.path.join(runner_tmp, "bin-model-rejected-postexec")
            cap = os.path.join(runner_tmp, "cap-model-rejected-postexec")
            make_stub(bin_dir, "codex", cap)
            rc, out, err = run_script(codex_script, [brief_file("mr-post-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "CODEX_MODEL": "totally-fake-model",
                "STUB_EVENTS_FILE": post_exec_fail,
                "STUB_RC": "1", "STUB_NO_OUT": "1",
            })
            check("model failure AFTER item.started: one invocation, failure, no pre-execution hint",
                  rc != 0 and len(read_calls(cap)) == 1 and "hint:" not in err,
                  f"rc={rc} calls={len(read_calls(cap))} err={err}")

            # A stored `fallback_model` key (retry removed in 2.22) is ignored
            # silently: unknown config keys are skipped.
            fb_cfg = cfg_dir_with("plugin-data-fallback",
                                  {"codex": {"fallback_model": "fb-model"}})
            rc, out, err, calls = codex_run("stored-fbkey-ignored", {"CLAUDE_PLUGIN_DATA": fb_cfg})
            check("config: a stored codex.fallback_model is ignored silently (one call, no -m, no note)",
                  rc == 0 and len(calls) == 1
                  and argv_value(calls[0][0], "-m") is None
                  and "fallback_model" not in err and "fb-model" not in out + err
                  and "model=cli-default (identity unverified)" in out,
                  f"rc={rc} calls={len(calls)} out={out} err={err}")

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

            # ---- (2.21) artifact guard: no runner artifact inside the repo ----
            # The log header truncates $LOG and `rm -f` clears $OUT (and the
            # events), while change detection EXCLUDES every artifact by design
            # — so before 2.21 a typo in `-o` naming a project file overwrote
            # it silently. Every artifact path is now refused before the first
            # write: exit 2, one line, no paid call, no file touched.
            ag_root = tempfile.mkdtemp(dir=runner_tmp, prefix="artguard-")
            ag_repo = make_repo("repo", base=ag_root)
            ag_tracked = write_file(os.path.join(ag_repo, "tracked.md"), "committed content\n")
            write_file(os.path.join(ag_repo, "sub", "keep.md"), "sub\n")
            subprocess.run(["git", "-C", ag_repo, "add", "-A"], check=True, capture_output=True)
            subprocess.run(["git", "-C", ag_repo, "commit", "-q", "-m", "seed"],
                           check=True, capture_output=True)
            ag_tracked_bytes = open(ag_tracked, "rb").read()
            ag_out = os.path.join(ag_root, "outside")
            os.makedirs(ag_out, exist_ok=True)
            ag_brief = write_file(os.path.join(ag_out, "brief.md"), "Test brief body.\n")
            # outside -> INTO the repo (a directory link, so the reply is new)
            os.symlink(ag_repo, os.path.join(ag_out, "into-repo"))
            # inside the repo -> OUTSIDE (the lexical path is what is inside)
            os.symlink(os.path.join(ag_out, "target.md"), os.path.join(ag_repo, "out-link.md"))
            # an outside artifact hard-linked to a second outside name
            ag_hard = write_file(os.path.join(ag_out, "hard.md"), "old reply\n")
            os.link(ag_hard, os.path.join(ag_out, "hard-twin.md"))
            ag_wt = os.path.join(ag_root, "linked-wt")
            subprocess.run(["git", "-C", ag_repo, "worktree", "add", "-q", "--detach", ag_wt],
                           check=True, capture_output=True)
            ag_nongit = os.path.join(ag_root, "nongit")
            os.makedirs(ag_nongit, exist_ok=True)

            def ag_run(who, slug, args, cwd):
                bin_dir = os.path.join(ag_root, f"bin-{who}-{slug}")
                cap = os.path.join(ag_root, f"cap-{who}-{slug}")
                make_stub(bin_dir, who, cap)
                rc, out, err = run_script(codex_script if who == "codex" else claude_script,
                                          args, {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")},
                                          cwd=cwd)
                # `called`: a real reviewer call was recorded. `traced`: the
                # stub left ANY trace — a refusal must not even reach the
                # `--version` probe.
                called = os.path.isfile(os.path.join(cap, "call_1.stdin"))
                traced = os.path.isdir(cap) and bool(os.listdir(cap))
                return rc, out, err, (called, traced)

            def ag_refused(rc, err, stub, reason="artifact path is inside the reviewed repository"):
                lines = err.strip().splitlines()
                return (rc == 2 and not stub[1] and len(lines) == 1 and reason in lines[0]
                        and "pass -o with a path outside it" in lines[0])

            ag_inside = "artifact path is inside the reviewed repository"
            for who in ("codex", "claude"):
                tag = f"artifact guard ({who})"
                ag_status = subprocess.run(["git", "-C", ag_repo, "status", "--porcelain"],
                                           capture_output=True, text=True).stdout
                rc, out, err, stub = ag_run(who, "tracked", ["-o", ag_tracked, ag_brief], ag_repo)
                check(f"{tag}: -o on a TRACKED file -> exit 2, one refusal line, reviewer never called",
                      ag_refused(rc, err, stub), f"rc={rc} stub={stub} err={err}")
                check(f"{tag}: -o on a TRACKED file -> the file's bytes are unchanged",
                      open(ag_tracked, "rb").read() == ag_tracked_bytes)
                check(f"{tag}: -o on a TRACKED file -> no log or events created, git status unchanged",
                      not os.path.exists(os.path.join(ag_repo, "tracked.log"))
                      and not os.path.exists(os.path.join(ag_repo, "tracked.events.jsonl"))
                      and subprocess.run(["git", "-C", ag_repo, "status", "--porcelain"],
                                         capture_output=True, text=True).stdout == ag_status)
                rc, out, err, stub = ag_run(who, "untracked",
                                              ["-o", os.path.join(ag_repo, "new-reply.md"), ag_brief],
                                              ag_repo)
                check(f"{tag}: -o on an untracked path inside the repo -> refused",
                      ag_refused(rc, err, stub) and not os.path.exists(
                          os.path.join(ag_repo, "new-reply.md")), f"rc={rc} err={err}")
                rc, out, err, stub = ag_run(who, "relative", ["-o", "rel-reply.md", ag_brief], ag_repo)
                check(f"{tag}: a RELATIVE -o resolving inside the repo -> refused",
                      ag_refused(rc, err, stub), f"rc={rc} err={err}")
                rc, out, err, stub = ag_run(who, "via-link",
                                              ["-o", os.path.join(ag_out, "into-repo", "r.md"), ag_brief],
                                              ag_repo)
                check(f"{tag}: -o through an outside symlink pointing INTO the repo -> refused",
                      ag_refused(rc, err, stub), f"rc={rc} err={err}")
                rc, out, err, stub = ag_run(who, "in-link",
                                              ["-o", os.path.join(ag_repo, "out-link.md"), ag_brief],
                                              ag_repo)
                check(f"{tag}: -o on an in-repo symlink pointing OUTSIDE -> refused",
                      ag_refused(rc, err, stub)
                      and not os.path.exists(os.path.join(ag_out, "target.md")), f"rc={rc} err={err}")
                rc, out, err, stub = ag_run(who, "outside",
                                              ["-o", os.path.join(ag_out, f"{who}-reply.md"), ag_brief],
                                              ag_repo)
                check(f"{tag}: -o outside the repo -> the review runs",
                      rc == 0 and stub[0], f"rc={rc} err={err}")
                in_brief = write_file(os.path.join(ag_repo, f"{who}-brief.md"), "Test brief body.\n")
                rc, out, err, stub = ag_run(who, "brief-in", [in_brief], ag_repo)
                check(f"{tag}: no -o and the brief inside the repo -> refused (default reply inside)",
                      ag_refused(rc, err, stub) and f"{who}-brief.reply.md" in err, f"rc={rc} err={err}")
                os.remove(in_brief)
                rc, out, err, stub = ag_run(who, "brief-out", [ag_brief], ag_repo)
                check(f"{tag}: no -o and the brief outside the repo -> the review runs",
                      rc == 0 and stub[0], f"rc={rc} err={err}")
                rc, out, err, stub = ag_run(who, "subdir", ["-o", "../tracked.md", ag_brief],
                                              os.path.join(ag_repo, "sub"))
                check(f"{tag}: invoked from a SUBDIRECTORY, -o ../tracked.md -> refused, bytes unchanged",
                      ag_refused(rc, err, stub)
                      and open(ag_tracked, "rb").read() == ag_tracked_bytes, f"rc={rc} err={err}")
                rc, out, err, stub = ag_run(who, "subdir-out",
                                              ["-o", os.path.join(ag_out, f"{who}-sub.md"), ag_brief],
                                              os.path.join(ag_repo, "sub"))
                check(f"{tag}: invoked from a SUBDIRECTORY, -o outside -> the review runs",
                      rc == 0 and stub[0], f"rc={rc} err={err}")
                rc, out, err, stub = ag_run(who, "worktree",
                                              ["-o", os.path.join(ag_wt, "wt-reply.md"), ag_brief], ag_wt)
                check(f"{tag}: a LINKED worktree as cwd, -o under its top level -> refused",
                      ag_refused(rc, err, stub), f"rc={rc} err={err}")
                rc, out, err, stub = ag_run(who, "hardlink", ["-o", ag_hard, ag_brief], ag_repo)
                check(f"{tag}: an existing hard-linked outside artifact -> refused",
                      ag_refused(rc, err, stub, "more than one hard link")
                      and open(ag_hard).read() == "old reply\n", f"rc={rc} err={err}")
                rc, out, err, stub = ag_run(who, "nongit",
                                              ["-o", os.path.join(ag_out, f"{who}-ng.md"), ag_brief],
                                              ag_nongit)
                if who == "codex":
                    check(f"{tag}: a non-git cwd has nothing to protect -> the review runs",
                          rc == 0 and stub[0], f"rc={rc} err={err}")
                else:
                    # claude has no sandbox: its OWN non-git policy refuses —
                    # the guard must not be the reason.
                    check(f"{tag}: a non-git cwd passes the guard (the runner's own non-git policy decides)",
                          rc == 2 and not stub[0] and "outside a git repo" in err
                          and ag_inside not in err, f"rc={rc} err={err}")

            AG_DIR_INSIDE = "artifact directory is inside the reviewed repository"

            # ---- (2.21 confirmation G2) for mktemp artifacts only the
            # DIRECTORY is judged: an existing entry literally named like the
            # template is not the file mktemp will create, so it must not
            # refuse — whatever it is. A TMPDIR that is itself inside the repo
            # (lexically, or resolved through a link) still refuses.
            g2_tmp = os.path.join(ag_root, "g2-tmp")
            os.makedirs(g2_tmp, exist_ok=True)
            for who in ("codex", "claude"):
                g2_script = codex_script if who == "codex" else claude_script
                g2_lits = [os.path.join(g2_tmp, f"{who}_{kind}.XXXXXX.md")
                           for kind in ("effective", "brief")]
                for shape in ("directory", "inward symlink"):
                    for lit in g2_lits:
                        if shape == "directory":
                            os.makedirs(lit, exist_ok=True)
                        else:
                            os.rmdir(lit)
                            os.symlink(ag_tracked, lit)
                    g2_bin = os.path.join(ag_root, f"bin-{who}-g2-{shape[0]}")
                    g2_cap = os.path.join(ag_root, f"cap-{who}-g2-{shape[0]}")
                    make_stub(g2_bin, who, g2_cap)
                    rc, out, err = run_script(
                        g2_script, ["-o", os.path.join(ag_out, f"g2-{who}-{shape[0]}.md"), "-"],
                        {"PATH": g2_bin + os.pathsep + os.environ.get("PATH", ""),
                         "TMPDIR": g2_tmp},
                        stdin_data="Stdin brief body.\n", cwd=ag_repo)
                    check(f"artifact guard ({who}): a {shape} literally named like the mktemp "
                          "template in TMPDIR -> the review runs (mktemp picks another name)",
                          rc == 0 and bool(read_calls(g2_cap))
                          and open(ag_tracked, "rb").read() == ag_tracked_bytes,
                          f"rc={rc} err={err}")
                for lit in g2_lits:
                    os.remove(lit)
                for slug, g2_bad in (("resolved", os.path.join(ag_out, "into-repo")),
                                     ("lexical", os.path.join(ag_repo, "sub"))):
                    g2_bin = os.path.join(ag_root, f"bin-{who}-g2-{slug}")
                    g2_cap = os.path.join(ag_root, f"cap-{who}-g2-{slug}")
                    make_stub(g2_bin, who, g2_cap)
                    g2_status = subprocess.run(["git", "-C", ag_repo, "status", "--porcelain"],
                                               capture_output=True, text=True).stdout
                    rc, out, err = run_script(
                        g2_script, ["-o", os.path.join(ag_out, f"g2-{who}-{slug}.md"), ag_brief],
                        {"PATH": g2_bin + os.pathsep + os.environ.get("PATH", ""),
                         "TMPDIR": g2_bad}, cwd=ag_repo)
                    g2_lines = err.strip().splitlines()
                    check(f"artifact guard ({who}): a TMPDIR {slug}ly inside the repo -> refused, "
                          "reviewer never called, nothing written",
                          rc == 2 and len(g2_lines) == 1 and AG_DIR_INSIDE in g2_lines[0]
                          and not (os.path.isdir(g2_cap) and os.listdir(g2_cap))
                          and subprocess.run(["git", "-C", ag_repo, "status", "--porcelain"],
                                             capture_output=True, text=True).stdout == g2_status,
                          f"rc={rc} err={err}")

            # ---- (2.21 review F3) a stdin brief is read only AFTER every
            # path known from argv is judged: with -o inside the repo the
            # temp brief is never created (mktemp never runs).
            for who in ("codex", "claude"):
                f3_bin = os.path.join(ag_root, f"bin-{who}-f3")
                f3_cap = os.path.join(ag_root, f"cap-{who}-f3")
                f3_calls = os.path.join(ag_root, f"f3-{who}-mktemp.calls")
                make_stub(f3_bin, who, f3_cap)
                with open(os.path.join(f3_bin, "mktemp"), "w") as f:
                    f.write(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{f3_calls}"\n'
                            f'exec {shutil.which("mktemp")} "$@"\n')
                os.chmod(os.path.join(f3_bin, "mktemp"), 0o755)
                rc, out, err = run_script(codex_script if who == "codex" else claude_script,
                                          ["-o", ag_tracked, "-"],
                                          {"PATH": f3_bin + os.pathsep + os.environ.get("PATH", "")},
                                          stdin_data="Stdin brief body.\n", cwd=ag_repo)
                f3_traced = os.path.isdir(f3_cap) and bool(os.listdir(f3_cap))
                check(f"artifact guard ({who}): stdin brief with -o inside the repo -> refused BEFORE "
                      "stdin is read (no temp brief created)",
                      ag_refused(rc, err, (False, f3_traced)) and not os.path.exists(f3_calls)
                      and open(ag_tracked, "rb").read() == ag_tracked_bytes, f"rc={rc} err={err}")

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
            # Since 2.21 the runners refuse any artifact inside the repo, so the
            # exclusion can no longer be reached through them; it is kept (and
            # pinned here, on the helper itself) as defense in depth. A runner
            # artifact written into a NEW directory must stay excluded: a
            # collapsed `fresh/` status entry would hide it (-uall).
            newdir_repo = make_repo("repo-newdir")
            newdir = os.path.join(newdir_repo, "fresh")
            os.makedirs(newdir, exist_ok=True)  # empty: invisible to git
            nd_art = os.path.join(newdir, "reply.md")
            detect_py = os.path.join(SCRIPTS, "lib", "detect.py")
            nd_before = os.path.join(runner_tmp, "newdir-before.json")
            nd_after = os.path.join(runner_tmp, "newdir-after.json")
            subprocess.run(["python3", detect_py, "snapshot", newdir_repo, nd_before, nd_art],
                           check=True, capture_output=True)
            write_file(nd_art, "reply\n")
            subprocess.run(["python3", detect_py, "snapshot", newdir_repo, nd_after, nd_art],
                           check=True, capture_output=True)
            p = subprocess.run(["python3", detect_py, "compare", nd_before, nd_after],
                               capture_output=True, text=True)
            check("change detection: artifacts in a new directory are excluded (-uall, not a collapsed dir)",
                  p.returncode == 0 and "changed=\n" in p.stdout, f"rc={p.returncode} out={p.stdout}")

            # ---- (F4b) snapshot git calls take --no-optional-locks: a plain
            # `git status` may refresh the reviewed repo's .git/index ----
            nol_bin = os.path.join(runner_tmp, "bin-git-argv")
            os.makedirs(nol_bin, exist_ok=True)
            nol_log = os.path.join(runner_tmp, "git-argv.log")
            with open(os.path.join(nol_bin, "git"), "w") as f:
                f.write("#!/usr/bin/env bash\n"
                        f"printf '%s\\n' \"$*\" >> '{nol_log}'\n"
                        f"exec {shutil.which('git')} \"$@\"\n")
            os.chmod(os.path.join(nol_bin, "git"), 0o755)
            nol_env = dict(os.environ, PATH=nol_bin + os.pathsep + os.environ.get("PATH", ""))
            p = subprocess.run(["python3", detect_py, "snapshot", newdir_repo,
                                os.path.join(runner_tmp, "nol.json")],
                               capture_output=True, text=True, env=nol_env)
            nol_lines = (open(nol_log).read().splitlines() if os.path.isfile(nol_log) else [])
            nol_sd = [l for l in nol_lines if " status " in f" {l} " or " diff " in f" {l} "]
            check("change detection: every snapshot git status/diff runs with --no-optional-locks",
                  p.returncode == 0 and len(nol_sd) == 3
                  and all(l.startswith("--no-optional-locks ") for l in nol_sd),
                  f"rc={p.returncode} calls={nol_lines} err={p.stderr}")

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
            def make_minimal_bin(name, capture_dir, stub_name="codex", omit=()):
                """A PATH with everything the runner needs EXCEPT timeout(1),
                minus anything `omit` names (the no-realpath fixtures below
                drop `realpath` to exercise the python3 resolver)."""
                d = os.path.join(runner_tmp, name)
                os.makedirs(d, exist_ok=True)
                for tool in ("python3", "git", "bash", "sh", "sed", "awk", "grep",
                             "cat", "head", "tail", "rm", "mv", "mkdir", "mktemp",
                             "date", "realpath", "chmod", "env"):
                    if tool in omit:
                        continue
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
                os.path.join("host-codex-claude", ".codex", "plugins", "data"),
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
            astra_cfg = cfg_dir_with(
                os.path.join("host-claude", ".claude", "plugins", "data"),
                {"codex": {"model": "gpt-6-astra"}})
            rc, out, err = run_script(claude_script, [brief_file("claude-wrong-host-brief.md")], {
                "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                "CLAUDE_PLUGIN_DATA": astra_cfg})
            calls = read_calls(cap)
            check("claude: a /.claude/ HOST config's codex.model is IGNORED (no --model passed)",
                  rc == 0 and bool(calls) and "--model" not in calls[0][0]
                  and "model=cli-default (identity unverified)" in out,
                  f"rc={rc} argv={calls[:1]} out={out}")

            # ---- (o2) claude runner effort (2.24): env HJW_CLAUDE_EFFORT > config
            # codex.effort > UNSET = no --effort flag at all. Unlike the codex
            # runner there is NO runner default: unset must never imply a known
            # effort, so the CLI's own default (and its settings) applies. ----
            def claude_effort_run(label, env_extra):
                e_bin = os.path.join(runner_tmp, f"bin-claude-effort-{label}")
                e_cap = os.path.join(runner_tmp, f"cap-claude-effort-{label}")
                make_stub(e_bin, "claude", e_cap)
                env = {"PATH": e_bin + os.pathsep + os.environ.get("PATH", "")}
                env.update(env_extra)
                e_brief = brief_file(f"claude-effort-{label}.md")
                e_rc, e_out, e_err = run_script(claude_script, [e_brief], env)
                e_log_path = os.path.splitext(e_brief)[0] + ".reply.log"
                e_log = (open(e_log_path, encoding="utf-8").read()
                         if os.path.isfile(e_log_path) else "")
                e_calls = read_calls(e_cap)
                return e_rc, e_out, e_err, (e_calls[0][0] if e_calls else None), e_log

            rc, out, err, argv, log = claude_effort_run("env", {"HJW_CLAUDE_EFFORT": " high "})
            check("claude effort: env HJW_CLAUDE_EFFORT (trimmed) -> --effort high in argv",
                  rc == 0 and argv is not None and argv_value(argv, "--effort") == "high",
                  f"rc={rc} argv={argv}")
            check("claude effort: (env) source on the progress line, log header and result line",
                  "effort=high (env)" in err and "effort=high (env)" in log.splitlines()[0]
                  and "effort=high (env)" in out, f"err={err} log={log[:300]} out={out}")

            rc, out, err, argv, log = claude_effort_run("env-invalid", {"HJW_CLAUDE_EFFORT": "insane"})
            check("claude effort: invalid ENV -> exit 2 with the codex-shaped message",
                  rc == 2 and "invalid HJW_CLAUDE_EFFORT: insane (must be one of: low, medium, high, xhigh)" in err,
                  f"rc={rc} err={err}")
            check("claude effort: invalid ENV -> claude never invoked", argv is None, argv)

            # Claude Code exports CLAUDE_EFFORT (the HOST's session effort) to
            # every child — measured 2026-10-08. It must never reach the runner.
            rc, out, err, argv, log = claude_effort_run("inherited-host", {"CLAUDE_EFFORT": "max"})
            check("claude effort: an inherited CLAUDE_EFFORT=max is IGNORED -> no --effort, unset disclosed",
                  rc == 0 and argv is not None and "--effort" not in argv
                  and "effort=unset (CLI default)" in log.splitlines()[0]
                  and "effort=unset (CLI default)" in out, f"rc={rc} argv={argv} err={err}")

            claude_effort_cfg = cfg_dir_with(
                os.path.join("host-codex-claude-effort", ".codex", "plugins", "data"),
                {"codex": {"effort": "xhigh"}})
            rc, out, err, argv, log = claude_effort_run("config", {"CLAUDE_PLUGIN_DATA": claude_effort_cfg})
            check("claude effort: codex-host config codex.effort -> --effort xhigh, (config) disclosed",
                  rc == 0 and argv is not None and argv_value(argv, "--effort") == "xhigh"
                  and "effort=xhigh (config)" in out and "effort=xhigh (config)" in log,
                  f"rc={rc} argv={argv} out={out}")

            claude_bad_effort_cfg = cfg_dir_with(
                os.path.join("host-codex-claude-effort-bad", ".codex", "plugins", "data"),
                {"codex": {"effort": "insane"}})
            rc, out, err, argv, log = claude_effort_run("config-invalid",
                                                        {"CLAUDE_PLUGIN_DATA": claude_bad_effort_cfg})
            check("claude effort: invalid CONFIG -> one note, NO --effort flag, run continues",
                  rc == 0 and "note: config codex.effort 'insane' invalid; passing no effort flag (CLI default)" in err
                  and argv is not None and "--effort" not in argv
                  and "effort=unset (CLI default)" in out, f"rc={rc} err={err} argv={argv} out={out}")

            rc, out, err, argv, log = claude_effort_run("unset", {})
            check("claude effort: unset -> NO --effort flag; header says effort=unset (CLI default)",
                  rc == 0 and argv is not None and "--effort" not in argv
                  and "effort=unset (CLI default)" in log.splitlines()[0]
                  and "effort=unset (CLI default)" in out, f"rc={rc} argv={argv} log={log[:300]}")

            claude_host_effort_cfg = cfg_dir_with(
                os.path.join("host-claude-effort", ".claude", "plugins", "data"),
                {"codex": {"effort": "xhigh"}})
            rc, out, err, argv, log = claude_effort_run("wrong-host",
                                                        {"CLAUDE_PLUGIN_DATA": claude_host_effort_cfg})
            check("claude effort: a /.claude/ HOST config's codex.effort is IGNORED (ownership) -> unset",
                  rc == 0 and argv is not None and "--effort" not in argv
                  and "effort=unset (CLI default)" in out, f"rc={rc} argv={argv} out={out}")

            claude_junk_cfg = cfg_dir_with(
                os.path.join("host-codex-claude-junk", ".codex", "plugins", "data"),
                {"codex": {"model": 123, "effort": []}})
            rc, out, err, argv, log = claude_effort_run("config-nonstring",
                                                        {"CLAUDE_PLUGIN_DATA": claude_junk_cfg})
            check("claude config: non-string model AND effort -> one note per key, nothing passed",
                  rc == 0 and "note: config codex.model ignored (not a string)" in err
                  and "note: config codex.effort ignored (not a string)" in err
                  and argv is not None and "--effort" not in argv and "--model" not in argv,
                  f"rc={rc} err={err} argv={argv}")

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

            cfg_dir3 = os.path.join(runner_tmp, "plugin-data-malformed", "haejwo-haejwo")
            os.makedirs(cfg_dir3, exist_ok=True)
            with open(os.path.join(cfg_dir3, "config.json"), "w") as f:
                f.write("{ not valid json !!!")
            sbx = sandbox_used({"CLAUDE_PLUGIN_DATA": cfg_dir3}, "config-malformed")
            check("sandbox precedence: malformed config JSON falls back to read-only",
                  sbx == "read-only", sbx)

            # malformed SHAPE (codex is not an object) is "no config" too —
            # model/effort must not crash or leak a value.
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
            empty_data_dir = os.path.join(runner_tmp, "plugin-data-empty", "haejwo-haejwo")
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

            # ---- (P1, 2.17) the config path is OWNED, never trusted from the
            # shell. FIELD DEFECT 2026-09-28: a Claude Code session's Bash env
            # carried CLAUDE_PLUGIN_DATA=<...>/data/codex-openai-codex (the
            # LAST loaded plugin's dir), the runner read it verbatim, found no
            # config, and silently ran three consults at the CLI default model
            # in a read-only sandbox the owner had configured away from.
            # Resolution is structural -> haejwo-named env -> derived -> none,
            # and NOTHING falls back once an owner path is selected.
            own_root = os.path.realpath(tempfile.mkdtemp(dir=runner_tmp, prefix="own-"))

            def fake_install(name, vendor, payload, version="9.9.9"):
                """The real installed-cache layout:
                <plugins>/cache/haejwo/haejwo/<ver>/scripts/<runner> owns
                <plugins>/data/haejwo-haejwo/config.json. `vendor` is the
                .claude / .codex component the host dir really carries — it is
                what names the host for the host-relative `codex` block.
                payload=None builds the install with NO owner config."""
                plugins = os.path.join(own_root, name, vendor, "plugins")
                scripts = os.path.join(plugins, "cache", "haejwo", "haejwo",
                                       version, "scripts")
                shutil.copytree(SCRIPTS, scripts,
                                ignore=shutil.ignore_patterns("__pycache__"))
                data = os.path.join(plugins, "data", "haejwo-haejwo")
                os.makedirs(data, exist_ok=True)
                if payload is not None:
                    with open(os.path.join(data, "config.json"), "w") as f:
                        json.dump(payload, f)
                return scripts, os.path.join(data, "config.json")

            def foreign_dir(name, payload=None):
                """Another plugin's data dir — the exact shape the field
                defect handed the runner. Its config must NEVER be read."""
                d = os.path.join(own_root, name, "codex-openai-codex")
                os.makedirs(d, exist_ok=True)
                if payload is not None:
                    with open(os.path.join(d, "config.json"), "w") as f:
                        json.dump(payload, f)
                return d

            def own_run(label, script, env_extra, cli="codex"):
                """One hermetic run that also hands back $LOG (the header is
                where the selected config path is disclosed)."""
                bin_dir = os.path.join(runner_tmp, f"bin-own-{label}")
                cap = os.path.join(runner_tmp, f"cap-own-{label}")
                make_stub(bin_dir, cli, cap)
                env = {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}
                env.update(env_extra)
                brief = brief_file(f"own-{label}.md")
                rc, out, err = run_script(script, [brief], env)
                log_path = os.path.splitext(brief)[0] + ".reply.log"
                log = (open(log_path, encoding="utf-8").read()
                       if os.path.isfile(log_path) else "")
                return rc, out, err, read_calls(cap), log

            # (t1) structural wins over a FOREIGN CLAUDE_PLUGIN_DATA, the
            # owner's values are applied, and the foreign variable is disclosed
            # exactly once — in $LOG and on stderr.
            t1_scripts, t1_cfg = fake_install("t1", ".claude", {"codex": {
                "model": "owner-model", "effort": "high",
                "consult_sandbox": "danger-full-access"}})
            t1_foreign = foreign_dir("t1", {"codex": {
                "model": "foreign-model", "consult_sandbox": "read-only"}})
            rc, out, err, calls, log = own_run(
                "t1-codex", os.path.join(t1_scripts, "codex_consult.sh"),
                {"CLAUDE_PLUGIN_DATA": t1_foreign})
            argv = calls[0][0] if calls else []
            note = ("# config: CLAUDE_PLUGIN_DATA=codex-openai-codex is not haejwo's "
                    f"data dir — using {t1_cfg} (structural)")
            check("P1 t1 codex: a FOREIGN CLAUDE_PLUGIN_DATA is ignored; the structural "
                  "owner config is used",
                  rc == 0 and argv_value(argv, "-m") == "owner-model"
                  and argv_value(argv, "-s") == "danger-full-access"
                  and 'model_reasoning_effort="high"' in argv,
                  f"rc={rc} argv={argv} err={err}")
            check("P1 t1 codex: log header discloses config=<owner path> (structural) "
                  "config_status=ok",
                  f"config={t1_cfg} (structural) config_status=ok" in log.splitlines()[0]
                  if log else False, log[:400])
            check("P1 t1 codex: the foreign variable is disclosed EXACTLY once in $LOG "
                  "and once on stderr",
                  log.count(note) == 1 and err.count(note) == 1,
                  f"log={log.count(note)} err={err.count(note)} note={note}")
            check("P1 t1 codex: the foreign dir's own config value is never read",
                  "foreign-model" not in out + err + log, f"out={out} err={err}")

            t1c_scripts, t1c_cfg = fake_install("t1c", ".codex", {"codex": {
                "model": "owner-claude-model"}})
            t1c_foreign = foreign_dir("t1c", {"codex": {"model": "foreign-model"}})
            rc, out, err, calls, log = own_run(
                "t1-claude", os.path.join(t1c_scripts, "claude_consult.sh"),
                {"CLAUDE_PLUGIN_DATA": t1c_foreign}, cli="claude")
            argv = calls[0][0] if calls else []
            check("P1 t1 claude: structural owner config used, foreign env ignored",
                  rc == 0 and argv_value(argv, "--model") == "owner-claude-model"
                  and f"config={t1c_cfg} (structural) config_status=ok" in log,
                  f"rc={rc} argv={argv} log={log[:300]}")
            check("P1 t1 claude: foreign variable disclosed once in $LOG and on stderr",
                  log.count("is not haejwo's data dir") == 1
                  and err.count("is not haejwo's data dir") == 1,
                  f"log={log[:300]} err={err[:300]}")

            # (t2) a working-tree runner has no structural layout, so a
            # CLAUDE_PLUGIN_DATA NAMED haejwo-haejwo is honored — and labeled.
            t2_cfg = cfg_dir_with("t2-env", {"codex": {"model": "env-dir-model",
                                                       "effort": "low"}})
            rc, out, err, calls, log = own_run("t2-codex", codex_script,
                                               {"CLAUDE_PLUGIN_DATA": t2_cfg})
            argv = calls[0][0] if calls else []
            check("P1 t2 codex: CLAUDE_PLUGIN_DATA named haejwo-haejwo is honored, "
                  "labeled (env), values applied",
                  rc == 0 and argv_value(argv, "-m") == "env-dir-model"
                  and 'model_reasoning_effort="low"' in argv
                  and f"config={os.path.join(t2_cfg, 'config.json')} (env) config_status=ok" in log,
                  f"rc={rc} argv={argv} log={log[:300]}")
            check("P1 t2 codex: an accepted env dir raises no foreign note",
                  "is not haejwo's data dir" not in log + err, f"log={log[:300]} err={err}")

            t2c_cfg = cfg_dir_with(os.path.join("t2c-env", ".codex", "plugins", "data"),
                                   {"codex": {"model": "env-dir-claude-model"}})
            rc, out, err, calls, log = own_run("t2-claude", claude_script,
                                               {"CLAUDE_PLUGIN_DATA": t2c_cfg},
                                               cli="claude")
            argv = calls[0][0] if calls else []
            check("P1 t2 claude: haejwo-named env dir honored and labeled (env)",
                  rc == 0 and argv_value(argv, "--model") == "env-dir-claude-model"
                  and f"config={os.path.join(t2c_cfg, 'config.json')} (env) config_status=ok" in log,
                  f"rc={rc} argv={argv} log={log[:300]}")

            # (t3) no structural layout and no usable variable -> the DERIVED
            # canonical path, chosen by /.codex/ in the runner's own path.
            t3_home = os.path.join(own_root, "t3-home")
            os.makedirs(t3_home, exist_ok=True)
            rc, out, err, calls, log = own_run(
                "t3-claude-host", codex_script,
                {"CLAUDE_PLUGIN_DATA": None, "HOME": t3_home})
            t3_derived = os.path.join(t3_home, ".claude", "plugins", "data",
                                      "haejwo-haejwo", "config.json")
            check("P1 t3 codex: unset variable -> derived ~/.claude path, labeled (derived), "
                  "status absent",
                  rc == 0 and f"config={t3_derived} (derived) config_status=absent" in log,
                  f"rc={rc} log={log[:300]}")

            t3_twin = os.path.join(own_root, "t3-twin", ".codex", "tool", "scripts")
            shutil.copytree(SCRIPTS, t3_twin,
                            ignore=shutil.ignore_patterns("__pycache__"))
            rc, out, err, calls, log = own_run(
                "t3-codex-host", os.path.join(t3_twin, "codex_consult.sh"),
                {"CLAUDE_PLUGIN_DATA": None, "HOME": t3_home})
            t3_derived_codex = os.path.join(t3_home, ".codex", "plugins", "data",
                                            "haejwo-haejwo", "config.json")
            check("P1 t3 codex: a runner under /.codex/ derives the ~/.codex twin",
                  rc == 0 and f"config={t3_derived_codex} (derived) config_status=absent" in log,
                  f"rc={rc} log={log[:300]}")

            # (t4) NO fallback after selection: the owner path is structural and
            # its file is MISSING, while BOTH a haejwo-named env dir and the
            # derived path hold danger-full-access. Neither may be resurrected.
            t4_scripts, t4_cfg = fake_install("t4", ".claude", None)
            t4_env = cfg_dir_with("t4-env", {"codex": {
                "model": "env-model", "consult_sandbox": "danger-full-access"}})
            t4_home = os.path.join(own_root, "t4-home")
            t4_home_cfg = os.path.join(t4_home, ".claude", "plugins", "data", "haejwo-haejwo")
            os.makedirs(t4_home_cfg, exist_ok=True)
            with open(os.path.join(t4_home_cfg, "config.json"), "w") as f:
                json.dump({"codex": {"model": "home-model",
                                     "consult_sandbox": "danger-full-access"}}, f)
            rc, out, err, calls, log = own_run(
                "t4", os.path.join(t4_scripts, "codex_consult.sh"),
                {"CLAUDE_PLUGIN_DATA": t4_env, "HOME": t4_home})
            argv = calls[0][0] if calls else []
            check("P1 t4: structural path with NO file -> config_status=absent, never a "
                  "fallback to the env or derived path (no stale danger-full-access)",
                  rc == 0
                  and f"config={t4_cfg} (structural) config_status=absent" in log
                  and argv_value(argv, "-s") == "read-only"
                  and argv_value(argv, "-m") is None
                  and "model=cli-default (identity unverified)" in out,
                  f"rc={rc} argv={argv} out={out} log={log[:300]}")

            # (t5) malformed AT the owner path: status says so, behaviour is the
            # unchanged one (no config -> read-only, no model).
            t5_scripts, t5_cfg = fake_install("t5", ".claude", None)
            with open(t5_cfg, "w") as f:
                f.write("{ not valid json !!!")
            rc, out, err, calls, log = own_run(
                "t5", os.path.join(t5_scripts, "codex_consult.sh"), {})
            argv = calls[0][0] if calls else []
            check("P1 t5: malformed file at the owner path -> config_status=malformed, "
                  "read-only + cli-default unchanged",
                  rc == 0
                  and f"config={t5_cfg} (structural) config_status=malformed" in log
                  and argv_value(argv, "-s") == "read-only"
                  and "model=cli-default (identity unverified)" in out,
                  f"rc={rc} argv={argv} out={out} log={log[:300]}")

            # (t6) CUSTOM plugin root: the selected path names no vendor, so the
            # host follows the RUNNER KIND — codex_consult.sh is a Claude host's
            # reviewer, claude_consult.sh a Codex host's. Both read their block.
            t6_scripts, t6_cfg = fake_install("t6", "custom-root",
                                              {"codex": {"model": "custom-root-model"}})
            rc, out, err, calls, log = own_run(
                "t6-codex", os.path.join(t6_scripts, "codex_consult.sh"), {})
            argv = calls[0][0] if calls else []
            check("P1 t6 codex: a custom plugin root (no /.claude/ or /.codex/) reads "
                  "codex.model — the runner kind names the host",
                  rc == 0 and argv_value(argv, "-m") == "custom-root-model"
                  and f"config={t6_cfg} (structural) config_status=ok" in log,
                  f"rc={rc} argv={argv} log={log[:300]}")

            t6c_scripts, t6c_cfg = fake_install("t6c", "custom-root",
                                                {"codex": {"model": "custom-root-claude"}})
            rc, out, err, calls, log = own_run(
                "t6-claude", os.path.join(t6c_scripts, "claude_consult.sh"), {},
                cli="claude")
            argv = calls[0][0] if calls else []
            check("P1 t6 claude: same custom root, claude runner reads codex.model too",
                  rc == 0 and argv_value(argv, "--model") == "custom-root-claude"
                  and f"config={t6c_cfg} (structural) config_status=ok" in log,
                  f"rc={rc} argv={argv} log={log[:300]}")

            # (t7) nothing is determinable: no structural layout, a foreign
            # variable, and an empty $HOME. Source `none`, and the runner still
            # says out loud which variable it refused.
            t7_foreign = foreign_dir("t7", {"codex": {"consult_sandbox": "danger-full-access"}})
            rc, out, err, calls, log = own_run(
                "t7", codex_script, {"CLAUDE_PLUGIN_DATA": t7_foreign, "HOME": ""})
            argv = calls[0][0] if calls else []
            check("P1 t7: empty $HOME + no structural layout + foreign variable -> "
                  "config=none (none) config_status=none, read-only, note still emitted",
                  rc == 0
                  and "config=none (none) config_status=none" in log
                  and argv_value(argv, "-s") == "read-only"
                  and log.count("is not haejwo's data dir") == 1
                  and err.count("is not haejwo's data dir") == 1,
                  f"rc={rc} argv={argv} log={log[:300]} err={err[:300]}")

            # (t8, 2.17.0) trailing separators are not a classification.
            # `.../haejwo-haejwo/` and `.../haejwo-haejwo//` are the SAME
            # directory, but `${v%/}` stripped exactly one, so the `//`
            # spelling was read as foreign and fell through to `derived`.
            t8_cfg = cfg_dir_with("t8-env", {"codex": {"model": "slash-model"}})
            for slash_label, suffix in (("one slash", "/"), ("two slashes", "//")):
                rc, out, err, calls, log = own_run(
                    f"t8-{len(suffix)}", codex_script,
                    {"CLAUDE_PLUGIN_DATA": t8_cfg + suffix})
                argv = calls[0][0] if calls else []
                check(f"P1 t8: CLAUDE_PLUGIN_DATA with {slash_label} trailing still "
                      "classifies as (env) — same dir, same source, no foreign note",
                      rc == 0 and argv_value(argv, "-m") == "slash-model"
                      and f"config={t8_cfg}{suffix}/config.json (env) config_status=ok" in log
                      and "is not haejwo's data dir" not in log + err,
                      f"rc={rc} argv={argv} log={log[:300]} err={err[:200]}")

            # (t9) the defect that made it matter: the `//` env dir holds NO
            # config while the derived path holds danger-full-access. Source
            # must stay (env)/absent, and the stale consent must not revive.
            t9_home = os.path.join(own_root, "t9-home")
            t9_home_cfg = os.path.join(t9_home, ".claude", "plugins", "data",
                                       "haejwo-haejwo")
            os.makedirs(t9_home_cfg, exist_ok=True)
            with open(os.path.join(t9_home_cfg, "config.json"), "w") as f:
                json.dump({"codex": {"model": "home-model",
                                     "consult_sandbox": "danger-full-access"}}, f)
            t9_env = os.path.join(own_root, "t9-env", "haejwo-haejwo")
            os.makedirs(t9_env, exist_ok=True)  # no config.json inside
            rc, out, err, calls, log = own_run(
                "t9", codex_script,
                {"CLAUDE_PLUGIN_DATA": t9_env + "//", "HOME": t9_home})
            argv = calls[0][0] if calls else []
            # CFG_PATH keeps the variable's own spelling: "<dir>//" + "/config.json".
            t9_disp = f"config={t9_env}///config.json (env) config_status=absent"
            check("P1 t9: a `//`-spelled env dir with no file -> (env) config_status=absent, "
                  "read-only — the derived path's danger-full-access is NOT resurrected",
                  rc == 0
                  and t9_disp in log
                  and argv_value(argv, "-s") == "read-only"
                  and argv_value(argv, "-m") is None,
                  f"rc={rc} argv={argv} log={log[:300]}")

            # (t10) foreignness is a property of the VARIABLE, not of which
            # source won. A structural install whose host also exports a
            # haejwo-named dir is not "foreign" — the note used to say
            # "CLAUDE_PLUGIN_DATA=haejwo-haejwo is not haejwo's data dir".
            t10_scripts, t10_cfg = fake_install("t10", ".claude",
                                                {"codex": {"model": "own-model"}})
            rc, out, err, calls, log = own_run(
                "t10-self", os.path.join(t10_scripts, "codex_consult.sh"),
                {"CLAUDE_PLUGIN_DATA": os.path.dirname(t10_cfg)})
            check("P1 t10: structural wins while the variable names haejwo's OWN data "
                  "dir -> no foreign note",
                  rc == 0 and f"config={t10_cfg} (structural) config_status=ok" in log
                  and "is not haejwo's data dir" not in log + err,
                  f"rc={rc} log={log[:300]} err={err[:200]}")

            t10b = cfg_dir_with("t10b-other", {"codex": {"model": "other-haejwo-model"}})
            rc, out, err, calls, log = own_run(
                "t10-other", os.path.join(t10_scripts, "codex_consult.sh"),
                {"CLAUDE_PLUGIN_DATA": t10b})
            argv = calls[0][0] if calls else []
            check("P1 t10: a DIFFERENT haejwo-haejwo dir is still not foreign (no note), "
                  "and structural still wins the selection",
                  rc == 0 and argv_value(argv, "-m") == "own-model"
                  and f"config={t10_cfg} (structural) config_status=ok" in log
                  and "is not haejwo's data dir" not in log + err,
                  f"rc={rc} argv={argv} log={log[:300]} err={err[:200]}")

            # (t11) a config path may legally contain a newline, and the log
            # header is ONE line: display fields escape control characters
            # while CFG_PATH keeps the raw bytes it is opened with.
            t11_data = os.path.join(own_root, "t11-nl\nparent", "haejwo-haejwo")
            os.makedirs(t11_data, exist_ok=True)
            with open(os.path.join(t11_data, "config.json"), "w") as f:
                json.dump({"codex": {"model": "newline-model"}}, f)
            rc, out, err, calls, log = own_run(
                "t11", codex_script, {"CLAUDE_PLUGIN_DATA": t11_data})
            argv = calls[0][0] if calls else []
            t11_disp = ("config="
                        + os.path.join(t11_data, "config.json").replace("\n", "\\n")
                        + " (env) config_status=ok")
            check("P1 t11: a newline in the config path is escaped in the header (one "
                  "line) and the config is still read from the RAW path",
                  rc == 0 and bool(log) and t11_disp in log.splitlines()[0]
                  and argv_value(argv, "-m") == "newline-model",
                  f"rc={rc} argv={argv} log={log[:400]!r}")

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

            def make_repo_committed(name, base=None):
                """A repo with one commit — `make_repo` leaves HEAD unborn."""
                d = make_repo(name, base)
                write_file(os.path.join(d, "seed.txt"), "seed\n")
                subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
                subprocess.run(["git", "-C", d, "commit", "-q", "-m", "seed"],
                               check=True, capture_output=True)
                return d

            # ---- (2.22) --snapshot was removed: it is an unknown option on
            # both runners — exit 2, the parser's one line, no reviewer call. ----
            for who, script, cli in (("codex", codex_script, "codex"),
                                     ("claude", claude_script, "claude")):
                rs_bin = os.path.join(runner_tmp, f"bin-snapshot-removed-{who}")
                rs_cap = os.path.join(runner_tmp, f"cap-snapshot-removed-{who}")
                make_stub(rs_bin, cli, rs_cap)
                rs_dir = os.path.join(runner_tmp, f"snapshot-removed-{who}")
                rs_brief = brief_file(f"snapshot-removed-{who}/brief.md")
                rc, out, err = run_script(
                    script, ["--snapshot", rs_brief],
                    {"PATH": rs_bin + os.pathsep + os.environ.get("PATH", "")})
                check(f"{who} --snapshot (removed in 2.22): exit 2 with the unknown-option line",
                      rc == 2 and err.strip() == "unknown option: --snapshot",
                      f"rc={rc} out={out} err={err}")
                check(f"{who} --snapshot: the CLI is never invoked and nothing is written",
                      read_calls(rs_cap) == [] and sorted(os.listdir(rs_dir)) == ["brief.md"],
                      f"calls={read_calls(rs_cap)} left={sorted(os.listdir(rs_dir))}")
                rc, out, err = run_script(script, ["--help"], {})
                check(f"{who} --help: the usage names the 2.22 --snapshot removal and its replacement",
                      rc == 0 and "--snapshot: removed in 2.22 — review the live working copy" in out
                      and "[--snapshot]" not in out, out)

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

            # ---- (2.13) the shared library is a REQUIRED part of the runner ----
            # Every lib file is checked BEFORE any artifact is created: an
            # incomplete install must fail loudly and cheaply, naming the file
            # it could not find — never half-run a paid review on a runner
            # whose mechanics are missing. One file is renamed in a COPY of
            # the tree, so the check binds on the real resolution path.
            for who, cli in (("codex", "codex"), ("claude", "claude")):
                lm_tree = os.path.realpath(os.path.join(runner_tmp, f"libmissing-{who}"))
                shutil.copytree(SCRIPTS, lm_tree,
                                ignore=shutil.ignore_patterns("__pycache__"))
                lm_gone = os.path.join(lm_tree, "lib", "detect.py")
                os.rename(lm_gone, lm_gone + ".moved")
                lm_bin = os.path.join(runner_tmp, f"bin-libmissing-{who}")
                lm_cap = os.path.join(runner_tmp, f"cap-libmissing-{who}")
                make_stub(lm_bin, cli, lm_cap)
                lm_dir = os.path.join(runner_tmp, f"libmissing-brief-{who}")
                lm_brief = brief_file(f"libmissing-brief-{who}/brief.md")
                rc, out, err = run_script(
                    os.path.join(lm_tree, f"{who}_consult.sh"), [lm_brief],
                    {"PATH": lm_bin + os.pathsep + os.environ.get("PATH", "")})
                check(f"{who} lib missing: exit 3 naming the missing file",
                      rc == 3 and f"consult runner library missing: {lm_gone}" in err,
                      f"rc={rc} out={out} err={err}")
                check(f"{who} lib missing: the CLI is never invoked",
                      read_calls(lm_cap) == [], read_calls(lm_cap))
                check(f"{who} lib missing: no reply/log/events artifact is written",
                      sorted(os.listdir(lm_dir)) == ["brief.md"],
                      sorted(os.listdir(lm_dir)))

            # ---- (2.13) $HJW_LIB is ABSOLUTE and PHYSICAL, with no realpath ----
            # $0 is the only anchor the runner has: an unresolved symlink
            # would look for `lib/` beside the LINK. This
            # PATH deliberately omits realpath: the python3 resolver is what
            # is under test.
            for who, cli in (("codex", "codex"), ("claude", "claude")):
                nr_repo = make_repo_committed(f"repo-norealpath-{who}")
                shutil.copytree(SCRIPTS, os.path.join(nr_repo, "haejwo", "scripts"),
                                ignore=shutil.ignore_patterns("__pycache__"))
                os.makedirs(os.path.join(nr_repo, "sub", "dir"), exist_ok=True)
                # a RELATIVE symlink, resolved against the link's own directory
                os.symlink(os.path.join("haejwo", "scripts", f"{who}_consult.sh"),
                           os.path.join(nr_repo, f"link-{who}.sh"))
                subprocess.run(["git", "-C", nr_repo, "add", "-A"],
                               check=True, capture_output=True)
                subprocess.run(["git", "-C", nr_repo, "commit", "-q", "-m", "scripts"],
                               check=True, capture_output=True)
                nr_out = os.path.join(runner_tmp, f"norealpath-{who}.reply.md")
                nr_brief = brief_file(f"norealpath-{who}-brief.md")
                for i, (tag, rel, cwd, extra) in enumerate((
                        ("relative from the repo root",
                         f"./haejwo/scripts/{who}_consult.sh", nr_repo, []),
                        ("relative from a subdirectory",
                         f"../../haejwo/scripts/{who}_consult.sh",
                         os.path.join(nr_repo, "sub", "dir"), []),
                        ("through a symlink", f"./link-{who}.sh", nr_repo, []))):
                    # hermetic per invocation: fresh symlink farm, fresh capture
                    nr_cap = os.path.join(runner_tmp, f"cap-norealpath-{who}-{i}")
                    nr_bin = make_minimal_bin(f"bin-norealpath-{who}-{i}", nr_cap,
                                              stub_name=cli, omit=("realpath",))
                    if i == 0:
                        check(f"{who} no-realpath PATH: the `realpath` binary really "
                              "is absent",
                              shutil.which("realpath", path=nr_bin) is None, nr_bin)
                    rc, out, err = run_script(rel, extra + ["-o", nr_out, nr_brief],
                                              {"PATH": nr_bin}, cwd=cwd)
                    check(f"{who} no-realpath PATH, {tag}: the run succeeds",
                          rc == 0 and "STUB-REPLY-OK-1" in out,
                          f"rc={rc} out={out} err={err}")

            # ---- runner POSTCONDITIONS: what a finished run must have left on
            # disk, and what it must not have. Every check below is an explicit
            # postcondition on a named path — not an inventory comparison
            # against a reference run — and every one runs against BOTH
            # entrypoints, because the two resolve their own temp state
            # separately. ----
            print("== runner postconditions (temp cleanup, artifacts, signals, install) ==")

            # The temp names a RUNNER allocates. `<kind>_brief.*` is the stdin
            # brief, `<kind>_effective.*` the contract+brief it actually feeds
            # the reviewer, and `<kind>_snap.*` the change-detection metadata. They are
            # named here for the READER only: the postconditions below match the
            # WHOLE surviving set, never a list of prefixes, so a leftover under
            # a name nobody anticipated fails them just the same.

            for who, script, cli in (("codex", codex_script, "codex"),
                                     ("claude", claude_script, "claude")):
                # (1) a FILE brief with an explicit -o, in a TMPDIR of its
                # own so the postcondition can name every survivor.
                pc_tmp = os.path.join(runner_tmp, f"pc-tmp-{who}")
                pc_out_dir = os.path.join(runner_tmp, f"pc-out-{who}")
                os.makedirs(pc_tmp, exist_ok=True)
                os.makedirs(pc_out_dir, exist_ok=True)
                pc_bin = os.path.join(runner_tmp, f"bin-pc-{who}")
                pc_cap = os.path.join(runner_tmp, f"cap-pc-{who}")
                make_stub(pc_bin, cli, pc_cap)
                pc_out = os.path.join(pc_out_dir, "reply.md")
                pc_repo = make_repo_committed(f"repo-pc-{who}")
                rc, out, err = run_script(
                    script, ["-o", pc_out,
                             brief_file(f"pc-{who}-brief.md")],
                    {"PATH": pc_bin + os.pathsep + os.environ.get("PATH", ""),
                     "TMPDIR": pc_tmp}, cwd=pc_repo)
                check(f"{who} postconditions: the fixture run itself succeeded",
                      rc == 0 and "STUB-REPLY-OK-1" in out, f"rc={rc} err={err}")
                left = sorted(os.listdir(pc_tmp))
                check(f"{who} postconditions: the run's own dedicated $TMPDIR is "
                      "EMPTY — no effective brief, no detection metadata, and "
                      "nothing under any other name either",
                      left == [], f"survivors: {left}")
                pc_log = os.path.join(pc_out_dir, "reply.log")
                pc_events = os.path.join(pc_out_dir, "reply.events.jsonl")
                check(f"{who} postconditions: the CALLER's reply and log are preserved",
                      os.path.isfile(pc_out) and os.path.getsize(pc_out) > 0
                      and os.path.isfile(pc_log) and os.path.getsize(pc_log) > 0,
                      sorted(os.listdir(pc_out_dir)))
                # ARTIFACT INTEGRITY: the reply FILE is the reviewer's answer
                # byte for byte — a runner that merged its log into the reply,
                # or re-wrote it through a filter, would still print something
                # plausible on stdout.
                check(f"{who} postconditions: the reply FILE holds the reviewer's bytes "
                      "exactly",
                      open(pc_out, "rb").read() == b"STUB-REPLY-OK-1\n",
                      open(pc_out, "rb").read()[:120])
                check(f"{who} postconditions: the log is a DISTINCT file from the reply, "
                      "with the runner's own record in it",
                      os.path.realpath(pc_log) != os.path.realpath(pc_out)
                      and os.stat(pc_log).st_ino != os.stat(pc_out).st_ino
                      and f"# {who}_consult " in open(pc_log).read()
                      and "STUB-REPLY-OK-1" not in open(pc_log).read(),
                      f"log={open(pc_log).read()[:200]!r}")
                if who == "codex":
                    check("codex postconditions: the events stream is a THIRD distinct "
                          "file, preserved with the vendor's own JSONL in it",
                          os.path.isfile(pc_events)
                          and os.stat(pc_events).st_ino not in
                          (os.stat(pc_out).st_ino, os.stat(pc_log).st_ino)
                          and '"type":"turn.completed"' in open(pc_events).read(),
                          sorted(os.listdir(pc_out_dir)))

                # (2) a STDIN brief: the runner materializes the brief itself,
                # so its artifacts are DERIVED from that temp name and land in
                # $TMPDIR. The brief it created must go; what the caller reads
                # must stay.
                sc_tmp = os.path.join(runner_tmp, f"pc-stdin-tmp-{who}")
                os.makedirs(sc_tmp, exist_ok=True)
                sc_bin = os.path.join(runner_tmp, f"bin-pc-stdin-{who}")
                sc_cap = os.path.join(runner_tmp, f"cap-pc-stdin-{who}")
                make_stub(sc_bin, cli, sc_cap)
                sc_repo = make_repo_committed(f"repo-pc-stdin-{who}")
                rc, out, err = run_script(
                    script, ["-"],
                    {"PATH": sc_bin + os.pathsep + os.environ.get("PATH", ""),
                     "TMPDIR": sc_tmp}, stdin_data="Stdin brief body.\n", cwd=sc_repo)
                sc_left = sorted(os.listdir(sc_tmp))
                sc_reply = [n for n in sc_left if n.endswith(".reply.md")]
                sc_log = [n for n in sc_left if n.endswith(".reply.log")]
                sc_events = [n for n in sc_left if n.endswith(".reply.events.jsonl")]
                check(f"{who} postconditions: a stdin brief run succeeds and the brief "
                      "reached the reviewer",
                      rc == 0 and bool(read_calls(sc_cap))
                      and "Stdin brief body." in read_calls(sc_cap)[0][1],
                      f"rc={rc} err={err}")
                check(f"{who} postconditions: what survives in $TMPDIR is EXACTLY the "
                      "caller's artifacts and nothing else — the runner's own temp "
                      "brief, effective brief and detection metadata are all gone...",
                      sc_left == sorted(sc_reply + sc_log + sc_events), sc_left)
                check(f"{who} postconditions: ...while the reply and log DERIVED from it "
                      "survive for the caller",
                      len(sc_reply) == 1 and len(sc_log) == 1
                      and (len(sc_events) == 1 if who == "codex" else not sc_events),
                      sc_left)

            # ---- the wall clock and the process group on the CLAUDE runner.
            # The codex fixture above (G5a) proves it with no `timeout` binary on
            # PATH; this one proves the ORDINARY path on the other entrypoint —
            # the two runners install their own bound separately. ----
            ct_repo = make_repo_committed("repo-claude-timeout")
            ct_bin = os.path.join(runner_tmp, "bin-claude-timeout")
            ct_cap = os.path.join(runner_tmp, "cap-claude-timeout")
            make_stub(ct_bin, "claude", ct_cap)
            ct_pidfile = os.path.join(runner_tmp, "claude-timeout-descendant.pid")
            t0 = time.time()
            rc, out, err = run_script(
                claude_script, ["-o", os.path.join(runner_tmp, "claude-timeout.reply.md"),
                                brief_file("claude-timeout-brief.md")],
                {"PATH": ct_bin + os.pathsep + os.environ.get("PATH", ""),
                 "CLAUDE_TIMEOUT": "2", "STUB_SLEEP": "6",
                 "STUB_SPAWN_PIDFILE": ct_pidfile}, cwd=ct_repo)
            ct_elapsed = time.time() - t0
            check("claude timeout: an over-running reviewer is cut off at rc=124, on time",
                  rc == 124 and "timed out after 2s" in err and ct_elapsed < 2 + 3,
                  f"rc={rc} elapsed={ct_elapsed:.1f}s err={err}")
            check("claude timeout: the failure names the knob that tunes it",
                  "tune with CLAUDE_TIMEOUT" in err, err)
            ct_leaked = None
            check("claude timeout: stub recorded a descendant pid file",
                  os.path.isfile(ct_pidfile), ct_pidfile)
            if os.path.isfile(ct_pidfile):
                ct_raw = open(ct_pidfile).read().strip()
                ct_leaked = int(ct_raw) if ct_raw.isdigit() else 0
                check("claude timeout: descendant pid file holds a positive integer",
                      ct_leaked > 0, repr(ct_raw))
                if ct_leaked <= 0:
                    ct_leaked = None   # never poll pid 0 (that signals the group)
            if ct_leaked is not None:
                ct_deadline = time.time() + 3
                while time.time() < ct_deadline:
                    if not pid_running(ct_leaked):
                        ct_leaked = None
                        break
                    time.sleep(0.1)
            check("claude timeout: the whole process group dies (no surviving descendant)",
                  ct_leaked is None, f"pid still running: {ct_leaked}")

            # ---- an install with NO lib/ AT ALL. The renamed-file fixture above
            # proves the per-file check; this proves the case an incomplete
            # install actually produces — and that the path named is the one
            # derived from the RUNNER's own resolved location, not the working
            # tree's, which is the only reason the check can be trusted. ----
            for who, cli in (("codex", "codex"), ("claude", "claude")):
                nl_tree = os.path.realpath(os.path.join(runner_tmp, f"libabsent-{who}"))
                shutil.copytree(SCRIPTS, nl_tree,
                                ignore=shutil.ignore_patterns("__pycache__"))
                shutil.rmtree(os.path.join(nl_tree, "lib"))
                nl_bin = os.path.join(runner_tmp, f"bin-libabsent-{who}")
                nl_cap = os.path.join(runner_tmp, f"cap-libabsent-{who}")
                make_stub(nl_bin, cli, nl_cap)
                nl_dir = os.path.join(runner_tmp, f"libabsent-brief-{who}")
                nl_brief = brief_file(f"libabsent-brief-{who}/brief.md")
                rc, out, err = run_script(
                    os.path.join(nl_tree, f"{who}_consult.sh"), [nl_brief],
                    {"PATH": nl_bin + os.pathsep + os.environ.get("PATH", "")})
                want = os.path.join(nl_tree, "lib", "consult_common.sh")
                check(f"{who} lib ABSENT: exit 3 naming ITS OWN lib path",
                      rc == 3 and f"consult runner library missing: {want}" in err,
                      f"rc={rc} want={want} err={err}")
                check(f"{who} lib ABSENT: the working tree's lib/ is never fallen back to",
                      os.path.join(SCRIPTS, "lib") not in err, err)
                check(f"{who} lib ABSENT: the CLI is never invoked and nothing is written",
                      read_calls(nl_cap) == []
                      and sorted(os.listdir(nl_dir)) == ["brief.md"],
                      f"calls={read_calls(nl_cap)} left={sorted(os.listdir(nl_dir))}")

            # ==== (2.18) a runner invoked from a STALE cache path forwards
            # itself to the INSTALLED version. FIELD DEFECT 2026-09-28: a
            # session started on 2.16.1, 2.17.0 was installed while it was
            # open, `/reload-plugins` refreshed hooks and commands but did NOT
            # re-inject the SessionStart brief, and the host kept invoking the
            # literal 2.16.1 path it still carried in context — three more
            # consults ran with the 2.16.1 defect. Every fixture here is
            # HERMETIC: a fake <plugins> root in tmp holding real copies of
            # the working tree's scripts/ under cache/haejwo/haejwo/<ver>/,
            # each with its own manifest, plus a registry JSON. ====
            fw_root = os.path.realpath(tempfile.mkdtemp(dir=runner_tmp, prefix="fw-"))
            FW_HOP = " is stale — forwarding to "

            # The destination-completeness set lives in forward.py, and BOTH
            # entrypoints check the same files before they are allowed to run
            # python at all — so they cannot read the list from a file that is
            # itself on it. One literal is impossible; drift is not allowed.
            def fw_named_list(path, pattern, token=r"[^\s\"']+"):
                m = re.search(pattern, open(path, encoding="utf-8").read(), re.S)
                return re.findall(token, m.group(1)) if m else []

            fw_required = fw_named_list(
                os.path.join(SCRIPTS, "lib", "forward.py"),
                r"REQUIRED_LIB = \(([^)]*)\)", r'"([^"]+)"')
            fw_entry_lists = {
                who: fw_named_list(os.path.join(SCRIPTS, f"{who}_consult.sh"),
                                   r"for _hjw_f in ([^;]+); do")
                for who in ("codex", "claude")}
            check("2.18 completeness set: forward.py's REQUIRED_LIB and BOTH "
                  "entrypoints' pre-source checks name the SAME helpers — the "
                  "entrypoints cannot read the list from a file they are still "
                  "checking for, so a test keeps the three in sync",
                  len(fw_required) >= 5
                  and fw_entry_lists["codex"] == fw_required
                  and fw_entry_lists["claude"] == fw_required,
                  f"forward.py={fw_required} codex={fw_entry_lists['codex']} "
                  f"claude={fw_entry_lists['claude']}")

            def fw_manifest(root, version):
                d = os.path.join(root, ".claude-plugin")
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, "plugin.json"), "w") as f:
                    json.dump({"name": "haejwo", "version": version}, f)

            def fw_install(case, versions, vendor=".claude"):
                """<fw_root>/<case>/<vendor>/plugins/cache/haejwo/haejwo/<ver>/
                — the REAL installed-cache layout, one copy of the working
                tree's scripts/ per version. Returns the <plugins> root."""
                plugins = os.path.join(fw_root, case, vendor, "plugins")
                for v in versions:
                    root = os.path.join(plugins, "cache", "haejwo", "haejwo", v)
                    shutil.copytree(SCRIPTS, os.path.join(root, "scripts"),
                                    ignore=shutil.ignore_patterns("__pycache__"))
                    fw_manifest(root, v)
                os.makedirs(plugins, exist_ok=True)
                return plugins

            def fw_dir(plugins, version):
                return os.path.join(plugins, "cache", "haejwo", "haejwo", version)

            def fw_script(plugins, version, who):
                return os.path.join(fw_dir(plugins, version), "scripts",
                                    f"{who}_consult.sh")

            def fw_entry(plugins, version, install=None, scope="user"):
                e = {"scope": scope, "version": version}
                if install is not False:
                    e["installPath"] = (install if install is not None
                                        else fw_dir(plugins, version))
                return e

            def fw_registry(plugins, payload, raw=False):
                path = os.path.join(plugins, "installed_plugins.json")
                with open(path, "w") as f:
                    if raw:
                        f.write(payload)
                    else:
                        json.dump(payload, f)
                return path

            def fw_reg_entries(plugins, entries):
                return fw_registry(plugins, {"version": 2,
                                             "plugins": {"haejwo@haejwo": entries}})

            def fw_run(label, script, who, args=None, stdin_data="", cwd=None,
                       env_extra=None):
                """One hermetic runner invocation, with the reply/log paths and
                the stub's capture dir handed back."""
                bin_dir = os.path.join(runner_tmp, f"bin-fw-{label}")
                cap = os.path.join(runner_tmp, f"cap-fw-{label}")
                make_stub(bin_dir, who, cap)
                env = {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}
                if env_extra:
                    env.update(env_extra)
                if args is None:
                    args = [brief_file(f"fw-{label}.md")]
                rc, out, err = run_script(script, args, env,
                                          stdin_data=stdin_data, cwd=cwd)
                return rc, out, err, cap

            def fw_log_for(brief_path):
                p = os.path.splitext(brief_path)[0] + ".reply.log"
                return open(p, encoding="utf-8").read() if os.path.isfile(p) else ""

            def fw_hops(err):
                return [l for l in err.splitlines() if FW_HOP in l]

            def fw_fallbacks(err):
                # Counted as LINES, never as "the whole of stderr": a failed
                # exec also makes bash print its own (unescaped) error, so the
                # total stderr line count is not the runner's to promise.
                return [l for l in err.splitlines() if "forwarding failed" in l]

            def fw_cli_env(cap, idx=1):
                """The environment the REVIEWER process actually received."""
                p = os.path.join(cap, f"call_{idx}.env0")
                if not os.path.isfile(p):
                    return []
                return open(p, encoding="utf-8", errors="replace").read().split("\0")

            def fw_marker_gone(cap, idx=1):
                return not any(v.startswith("HJW_FORWARDED=")
                               for v in fw_cli_env(cap, idx))

            def fw_no_forward(slug, title, label, script, who, plugins=None,
                              env_extra=None):
                """The whole fail-open contract in one assertion set: nothing
                is forwarded, nothing is said about it, and the run completes
                locally with exactly one reviewer call."""
                brief = brief_file(f"fw-{label}.md")
                rc, out, err, cap = fw_run(label, script, who, args=[brief],
                                           env_extra=env_extra)
                calls = read_calls(cap)
                check(f"{slug}: {title} -> no forward, no diagnostic",
                      not fw_hops(err) and "forwarding failed" not in err,
                      f"err={err}")
                check(f"{slug}: {title} -> the run completes locally, exactly one "
                      "reviewer call",
                      rc == 0 and "STUB-REPLY-OK-1" in out and len(calls) == 1,
                      f"rc={rc} calls={len(calls)} err={err}")
                check(f"{slug}: {title} -> the hop marker never reaches the reviewer "
                      "process",
                      fw_marker_gone(cap),
                      [v for v in fw_cli_env(cap) if v.startswith("HJW_")])
                return rc, out, err, cap, brief

            # ---- (t1) UPGRADE: the invoked 9.8.0 forwards to the installed
            # 9.9.0 — the measured field case, on BOTH entrypoints. ----
            for who, vendor in (("codex", ".claude"), ("claude", ".claude")):
                t1_plugins = fw_install(f"t1-{who}", ["9.8.0", "9.9.0"], vendor)
                fw_reg_entries(t1_plugins, [fw_entry(t1_plugins, "9.9.0")])
                t1_brief = brief_file(f"fw-t1-{who}.md")
                rc, out, err, cap = fw_run(
                    f"t1-{who}", fw_script(t1_plugins, "9.8.0", who), who,
                    args=[t1_brief])
                hops = fw_hops(err)
                want = fw_script(t1_plugins, "9.9.0", who)
                check(f"t1 {who} upgrade: EXACTLY ONE forwarding line, naming both "
                      "versions and the destination",
                      len(hops) == 1 and hops[0] ==
                      f"# runner 9.8.0 is stale — forwarding to 9.9.0 ({want})",
                      f"hops={hops}")
                check(f"t1 {who} upgrade: the log header proves 9.9.0 RAN — plugin= is "
                      "read from the running process's own manifest",
                      "plugin=9.9.0" in fw_log_for(t1_brief),
                      fw_log_for(t1_brief)[:300])
                check(f"t1 {who} upgrade: the reply is produced and the reviewer is "
                      "invoked exactly once",
                      rc == 0 and "STUB-REPLY-OK-1" in out
                      and len(read_calls(cap)) == 1,
                      f"rc={rc} calls={len(read_calls(cap))} err={err}")
                check(f"t1 {who} upgrade: the hop marker NEVER reaches the reviewer "
                      "process",
                      fw_marker_gone(cap),
                      [v for v in fw_cli_env(cap) if v.startswith("HJW_")])

            # cross-host isolation: the same claude runner, installed under a
            # .codex plugins root that keeps NO registry, must run as invoked.
            # A Codex-owned runner never consults Claude's bookkeeping.
            t1x_plugins = fw_install("t1-xhost", ["9.8.0", "9.9.0"], ".codex")
            fw_no_forward("t1", "a .codex plugins root has no registry (cross-host "
                          "isolation)", "t1-xhost",
                          fw_script(t1x_plugins, "9.8.0", "claude"), "claude")

            # ---- (t2) DOWNGRADE: string inequality, both directions. The host
            # installed what it installed; "higher" is not this layer's call. ----
            t2_plugins = fw_install("t2", ["9.8.0", "9.9.0"])
            fw_reg_entries(t2_plugins, [fw_entry(t2_plugins, "9.8.0")])
            t2_brief = brief_file("fw-t2.md")
            rc, out, err, cap = fw_run("t2", fw_script(t2_plugins, "9.9.0", "codex"),
                                       "codex", args=[t2_brief])
            hops = fw_hops(err)
            check("t2 downgrade: an installed OLDER version is followed too — no "
                  "lexical version ordering",
                  len(hops) == 1 and "runner 9.9.0 is stale — forwarding to 9.8.0"
                  in hops[0] and "plugin=9.8.0" in fw_log_for(t2_brief),
                  f"hops={hops} log={fw_log_for(t2_brief)[:200]}")
            check("t2 downgrade: the run completes, one reviewer call, no marker leak",
                  rc == 0 and len(read_calls(cap)) == 1 and fw_marker_gone(cap),
                  f"rc={rc} calls={len(read_calls(cap))}")

            # ---- (t3) CURRENT: the registry names the invoked version. ----
            t3_plugins = fw_install("t3", ["9.8.0"])
            fw_reg_entries(t3_plugins, [fw_entry(t3_plugins, "9.8.0")])
            rc, out, err, cap, t3_brief = fw_no_forward(
                "t3", "the registry names the INVOKED version", "t3",
                fw_script(t3_plugins, "9.8.0", "codex"), "codex")
            check("t3 current: the header still discloses which version ran",
                  "plugin=9.8.0" in fw_log_for(t3_brief),
                  fw_log_for(t3_brief)[:200])

            # ---- (t4) the registry resolves to the INVOKED copy — directly,
            # and through a symlink. Path text is not identity. ----
            t4_plugins = fw_install("t4-direct", ["9.8.0"])
            # the same directory, spelled differently (redundant '.' + trailing
            # separator): normalization, not string comparison, decides.
            fw_reg_entries(t4_plugins, [fw_entry(
                t4_plugins, "9.8.0",
                install=os.path.join(fw_dir(t4_plugins, "9.8.0"), ".", ""))])
            fw_no_forward("t4", "the registry points at the invoked copy (direct)",
                          "t4-direct", fw_script(t4_plugins, "9.8.0", "codex"),
                          "codex")

            # A 9.9.0 install root whose manifest really says 9.9.0 but whose
            # scripts/ is a SYMLINK to 9.8.0's: every version check passes and
            # only PHYSICAL identity stops the runner exec'ing itself.
            t4s_plugins = fw_install("t4-symlink", ["9.8.0"])
            t4s_root = fw_dir(t4s_plugins, "9.9.0")
            os.makedirs(t4s_root, exist_ok=True)
            fw_manifest(t4s_root, "9.9.0")
            os.symlink(os.path.join(fw_dir(t4s_plugins, "9.8.0"), "scripts"),
                       os.path.join(t4s_root, "scripts"))
            fw_reg_entries(t4s_plugins, [fw_entry(t4s_plugins, "9.9.0")])
            fw_no_forward("t4", "the target resolves to the invoked file (symlink)",
                          "t4-symlink", fw_script(t4s_plugins, "9.8.0", "codex"),
                          "codex")

            # ---- (t5) AMBIGUOUS: two installs disagreeing about the version is
            # exactly where a guess would send a review to the wrong code. ----
            t5_plugins = fw_install("t5", ["9.7.0", "9.8.0", "9.9.0"])
            fw_reg_entries(t5_plugins, [fw_entry(t5_plugins, "9.9.0"),
                                        fw_entry(t5_plugins, "9.7.0")])
            fw_no_forward("t5", "two entries under one cache root with different "
                          "versions", "t5", fw_script(t5_plugins, "9.8.0", "codex"),
                          "codex")

            # ---- (t6) the registry is missing, malformed, or says nothing
            # about haejwo: fail open, never crash. ----
            t6_cases = [
                ("absent", None, False),
                ("malformed", "{ not json at all", True),
                ("not-an-object", "[1, 2, 3]", True),
                ("no-plugin-key", {"version": 2, "plugins": {"other@other": []}}, False),
                ("entry-without-installPath", {"version": 2, "plugins": {
                    "haejwo@haejwo": [{"scope": "user", "version": "9.9.0"}]}}, False),
                ("entry-not-an-object", {"version": 2, "plugins": {
                    "haejwo@haejwo": ["9.9.0"]}}, False),
                ("installPath-outside-the-cache-root", {"version": 2, "plugins": {
                    "haejwo@haejwo": [{"scope": "user", "version": "9.9.0",
                                       "installPath": "/nowhere/haejwo/9.9.0"}]}}, False),
            ]
            for name, payload, raw in t6_cases:
                t6_plugins = fw_install(f"t6-{name}", ["9.8.0", "9.9.0"])
                if payload is not None:
                    fw_registry(t6_plugins, payload, raw=raw)
                fw_no_forward("t6", f"registry {name}", f"t6-{name}",
                              fw_script(t6_plugins, "9.8.0", "codex"), "codex")

            # ---- (t7) the registry is host bookkeeping, not proof: an
            # INCOMPLETE target is never followed. EVERY helper the entrypoints
            # check before sourcing is required here, one at a time: a
            # destination missing one exits 3 AFTER it has replaced this
            # process, and by then there is no local fallback left to run. ----
            for helper in fw_required:
                t7_lib = fw_install(f"t7-lib-{helper}", ["9.8.0", "9.9.0"])
                fw_reg_entries(t7_lib, [fw_entry(t7_lib, "9.9.0")])
                os.remove(os.path.join(fw_dir(t7_lib, "9.9.0"), "scripts", "lib",
                                       helper))
                fw_no_forward("t7", f"the target lib is missing {helper}",
                              f"t7-lib-{helper}",
                              fw_script(t7_lib, "9.8.0", "codex"), "codex")

            t7_mm = fw_install("t7-manifest", ["9.8.0", "9.9.0"])
            fw_reg_entries(t7_mm, [fw_entry(t7_mm, "9.9.0")])
            fw_manifest(fw_dir(t7_mm, "9.9.0"), "9.9.1")   # target disagrees
            fw_no_forward("t7", "the target manifest contradicts the registry",
                          "t7-manifest", fw_script(t7_mm, "9.8.0", "codex"), "codex")

            t7_x = fw_install("t7-noexec", ["9.8.0", "9.9.0"])
            fw_reg_entries(t7_x, [fw_entry(t7_x, "9.9.0")])
            os.chmod(fw_script(t7_x, "9.9.0", "codex"), 0o644)
            fw_no_forward("t7", "the target runner is not executable",
                          "t7-noexec", fw_script(t7_x, "9.8.0", "codex"), "codex")

            # A target that passes EVERY check and still cannot be exec'd (the
            # kernel, not the validator, has the last word — here a missing
            # interpreter). A non-interactive bash EXITS on a failed `exec`
            # unless execfail is set, which would turn a broken cache entry
            # into a review that silently never ran.
            t7_f = fw_install("t7-execfail", ["9.8.0", "9.9.0"])
            fw_reg_entries(t7_f, [fw_entry(t7_f, "9.9.0")])
            t7_f_target = fw_script(t7_f, "9.9.0", "codex")
            with open(t7_f_target, "w") as f:
                f.write("#!/nonexistent/hjw-interpreter\nexit 0\n")
            os.chmod(t7_f_target, 0o755)
            t7_f_brief = brief_file("fw-t7-execfail.md")
            rc, out, err, cap = fw_run("t7-execfail",
                                       fw_script(t7_f, "9.8.0", "codex"), "codex",
                                       args=[t7_f_brief])
            check("t7 failed exec: EXACTLY ONE hop line and EXACTLY ONE fallback line "
                  "— bash prints its own (unescaped) error for the failed exec too, so "
                  "the assertion counts OUR lines, not stderr's",
                  len(fw_hops(err)) == 1
                  and fw_fallbacks(err) == ["# forwarding failed — running 9.8.0 locally"],
                  f"hops={fw_hops(err)} fallbacks={fw_fallbacks(err)} err={err!r}")
            check("t7 failed exec: the LOCAL run completes and it is 9.8.0 that ran",
                  rc == 0 and "STUB-REPLY-OK-1" in out
                  and len(read_calls(cap)) == 1
                  and "plugin=9.8.0" in fw_log_for(t7_f_brief),
                  f"rc={rc} calls={len(read_calls(cap))} log={fw_log_for(t7_f_brief)[:200]}")
            check("t7 failed exec: the marker is removed again before the reviewer runs",
                  fw_marker_gone(cap),
                  [v for v in fw_cli_env(cap) if v.startswith("HJW_")])

            # ---- (t8) LOOP PREVENTION: a forwarded process never forwards
            # again, however stale it looks, and it strips the marker. ----
            t8_plugins = fw_install("t8", ["9.8.0", "9.9.0"])
            fw_reg_entries(t8_plugins, [fw_entry(t8_plugins, "9.9.0")])
            rc, out, err, cap, t8_brief = fw_no_forward(
                "t8", "HJW_FORWARDED preset while genuinely stale", "t8",
                fw_script(t8_plugins, "9.8.0", "codex"), "codex",
                env_extra={"HJW_FORWARDED": "1"})
            check("t8 loop prevention: the marker is unset for the reviewer, and the "
                  "stale runner is the one that ran",
                  fw_marker_gone(cap) and "plugin=9.8.0" in fw_log_for(t8_brief),
                  [v for v in fw_cli_env(cap) if v.startswith("HJW_")])

            # ---- (t9) DEVELOPMENT CHECKOUT: a maintainer running the working
            # tree gets the working tree, whatever any registry says. ----
            t9_dev = os.path.join(fw_root, "t9-dev")
            shutil.copytree(SCRIPTS, os.path.join(t9_dev, "scripts"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            fw_manifest(t9_dev, "9.8.0")
            fw_registry(t9_dev, {"version": 2, "plugins": {"haejwo@haejwo": [
                {"scope": "user", "version": "9.9.0",
                 "installPath": os.path.join(t9_dev, "9.9.0")}]}})
            fw_no_forward("t9", "a checkout outside the installed-cache layout",
                          "t9-dev", os.path.join(t9_dev, "scripts",
                                                 "codex_consult.sh"), "codex")

            rc, out, err, cap, t9_brief = fw_no_forward(
                "t9", "THIS repository's own runner", "t9-worktree",
                codex_script, "codex")
            t9_ver = json.load(open(os.path.join(
                PLUGIN, ".claude-plugin", "plugin.json")))["version"]
            check("t9 working tree: the header still names the version that ran",
                  f"plugin={t9_ver}" in fw_log_for(t9_brief),
                  fw_log_for(t9_brief)[:200])

            # ---- (t10) FIDELITY: a forwarded run must be indistinguishable
            # from having invoked the installed runner directly — same argv
            # bytes (spaces and a newline included), same stdin, same cwd, and
            # the destination's exit status is the run's exit status. ----
            t10_plugins = fw_install("t10", ["9.8.0", "9.9.0"])
            fw_reg_entries(t10_plugins, [fw_entry(t10_plugins, "9.9.0")])
            t10_repo = make_repo("repo-fw-fidelity")
            t10_out = os.path.join(runner_tmp, "fw t10 reply\nwith newline.md")
            t10_stdin = "FIDELITY-BRIEF-MARKER body\n"
            rc, out, err, cap = fw_run(
                "t10", fw_script(t10_plugins, "9.8.0", "codex"), "codex",
                args=["--mode", "consult", "-o", t10_out, "-"],
                stdin_data=t10_stdin, cwd=t10_repo)
            calls = read_calls(cap)
            t10_cwd_file = os.path.join(cap, "call_1.cwd")
            t10_cwd = (open(t10_cwd_file).read().strip()
                       if os.path.isfile(t10_cwd_file) else "")
            check("t10 fidelity: an -o argument carrying a space AND a newline arrives "
                  "at the destination byte for byte",
                  len(fw_hops(err)) == 1 and rc == 0 and os.path.isfile(t10_out)
                  and "STUB-REPLY-OK-1" in open(t10_out, encoding="utf-8").read(),
                  f"rc={rc} exists={os.path.isfile(t10_out)} err={err}")
            check("t10 fidelity: the stdin brief survives the hop (it is consumed only "
                  "AFTER forwarding, by the destination)",
                  len(calls) == 1 and "FIDELITY-BRIEF-MARKER body" in calls[0][1],
                  calls[0][1][:200] if calls else "<no call>")
            check("t10 fidelity: the working directory is unchanged by the hop",
                  t10_cwd == os.path.realpath(t10_repo),
                  f"stub cwd={t10_cwd!r} want={os.path.realpath(t10_repo)!r}")
            # an explicit -o derives $LOG as `${OUT%.*}.log`, not `.reply.log`
            t10_log_path = os.path.splitext(t10_out)[0] + ".log"
            t10_log = (open(t10_log_path, encoding="utf-8").read()
                       if os.path.isfile(t10_log_path) else "")
            check("t10 fidelity: 9.9.0 is what ran",
                  "plugin=9.9.0" in t10_log, t10_log[:200])
            check("t10 fidelity: the hop marker never reaches the reviewer process",
                  fw_marker_gone(cap),
                  [v for v in fw_cli_env(cap) if v.startswith("HJW_")])

            # The destination's failure is the run's failure — never a retry of
            # the old runner, and never a status invented by the hop.
            t10f_plugins = fw_install("t10-rc", ["9.8.0", "9.9.0"])
            fw_reg_entries(t10f_plugins, [fw_entry(t10f_plugins, "9.9.0")])
            fail_env = {"STUB_RC": "1", "STUB_NO_OUT": "1"}
            rc_fwd, _, err_fwd, cap_fwd = fw_run(
                "t10-rc-fwd", fw_script(t10f_plugins, "9.8.0", "codex"), "codex",
                env_extra=fail_env)
            rc_direct, _, _, cap_direct = fw_run(
                "t10-rc-direct", fw_script(t10f_plugins, "9.9.0", "codex"), "codex",
                env_extra=fail_env)
            check("t10 fidelity: a forwarded run exits with the DESTINATION's status, "
                  "identical to invoking it directly",
                  len(fw_hops(err_fwd)) == 1 and rc_fwd != 0
                  and rc_fwd == rc_direct,
                  f"forwarded={rc_fwd} direct={rc_direct}")
            check("t10 fidelity: a failed destination is never retried on the old runner",
                  len(read_calls(cap_fwd)) == len(read_calls(cap_direct)),
                  f"forwarded={len(read_calls(cap_fwd))} direct={len(read_calls(cap_direct))}")
            check("t10 fidelity: a FAILING forwarded run still never leaks the hop "
                  "marker to the reviewer",
                  fw_marker_gone(cap_fwd),
                  [v for v in fw_cli_env(cap_fwd) if v.startswith("HJW_")])

            # ---- (t11) CONTAINMENT: a registry path is a string, not a
            # location. The install AND the executable must RESOLVE inside
            # THIS runner's own cache root — normalization answers traversal,
            # realpath answers symlinks. Ordinary path checks against
            # misconfiguration and casual tampering: validation and exec are
            # still two separate lookups, so this is not an atomic boundary. ----
            def fw_outside_install(case, version):
                """A complete, internally consistent install placed OUTSIDE any
                cache root — exactly what containment must refuse to follow."""
                root = os.path.join(fw_root, case)
                shutil.copytree(SCRIPTS, os.path.join(root, "scripts"),
                                ignore=shutil.ignore_patterns("__pycache__"))
                fw_manifest(root, version)
                return root

            # (a) traversal: the string starts with the cache root and climbs
            # straight back out of it. The raw prefix test cannot see this.
            t11_t = fw_install("t11-traversal", ["9.8.0"])
            t11_t_out = fw_outside_install("t11-traversal-outside", "9.9.0")
            t11_anchor = fw_dir(t11_t, "9.8.0")
            fw_reg_entries(t11_t, [fw_entry(
                t11_t, "9.9.0",
                install=os.path.join(t11_anchor,
                                     os.path.relpath(t11_t_out, t11_anchor)))])
            fw_no_forward("t11", "installPath spelled THROUGH the cache root but "
                          "normalizing outside it", "t11-traversal",
                          fw_script(t11_t, "9.8.0", "codex"), "codex")

            # (b) the install path is a symlink that lives inside the cache
            # root and points out of it: only realpath sees the difference.
            t11_l = fw_install("t11-link", ["9.8.0"])
            t11_l_out = fw_outside_install("t11-link-outside", "9.9.0")
            os.symlink(t11_l_out, fw_dir(t11_l, "9.9.0"))
            fw_reg_entries(t11_l, [fw_entry(t11_l, "9.9.0")])
            fw_no_forward("t11", "installPath is a symlink to a directory outside the "
                          "cache root", "t11-link",
                          fw_script(t11_l, "9.8.0", "codex"), "codex")

            # (c) a contained install directory says nothing about the file
            # that would actually be exec'd inside it.
            t11_x = fw_install("t11-exec", ["9.8.0", "9.9.0"])
            t11_x_out = fw_outside_install("t11-exec-outside", "9.9.0")
            t11_x_target = fw_script(t11_x, "9.9.0", "codex")
            os.remove(t11_x_target)
            os.symlink(os.path.join(t11_x_out, "scripts", "codex_consult.sh"),
                       t11_x_target)
            fw_reg_entries(t11_x, [fw_entry(t11_x, "9.9.0")])
            fw_no_forward("t11", "the target executable is a symlink pointing outside "
                          "the cache root", "t11-exec",
                          fw_script(t11_x, "9.8.0", "codex"), "codex")

            # ---- (t12) LEGACY DESTINATIONS: a pre-2.18 runner has no
            # hop-marker removal, so forwarding into one would hand
            # HJW_FORWARDED straight to the reviewer CLI. No version
            # comparison is needed to prevent it — 2.17 ships no
            # lib/forward.py, and the completeness set requires it. The fixture
            # is the ACTUAL 2.17.0 scripts, read out of git history, because a
            # copy of TODAY's scripts under a 2.17 label would prove nothing. ----
            t12_commit = "0c9cf4e"
            t12_ls = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", t12_commit, "--",
                 "haejwo/scripts"],
                cwd=os.path.dirname(HERE), capture_output=True, text=True)
            t12_files = t12_ls.stdout.split() if t12_ls.returncode == 0 else []
            if not t12_files:
                print(f"  SKIP t12 legacy destination: {t12_commit} is not in this "
                      "clone (shallow checkout or no git history)")
                # Release CI checks out full history (fetch-depth: 0) precisely
                # so this fixture runs; a silent skip THERE would hide a
                # regression. Locally (tarball, shallow clone) skipping is fine.
                check("forwarding/t12: the legacy-destination fixture RUNS under CI "
                      "(full-history checkout)", not os.environ.get("CI"),
                      f"{t12_commit} unreachable in a CI checkout")
            else:
                t12_plugins = fw_install("t12-legacy", ["2.18.0"])
                t12_root = fw_dir(t12_plugins, "2.17.0")
                for rel in t12_files:
                    blob = subprocess.run(["git", "show", f"{t12_commit}:{rel}"],
                                          cwd=os.path.dirname(HERE),
                                          capture_output=True)
                    dest = os.path.join(t12_root, os.path.relpath(rel, "haejwo"))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "wb") as f:
                        f.write(blob.stdout)
                    os.chmod(dest, 0o755)
                fw_manifest(t12_root, "2.17.0")
                t12_lib = os.path.join(t12_root, "scripts", "lib")
                check("t12 legacy: the fixture really IS a pre-2.18 destination — a "
                      "complete 2.17.0 install that ships no lib/forward.py",
                      os.path.isfile(fw_script(t12_plugins, "2.17.0", "codex"))
                      and os.path.isfile(os.path.join(t12_lib, "consult_common.sh"))
                      and not os.path.exists(os.path.join(t12_lib, "forward.py")),
                      sorted(os.listdir(t12_lib)) if os.path.isdir(t12_lib) else "<none>")
                fw_reg_entries(t12_plugins, [fw_entry(t12_plugins, "2.17.0")])
                rc, out, err, cap, t12_brief = fw_no_forward(
                    "t12", "an ACTUAL 2.17.0 install is never forwarded into (it has "
                    "no marker removal)", "t12-legacy",
                    fw_script(t12_plugins, "2.18.0", "codex"), "codex")
                check("t12 legacy: the invoked 2.18.0 runner is the one that ran",
                      "plugin=2.18.0" in fw_log_for(t12_brief),
                      fw_log_for(t12_brief)[:200])

            # ---- (t14, 2.22) OLD FORWARDER -> NEW INSTALL. The 2.18-2.21
            # forward.py requires lib/snapshot.py of every destination; 2.22
            # ships it only as a tombstone so those runners still forward
            # here. The stale side is the ACTUAL v2.21.0 scripts, read out of
            # git history — a relabeled copy of today's would prove nothing. ----
            t14_commit = "v2.21.0"
            t14_ls = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", t14_commit, "--",
                 "haejwo/scripts"],
                cwd=os.path.dirname(HERE), capture_output=True, text=True)
            t14_files = t14_ls.stdout.split() if t14_ls.returncode == 0 else []
            if not t14_files:
                print(f"  SKIP t14 old forwarder: {t14_commit} is not in this clone")
                check("forwarding/t14: the old-forwarder fixture RUNS under CI "
                      "(full-history checkout)", not os.environ.get("CI"),
                      f"{t14_commit} unreachable in a CI checkout")
            else:
                def t14_install(case, tombstone=True):
                    plugins = fw_install(case, ["9.9.0"])
                    root = fw_dir(plugins, "2.21.0")
                    for rel in t14_files:
                        if "__pycache__" in rel:
                            continue
                        blob = subprocess.run(["git", "show", f"{t14_commit}:{rel}"],
                                              cwd=os.path.dirname(HERE),
                                              capture_output=True)
                        dest = os.path.join(root, os.path.relpath(rel, "haejwo"))
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with open(dest, "wb") as f:
                            f.write(blob.stdout)
                        os.chmod(dest, 0o755)
                    fw_manifest(root, "2.21.0")
                    if not tombstone:
                        os.remove(os.path.join(fw_dir(plugins, "9.9.0"), "scripts",
                                               "lib", "snapshot.py"))
                    fw_reg_entries(plugins, [fw_entry(plugins, "9.9.0")])
                    return plugins

                t14_plugins = t14_install("t14-oldfwd")
                t14_old_req = fw_named_list(
                    os.path.join(fw_dir(t14_plugins, "2.21.0"), "scripts", "lib",
                                 "forward.py"),
                    r"REQUIRED_LIB = \(([^)]*)\)", r'"([^"]+)"')
                check("t14 old forwarder: the fixture really IS 2.21's forward.py — it "
                      "requires snapshot.py of a destination, today's does not",
                      "snapshot.py" in t14_old_req and "snapshot.py" not in fw_required,
                      f"old={t14_old_req} new={fw_required}")
                t14_tomb = subprocess.run(
                    [sys.executable, os.path.join(SCRIPTS, "lib", "snapshot.py")],
                    capture_output=True, text=True)
                check("t14 tombstone: lib/snapshot.py exits 2 with one sentence and "
                      "does nothing else",
                      t14_tomb.returncode == 2 and t14_tomb.stdout == ""
                      and t14_tomb.stderr.strip()
                      == "snapshot review was removed in 2.22; review the live working copy",
                      f"rc={t14_tomb.returncode} err={t14_tomb.stderr!r}")
                for who in ("codex", "claude"):
                    t14_brief = brief_file(f"fw-t14-{who}.md")
                    rc, out, err, cap = fw_run(
                        f"t14-{who}", fw_script(t14_plugins, "2.21.0", who), who,
                        args=[t14_brief])
                    hops = fw_hops(err)
                    check(f"t14 {who} old forwarder: a 2.21.0 runner forwards to the new "
                          "install carrying the tombstone, and the new install ran",
                          rc == 0 and len(hops) == 1
                          and "runner 2.21.0 is stale — forwarding to 9.9.0" in hops[0]
                          and len(read_calls(cap)) == 1
                          and "plugin=9.9.0" in fw_log_for(t14_brief)
                          and fw_marker_gone(cap),
                          f"rc={rc} hops={hops} calls={len(read_calls(cap))} "
                          f"log={fw_log_for(t14_brief)[:200]}")
                # Control: WITHOUT the tombstone the old forwarder judges the
                # new install incomplete and runs as invoked — the tombstone is
                # what keeps 2.18-2.21 sessions forwarding.
                t14n_plugins = t14_install("t14-notomb", tombstone=False)
                rc, out, err, cap, t14n_brief = fw_no_forward(
                    "t14", "a 2.21.0 forwarder facing a destination WITHOUT the "
                    "tombstone", "t14-notomb",
                    fw_script(t14n_plugins, "2.21.0", "codex"), "codex")
                check("t14 control: without the tombstone it is 2.21.0 that ran",
                      "plugin=2.21.0" in fw_log_for(t14n_brief),
                      fw_log_for(t14n_brief)[:200])

            # ---- (t15, 2.22) NEW FORWARDER -> LEGACY DESTINATION. A 2.18-2.21
            # destination's OWN runners require lib/snapshot.py and exit 3
            # without it — after the exec, with no local fallback left. Since
            # 2.26 the support floor refuses such a destination even WITH it
            # (W39 below). The destination runner is a stand-in leaving a marker. ----
            for t15_case, t15_keep in (("t15-nosnap", False), ("t15-snap", True)):
                t15_plugins = fw_install(t15_case, ["9.9.0", "2.21.0"])
                t15_marker = os.path.join(fw_root, f"{t15_case}-target-ran")
                t15_target = fw_script(t15_plugins, "2.21.0", "codex")
                with open(t15_target, "w") as f:
                    f.write("#!/usr/bin/env bash\n"
                            f"echo ran > '{t15_marker}'\n"
                            "echo LEGACY-TARGET-RAN\n")
                os.chmod(t15_target, 0o755)
                if not t15_keep:
                    os.remove(os.path.join(fw_dir(t15_plugins, "2.21.0"), "scripts",
                                           "lib", "snapshot.py"))
                fw_reg_entries(t15_plugins, [fw_entry(t15_plugins, "2.21.0")])
                t15_self = fw_script(t15_plugins, "9.9.0", "codex")
                if not t15_keep:
                    rc, out, err, cap, t15_brief = fw_no_forward(
                        "t15", "a 2.21.0 destination WITHOUT lib/snapshot.py",
                        t15_case, t15_self, "codex")
                    check("t15 legacy completeness: without snapshot.py the invoked "
                          "9.9.0 ran and the destination never did",
                          "plugin=9.9.0" in fw_log_for(t15_brief)
                          and not os.path.exists(t15_marker)
                          and "LEGACY-TARGET-RAN" not in out,
                          f"marker={os.path.exists(t15_marker)} "
                          f"log={fw_log_for(t15_brief)[:200]}")
                else:
                    rc, out, err, cap = fw_run(t15_case, t15_self, "codex")
                    hops = fw_hops(err)
                    check("t15 legacy completeness: WITH snapshot.py the 2.21.0 "
                          "destination is still NOT forwarded into — it is below the "
                          "support floor (2.26); no marker",
                          rc == 0 and not hops and "below the supported floor" in err
                          and not os.path.exists(t15_marker)
                          and "LEGACY-TARGET-RAN" not in out,
                          f"rc={rc} hops={hops} marker={os.path.exists(t15_marker)} "
                          f"err={err}")

            # ---- (t13, 2.21) the artifact guard HOLDS ACROSS A HOP. The
            # registry may select an OLDER install (t2: downgrades are
            # followed), and a pre-2.21 runner has no artifact guard — so the
            # INVOKED runner judges the argv before the exec. The destination
            # here is a complete install whose runner is a guardless stand-in
            # that leaves a marker the moment it runs: a refusal must leave
            # no marker, no paid call, no temp brief, and must not read stdin.
            FW_INSIDE = "artifact path is inside the reviewed repository"
            for who in ("codex", "claude"):
                t13_plugins = fw_install(f"t13-{who}", ["9.9.0", "9.8.0"])
                t13_marker = os.path.join(fw_root, f"t13-{who}-target-ran")
                t13_target = fw_script(t13_plugins, "9.8.0", who)
                with open(t13_target, "w") as f:
                    f.write("#!/usr/bin/env bash\n"
                            "# a guardless pre-2.21 destination\n"
                            f"echo ran > '{t13_marker}'\n"
                            f"env | grep -E '^GIT_(DIR|WORK_TREE|INDEX_FILE)=' >> '{t13_marker}'\n"
                            "echo GUARDLESS-TARGET-RAN\n")
                os.chmod(t13_target, 0o755)
                fw_reg_entries(t13_plugins, [fw_entry(t13_plugins, "9.8.0")])
                t13_self = fw_script(t13_plugins, "9.9.0", who)

                def t13_refused(rc, out, err, cap):
                    lines = err.strip().splitlines()
                    return (rc == 2 and len(lines) == 1 and FW_INSIDE in lines[0]
                            and not fw_hops(err) and not os.path.exists(t13_marker)
                            and "GUARDLESS-TARGET-RAN" not in out
                            and not (os.path.isdir(cap) and os.listdir(cap)))

                # (1) `-o` inside the repository: refused BEFORE the exec.
                t13_in = os.path.join(repo_dir, f"t13-{who}-reply.md")
                for spelling, args in (("-o X", ["-o", t13_in]),
                                       ("-oX", ["-o" + t13_in])):
                    rc, out, err, cap = fw_run(
                        f"t13-{who}-in-{len(args)}", t13_self, who,
                        args=args + [brief_file(f"fw-t13-{who}.md")])
                    check(f"t13 {who} hop guard: `{spelling}` inside the repo with a "
                          "registry-selected OLDER guardless target -> exit 2, one "
                          "refusal line, never forwarded (no marker, no reviewer call)",
                          t13_refused(rc, out, err, cap),
                          f"rc={rc} marker={os.path.exists(t13_marker)} err={err}")
                    check(f"t13 {who} hop guard: `{spelling}` refusal wrote nothing in "
                          "the repo",
                          not os.path.exists(t13_in)
                          and not os.path.exists(os.path.splitext(t13_in)[0] + ".log"))

                # (2) the brief positional inside the repo, no `-o`: its default
                # reply lands next to it — refused too.
                t13_rbrief = write_file(os.path.join(repo_dir, f"t13-{who}-brief.md"),
                                        "Test brief body.\n")
                try:
                    rc, out, err, cap = fw_run(f"t13-{who}-rbrief", t13_self, who,
                                               args=[t13_rbrief])
                    check(f"t13 {who} hop guard: a brief INSIDE the repo with no `-o` "
                          "-> refused before the exec",
                          t13_refused(rc, out, err, cap)
                          and not os.path.exists(os.path.join(
                              repo_dir, f"t13-{who}-brief.reply.md")),
                          f"rc={rc} marker={os.path.exists(t13_marker)} err={err}")
                finally:
                    os.remove(t13_rbrief)

                # (3) a `-` brief with `-o` inside: refused before stdin is
                # read. Stdin is a FILE whose offset the child shares, so any
                # read moves it; $TMPDIR is a fresh dir that must stay empty
                # (no temp brief was ever created).
                t13_tmpdir = tempfile.mkdtemp(dir=runner_tmp, prefix=f"t13-{who}-tmp-")
                t13_stdin = os.path.join(runner_tmp, f"t13-{who}-stdin.md")
                with open(t13_stdin, "w") as f:
                    f.write("Stdin brief body.\n" * 64)
                t13_bin = os.path.join(runner_tmp, f"bin-fw-t13-{who}-stdin")
                t13_cap = os.path.join(runner_tmp, f"cap-fw-t13-{who}-stdin")
                make_stub(t13_bin, who, t13_cap)
                t13_env = dict(os.environ)
                for var in ("CODEX_MODEL", "CODEX_EFFORT", "CODEX_SANDBOX",
                            "CLAUDE_MODEL", "CODEX_TIMEOUT", "CLAUDE_TIMEOUT",
                            "CLAUDE_CODE_SESSION_ID", "HJW_CLAUDE_EFFORT"):
                    t13_env.pop(var, None)
                t13_env.update({
                    "PATH": t13_bin + os.pathsep + os.environ.get("PATH", ""),
                    "CLAUDE_PLUGIN_DATA": empty_data_dir_named("nocfg-"),
                    "TMPDIR": t13_tmpdir})
                with open(t13_stdin, "rb") as t13_in_f:
                    p = subprocess.run(["bash", t13_self, "-o", t13_in, "-"],
                                       stdin=t13_in_f, capture_output=True, text=True,
                                       timeout=60, cwd=repo_dir, env=t13_env)
                    t13_off = os.lseek(t13_in_f.fileno(), 0, os.SEEK_CUR)
                check(f"t13 {who} hop guard: a `-` brief with `-o` inside -> refused "
                      "before the exec",
                      t13_refused(p.returncode, p.stdout, p.stderr, t13_cap),
                      f"rc={p.returncode} marker={os.path.exists(t13_marker)} err={p.stderr}")
                check(f"t13 {who} hop guard: ... and before stdin is read (offset 0, "
                      "no temp brief created)",
                      t13_off == 0 and not os.listdir(t13_tmpdir),
                      f"offset={t13_off} tmpdir={os.listdir(t13_tmpdir)}")

                # (4) `-o` OUTSIDE the repository: forwarded exactly as before.
                rc, out, err, cap = fw_run(
                    f"t13-{who}-out", t13_self, who,
                    args=["-o", os.path.join(runner_tmp, f"t13-{who}-out.md"),
                          brief_file(f"fw-t13-{who}-out.md")])
                hops = fw_hops(err)
                check(f"t13 {who} hop guard: `-o` OUTSIDE the repo -> forwarded to the "
                      "older target as before (one hop line, the target ran)",
                      rc == 0 and len(hops) == 1
                      and "runner 9.9.0 is stale — forwarding to 9.8.0" in hops[0]
                      and os.path.exists(t13_marker) and "GUARDLESS-TARGET-RAN" in out,
                      f"rc={rc} hops={hops} out={out} err={err}")

                # (5, 2.26 review a) a 2.22–2.25 target's detection inherits
                # GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE: the hop never passes them.
                t13_other = make_repo_committed(f"repo-t13-git-{who}")
                os.remove(t13_marker)
                rc, out, err, cap = fw_run(
                    f"t13-{who}-gitenv", t13_self, who,
                    args=["-o", os.path.join(runner_tmp, f"t13-{who}-gitenv.md"),
                          brief_file(f"fw-t13-{who}-gitenv.md")],
                    env_extra={"GIT_DIR": os.path.join(t13_other, ".git"),
                               "GIT_WORK_TREE": t13_other,
                               "GIT_INDEX_FILE": os.path.join(t13_other, ".git", "index")})
                t13_seen = (open(t13_marker).read().splitlines()
                            if os.path.exists(t13_marker) else None)
                check(f"t13 {who} hop: GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE -> another repo "
                      "in the caller's env -> the forwarded target sees none of them",
                      rc == 0 and len(fw_hops(err)) == 1 and t13_seen == ["ran"],
                      f"rc={rc} seen={t13_seen} err={err}")


            # ==== (2.25, W33) runner lifecycle & detection: B2 a parent
            # signal reaps bounded.py's child group, B4 umask 077 on every
            # runner artifact, B5 only a genuinely unborn HEAD is "unborn". ====
            print("== runner lifecycle & detection (2.25: B2 signals, B4 umask, B5 unborn) ==")
            bounded_py = os.path.join(SCRIPTS, "lib", "bounded.py")
            for sig, want in ((signal.SIGTERM, 143), (signal.SIGINT, 130)):
                b2_pid = os.path.join(runner_tmp, f"b2-{sig.name}.pid")
                b2 = subprocess.Popen(
                    ["python3", bounded_py, "30", "python3", "-c",
                     "import subprocess, sys, time\n"
                     "p = subprocess.Popen(['sleep', '60'])\n"
                     "open(sys.argv[1], 'w').write(str(p.pid))\n"
                     "time.sleep(60)\n", b2_pid])
                b2_deadline = time.time() + 10
                while time.time() < b2_deadline and not (
                        os.path.isfile(b2_pid) and open(b2_pid).read().strip()):
                    time.sleep(0.05)
                b2_gc = int(open(b2_pid).read().strip()) if os.path.isfile(b2_pid) else 0
                b2_pgid = os.getpgid(b2_gc) if b2_gc > 0 else 0
                b2.send_signal(sig)
                try:
                    b2_rc = b2.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    b2.kill()
                    b2_rc = None
                def b2_live_members(pgid):
                    # Live (non-zombie) processes in the group: an orphaned
                    # zombie awaiting a subreaper still answers killpg(0).
                    live = []
                    for d in os.listdir("/proc"):
                        if not d.isdigit():
                            continue
                        try:
                            f = open(f"/proc/{d}/stat").read().rsplit(")", 1)[1].split()
                        except Exception:
                            continue
                        if int(f[2]) == pgid and f[0] != "Z":
                            live.append(int(d))
                    return live
                b2_gone = False
                b2_deadline = time.time() + 3
                while b2_pgid > 0 and time.time() < b2_deadline:
                    if not b2_live_members(b2_pgid):
                        b2_gone = True
                        break
                    time.sleep(0.1)
                check(f"B2 bounded.py: {sig.name} to the parent kills the child's WHOLE "
                      "process group (grandchild included)",
                      b2_pgid > 0 and b2_pgid != os.getpgid(0) and b2_gone,
                      f"pgid={b2_pgid} gone={b2_gone}")
                check(f"B2 bounded.py: {sig.name} is re-raised, so a shell sees {want}",
                      b2_rc == -sig, f"rc={b2_rc}")
                if not b2_gone and b2_pgid > 0:
                    try:
                        os.killpg(b2_pgid, signal.SIGKILL)
                    except Exception:
                        pass
            b2_sh = subprocess.run(
                ["bash", "-c", 'python3 "$1" 30 sleep 30 & b=$!; sleep 0.5; kill -TERM "$b"; '
                 'wait "$b"; echo "rc=$?"', "_", bounded_py],
                capture_output=True, text=True, timeout=20)
            check("B2 bounded.py: the exit status a shell reads after SIGTERM is 143",
                  b2_sh.stdout.strip() == "rc=143", b2_sh.stdout + b2_sh.stderr)

            for who, script, cli in (("codex", codex_script, "codex"),
                                     ("claude", claude_script, "claude")):
                # B4: a stdin brief run under a permissive caller umask leaves
                # ONLY owner-private artifacts in $TMPDIR.
                um_tmp = os.path.join(runner_tmp, f"b4-tmp-{who}")
                os.makedirs(um_tmp, exist_ok=True)
                um_bin = os.path.join(runner_tmp, f"bin-b4-{who}")
                um_cap = os.path.join(runner_tmp, f"cap-b4-{who}")
                make_stub(um_bin, cli, um_cap)
                um_repo = make_repo_committed(f"repo-b4-{who}")
                um_old = os.umask(0o022)
                try:
                    rc, out, err = run_script(
                        script, ["-"],
                        {"PATH": um_bin + os.pathsep + os.environ.get("PATH", ""),
                         "TMPDIR": um_tmp}, stdin_data="Umask brief.\n", cwd=um_repo)
                finally:
                    os.umask(um_old)
                um_modes = {n: oct(os.stat(os.path.join(um_tmp, n)).st_mode & 0o777)
                            for n in sorted(os.listdir(um_tmp))}
                check(f"B4 {who}: a stdin-brief run under umask 022 leaves its artifacts "
                      "0600 (reply, log" + (", events" if who == "codex" else "") + ")",
                      rc == 0 and len(um_modes) == (3 if who == "codex" else 2)
                      and set(um_modes.values()) == {"0o600"},
                      f"rc={rc} modes={um_modes} err={err}")

                # B5: an UNBORN repository (no commits) is not a git error.
                ub_bin = os.path.join(runner_tmp, f"bin-b5-unborn-{who}")
                ub_cap = os.path.join(runner_tmp, f"cap-b5-unborn-{who}")
                make_stub(ub_bin, cli, ub_cap)
                ub_repo = make_repo(f"repo-b5-unborn-{who}")
                rc, out, err = run_script(
                    script, ["-o", os.path.join(runner_tmp, f"b5-unborn-{who}.md"),
                             brief_file(f"b5-unborn-{who}-brief.md")],
                    {"PATH": ub_bin + os.pathsep + os.environ.get("PATH", "")}, cwd=ub_repo)
                check(f"B5 {who}: a repository with NO commits -> the run proceeds",
                      rc == 0 and "STUB-REPLY-OK-1" in out and len(read_calls(ub_cap)) == 1,
                      f"rc={rc} err={err}")

                # B5: a BROKEN branch ref prints the same "Needed a single
                # revision" as an unborn one — it must fail closed, unpaid.
                br_bin = os.path.join(runner_tmp, f"bin-b5-broken-{who}")
                br_cap = os.path.join(runner_tmp, f"cap-b5-broken-{who}")
                make_stub(br_bin, cli, br_cap)
                br_repo = make_repo_committed(f"repo-b5-broken-{who}")
                br_ref = subprocess.run(["git", "-C", br_repo, "symbolic-ref", "HEAD"],
                                        capture_output=True, text=True).stdout.strip()
                write_file(os.path.join(br_repo, ".git", *br_ref.split("/")), "garbage\n")
                rc, out, err = run_script(
                    script, ["-o", os.path.join(runner_tmp, f"b5-broken-{who}.md"),
                             brief_file(f"b5-broken-{who}-brief.md")],
                    {"PATH": br_bin + os.pathsep + os.environ.get("PATH", "")}, cwd=br_repo)
                check(f"B5 {who}: a corrupted .git (garbage branch ref) -> 'change detection "
                      "unavailable', the run fails and the CLI is never invoked",
                      rc == 1 and "change detection unavailable" in err
                      and read_calls(br_cap) == [],
                      f"rc={rc} calls={len(read_calls(br_cap))} err={err}")

            b5_snap = os.path.join(runner_tmp, "b5-unborn-direct.json")
            b5 = subprocess.run(["python3", os.path.join(SCRIPTS, "lib", "detect.py"), "snapshot",
                                 make_repo("repo-b5-direct"), b5_snap],
                                capture_output=True, text=True, timeout=30)
            check("B5 detect.py snapshot: an unborn repository records head='unborn'",
                  b5.returncode == 0 and json.load(open(b5_snap))["head"] == "unborn",
                  f"rc={b5.returncode} err={b5.stderr}")

            # 2.25 fix: the unborn match must not depend on the caller's locale.
            # This host ships no git translations, so a PATH shim localizes
            # git's stderr unless LC_ALL=C — as a translated git would.
            loc_bin = os.path.join(runner_tmp, "bin-b5-locale")
            write_file(os.path.join(loc_bin, "git"), (
                "#!/usr/bin/env python3\nimport os, subprocess, sys\n"
                "r = subprocess.run([%r] + sys.argv[1:], capture_output=True)\n"
                "sys.stdout.buffer.write(r.stdout)\nerr = r.stderr\n"
                "if os.environ.get('LC_ALL') != 'C':\n"
                "    for en, de in ((b'Needed a single revision', b'Brauche eine einzelne Revision'),\n"
                "                   (b'unknown revision', b'unbekannte Revision'),\n"
                "                   (b\"ambiguous argument 'HEAD'\", b\"mehrdeutiges Argument 'HEAD'\")):\n"
                "        err = err.replace(en, de)\n"
                "sys.stderr.buffer.write(err)\nsys.exit(r.returncode)\n") % shutil.which("git"))
            os.chmod(os.path.join(loc_bin, "git"), 0o755)
            loc_env = dict(os.environ, LANG="de_DE.UTF-8", LC_ALL="de_DE.UTF-8",
                           PATH=loc_bin + os.pathsep + os.environ.get("PATH", ""))
            loc_repo = make_repo("repo-b5-locale")
            loc_ctl = subprocess.run(["git", "-C", loc_repo, "rev-parse", "--verify", "HEAD"],
                                     capture_output=True, text=True, env=loc_env, timeout=30)
            loc_snap = os.path.join(runner_tmp, "b5-unborn-locale.json")
            loc = subprocess.run(["python3", os.path.join(SCRIPTS, "lib", "detect.py"), "snapshot",
                                  loc_repo, loc_snap],
                                 capture_output=True, text=True, env=loc_env, timeout=30)
            check("B5 detect.py snapshot: unborn under a German-locale git is still 'unborn' "
                  "(git runs under LC_ALL=C)",
                  "Brauche eine einzelne Revision" in loc_ctl.stderr and loc.returncode == 0
                  and json.load(open(loc_snap))["head"] == "unborn",
                  f"ctl={loc_ctl.stderr!r} rc={loc.returncode} err={loc.stderr}")

            # ==== (2.26, W37) B1: an inherited GIT_DIR/GIT_WORK_TREE never
            # redirects detection or the artifact guard away from the cwd's
            # repository; reused artifacts are tightened to 0600. ====
            print("== runner git env & reused artifacts (2.26: B1, umask repair) ==")
            for who, script, cli in (("codex", codex_script, "codex"),
                                     ("claude", claude_script, "claude")):
                ge_a = make_repo_committed(f"repo-b1-a-{who}")
                ge_b = make_repo_committed(f"repo-b1-b-{who}")
                ge_env = {"GIT_DIR": os.path.join(ge_b, ".git"), "GIT_WORK_TREE": ge_b}
                for tag, target, want_rc in (("cwd", ge_a, 1), ("other", ge_b, 0)):
                    ge_bin = os.path.join(runner_tmp, f"bin-b1-{who}-{tag}")
                    make_stub(ge_bin, cli, os.path.join(runner_tmp, f"cap-b1-{who}-{tag}"))
                    rc, out, err = run_script(
                        script, ["-o", os.path.join(runner_tmp, f"b1-{who}-{tag}.md"),
                                 brief_file(f"b1-{who}-{tag}-brief.md")],
                        dict(ge_env, PATH=ge_bin + os.pathsep + os.environ.get("PATH", ""),
                             STUB_TOUCH_FILE=os.path.join(target, "seed.txt")), cwd=ge_a)
                    check(f"B1 {who}: GIT_DIR/GIT_WORK_TREE -> another repo; a change in the "
                          f"{tag} repo " + ("FAILS the run (detection covers the cwd repo)"
                                            if want_rc else "does not fail it"),
                          rc == want_rc and (("repository changed during the run" in err
                                              and "seed.txt" in err) if want_rc
                                             else "STUB-REPLY-OK-1" in out),
                          f"rc={rc} out={out} err={err}")
                    ge_seen = [v for v in fw_cli_env(os.path.join(runner_tmp, f"cap-b1-{who}-{tag}"))
                               if v.startswith(("GIT_DIR=", "GIT_WORK_TREE=", "GIT_INDEX_FILE="))]
                    check(f"B1 {who} ({tag}): the reviewer process inherits no GIT_DIR/GIT_WORK_TREE",
                          ge_seen == [] and fw_cli_env(os.path.join(
                              runner_tmp, f"cap-b1-{who}-{tag}")) != [], f"seen={ge_seen}")
                ge_bin = os.path.join(runner_tmp, f"bin-b1-{who}-guard")
                ge_cap = os.path.join(runner_tmp, f"cap-b1-{who}-guard")
                make_stub(ge_bin, cli, ge_cap)
                rc, out, err = run_script(
                    script, ["-o", os.path.join(ge_a, "in-repo-reply.md"),
                             brief_file(f"b1-{who}-guard-brief.md")],
                    dict(ge_env, PATH=ge_bin + os.pathsep + os.environ.get("PATH", "")), cwd=ge_a)
                check(f"B1 {who}: with GIT_DIR -> another repo, `-o` inside the cwd repo is "
                      "still refused (exit 2, no reviewer call)",
                      rc == 2 and "inside the reviewed repository" in err
                      and read_calls(ge_cap) == [], f"rc={rc} err={err}")

                # umask repair: reused reply/log/events created 0644 end 0600.
                rr_bin = os.path.join(runner_tmp, f"bin-umr-{who}")
                make_stub(rr_bin, cli, os.path.join(runner_tmp, f"cap-umr-{who}"))
                rr_out = os.path.join(runner_tmp, f"umr-{who}.md")
                rr_files = [rr_out, os.path.join(runner_tmp, f"umr-{who}.log")]
                if who == "codex":
                    rr_files.append(os.path.join(runner_tmp, f"umr-{who}.events.jsonl"))
                for f in rr_files:
                    write_file(f, "stale\n")
                    os.chmod(f, 0o644)
                um_old = os.umask(0o022)
                try:
                    rc, out, err = run_script(
                        script, ["-o", rr_out, brief_file(f"umr-{who}-brief.md")],
                        {"PATH": rr_bin + os.pathsep + os.environ.get("PATH", "")},
                        cwd=make_repo_committed(f"repo-umr-{who}"))
                finally:
                    os.umask(um_old)
                rr_modes = {os.path.basename(f): oct(os.stat(f).st_mode & 0o777)
                            for f in rr_files if os.path.exists(f)}
                check(f"umask repair {who}: a reused 0644 reply/log"
                      + ("/events" if who == "codex" else "") + " ends 0600",
                      rc == 0 and len(rr_modes) == len(rr_files)
                      and set(rr_modes.values()) == {"0o600"},
                      f"rc={rc} modes={rr_modes} err={err}")


            # ==== (2.26, W39) S-C SUPPORT FLOOR. forward.py follows a
            # registry-selected install in BOTH directions (t2), but never one
            # below SUPPORTED_FLOOR: the invoked runner stays local and says
            # so in one line. Tested AT and BELOW the floor; both destinations
            # are complete copies of today's scripts (snapshot.py tombstone
            # included), so only the floor can stop the below-floor hop. ====
            sf_src = open(os.path.join(SCRIPTS, "lib", "forward.py"), encoding="utf-8").read()
            sf_m = re.search(r'^SUPPORTED_FLOOR = "(\d+)\.(\d+)\.(\d+)"', sf_src, re.M)
            check("W39 floor: forward.py defines SUPPORTED_FLOOR as a dotted triple",
                  sf_m is not None and int(sf_m.group(2)) > 0, str(sf_m))
            sf_floor = ".".join(sf_m.groups()) if sf_m else "0.1.0"
            sf_mm = tuple(int(x) for x in sf_floor.split(".")[:2])
            sf_below = f"{sf_mm[0]}.{sf_mm[1] - 1}.0"
            for who in ("codex", "claude"):
                sf_p = fw_install(f"sf-below-{who}", ["9.9.0", sf_below])
                fw_reg_entries(sf_p, [fw_entry(sf_p, sf_below)])
                sf_brief = brief_file(f"fw-sf-below-{who}.md")
                rc, out, err, cap = fw_run(f"sf-below-{who}", fw_script(sf_p, "9.9.0", who),
                                           who, args=[sf_brief])
                sf_lines = [l for l in err.splitlines() if "supported floor" in l]
                check(f"W39 floor {who}: a complete {sf_below} destination (below the floor) "
                      "is NOT forwarded to — one line, then the invoked 9.9.0 runs locally",
                      rc == 0 and not fw_hops(err) and sf_lines == [
                          f"# installed runner {sf_below} is below the supported floor "
                          f"{sf_floor}; running locally"]
                      and "plugin=9.9.0" in fw_log_for(sf_brief)
                      and len(read_calls(cap)) == 1 and fw_marker_gone(cap),
                      f"rc={rc} err={err} log={fw_log_for(sf_brief)[:200]}")
                sf_p = fw_install(f"sf-at-{who}", ["9.9.0", sf_floor])
                fw_reg_entries(sf_p, [fw_entry(sf_p, sf_floor)])
                sf_brief = brief_file(f"fw-sf-at-{who}.md")
                rc, out, err, cap = fw_run(f"sf-at-{who}", fw_script(sf_p, "9.9.0", who),
                                           who, args=[sf_brief])
                hops = fw_hops(err)
                check(f"W39 floor {who}: a {sf_floor} destination (AT the floor) is forwarded "
                      "to, and it ran",
                      rc == 0 and len(hops) == 1 and hops[0] ==
                      f"# runner 9.9.0 is stale — forwarding to {sf_floor} "
                      f"({fw_script(sf_p, sf_floor, who)})"
                      and "supported floor" not in err
                      and f"plugin={sf_floor}" in fw_log_for(sf_brief)
                      and len(read_calls(cap)) == 1 and fw_marker_gone(cap),
                      f"rc={rc} hops={hops} err={err}")
            # An unparsable version is not known to be at the floor: refused,
            # with the floor record (never silently followed).
            sf_p = fw_install("sf-unparsable", ["9.9.0", sf_floor + "-rc1"])
            fw_reg_entries(sf_p, [fw_entry(sf_p, sf_floor + "-rc1")])
            sf_fwd = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS, "lib", "forward.py"), "target",
                 os.path.join(sf_p, "installed_plugins.json"),
                 os.path.join(sf_p, "cache", "haejwo", "haejwo"), "9.9.0",
                 fw_script(sf_p, "9.9.0", "codex"), "codex_consult.sh"],
                capture_output=True, text=True)
            sf_end = "\x04__HJW_SNAP_END__"
            check("W39 floor: an unparsable registry version is treated as below the floor",
                  sf_fwd.returncode == 0 and sf_fwd.stdout
                  == f"{sf_floor}-rc1{sf_end}floor {sf_floor}{sf_end}", repr(sf_fwd.stdout))

            # DRIFT: the floor and the compat code it licenses move TOGETHER.
            # Each site that exists only for installs older than the floor
            # names the floor's major.minor as its NEWEST version; raising
            # the floor fails here until that code is deleted (a deleted site
            # is skipped; a present site naming no version fails).
            sf_sites = {}
            sf_leg = re.search(r"((?:^#[^\n]*\n)+)LEGACY_LIB = ", sf_src, re.M)
            if "LEGACY_LIB" in sf_src:
                sf_sites["lib/forward.py LEGACY_LIB comment"] = sf_leg.group(1) if sf_leg else ""
            sf_tomb = os.path.join(SCRIPTS, "lib", "snapshot.py")
            if os.path.exists(sf_tomb):
                sf_sites["lib/snapshot.py tombstone"] = open(sf_tomb, encoding="utf-8").read()
            sf_newest = {}
            for site, text in sf_sites.items():
                vs = [(int(a), int(b)) for a, b in re.findall(r"\b(\d+)\.(\d+)(?:\.\d+)?\b", text)]
                sf_newest[site] = max(vs) if vs else None
            check("W39 floor drift: SUPPORTED_FLOOR equals the version every compat site "
                  "is anchored to (LEGACY_LIB, the snapshot.py tombstone)",
                  bool(sf_sites) and all(v == sf_mm for v in sf_newest.values()),
                  f"floor={sf_floor} sites={sf_newest}")
            sf_readme = open(os.path.join(PLUGIN, "README.md"), encoding="utf-8").read()
            check("W39 floor: the plugin README states the same floor",
                  f"runners support installs >= {sf_floor}" in sf_readme)

            # ==== (2.26, W39) D8 STATUS COLLECTOR: /haejwo:status runs one
            # read-only, stdlib-only script; the host interprets its block. ====
            st_root = tempfile.mkdtemp(dir=runner_tmp, prefix="status-")
            st_script = os.path.join(SCRIPTS, "status_collect.py")
            st_md = open(os.path.join(PLUGIN, "commands", "status.md"), encoding="utf-8").read()
            check("W39 status: commands/status.md runs scripts/status_collect.py",
                  "scripts/status_collect.py" in st_md and os.path.isfile(st_script))

            def st_run(data_dir, sid, env_extra=None):
                env = {k: v for k, v in os.environ.items()
                       if k not in ("HAEJWO_GATE", "CODEX_MODEL", "CODEX_EFFORT",
                                    "CODEX_SANDBOX", "CODEX_TIMEOUT", "CLAUDE_MODEL",
                                    "HJW_CLAUDE_EFFORT", "CLAUDE_TIMEOUT")}
                env["TZ"] = "UTC"
                env.update(env_extra or {})
                p = subprocess.run([sys.executable, st_script, data_dir, sid],
                                   capture_output=True, text=True, env=env, timeout=30)
                return p.returncode, p.stdout, p.stderr

            def st_tree(root):
                snap = {}
                for d, _, fs in os.walk(root):
                    for fn in fs:
                        path = os.path.join(d, fn)
                        snap[path] = open(path, "rb").read()
                return snap

            st_data = os.path.join(st_root, "fx")
            os.makedirs(os.path.join(st_data, "state"))
            write_file(os.path.join(st_data, "config.json"), json.dumps({
                "configured": True,
                "gate": {"enabled": True, "max_files_per_turn": 3, "bash_guard": False},
                "models": {"default_worker": "sonnet"},
                "codex": {"enabled": True, "verified_at": None, "model": "m1",
                          "effort": "high", "consult_sandbox": "read-only"}}))
            write_file(os.path.join(st_data, "state", "sess-W39-status.json"), json.dumps(
                {"prompt_id": "p1", "files": ["/r/a.py", "/r/b.py"], "updated_at": 0}))

            def st_rec(ts, sid="sess-W39-sta", **k):
                r = {"v": 2, "agent_type": None, "agent_id": None, "sid": sid}
                r.update(k)
                r["ts"] = ts
                return json.dumps(r) + "\n"

            write_file(os.path.join(st_data, "state", "observations.jsonl.1"),
                       st_rec(1000.0, sid="other-sessio", hook="gate", tool="Edit",
                              path="/r/x.py", decision="allow", via="budget"))
            write_file(os.path.join(st_data, "state", "observations.jsonl"), "".join([
                st_rec(2000.0, hook="gate", tool="Edit", path="/r/a.py",
                       decision="deny", via="budget"),
                st_rec(2001.0, hook="gate", tool="Edit", path="/r/b.py",
                       decision="deny", via="budget"),
                st_rec(2002.0, hook="bash_guard", decision="deny", via="redirect",
                       target="/r/c.py"),
                st_rec(2003.0, hook="delegation", subagent_type="haejwo:default-worker",
                       requested_model=None, plan_marker_kind="none", decision="allow",
                       tier_pin_check="pass:not-a-tier"),
                st_rec(2004.0, hook="delegation", subagent_type="codex:codex-rescue",
                       requested_model=None, plan_marker_kind="none", decision="allow",
                       tier_pin_check="pass:not-a-tier"),
                st_rec(2005.0, hook="gate", tool="Write", path="/r/d.py", decision="allow",
                       via="subagent-exempt", agent_type="haejwo:task-worker", agent_id="a1"),
                st_rec(2006.0, hook="bash_guard", decision="allow", via="no-code-write", zz=1),
                "{not json\n"]))
            st_before = st_tree(st_data)
            rc, out, err = st_run(st_data, "sess-W39-status", {"CODEX_EFFORT": "low"})
            st_want = [
                f"[haejwo status] data={st_data} host=claude session=sess-W39-status",
                "config: configured=yes gate=ON budget=3 bash_guard=OFF delegation_guard=ON (effective)",
                "tiers (models): deep_reasoner=inherit default_worker=sonnet task_worker=opus",
                "env overrides: CODEX_EFFORT=low",
                "this turn: 2/3 code edits: /r/a.py, /r/b.py",
                "reviewer (codex): enabled model=m1 effort=high sandbox=read-only consent=none "
                "verified=never verified",
                "observations (machine-wide, both files): records=8 span=1970-01-01 00:16..1970-01-01 "
                "00:33 distinct_sids=2 denies=3 unparsable_lines=1",
                "observations (this session, sid sess-W39-sta): 7 records — bash_guard=2 "
                "delegation=2 gate=3; subagent-origin records: haejwo:task-worker=1",
                "  1970-01-01 00:33 gate deny/budget actor=main Edit a.py",
                "  1970-01-01 00:33 gate deny/budget actor=main Edit b.py",
                "  1970-01-01 00:33 bash_guard deny/redirect actor=main /r/c.py",
                "  1970-01-01 00:33 delegation allow/- actor=main haejwo:default-worker model=None",
                "  1970-01-01 00:33 delegation allow/- actor=main codex:codex-rescue model=None",
                "  1970-01-01 00:33 gate allow/subagent-exempt actor=haejwo:task-worker Write d.py",
                "  1970-01-01 00:33 bash_guard allow/no-code-write actor=main",
                "delegations (this session): 1 — haejwo:default-worker/None=1 "
                "(type/requested_model; None = no override)",
                "  denies=0 tier_pin_check: pass:not-a-tier=1; plan_marker_kind none=1",
                "  codex: rescue delegations=1",
                "anomalies (this session): 3",
                "  deny streak x3 (bash_guard, gate) ending 1970-01-01 00:33",
                "  actor haejwo:task-worker x1 has no delegation record this session",
                "  new shape x1: hook=bash_guard v=2 keys=zz",
                "summary: gate ACTIVE, budget 3, configured yes",
            ]
            st_got = out.splitlines()
            check("W39 status: the collector prints the expected block for a fixture data dir "
                  "(effective flags, env override, counter, reviewer, observations, "
                  "delegations, anomalies), exit 0, nothing on stderr",
                  rc == 0 and err == "" and st_got == st_want,
                  "\n".join(f"{'==' if a == b else '!='} {a!r} | {b!r}"
                            for a, b in zip(st_got + [""] * 40, st_want + [""] * 40)
                            if a or b))
            check("W39 status: the collector is read-only (fixture tree byte-identical)",
                  st_tree(st_data) == st_before)

            # Stale verification names the HOST's spelling of setup.
            st_cfg = json.loads(open(os.path.join(st_data, "config.json")).read())
            st_cfg["codex"]["verified_at"] = int(time.time()) - 40 * 86400
            write_file(os.path.join(st_data, "config.json"), json.dumps(st_cfg))
            rc, out, err = st_run(st_data, "sess-W39-status")
            check("W39 status: verified 40 days ago while enabled -> the re-verify hint",
                  rc == 0 and "(40d ago) — re-run /haejwo:setup to re-verify" in out, out)

            # Malformed everything, unknown session id: still exit 0, notes in the block.
            st_bad = os.path.join(st_root, "bad")
            os.makedirs(os.path.join(st_bad, "state", "observations.jsonl"))
            write_file(os.path.join(st_bad, "config.json"), "{bad")
            write_file(os.path.join(st_bad, "state", "sess-bad.json"), "nope")
            write_file(os.path.join(st_bad, "state", "observations.jsonl.1"), "[1,2]\n")
            for sid, label in (("sess-bad", "known"), ("$CLAUDE_CODE_SESSION_ID", "unsubstituted")):
                rc, out, err = st_run(st_bad, sid)
                check(f"W39 status: malformed config/state/observations ({label} session id) "
                      "-> exit 0 with notes, no traceback",
                      rc == 0 and "Traceback" not in out + err
                      and out.splitlines()[1].startswith("CONFIG MALFORMED")
                      and "state file sess-bad.json unreadable (JSONDecodeError)" in out
                      and "note: observations.jsonl unreadable (IsADirectoryError)" in out
                      and "unparsable_lines=1" in out
                      and out.rstrip().endswith("summary: gate OFF, budget 2, configured no")
                      and (label == "known" or ("may be another session's" in out
                                                and "machine-wide line only" in out)),
                      f"rc={rc} out={out} err={err}")
            rc, out, err = st_run(os.path.join(st_root, "missing"), "")
            check("W39 status: a missing data dir and no session id -> exit 0, defaults",
                  rc == 0 and "data dir not found" in out
                  and "summary: gate ACTIVE, budget 2, configured no" in out, out)

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
