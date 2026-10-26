"""Field round 3, bundle B (2.33.0): the fact-based re-presentation layer —
the turn checklist (turn_reset.py), post_bash.py (push-returned and
review-invoked lines), handback_nudge.py, the push-call facts in
report_check.py's reason, and the session counters behind them. Wired into
tests/test_hooks.py main(); standalone:
cd tests && python3 -c "import r3_a as m; m.run_section()"

The strings below are the byte-exact contract of the plan ledger (host
decisions, 2026-10-26); they are spelled out here, not imported, so a
change to a script's constant fails this file.
"""
import json
import os
import re
import shutil
import tempfile
import time

import test_hooks as T
from hjw_common import DEFAULT_CONFIG, load_config_with_status, state_file

CHECKLIST_T = (
    "[haejwo turn] So far: {h} hand-backs, {r} review runs, {p} push-bearing "
    "calls. Match approval to listed actions, scope, order. Decide each "
    "material finding fact vs remedy, with reasons. At acceptance link the "
    "final diff to the review and its closing statement; label worker-run and "
    "same-model results.")
HANDBACK_T = (
    "[haejwo] hand-back: {agent_type} ({model}; {n} tool uses; completed) "
    "returned. Decide each material finding fact vs remedy, with reasons; "
    "label its numbers worker-run; accept only diffs tracing to the brief or "
    "a disclosed judgment call.")
REVIEW_T = (
    "[haejwo] review runner invoked ({runner}; tool call {id}). When its reply "
    "is read: decide each material finding fact vs remedy, with reasons; carry "
    "the closing statement (scope, verification, omissions) into the report; "
    "a same-model review is not independent.")
PUSH_T = (
    "[haejwo] push-bearing Bash call returned (tool call {id}); quoted shell "
    "output tail: \"{tail}\"{interrupted}. The Outward actions line states "
    "repo; branch; action; result and the authorizing words.")
STOP_REASON = (
    "[haejwo] This turn requested git push. Finish with the record lines "
    "(presence is all this hook checks): `Outward actions: <repo; branch; "
    "action; result> - authorized by: <user words, time>` and `Final-diff "
    "review: <change id; reply; closing statement | not run because: "
    "<reason>>`.")
CMD_SHAPE = ('python3 "${{CLAUDE_PLUGIN_ROOT}}/scripts/{}" '
             '"${{CLAUDE_PLUGIN_ROOT}}" "${{CLAUDE_PLUGIN_DATA}}"')

SID = "sess-R3"


def _cfg(data, cfg):
    with open(os.path.join(data, "config.json"), "w") as f:
        f.write(cfg if isinstance(cfg, str) else json.dumps(cfg))


def _seed(data, state, sid=SID):
    path = state_file(data, sid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f)


def _state(data, sid=SID):
    try:
        with open(state_file(data, sid)) as f:
            return json.load(f)
    except Exception:
        return {}


def _obs(data, hook=None):
    path = os.path.join(data, "state", "observations.jsonl")
    if not os.path.exists(path):
        return []
    recs = [json.loads(l) for l in open(path) if l.strip()]
    return [r for r in recs if hook is None or r.get("hook") == hook]


def _prompt(pid="p1", sid=SID, **extra):
    d = {"session_id": sid, "prompt_id": pid, "hook_event_name": "UserPromptSubmit"}
    d.update(extra)
    return d


def _pre_bash(cmd, tuid, pid="p1", sid=SID):
    return {"session_id": sid, "prompt_id": pid, "hook_event_name": "PreToolUse",
            "tool_name": "Bash", "tool_use_id": tuid, "cwd": "/repo",
            "tool_input": {"command": cmd}}


def _post_bash(cmd, tuid, pid="p1", sid=SID, stdout="", stderr="", **resp):
    r = {"stdout": stdout, "stderr": stderr, "isImage": False,
         "noOutputExpected": False}
    r.update(resp)
    return {"session_id": sid, "prompt_id": pid, "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "tool_use_id": tuid,
            "tool_input": {"command": cmd}, "tool_response": r}


def _post_agent(tuid, sid=SID, status="completed", subagent_type="haejwo:default-worker",
                **resp):
    r = {"status": status, "agentId": "a1", "agentType": "haejwo:default-worker",
         "content": [{"type": "text", "text": "done"}],
         "resolvedModel": "claude-opus-5-5", "totalToolUseCount": 7}
    if status is None:
        r.pop("status")
    r.update(resp)
    ti = {"prompt": "x"}
    if subagent_type is not None:
        ti["subagent_type"] = subagent_type
    return {"session_id": sid, "prompt_id": "p1", "hook_event_name": "PostToolUse",
            "tool_name": "Agent", "tool_use_id": tuid, "tool_input": ti,
            "tool_response": r}


def _stop(sid=SID, pid="p1"):
    return {"session_id": sid, "prompt_id": pid, "hook_event_name": "Stop",
            "stop_hook_active": False, "last_assistant_message": "Done."}


def _ctx(out, event):
    hso = out.get("hookSpecificOutput") or {}
    return hso.get("additionalContext", "") if hso.get("hookEventName") == event else ""


def run_section():
    print("== field round 3 bundle B: re-presentation layer ==")
    import handback_nudge
    import post_bash
    import report_check
    import turn_reset
    from bash_guard import review_runner_invoked
    from hjw_common import sanitize_quoted

    check = T.check
    tmp = tempfile.mkdtemp(prefix="hjw-r3a-")
    n = [0]
    codex_root = os.path.join(tmp, ".codex", "plugins", "haejwo")

    def fresh(cfg=None):
        n[0] += 1
        d = os.path.join(tmp, f"d{n[0]}")
        os.makedirs(d)
        if cfg is not None:
            _cfg(d, cfg)
        return d

    def silent(rc, out):
        return rc == 0 and out == {}

    try:
        # --- the contract strings, byte-exact, at rendered values -----------
        check("R3 constants equal the ledger templates (checklist, hand-back, review, push)",
              turn_reset.CHECKLIST == CHECKLIST_T and handback_nudge.HANDBACK == HANDBACK_T
              and post_bash.REVIEW_INVOKED == REVIEW_T and post_bash.PUSH_RETURNED == PUSH_T)
        check("R3 report_check REASON is the 2.32.0 reason, unchanged",
              report_check.REASON == STOP_REASON)
        ck = turn_reset.render_checklist({"handbacks": 12, "review_runs": 3, "push_calls": 45})
        check("R3 checklist rendered, exact",
              ck == "[haejwo turn] So far: 12 hand-backs, 3 review runs, 45 push-bearing "
                    "calls. Match approval to listed actions, scope, order. Decide each "
                    "material finding fact vs remedy, with reasons. At acceptance link the "
                    "final diff to the review and its closing statement; label worker-run "
                    "and same-model results.", ck)
        ck99 = turn_reset.render_checklist({"handbacks": 99, "review_runs": 99,
                                            "push_calls": 99})
        check("R3 checklist <= 300 chars at 2-digit counts", len(ck99) <= 300, len(ck99))
        check("R3 checklist: missing/invalid counters render as 0",
              turn_reset.render_checklist({"handbacks": True, "review_runs": -1})
              == CHECKLIST_T.format(h=0, r=0, p=0))
        hb = handback_nudge.render({"subagent_type": "haejwo:default-worker"},
                                   {"resolvedModel": "claude-opus-5-5",
                                    "totalToolUseCount": 12})
        check("R3 hand-back rendered, exact",
              hb == "[haejwo] hand-back: haejwo:default-worker (claude-opus-5-5; 12 tool "
                    "uses; completed) returned. Decide each material finding fact vs remedy, "
                    "with reasons; label its numbers worker-run; accept only diffs tracing to "
                    "the brief or a disclosed judgment call.", hb)
        hb_max = handback_nudge.render({"subagent_type": "a" * 200},
                                       {"resolvedModel": "m" * 200, "totalToolUseCount": 99})
        check("R3 hand-back <= 340 chars at its maximum (60-char fields, 2-digit count)",
              len(hb_max) <= 340, len(hb_max))
        # D7: {agent_type}/{model} sanitized and cut to 60 chars (boundary)
        check("R3 hand-back: a 61-char agent type / model is cut to 60 chars",
              handback_nudge.render({"subagent_type": "a" * 61},
                                    {"resolvedModel": "m" * 61, "totalToolUseCount": 1})
              == HANDBACK_T.format(agent_type="a" * 59 + "…", model="m" * 59 + "…", n=1))
        check("R3 hand-back: a 60-char agent type / model is kept whole",
              handback_nudge.render({"subagent_type": "a" * 60},
                                    {"resolvedModel": "m" * 60, "totalToolUseCount": 1})
              == HANDBACK_T.format(agent_type="a" * 60, model="m" * 60, n=1))
        check("R3 hand-back: a control-only agent type falls back (sanitized empty)",
              handback_nudge.render({"subagent_type": "\x1b[0m\x07"},
                                    {"agentType": "w\x00x"})
              == HANDBACK_T.format(agent_type="wx", model="model unverified", n="?"))
        check("R3 hand-back fallbacks: agentType, then 'agent'; 'model unverified'; '?'",
              handback_nudge.render({}, {"agentType": "Explore"})
              == HANDBACK_T.format(agent_type="Explore", model="model unverified", n="?")
              and handback_nudge.render(None, {"totalToolUseCount": "7",
                                               "resolvedModel": 5})
              == HANDBACK_T.format(agent_type="agent", model="model unverified", n="?")
              and handback_nudge.render({}, {"totalToolUseCount": True})
              == HANDBACK_T.format(agent_type="agent", model="model unverified", n="?"))
        tid30 = "toolu_" + "X" * 24
        rv = REVIEW_T.format(runner="claude_consult.sh", id=tid30)
        check("R3 review line <= 300 chars (30-char id, longer runner name)",
              len(rv) <= 300, len(rv))
        pmax = PUSH_T.format(id=tid30, tail="t" * 159 + "…",
                             interrupted="; interrupted: true")
        check("R3 push line <= 390 chars (160-char tail, 30-char id, interrupted)",
              len(pmax) <= 390, len(pmax))
        smax = report_check.block_reason({"outward_calls": 99, "outward_ids": [
            "toolu_" + str(k) * 24 for k in range(8)]})
        check("R3 Stop reason <= 600 chars at its maximum (count 99, eight 30-char ids)",
              len(smax) <= 600 and smax.endswith("; +91 more)."), len(smax))
        # D7: the tail is the last line non-empty AFTER sanitizing
        check("R3 post_bash tail: an ANSI-only last stderr line is skipped",
              post_bash.output_tail({"stderr": "real line\n\x1b[0m\x1b[K\n"})
              == "real line", post_bash.output_tail({"stderr": "real line\n\x1b[0m\n"}))
        check("R3 post_bash tail: a control-only stderr falls back to stdout",
              post_bash.output_tail({"stderr": "\x1b[0m\x07", "stdout": "o"}) == "o")

        # --- sanitizer -------------------------------------------------------
        check("R3 sanitize: control chars and ANSI dropped",
              sanitize_quoted("a\x07b\x1b[31mc\x00d\te") == "abcde",
              repr(sanitize_quoted("a\x07b\x1b[31mc\x00d\te")))
        check("R3 sanitize: URL credentials redacted",
              sanitize_quoted("To https://user:tok@github.com/x")
              == "To https://<redacted>@github.com/x")
        cut = sanitize_quoted("z" * 161)
        check("R3 sanitize: 161 chars cut to 160 with a trailing ellipsis",
              cut == "z" * 159 + "…" and len(cut) == 160, len(cut))
        check("R3 sanitize: 160 chars kept whole", sanitize_quoted("z" * 160) == "z" * 160)

        # --- config key --------------------------------------------------------
        check("R3 DEFAULT_CONFIG gate.turn_checklist is true (pilot)",
              DEFAULT_CONFIG["gate"]["turn_checklist"] is True)
        d = fresh({"gate": {"turn_checklist": "yes"}})
        cfg_, st_ = load_config_with_status(d)
        check("R3 invalid gate.turn_checklist -> false, named, nothing else off",
              st_ == "ok" and cfg_["gate"]["turn_checklist"] is False
              and cfg_["gate"]["enabled"] is True and cfg_["gate"]["bash_guard"] is True
              and cfg_.get("_ignored") == [
                  "gate.turn_checklist must be true or false (turn checklist off)"],
              str(cfg_.get("_ignored")))

        # --- review_runner_invoked -------------------------------------------
        pos = [
            ("haejwo/scripts/codex_consult.sh --mode consult -o x b.md", "codex_consult.sh"),
            ("nohup /p/haejwo/scripts/codex_consult.sh --mode consult b.md > r.log 2>&1 &",
             "codex_consult.sh"),
            ("CODEX_EFFORT=high nohup /p/scripts/codex_consult.sh b.md", "codex_consult.sh"),
            ("timeout 600 /p/scripts/claude_consult.sh -", "claude_consult.sh"),
            ("printf 'x' | env -u CODEX_MODEL CODEX_EFFORT=low "
             "\"${CLAUDE_PLUGIN_ROOT}/scripts/codex_consult.sh\" --mode consult -",
             "codex_consult.sh"),
            ("cat > b.md <<'EOF'\nbrief\nEOF\n/p/scripts/codex_consult.sh b.md",
             "codex_consult.sh"),
        ]
        for cmd, want in pos:
            check(f"R3 runner invoked: {cmd[:50]!r}", review_runner_invoked(cmd) == want,
                  str(review_runner_invoked(cmd)))
        neg = [
            "printf '%s' codex_consult.sh",
            'echo "codex_consult.sh"',
            "/p/scripts/codex_consult.sh --help",
            "cat > b.md <<'EOF'\n/p/scripts/codex_consult.sh b.md\nEOF",
            "bash -c '/p/scripts/codex_consult.sh b.md'",  # documented miss
            "ls haejwo/scripts/",
            "grep -n consult haejwo/scripts/codex_consult.sh",
        ]
        for cmd in neg:
            check(f"R3 runner NOT invoked: {cmd[:50]!r}", review_runner_invoked(cmd) is None,
                  str(review_runner_invoked(cmd)))

        # --- bash_guard state shape -------------------------------------------
        d = fresh()
        T.run("turn_reset.py", _prompt("p1"), d)
        T.run("bash_guard.py", _pre_bash("git push origin main", "toolu_1"), d)
        st = _state(d)
        check("R3 bash_guard: push -> outward_calls 1, outward_ids [id], push_calls 1, "
              "flag kept",
              st.get("outward_attempt") is True and st.get("outward_calls") == 1
              and st.get("outward_ids") == ["toolu_1"] and st.get("push_calls") == 1, str(st))
        check("R3 bash_guard: no command text in the state",
              "git push origin main" not in json.dumps(st), str(st))
        T.run("bash_guard.py", _pre_bash("git push origin main", "toolu_1"), d)
        st = _state(d)
        check("R3 bash_guard: a repeated tool_use_id changes nothing",
              st.get("outward_calls") == 1 and st.get("outward_ids") == ["toolu_1"]
              and st.get("push_calls") == 1, str(st))
        T.run("bash_guard.py", _pre_bash("echo git push", "toolu_e"), d)
        check("R3 bash_guard: a non-push call adds no id",
              _state(d).get("outward_ids") == ["toolu_1"])
        for k in range(2, 11):
            T.run("bash_guard.py", _pre_bash("git push", f"toolu_{k}"), d)
        st = _state(d)
        check("R3 bash_guard: 10 push calls -> outward_calls 10, ids capped at 8",
              st.get("outward_calls") == 10
              and st.get("outward_ids") == [f"toolu_{k}" for k in range(1, 9)]
              and st.get("push_calls") == 10
              and st.get("outward_seen") == [f"toolu_{k}" for k in range(1, 11)], str(st))
        T.run("bash_guard.py", _pre_bash("git push", "toolu_9"), d)
        T.run("bash_guard.py", _pre_bash("git push", "toolu_10"), d)
        st = _state(d)
        check("R3 bash_guard (D2): replaying the 9th/10th id (past the display cap) "
              "changes no counter",
              st.get("outward_calls") == 10 and st.get("push_calls") == 10
              and len(st.get("outward_ids")) == 8 and len(st.get("outward_seen")) == 10,
              str(st))
        rc, out = T.run("report_check.py", _stop(), d)
        check("R3 report_check: 10 calls -> reason lists 8 ids and '+2 more'",
              out.get("reason") == STOP_REASON + " Detected push-bearing tool calls this turn: "
              "10 (" + ", ".join(f"toolu_{k}" for k in range(1, 9)) + "; +2 more).",
              out.get("reason"))
        T.run("turn_reset.py", _prompt("p2"), d)
        st = _state(d)
        check("R3 turn_reset: turn facts cleared, push_calls and outward_seen carried",
              "outward_calls" not in st and "outward_ids" not in st
              and "outward_attempt" not in st and st.get("push_calls") == 10
              and len(st.get("outward_seen", [])) == 10
              and st.get("prompt_id") == "p2", str(st))
        T.run("bash_guard.py", _pre_bash("git push", "toolu_x", pid="p3"), d)
        st = _state(d)
        check("R3 bash_guard lazy turn reset: new turn facts, session counter carried",
              st.get("prompt_id") == "p3" and st.get("outward_calls") == 1
              and st.get("outward_ids") == ["toolu_x"] and st.get("push_calls") == 11,
              str(st))
        d2 = fresh()
        T.run("bash_guard.py", {k: v for k, v in _pre_bash("git push", None).items()
                                if k != "tool_use_id"}, d2)
        st = _state(d2)
        check("R3 bash_guard: no tool_use_id -> counted, no id",
              st.get("outward_calls") == 1 and st.get("outward_ids") == []
              and st.get("outward_seen") == [], str(st))
        # outward_seen stops at 64: later ids are counted on every replay (README:
        # no dedupe, no return note)
        d2 = fresh()
        _seed(d2, {"prompt_id": "p1", "files": [], "updated_at": time.time(),
                   "outward_seen": [f"s{k}" for k in range(64)]})
        T.run("bash_guard.py", _pre_bash("git push", "toolu_65"), d2)
        T.run("bash_guard.py", _pre_bash("git push", "toolu_65"), d2)
        st = _state(d2)
        check("R3 bash_guard: outward_seen full (64) -> a later id is not deduped",
              len(st.get("outward_seen")) == 64 and "toolu_65" not in st["outward_seen"]
              and st.get("outward_calls") == 2, str(st)[:300])
        rc, out = T.run("post_bash.py", _post_bash("git push", "toolu_65", stderr="x"), d2)
        check("R3 post_bash: an id beyond the 64-id session list gets no return note",
              silent(rc, out), str(out))
        T.run("bash_guard.py", _pre_bash("git push", "s3"), d2)
        check("R3 bash_guard: an id already in outward_seen is not recounted",
              _state(d2).get("outward_calls") == 2, str(_state(d2))[:300])

        # --- counters carried across every reset (never reset by them) ------
        counters = {"handbacks": 4, "review_runs": 2, "push_calls": 3,
                    "handback_ids": ["t1"], "review_ids": ["r1"],
                    "outward_seen": ["o1"], "push_returned_ids": ["o1"],
                    "checklist_assigned": True}
        d = fresh()
        _seed(d, dict(counters, prompt_id="p1", files=["/repo/a.py"],
                      outward_calls=2, outward_ids=["x"], outward_attempt=True,
                      updated_at=time.time()))
        T.run("gate.py", T.edit_payload("/repo/src/q.py", sid=SID, pid="p2"), d)
        st = _state(d)
        check("R3 gate.py lazy reset carries the session counters and id lists",
              all(st.get(k) == v for k, v in counters.items())
              and st.get("files") == ["/repo/src/q.py"] and "outward_ids" not in st, str(st))
        from gate import STALE_TURN_SECONDS
        _seed(d, dict(counters, prompt_id="p2", files=["/repo/a.py"],
                      updated_at=time.time() - STALE_TURN_SECONDS - 60))
        T.run("gate.py", T.edit_payload("/repo/src/r.py", sid=SID, pid="p2"), d)
        st = _state(d)
        check("R3 gate.py stale reset carries the session counters",
              all(st.get(k) == v for k, v in counters.items())
              and st.get("files") == ["/repo/src/r.py"], str(st))
        T.run("turn_reset.py", _prompt("p3"), d)
        st = _state(d)
        check("R3 turn_reset carries the session counters (does not reset them)",
              all(st.get(k) == v for k, v in counters.items()), str(st))

        # --- turn checklist emission and exemptions --------------------------
        d = fresh()
        _seed(d, {"prompt_id": "p0", "files": ["/repo/a.py"], "handbacks": 3,
                  "review_runs": 1, "push_calls": 2})
        rc, out = T.run("turn_reset.py", _prompt("p1"), d)
        recs = _obs(d, "turn_checklist")
        check("R3 checklist: emitted as UserPromptSubmit context with the session counts",
              rc == 0 and _ctx(out, "UserPromptSubmit") == CHECKLIST_T.format(h=3, r=1, p=2)
              and T.no_decision_key(out), str(out))
        check("R3 checklist: the reset still happened",
              _state(d).get("files") == [] and _state(d).get("prompt_id") == "p1")
        check("R3 checklist observations (D5): assigned-on once, then note/emitted with chars",
              [(r.get("decision"), r.get("via"), r.get("chars")) for r in recs]
              == [("none", "assigned-on", None),
                  ("note", "emitted", len(CHECKLIST_T.format(h=3, r=1, p=2)))], str(recs))
        check("R3 checklist: session flag checklist_assigned set",
              _state(d).get("checklist_assigned") is True, str(_state(d)))
        rc, out = T.run("turn_reset.py", _prompt("p2"), d)
        recs = _obs(d, "turn_checklist")
        check("R3 checklist (D5): two prompts -> one assigned record + two emitted records",
              _ctx(out, "UserPromptSubmit") == CHECKLIST_T.format(h=3, r=1, p=2)
              and [r.get("via") for r in recs] == ["assigned-on", "emitted", "emitted"],
              str(recs))

        def reset_checks(label, data, env=None, root=None, payload=None,
                         off_records=0):
            _seed(data, {"prompt_id": "p0", "files": ["/repo/a.py"], "handbacks": 5})
            rc, out = T.run("turn_reset.py", payload or _prompt("p1"), data,
                            env_extra=env, root=root)
            st = _state(data)
            check(f"R3 checklist exempt ({label}): no output, reset still done",
                  silent(rc, out) and (payload is not None
                                       or (st.get("files") == [] and st.get("prompt_id") == "p1"
                                           and st.get("handbacks") == 5)),
                  str((rc, out, st)))
            recs = _obs(data, "turn_checklist")
            check(f"R3 checklist exempt ({label}): {off_records} assigned-off record(s), "
                  "no emission",
                  [r.get("via") for r in recs] == ["assigned-off"] * off_records
                  and all(r.get("decision") == "none" for r in recs), str(recs))

        reset_checks("HAEJWO_GATE=off", fresh(), env={"HAEJWO_GATE": "off"})
        reset_checks("Codex host", fresh(), root=codex_root)
        reset_checks("malformed config", fresh("{not json"))
        reset_checks("gate.enabled false", fresh({"gate": {"enabled": False}}), off_records=1)
        reset_checks("turn_checklist false", fresh({"gate": {"turn_checklist": False}}),
                     off_records=1)
        dinv = fresh({"gate": {"turn_checklist": "on"}})
        reset_checks("invalid turn_checklist", dinv, off_records=1)
        T.run("turn_reset.py", _prompt("p2"), dinv)
        T.run("turn_reset.py", _prompt("p3"), dinv)
        recs = _obs(dinv, "turn_checklist")
        check("R3 checklist off (D5): one assigned-off record across turns, no emissions",
              [r.get("via") for r in recs] == ["assigned-off"]
              and _state(dinv).get("checklist_assigned") is True
              and _state(dinv).get("prompt_id") == "p3", str(recs))
        # an unsaved assignment flag writes no record (it would repeat)
        dpf = fresh()
        os.makedirs(state_file(dpf, SID))  # the state path is a directory: save fails
        rc, out = T.run("turn_reset.py", _prompt("p1"), dpf)
        check("R3 checklist: reset save failed -> line still emitted, no assigned record",
              rc == 0 and _ctx(out, "UserPromptSubmit") == CHECKLIST_T.format(h=0, r=0, p=0)
              and [r.get("via") for r in _obs(dpf, "turn_checklist")] == ["emitted"],
              str((out, _obs(dpf))))
        dsub = fresh()
        _seed(dsub, {"prompt_id": "p0", "files": ["/repo/a.py"]})
        rc, out = T.run("turn_reset.py", _prompt("p1", agent_id="a1", agent_type="w"), dsub)
        check("R3 checklist exempt (subagent): no output, state untouched (as before)",
              silent(rc, out) and _state(dsub).get("prompt_id") == "p0", str(out))
        check("R3 checklist exempt (subagent): no record", not _obs(dsub))

        # --- post_bash: push-returned ----------------------------------------
        d = fresh()
        T.run("turn_reset.py", _prompt("p1"), d)
        T.run("bash_guard.py", _pre_bash("git push origin main", "toolu_P"), d)
        push_cmd = "git push origin main"
        rc, out = T.run("post_bash.py", _post_bash(
            push_cmd, "toolu_P", stdout="ignored\n",
            stderr="To https://u:tok@github.com/x/y.git\n   abc..def  main -> main\n\n",
            interrupted=False), d)
        want = PUSH_T.format(id="toolu_P", tail="abc..def  main -> main", interrupted="")
        check("R3 post_bash: recorded id -> push line, stderr's last non-empty line",
              rc == 0 and _ctx(out, "PostToolUse") == want and T.no_decision_key(out),
              str(out))
        recs = _obs(d, "post_bash")
        check("R3 post_bash observation: note/push-returned with chars",
              len(recs) == 1 and recs[0].get("decision") == "note"
              and recs[0].get("via") == "push-returned" and recs[0].get("chars") == len(want),
              str(recs))
        rc, out = T.run("post_bash.py", _post_bash(
            push_cmd, "toolu_P", stderr="To https://u:tok@github.com/x/y.git\n"), d)
        check("R3 post_bash (D3): the same tool_use_id again -> no second line",
              silent(rc, out) and len(_obs(d, "post_bash")) == 1
              and _state(d).get("push_returned_ids") == ["toolu_P"], str(out))

        def pushed(tuid, **kw):
            T.run("bash_guard.py", _pre_bash(push_cmd, tuid), d)
            return T.run("post_bash.py", _post_bash(push_cmd, tuid, **kw), d)

        rc, out = pushed("toolu_P2", stdout="out line\n", stderr=" \n", interrupted=True)
        check("R3 post_bash: empty stderr -> stdout tail; interrupted true said",
              _ctx(out, "PostToolUse") == PUSH_T.format(
                  id="toolu_P2", tail="out line", interrupted="; interrupted: true"), str(out))
        rc, out = pushed("toolu_P3")
        check("R3 post_bash: no output -> \"(no output)\"; interrupted missing -> not said",
              _ctx(out, "PostToolUse") == PUSH_T.format(
                  id="toolu_P3", tail="(no output)", interrupted=""), str(out))
        rc, out = pushed("toolu_P4", stderr="remote: https://me:s3cr3t@host/r \x1b[0m\x07ok")
        check("R3 post_bash: tail sanitized (credentials, control chars)",
              _ctx(out, "PostToolUse") == PUSH_T.format(
                  id="toolu_P4", tail="remote: https://<redacted>@host/r ok", interrupted=""),
              str(out))
        rc, out = pushed("toolu_P5", stderr="e" * 300)
        check("R3 post_bash: a long tail is cut to 160 chars with an ellipsis",
              _ctx(out, "PostToolUse") == PUSH_T.format(
                  id="toolu_P5", tail="e" * 159 + "…", interrupted=""), str(out))
        rc, out = pushed("toolu_P6", stderr="To host\n   a..b  main -> main\n\x1b[0m\x1b[K\n")
        check("R3 post_bash (D7): an ANSI-only last stderr line is skipped",
              _ctx(out, "PostToolUse") == PUSH_T.format(
                  id="toolu_P6", tail="a..b  main -> main", interrupted=""), str(out))
        for k in range(9, 12):  # D3: calls past the 8-id display list still get their note
            T.run("bash_guard.py", _pre_bash(push_cmd, f"toolu_Q{k}"), d)
        rc, out = T.run("post_bash.py", _post_bash(push_cmd, "toolu_Q11", stderr="q"), d)
        check("R3 post_bash (D3): the 11th push-bearing call of a turn gets its note",
              len(_state(d).get("outward_ids")) == 8
              and _ctx(out, "PostToolUse") == PUSH_T.format(id="toolu_Q11", tail="q",
                                                            interrupted=""), str(out))
        rc, out = T.run("post_bash.py", _post_bash(push_cmd, "toolu_OTHER", stderr="x"), d)
        check("R3 post_bash: an id the guard did not record -> no line", silent(rc, out),
              str(out))
        T.run("bash_guard.py", _pre_bash(push_cmd, "toolu_P7"), d)
        rc, out = T.run("post_bash.py", _post_bash(push_cmd, "toolu_P7", pid="p9",
                                                   stderr="x"), d)
        check("R3 post_bash: the state's prompt_id differs -> no line, no claim",
              silent(rc, out) and "toolu_P7" not in _state(d).get("push_returned_ids", []),
              str(out))

        # --- post_bash: review-invoked ----------------------------------------
        rcmd = "nohup /p/haejwo/scripts/codex_consult.sh --mode consult b.md > r.log 2>&1 &"
        rc, out = T.run("post_bash.py", _post_bash(rcmd, "toolu_R"), d)
        check("R3 post_bash: runner invocation -> review line, exact",
              _ctx(out, "PostToolUse") == REVIEW_T.format(runner="codex_consult.sh",
                                                          id="toolu_R"), str(out))
        check("R3 post_bash: review_runs bumped, id recorded",
              _state(d).get("review_runs") == 1 and _state(d).get("review_ids") == ["toolu_R"],
              str(_state(d)))
        rc, out = T.run("post_bash.py", _post_bash(rcmd, "toolu_R"), d)
        check("R3 post_bash: the same tool_use_id again -> no line, no bump",
              silent(rc, out) and _state(d).get("review_runs") == 1, str(out))
        dw = fresh()
        for k in range(65):
            T.run("post_bash.py", _post_bash(rcmd, f"toolu_rw{k}"), dw)
        rc, out = T.run("post_bash.py", _post_bash(rcmd, "toolu_rw0"), dw)
        check("R3 review dedupe: rolling window of 64 (evicted id replays)",
              _ctx(out, "PostToolUse") == REVIEW_T.format(runner="codex_consult.sh",
                                                          id="toolu_rw0")
              and _state(dw).get("review_runs") == 66, str(_state(dw).get("review_runs")))
        recs = [r for r in _obs(d, "post_bash") if r.get("via") == "review-invoked"]
        check("R3 post_bash observation: one note/review-invoked with chars",
              len(recs) == 1 and recs[0].get("chars") == len(REVIEW_T.format(
                  runner="codex_consult.sh", id="toolu_R")), str(recs))
        both = "/p/scripts/claude_consult.sh - < b.md && git push"
        T.run("bash_guard.py", _pre_bash(both, "toolu_B"), d)
        rc, out = T.run("post_bash.py", _post_bash(both, "toolu_B", stderr="done"), d)
        check("R3 post_bash: both on one call -> push line then review line",
              _ctx(out, "PostToolUse") == PUSH_T.format(id="toolu_B", tail="done",
                                                        interrupted="")
              + "\n" + REVIEW_T.format(runner="claude_consult.sh", id="toolu_B"), str(out))
        rc, out = T.run("post_bash.py", _post_bash("ls -la", "toolu_L", stdout="x"), d)
        check("R3 post_bash: an ordinary call -> no output", silent(rc, out), str(out))

        # --- post_bash exemptions ---------------------------------------------
        def post_exempt(label, data, env=None, root=None, payload=None):
            _seed(data, {"prompt_id": "p1", "files": [], "outward_ids": ["toolu_P"],
                         "outward_calls": 1, "outward_attempt": True})
            p = payload if payload is not None else _post_bash(rcmd + " ; git push", "toolu_P",
                                                                stderr="x")
            rc, out = T.run("post_bash.py", p, data, env_extra=env, root=root)
            check(f"R3 post_bash exempt ({label}): no output, no record, no bump, no claim",
                  silent(rc, out) and not _obs(data, "post_bash")
                  and "review_runs" not in _state(data)
                  and "push_returned_ids" not in _state(data), str(out))

        sub = _post_bash(rcmd, "toolu_P", stderr="x")
        sub.update(agent_id="a1", agent_type="w")
        post_exempt("subagent", fresh(), payload=sub)
        post_exempt("HAEJWO_GATE=off", fresh(), env={"HAEJWO_GATE": "off"})
        post_exempt("Codex host", fresh(), root=codex_root)
        post_exempt("malformed config", fresh("{not json"))
        post_exempt("gate.enabled false", fresh({"gate": {"enabled": False}}))
        post_exempt("gate.bash_guard false", fresh({"gate": {"bash_guard": False}}))
        post_exempt("invalid gate.bash_guard", fresh({"gate": {"bash_guard": 1}}))
        post_exempt("malformed payload", fresh(), payload="{not json")

        # --- handback_nudge ---------------------------------------------------
        d = fresh()
        rc, out = T.run("handback_nudge.py", _post_agent("toolu_H1"), d)
        want = HANDBACK_T.format(agent_type="haejwo:default-worker", model="claude-opus-5-5",
                                 n=7)
        check("R3 hand-back: completed -> line, exact, PostToolUse context",
              rc == 0 and _ctx(out, "PostToolUse") == want and T.no_decision_key(out),
              str(out))
        recs = _obs(d, "handback_nudge")
        check("R3 hand-back observation: note/completed with chars",
              len(recs) == 1 and recs[0].get("decision") == "note"
              and recs[0].get("via") == "completed" and recs[0].get("chars") == len(want),
              str(recs))
        rc, out = T.run("handback_nudge.py", _post_agent("toolu_H1"), d)
        check("R3 hand-back: the same tool_use_id again -> no line, no bump",
              silent(rc, out) and _state(d).get("handbacks") == 1
              and _state(d).get("handback_ids") == ["toolu_H1"], str(_state(d)))
        rc, out = T.run("handback_nudge.py", _post_agent(
            "toolu_H2", subagent_type=None, resolvedModel=None, totalToolUseCount=None), d)
        check("R3 hand-back: agentType fallback, model unverified, '?' count",
              _ctx(out, "PostToolUse") == HANDBACK_T.format(
                  agent_type="haejwo:default-worker", model="model unverified", n="?"),
              str(out))
        for label, p in (("status async_launched", _post_agent("toolu_H3",
                                                               status="async_launched")),
                         ("status missing", _post_agent("toolu_H4", status=None)),
                         ("tool_response not an object",
                          dict(_post_agent("toolu_H5"), tool_response="done"))):
            rc, out = T.run("handback_nudge.py", p, d)
            check(f"R3 hand-back: {label} -> no output", silent(rc, out), str(out))
        check("R3 hand-back: non-completed calls add no record and no count",
              len(_obs(d, "handback_nudge")) == 2 and _state(d).get("handbacks") == 2,
              str(_state(d)))
        for k in range(70):
            T.run("handback_nudge.py", _post_agent(f"toolu_c{k}"), d)
        st = _state(d)
        check("R3 hand-back: handback_ids capped at 64 (newest kept), count 72",
              len(st.get("handback_ids", [])) == 64 and st.get("handbacks") == 72
              and st["handback_ids"][-1] == "toolu_c69", str(len(st.get("handback_ids", []))))
        dw = fresh()
        for k in range(65):
            T.run("handback_nudge.py", _post_agent(f"toolu_w{k}"), dw)
        rc, out = T.run("handback_nudge.py", _post_agent("toolu_w0"), dw)
        check("R3 hand-back dedupe: rolling window of 64 (evicted id replays)",
              bool(_ctx(out, "PostToolUse")) and _state(dw).get("handbacks") == 66,
              str(_state(dw).get("handbacks")))
        T.run("turn_reset.py", _prompt("p2"), d)
        st = _state(d)
        check("R3 turn_reset keeps handbacks and the capped handback_ids",
              st.get("handbacks") == 72 and len(st.get("handback_ids", [])) == 64, str(st)[:200])

        def hb_exempt(label, data, env=None, root=None, payload=None):
            rc, out = T.run("handback_nudge.py",
                            _post_agent("toolu_E") if payload is None else payload, data,
                            env_extra=env, root=root)
            check(f"R3 hand-back exempt ({label}): no output, no record",
                  silent(rc, out) and not _obs(data, "handback_nudge")
                  and "handbacks" not in _state(data), str(out))

        hsub = _post_agent("toolu_E")
        hsub.update(agent_id="a1", agent_type="w")
        hb_exempt("subagent", fresh(), payload=hsub)
        hb_exempt("HAEJWO_GATE=off", fresh(), env={"HAEJWO_GATE": "off"})
        hb_exempt("Codex host", fresh(), root=codex_root)
        hb_exempt("malformed config", fresh("{not json"))
        hb_exempt("gate.enabled false", fresh({"gate": {"enabled": False}}))
        hb_exempt("gate.delegation_guard false", fresh({"gate": {"delegation_guard": False}}))
        hb_exempt("malformed payload", fresh(), payload="")

        # --- report_check reason with the facts ------------------------------
        for count, ids in ((1, ["toolu_a"]), (8, [f"toolu_{k}" for k in range(8)]),
                           (9, [f"toolu_{k}" for k in range(8)])):
            d = fresh()
            _seed(d, {"prompt_id": "p1", "files": [], "outward_attempt": True,
                      "outward_calls": count, "outward_ids": ids})
            rc, out = T.run("report_check.py", _stop(), d)
            more = "; +1 more" if count == 9 else ""
            check(f"R3 report_check reason with {count} recorded call(s), exact",
                  out.get("decision") == "block" and out.get("reason") == STOP_REASON
                  + f" Detected push-bearing tool calls this turn: {count} "
                    f"({', '.join(ids)}{more}).", out.get("reason"))
        d = fresh()
        _seed(d, {"prompt_id": "p1", "files": [], "outward_attempt": True})
        rc, out = T.run("report_check.py", _stop(), d)
        check("R3 report_check: a flag-only (2.32.0) state keeps the plain reason",
              out.get("reason") == STOP_REASON, out.get("reason"))
        d = fresh()
        _seed(d, {"prompt_id": "p1", "files": [], "outward_attempt": True,
                  "outward_calls": 2, "outward_ids": []})
        rc, out = T.run("report_check.py", _stop(), d)
        check("R3 report_check: a count without ids says so",
              out.get("reason") == STOP_REASON
              + " Detected push-bearing tool calls this turn: 2 (ids not recorded).",
              out.get("reason"))

        # --- integrated flow: heredoc commit + push (S2) -> post_bash -> Stop -
        d = fresh()
        s2 = ("git add -A && git commit -q -F - <<'EOF'\nfix: x\n\ngit push is not run here\n"
              "EOF\ngit push origin HEAD")
        T.run("turn_reset.py", _prompt("p1"), d)
        rc, out = T.run("bash_guard.py", _pre_bash(s2, "toolu_S2"), d)
        pre_ok = "outward action requested" in T.ctx_of(out)
        rc, out = T.run("post_bash.py", _post_bash(
            s2, "toolu_S2", stderr="To github.com:o/r.git\n   1..2  HEAD -> main"), d)
        post_ok = _ctx(out, "PostToolUse") == PUSH_T.format(
            id="toolu_S2", tail="1..2  HEAD -> main", interrupted="")
        rc, out = T.run("report_check.py", _stop(), d)
        check("R3 flow: bash_guard(S2 push) -> post_bash(same id) -> report_check "
              "(reason carries the id)",
              pre_ok and post_ok and out.get("reason") == STOP_REASON
              + " Detected push-bearing tool calls this turn: 1 (toolu_S2).",
              str((pre_ok, post_ok, out)))
        rc, out = T.run("turn_reset.py", _prompt("p2"), d)
        check("R3 flow: the next checklist counts the push",
              _ctx(out, "UserPromptSubmit") == CHECKLIST_T.format(h=0, r=0, p=1), str(out))

        # --- D6: the Stop block observation carries the reason's length ------
        d = fresh()
        _seed(d, {"prompt_id": "p1", "files": [], "outward_attempt": True,
                  "outward_calls": 1, "outward_ids": ["toolu_a"]})
        rc, out = T.run("report_check.py", _stop(), d)
        recs = _obs(d, "report_check")
        check("R3 report_check observation (D6): block with chars = len(reason)",
              len(recs) == 1 and recs[0].get("decision") == "block"
              and recs[0].get("chars") == len(out.get("reason", "")) > 0, str(recs))

        # --- D1: counter saves never move turn freshness ---------------------
        from gate import STALE_TURN_SECONDS as STALE
        rcmd2 = "/p/scripts/codex_consult.sh b.md; git push"

        def stale_seed(data):
            old = time.time() - STALE - 60
            _seed(data, {"prompt_id": "p1", "files": ["/repo/a.py", "/repo/b.py"],
                         "updated_at": old, "outward_seen": ["toolu_DP"]})
            return old

        dctl = fresh()
        stale_seed(dctl)
        rc, out = T.run("gate.py", T.edit_payload("/repo/c.py", sid=SID, pid="p1"), dctl)
        check("R3 D1 control: a stale state with 2 files allows the next edit (stale reset)",
              rc == 0 and T.decision(out) != "deny", str(out))
        dhb = fresh()
        old = stale_seed(dhb)
        rc, out = T.run("handback_nudge.py", _post_agent("toolu_DH"), dhb)
        check("R3 D1: hand-back bump on a stale state emits and keeps updated_at",
              _ctx(out, "PostToolUse") != "" and _state(dhb).get("updated_at") == old
              and _state(dhb).get("handbacks") == 1, str(_state(dhb)))
        rc, out = T.run("gate.py", T.edit_payload("/repo/c.py", sid=SID, pid="p1"), dhb)
        check("R3 D1: after the hand-back bump the next edit is still ALLOWED",
              rc == 0 and T.decision(out) != "deny", str(out))
        dpb = fresh()
        old = stale_seed(dpb)
        rc, out = T.run("post_bash.py", _post_bash(rcmd2, "toolu_DP", stderr="x"), dpb)
        st = _state(dpb)
        check("R3 D1: push claim + review bump on a stale state emit both and keep updated_at",
              _ctx(out, "PostToolUse").count("[haejwo]") == 2 and st.get("updated_at") == old
              and st.get("push_returned_ids") == ["toolu_DP"] and st.get("review_runs") == 1,
              str((out, st)))
        rc, out = T.run("gate.py", T.edit_payload("/repo/c.py", sid=SID, pid="p1"), dpb)
        check("R3 D1: after the post_bash saves the next edit is still ALLOWED",
              rc == 0 and T.decision(out) != "deny", str(out))

        # --- D4: a claim that cannot be persisted -> silent ------------------
        import fcntl
        dlk = fresh()
        _seed(dlk, {"prompt_id": "p1", "files": [], "updated_at": time.time(),
                    "outward_seen": ["toolu_LP"]})
        before = open(state_file(dlk, SID), "rb").read()
        holder = open(state_file(dlk, SID) + ".lock", "w")
        try:
            fcntl.flock(holder, fcntl.LOCK_EX)
            rc1, out1 = T.run("handback_nudge.py", _post_agent("toolu_LH"), dlk)
            rc2, out2 = T.run("post_bash.py", _post_bash(rcmd2, "toolu_LP", stderr="x"), dlk)
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()
        check("R3 D4 lock unavailable: hand-back and post_bash silent, exit 0, state untouched",
              silent(rc1, out1) and silent(rc2, out2)
              and open(state_file(dlk, SID), "rb").read() == before
              and not _obs(dlk, "post_bash") and not _obs(dlk, "handback_nudge"),
              str((rc1, out1, rc2, out2)))
        dsv = fresh()
        _seed(dsv, {"prompt_id": "p1", "files": [], "updated_at": time.time(),
                    "outward_seen": ["toolu_SP"]})
        os.makedirs(state_file(dsv, SID) + ".tmp")  # the save's temp file: EISDIR
        rc1, out1 = T.run("handback_nudge.py", _post_agent("toolu_SH"), dsv)
        rc2, out2 = T.run("post_bash.py", _post_bash(rcmd2, "toolu_SP", stderr="x"), dsv)
        rc3, out3 = T.run("handback_nudge.py", _post_agent("toolu_SH"), dsv)
        check("R3 D4 save fails (EISDIR): no note, exit 0 — also on the repeat",
              silent(rc1, out1) and silent(rc2, out2) and silent(rc3, out3)
              and not _obs(dsv, "post_bash") and not _obs(dsv, "handback_nudge"),
              str((rc1, out1, rc2, out2, rc3, out3)))

        import contextlib
        import errno as _errno
        import io
        import sys as _sys
        import hjw_common as H
        dex = fresh()
        _seed(dex, {"prompt_id": "p1", "files": [], "updated_at": time.time(),
                    "outward_seen": ["toolu_XP"]})

        def boom(*a, **k):
            raise RuntimeError("injected")

        def eacces(*a, **k):
            return _errno.errorcode[_errno.EACCES]

        saved = (H.load_state, H.save_state, post_bash.load_state, post_bash.save_state)
        try:
            H.load_state = boom
            r_exc = H.bump_session_counter(dex, SID, "handbacks", "t1", "handback_ids")
            H.load_state, H.save_state = saved[0], eacces
            r_acc = H.bump_session_counter(dex, SID, "handbacks", "t2", "handback_ids")
            H.save_state = saved[1]
            post_bash.save_state = eacces
            c_acc = post_bash.claim_push_return({"prompt_id": "p1"}, dex, SID, "toolu_XP")
            post_bash.save_state, post_bash.load_state = saved[3], boom
            c_exc = post_bash.claim_push_return({"prompt_id": "p1"}, dex, SID, "toolu_XP")
        finally:
            H.load_state, H.save_state, post_bash.load_state, post_bash.save_state = saved
        check("R3 D4 in-process: exception / EACCES save -> bump and push claim return False",
              (r_exc, r_acc, c_acc, c_exc) == (False, False, False, False),
              str((r_exc, r_acc, c_acc, c_exc)))

        def run_main(mod, payload):
            buf = io.StringIO()
            argv, stdin, mask = _sys.argv, _sys.stdin, os.umask(0o022)
            _sys.argv, _sys.stdin = ["hook", T.PLUGIN, dex], io.StringIO(json.dumps(payload))
            code = "returned"
            try:
                with contextlib.redirect_stdout(buf):
                    mod.main()  # sets umask 077: restored below
            except SystemExit as e:
                code = e.code
            finally:
                _sys.argv, _sys.stdin = argv, stdin
                os.umask(mask)
            return code, buf.getvalue()

        try:
            H.load_state = boom
            post_bash.load_state = boom
            m1 = run_main(handback_nudge, _post_agent("toolu_XH"))
            m2 = run_main(post_bash, _post_bash(rcmd2, "toolu_XP", stderr="x"))
        finally:
            H.load_state, H.save_state, post_bash.load_state, post_bash.save_state = saved
        check("R3 D4 exception inside the claim: both hooks exit 0 with no output",
              m1 == (0, "") and m2 == (0, ""), str((m1, m2)))
        check("R3 D4: nothing persisted or observed by the failed claims",
              "handbacks" not in _state(dex) and "push_returned_ids" not in _state(dex)
              and not _obs(dex, "post_bash") and not _obs(dex, "handback_nudge"),
              str(_state(dex)))

        # --- hooks.json and source canaries ---------------------------------
        hooks = json.load(open(os.path.join(T.PLUGIN, "hooks", "hooks.json"),
                               encoding="utf-8"))
        post = hooks.get("hooks", {}).get("PostToolUse") or []
        shape = [(e.get("matcher"), h.get("type"), h.get("command"), h.get("timeout"),
                  h.get("statusMessage")) for e in post for h in e.get("hooks", [])]
        check("R3 hooks.json PostToolUse: Bash -> post_bash.py, Task|Agent -> "
              "handback_nudge.py, the shared command shape",
              shape == [("Bash", "command", CMD_SHAPE.format("post_bash.py"), 10,
                         "haejwo post-bash"),
                        ("^(Task|Agent)$", "command", CMD_SHAPE.format("handback_nudge.py"),
                         10, "haejwo hand-back")], str(shape))
        for fn in ("post_bash.py", "handback_nudge.py", "turn_reset.py"):
            src = open(os.path.join(T.SCRIPTS, fn), encoding="utf-8").read()
            check(f"R3 canary: {fn} never emits a permission decision or a block",
                  not re.search(r"""["'](permissionDecision|decision["']:\s*["']block|"""
                                r"""approve)["']""", src), fn)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
