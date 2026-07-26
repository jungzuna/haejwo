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
from session_brief import EMERGENCY_CORE, MAX_LEN  # noqa: E402

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
            "ONLY when the brief has the exact",    # 3. task-worker exact-answer litmus
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

        # diet budget — target 3000, actual 3152 at 2.9.0; consensus-kept host
        # contracts cost the overage; re-audit at next rules change
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

        with open(os.path.join(data, "config.json"), "w") as f:
            json.dump({"configured": True,
                       "gate": {"enabled": True, "max_files_per_turn": 2, "bash_guard": True}}, f)
        rc, out = run("session_brief.py", {"hook_event_name": "SessionStart"}, data)
        ctx = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("configured -> rules + config summary",
              "haejwo config" in ctx and "delegate" in ctx.lower())
        check("claude host summary has no codex-tiers leakage",
              "codex tiers" not in ctx and "models:" in ctx and "codex reviewer" in ctx)

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

        print(f"\n{PASS} passed, {len(FAIL)} failed")
        if FAIL:
            print("FAILED:", *FAIL, sep="\n  - ")
            sys.exit(1)
    finally:
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    main()
