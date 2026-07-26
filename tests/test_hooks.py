#!/usr/bin/env python3
"""Synthetic hook-contract tests for haejwo gate scripts.

Pipes realistic hook JSON payloads into the actual scripts (subprocess, the
real CLI contract) and asserts allow/deny/reset behavior. No Claude Code
required. Run: python3 tests/test_hooks.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "haejwo")
SCRIPTS = os.path.join(PLUGIN, "scripts")
sys.path.insert(0, SCRIPTS)
from hjw_common import DEFAULT_CONFIG, observe, prune_state  # noqa: E402
from session_brief import CORE_BODY, EMERGENCY_CORE, MAX_LEN, UNCONFIGURED_CORE  # noqa: E402

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
        assert len(CANARIES) == 13, "rules-diet canary set must stay at exactly 13"
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

        # Concurrent edits can't slip under the budget (flock serializes)
        procs = []
        for i in range(4):
            p = subprocess.Popen(
                ["python3", os.path.join(SCRIPTS, "gate.py"), PLUGIN, data],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            procs.append((p, json.dumps(edit_payload(f"/repo/r/r{i}.py", sid="sess-R"))))
        outs = []
        for p, payload_s in procs:
            stdout, _ = p.communicate(payload_s, timeout=20)
            try:
                outs.append(json.loads(stdout.strip().splitlines()[-1]))
            except Exception:
                outs.append({})
        denies = sum(1 for o in outs if decision(o) == "deny")
        check("4 concurrent edits -> exactly 2 denied (no race undercount)",
              denies == 2, f"denies={denies}")

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
        check("unconfigured -> core body present (honest cause split)",
              CORE_BODY in ctx and UNCONFIGURED_CORE in ctx, ctx)
        check("unconfigured -> does NOT claim the rules file is unreadable "
              "(that's the configured-degrade cause, not this one)",
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
              and "spawn_agent" in ctx and "gpt-5.6-luna" in ctx, ctx)

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

        print("== hjw_common.DEFAULT_CONFIG ==")
        check("models.deep_reasoner defaults to inherit (2.10: was opus)",
              DEFAULT_CONFIG["models"]["deep_reasoner"] == "inherit")
        check("models_codex defaults",
              DEFAULT_CONFIG["models_codex"] == {
                  "deep_reasoner": "inherit",
                  "default_worker": "gpt-5.6-terra",
                  "task_worker": "gpt-5.6-luna",
              })
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
        # (wording drift, punctuation, ordering) is caught even if the
        # 'haiku'/'sonnet' fragments themselves survive unchanged.
        CLAUDE_HOST_DENY_TEXT = (
            "[haejwo gate] Delegation to generic agent 'general-purpose' without an "
            "explicit model — it would INHERIT the session model (judgment rates for "
            "execution). Pass model: 'haiku' (locate) or 'sonnet' (read/summarize), or "
            "delegate to haejwo:default-worker / haejwo:task-worker instead. Emergency "
            "override: /haejwo:gate off."
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
        #    falls back to Claude wording; still denies; exit unchanged.
        with open(os.path.join(codex_data, "config.json"), "w") as f:
            json.dump({"models_codex": "not-a-dict"}, f)
        rc, out = run("delegation_gate.py",
                      task_payload("general-purpose", sid="sess-HOST4"), codex_data)
        reason4 = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
        check("Codex host + malformed models_codex: still denies, rc0 (exit unchanged)",
              rc == 0 and decision(out) == "deny", str(out))
        check("Codex host + malformed models_codex: falls back to full Claude wording, byte-identical",
              reason4 == CLAUDE_HOST_DENY_TEXT, reason4)

        os.remove(os.path.join(codex_data, "config.json"))

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
            repo_dir = os.path.join(runner_tmp, "repo")
            os.makedirs(repo_dir, exist_ok=True)
            subprocess.run(["git", "init", "-q", repo_dir], check=True, capture_output=True)

            codex_script = os.path.join(SCRIPTS, "codex_consult.sh")
            claude_script = os.path.join(SCRIPTS, "claude_consult.sh")
            CONTRACT_HEAD = "REVIEWER CONTRACT: analyze and reply only."

            def make_stub(bin_dir, name, capture_dir, model_fallback=False):
                """Stub codex/claude executable: captures argv (one arg per
                line) + full stdin per invocation into capture_dir, replies
                non-empty. --version calls are NOT captured/counted (the
                runners probe --version for their log header before the real
                call). If argv contains '-o <path>' the reply is written
                there (matches real codex's -o contract); else to stdout
                (matches claude, and codex's --resume path)."""
                fallback_block = ""
                if model_fallback:
                    fallback_block = (
                        'model=""\n'
                        'prev=""\n'
                        'for a in "$@"; do\n'
                        '  if [ "$prev" = "-m" ]; then model="$a"; fi\n'
                        '  prev="$a"\n'
                        'done\n'
                        'if [ ! -f "$CAP/_ff_done" ]; then\n'
                        '  touch "$CAP/_ff_done"\n'
                        '  echo "unknown model: ${model:-unspecified}" >&2\n'
                        '  exit 1\n'
                        'fi\n'
                    )
                script = (
                    "#!/usr/bin/env bash\n"
                    "set +u\n"
                    'if [ "${1:-}" = "--version" ]; then\n'
                    '  echo "stub-version 0.0.0"\n'
                    "  exit 0\n"
                    "fi\n"
                    f'CAP="{capture_dir}"\n'
                    'mkdir -p "$CAP"\n'
                    'idx_file="$CAP/_idx"\n'
                    'if [ -f "$idx_file" ]; then idx=$(( $(cat "$idx_file") + 1 )); else idx=1; fi\n'
                    'echo "$idx" > "$idx_file"\n'
                    'printf \'%s\\n\' "$@" > "$CAP/call_${idx}.argv"\n'
                    'cat > "$CAP/call_${idx}.stdin"\n'
                    'out=""\n'
                    'prev=""\n'
                    'for a in "$@"; do\n'
                    '  if [ "$prev" = "-o" ]; then out="$a"; fi\n'
                    '  prev="$a"\n'
                    'done\n'
                    + fallback_block +
                    'reply="STUB-REPLY-OK-${idx}"\n'
                    'if [ -n "$out" ]; then\n'
                    '  printf \'%s\\n\' "$reply" > "$out"\n'
                    'else\n'
                    '  printf \'%s\\n\' "$reply"\n'
                    'fi\n'
                    'exit 0\n'
                )
                path = os.path.join(bin_dir, name)
                with open(path, "w") as f:
                    f.write(script)
                os.chmod(path, 0o755)
                return path

            def run_script(script, args, extra_env, stdin_data=""):
                env = dict(os.environ)
                env.pop("CODEX_SANDBOX", None)
                env.pop("CLAUDE_PLUGIN_DATA", None)
                env.update(extra_env)
                p = subprocess.run(
                    ["bash", script] + args, input=stdin_data,
                    capture_output=True, text=True, timeout=20, cwd=repo_dir, env=env,
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

            # ---- contract prepend on top of captured stdin: file brief,
            # stdin brief, --resume (both runners) ----
            for label, script in (("codex", codex_script), ("claude", claude_script)):
                bin_dir = os.path.join(runner_tmp, f"bin-{label}-contract")
                os.makedirs(bin_dir, exist_ok=True)
                env = {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}

                brief_path = os.path.join(runner_tmp, f"{label}-brief-file.md")
                with open(brief_path, "w") as f:
                    f.write("Test brief body.\n")

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

                cap3 = os.path.join(runner_tmp, f"cap-{label}-resume")
                make_stub(bin_dir, label, cap3)
                rc, out, err = run_script(script, ["--resume", brief_path], env)
                calls3 = read_calls(cap3)
                check(f"{label} --resume: run succeeds", rc == 0, f"rc={rc} out={out} err={err}")
                check(f"{label} --resume: contract at top of captured stdin",
                      bool(calls3) and calls3[0][1].startswith(CONTRACT_HEAD), calls3[:1])

            # ---- codex CODEX_MODEL-unavailable fallback rerun: contract on
            # BOTH the failing initial call and the retry ----
            bin_dir = os.path.join(runner_tmp, "bin-codex-fallback")
            os.makedirs(bin_dir, exist_ok=True)
            cap = os.path.join(runner_tmp, "cap-codex-fallback")
            make_stub(bin_dir, "codex", cap, model_fallback=True)
            env = {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                   "CODEX_MODEL": "totally-fake-model"}
            brief_path = os.path.join(runner_tmp, "codex-fallback-brief.md")
            with open(brief_path, "w") as f:
                f.write("Fallback test brief.\n")
            rc, out, err = run_script(codex_script, [brief_path], env)
            calls = read_calls(cap)
            check("codex CODEX_MODEL fallback: two calls captured (fail then retry)",
                  len(calls) == 2, calls)
            check("codex CODEX_MODEL fallback: run eventually succeeds",
                  rc == 0, f"rc={rc} out={out} err={err}")
            if len(calls) == 2:
                check("codex CODEX_MODEL fallback: 1st (failing) call stdin carries contract",
                      calls[0][1].startswith(CONTRACT_HEAD), calls[0])
                check("codex CODEX_MODEL fallback: 2nd (retry) call stdin carries contract",
                      calls[1][1].startswith(CONTRACT_HEAD), calls[1])

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
                brief_path = os.path.join(runner_tmp, f"sandbox-brief-{label}.md")
                with open(brief_path, "w") as f:
                    f.write("Sandbox precedence test brief.\n")
                run_script(codex_script, [brief_path], env)
                calls = read_calls(cap)
                argv = calls[0][0] if calls else []
                sbx = None
                for i, a in enumerate(argv):
                    if a == "-s" and i + 1 < len(argv):
                        sbx = argv[i + 1]
                return sbx

            sbx = sandbox_used({"CODEX_SANDBOX": "workspace-write"}, "env-wins")
            check("sandbox precedence: env CODEX_SANDBOX wins", sbx == "workspace-write", sbx)

            cfg_dir = os.path.join(runner_tmp, "plugin-data-danger")
            os.makedirs(cfg_dir, exist_ok=True)
            with open(os.path.join(cfg_dir, "config.json"), "w") as f:
                json.dump({"codex": {"consult_sandbox": "danger-full-access"}}, f)
            sbx = sandbox_used({"CLAUDE_PLUGIN_DATA": cfg_dir}, "config-danger")
            check("sandbox precedence: config consult_sandbox used when env unset",
                  sbx == "danger-full-access", sbx)

            cfg_dir2 = os.path.join(runner_tmp, "plugin-data-invalid")
            os.makedirs(cfg_dir2, exist_ok=True)
            with open(os.path.join(cfg_dir2, "config.json"), "w") as f:
                json.dump({"codex": {"consult_sandbox": "yolo"}}, f)
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

            # empty $HOME in the derived-path branch (CLAUDE_PLUGIN_DATA unset)
            # must not crash under `set -u` and must resolve to "no config".
            sbx = sandbox_used({"HOME": ""}, "home-empty")
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
            brief_path = os.path.join(runner_tmp, "badenv-brief.md")
            with open(brief_path, "w") as f:
                f.write("Bad CODEX_SANDBOX brief.\n")
            rc, out, err = run_script(codex_script, [brief_path], env)
            combined = out + err
            check("invalid CODEX_SANDBOX env: exit 2 (loud error, not silent downgrade)",
                  rc == 2, f"rc={rc}")
            check("invalid CODEX_SANDBOX env: message names all three valid values",
                  "read-only" in combined and "workspace-write" in combined
                  and "danger-full-access" in combined, combined)
            check("invalid CODEX_SANDBOX env: codex never invoked (fails before the call)",
                  len(read_calls(cap)) == 0, read_calls(cap))

            # ---- claude runner: --disallowedTools present on normal AND resume runs ----
            disallow_bin_dir = os.path.join(runner_tmp, "bin-claude-disallow")
            os.makedirs(disallow_bin_dir, exist_ok=True)
            env = {"PATH": disallow_bin_dir + os.pathsep + os.environ.get("PATH", "")}
            brief_path = os.path.join(runner_tmp, "claude-disallow-brief.md")
            with open(brief_path, "w") as f:
                f.write("disallowedTools test brief.\n")

            cap = os.path.join(runner_tmp, "cap-claude-disallow-normal")
            make_stub(disallow_bin_dir, "claude", cap)
            rc, out, err = run_script(claude_script, [brief_path], env)
            calls = read_calls(cap)
            argv = calls[0][0] if calls else []
            check("claude normal run: --disallowedTools flag present",
                  "--disallowedTools" in argv, argv)
            check("claude normal run: Edit,Write,NotebookEdit value present",
                  "Edit,Write,NotebookEdit" in argv, argv)

            cap2 = os.path.join(runner_tmp, "cap-claude-disallow-resume")
            make_stub(disallow_bin_dir, "claude", cap2)
            rc, out, err = run_script(claude_script, ["--resume", brief_path], env)
            calls2 = read_calls(cap2)
            argv2 = calls2[0][0] if calls2 else []
            check("claude --resume run: --disallowedTools flag present",
                  "--disallowedTools" in argv2, argv2)
            check("claude --resume run: Edit,Write,NotebookEdit value present",
                  "Edit,Write,NotebookEdit" in argv2, argv2)
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
