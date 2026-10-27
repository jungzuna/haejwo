"""Field round 3, bundle B (2.33.0) as stabilized in 2.34.0: the
re-presentation layer that stays — post_bash.py (push-returned line and the
detected-runner-call note), the push-call facts in report_check.py's reason,
the session id lists behind them — and the 2.34.0 removals (the turn
checklist, handback_nudge.py and the session counters; debate 6,
2026-10-27), the gate `via` split, the retired observation schemas. Wired
into tests/test_hooks.py main(); standalone:
cd tests && python3 -c "import r3_a as m; m.run_section()"

The strings below are the byte-exact contract of the plan ledgers (host
decisions, 2026-10-26 and 2026-10-27); they are spelled out here, not imported, so a
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

RUNNER_T = (
    "[haejwo] detected runner call ({runner}; tool call {id}): command syntax "
    "only — whether it ran, finished or reviewed anything is not established. "
    "When its reply is read: decide each material finding fact vs remedy, with "
    "reasons; carry the closing statement (scope, verification, omissions) into "
    "the report; a same-model review is not independent.")
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


def _stop(sid=SID, pid="p1"):
    return {"session_id": sid, "prompt_id": pid, "hook_event_name": "Stop",
            "stop_hook_active": False, "last_assistant_message": "Done."}


def _ctx(out, event):
    hso = out.get("hookSpecificOutput") or {}
    return hso.get("additionalContext", "") if hso.get("hookEventName") == event else ""


def run_section():
    print("== field round 3 bundle B: re-presentation layer ==")
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
        check("R3 constants equal the ledger templates (runner note, push)",
              post_bash.RUNNER_NOTE == RUNNER_T and post_bash.PUSH_RETURNED == PUSH_T)
        check("2.34 turn_reset has no checklist; post_bash has no REVIEW_INVOKED",
              not hasattr(turn_reset, "CHECKLIST")
              and not hasattr(turn_reset, "render_checklist")
              and not hasattr(post_bash, "REVIEW_INVOKED"))
        check("R3 report_check REASON is the 2.32.0 reason, unchanged",
              report_check.REASON == STOP_REASON)
        tid30 = "toolu_" + "X" * 24
        rv = RUNNER_T.format(runner="claude_consult.sh", id=tid30)
        check("2.34 runner note <= 400 chars (30-char id, claude_consult.sh)",
              len(rv) <= 400, len(rv))
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
        check("2.34 DEFAULT_CONFIG has no gate.turn_checklist (pilot removed)",
              "turn_checklist" not in DEFAULT_CONFIG["gate"])
        for val in (True, False, "garbage"):
            d = fresh({"gate": {"turn_checklist": val}})
            raw = open(os.path.join(d, "config.json"), "rb").read()
            cfg_, st_ = load_config_with_status(d)
            gate_ = {k: v for k, v in cfg_["gate"].items() if k != "turn_checklist"}
            check(f"2.34 obsolete gate.turn_checklist={val!r}: no _ignored note, "
                  "every gate at its default",
                  st_ == "ok" and "_ignored" not in cfg_
                  and gate_ == DEFAULT_CONFIG["gate"], str((st_, cfg_.get("_ignored"), gate_)))
            rc, out = T.run("turn_reset.py", _prompt("p1"), d)
            rc2, out2 = T.run("gate.py", T.edit_payload("/repo/src/a.py", sid=SID, pid="p1"), d)
            check(f"2.34 obsolete gate.turn_checklist={val!r}: no context, edit allowed, "
                  "config.json not rewritten",
                  silent(rc, out) and rc2 == 0 and T.decision(out2) != "deny"
                  and "config" not in json.dumps(out2)
                  and open(os.path.join(d, "config.json"), "rb").read() == raw,
                  str((out, out2)))

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
            # 2.34.0: the probe marker counts only BEFORE the command word of
            # the runner's own segment
            ("/p/scripts/codex_consult.sh HJW_PROBE=1 b.md", "codex_consult.sh"),
            ("HJW_PROBE=1 true; /p/scripts/claude_consult.sh -", "claude_consult.sh"),
            ("echo HJW_PROBE=1 | /p/scripts/codex_consult.sh -", "codex_consult.sh"),
            ("printf 'reply HAEJWO-OK' | /p/scripts/codex_consult.sh -", "codex_consult.sh"),
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
            # 2.34.0 setup probes (commands/setup.md): bare prefix and env forms
            "HJW_PROBE=1 /p/scripts/codex_consult.sh b.md",
            "printf 'x' | HJW_PROBE=1 \"${CLAUDE_PLUGIN_ROOT}/scripts/claude_consult.sh\" -",
            "env HJW_PROBE=1 /p/scripts/codex_consult.sh -o r.md b.md",
            "env -u CODEX_MODEL HJW_PROBE=setup CODEX_EFFORT=low nohup "
            "/p/scripts/claude_consult.sh b.md",
            "HJW_PROBE= timeout 60 /p/scripts/codex_consult.sh b.md",
        ]
        for cmd in neg:
            check(f"R3 runner NOT invoked: {cmd[:50]!r}", review_runner_invoked(cmd) is None,
                  str(review_runner_invoked(cmd)))
        # 2.34.0 diff review (BLOCKER): the marker counts by syntactic ROLE —
        # a bare prefix assignment or an env assignment, never an option
        # operand, never a word after the command word, never timeout's
        # command word (`timeout 90 HJW_PROBE=1 x` execs "HJW_PROBE=1").
        R_ = "/p/codex_consult.sh b.md"
        role_cases = (
            ("env -C HJW_PROBE=1 " + R_, "codex_consult.sh"),          # -C operand
            ("env --chdir=HJW_PROBE=1 " + R_, "codex_consult.sh"),
            ("env -S 'HJW_PROBE=1' " + R_, "codex_consult.sh"),         # -S operand
            ("env -u HJW_PROBE=1 " + R_, "codex_consult.sh"),           # -u operand
            ("timeout 90 HJW_PROBE=1 " + R_, "codex_consult.sh"),       # timeout's command
            ("nohup HJW_PROBE=1 " + R_, "codex_consult.sh"),            # nohup's command
            ("/p/codex_consult.sh HJW_PROBE=1 b.md", "codex_consult.sh"),  # after the command
            ("env -u X HJW_PROBE=1 " + R_, None),
            ("env -uX HJW_PROBE=1 " + R_, None),
            ("env -- HJW_PROBE=1 " + R_, None),
            ("HJW_PROBE=1 env -u X " + R_, None),
            ("nohup env HJW_PROBE=1 " + R_, None),
            ("time HJW_PROBE=1 " + R_, None),
            # the two live probes of commands/setup.md (Claude host, Codex host)
            ("printf 'MODE: consult\\nReply with exactly: HAEJWO-OK\\n' | env -u CODEX_MODEL "
             "HJW_PROBE=1 CODEX_EFFORT=low CODEX_TIMEOUT=90 "
             "\"${CLAUDE_PLUGIN_ROOT}/scripts/codex_consult.sh\" --mode consult -", None),
            ("env -u CLAUDE_MODEL HJW_PROBE=1 HJW_CLAUDE_EFFORT=low "
             "\"${CLAUDE_PLUGIN_ROOT}/scripts/claude_consult.sh\" --mode consult -", None),
        )
        for cmd, want in role_cases:
            got = review_runner_invoked(cmd)
            check(f"2.34 probe marker by role: {cmd[:60]!r} -> "
                  f"{'probe (None)' if want is None else want}", got == want, str(got))
        # re-review: GNU getopt_long abbreviations and unknown options. Checked on
        # _probe_marked directly (end = the runner word), since the shared push
        # grammar does not resolve `--un X` and would miss the runner anyway.
        import shlex as _shlex
        from bash_guard import _probe_marked
        long_cases = (
            ("env --chd HJW_PROBE=1 " + R_, False),        # --chd = --chdir: operand
            ("env --chd=HJW_PROBE=1 " + R_, False),
            ("env --c HJW_PROBE=1 " + R_, False),          # unique prefix of --chdir
            ("env --spl HJW_PROBE=1 " + R_, False),        # --split-string
            ("env --arg HJW_PROBE=1 " + R_, False),        # --argv0
            ("env --un X HJW_PROBE=1 " + R_, True),        # --unset X, then the marker
            ("env --unset=X HJW_PROBE=1 " + R_, True),
            ("env --nosuch HJW_PROBE=1 " + R_, False),     # unknown: conservative
            ("env --de HJW_PROBE=1 " + R_, False),         # ambiguous: conservative
            ("env -x HJW_PROBE=1 " + R_, False),           # unknown letter: conservative
            ("env --null HJW_PROBE=1 " + R_, True),
            ("env --nu HJW_PROBE=1 " + R_, True),          # unique prefix of --null
            ("env --block-signal HJW_PROBE=1 " + R_, True),  # optional =SIG only
            ("env -i HJW_PROBE=1 " + R_, True),
            ("env -0v HJW_PROBE=1 " + R_, True),
            ("env - HJW_PROBE=1 " + R_, True),
        )
        for cmd, want in long_cases:
            ws = _shlex.split(cmd)
            got = _probe_marked(ws, ws.index("/p/codex_consult.sh"))
            check(f"2.34 probe marker, env option walk: {cmd[:60]!r} -> "
                  f"{'probe' if want else 'not a probe'}", got is want, str(got))
        for cmd, want in (("env --chd HJW_PROBE=1 " + R_, "codex_consult.sh"),
                          ("env --chd=HJW_PROBE=1 " + R_, "codex_consult.sh"),
                          ("env --nosuch HJW_PROBE=1 " + R_, "codex_consult.sh"),
                          ("env --unset=X HJW_PROBE=1 " + R_, None),
                          ("env --null HJW_PROBE=1 " + R_, None),
                          ("env -i HJW_PROBE=1 " + R_, None),
                          # re-review 2: split-string in any spelling ends the
                          # marker search for the segment (conservative)
                          ("env --spl=-C HJW_PROBE=1 " + R_, "codex_consult.sh"),
                          ("env -S -C HJW_PROBE=1 " + R_, "codex_consult.sh"),
                          ("env -S'-u X' HJW_PROBE=1 " + R_, "codex_consult.sh"),
                          ("env --split-string=-i HJW_PROBE=1 " + R_, "codex_consult.sh"),
                          ("env -iS -C HJW_PROBE=1 " + R_, "codex_consult.sh"),
                          ("env --s=-i HJW_PROBE=1 " + R_, "codex_consult.sh"),
                          ("HJW_PROBE=1 env -S -C d " + R_, None)):
            got = review_runner_invoked(cmd)
            check(f"2.34 runner note, env option walk: {cmd[:60]!r} -> "
                  f"{'probe (None)' if want is None else want}", got == want, str(got))

        # --- bash_guard state shape -------------------------------------------
        d = fresh()
        T.run("turn_reset.py", _prompt("p1"), d)
        T.run("bash_guard.py", _pre_bash("git push origin main", "toolu_1"), d)
        st = _state(d)
        check("R3 bash_guard: push -> outward_calls 1, outward_ids [id], flag kept; "
              "2.34: no push_calls session counter",
              st.get("outward_attempt") is True and st.get("outward_calls") == 1
              and st.get("outward_ids") == ["toolu_1"] and "push_calls" not in st, str(st))
        check("R3 bash_guard: no command text in the state",
              "git push origin main" not in json.dumps(st), str(st))
        T.run("bash_guard.py", _pre_bash("git push origin main", "toolu_1"), d)
        st = _state(d)
        check("R3 bash_guard: a repeated tool_use_id changes nothing",
              st.get("outward_calls") == 1 and st.get("outward_ids") == ["toolu_1"], str(st))
        T.run("bash_guard.py", _pre_bash("echo git push", "toolu_e"), d)
        check("R3 bash_guard: a non-push call adds no id",
              _state(d).get("outward_ids") == ["toolu_1"])
        for k in range(2, 11):
            T.run("bash_guard.py", _pre_bash("git push", f"toolu_{k}"), d)
        st = _state(d)
        check("R3 bash_guard: 10 push calls -> outward_calls 10, ids capped at 8",
              st.get("outward_calls") == 10
              and st.get("outward_ids") == [f"toolu_{k}" for k in range(1, 9)]
              and st.get("outward_seen") == [f"toolu_{k}" for k in range(1, 11)], str(st))
        T.run("bash_guard.py", _pre_bash("git push", "toolu_9"), d)
        T.run("bash_guard.py", _pre_bash("git push", "toolu_10"), d)
        st = _state(d)
        check("R3 bash_guard (D2): replaying the 9th/10th id (past the display cap) "
              "changes no counter",
              st.get("outward_calls") == 10
              and len(st.get("outward_ids")) == 8 and len(st.get("outward_seen")) == 10,
              str(st))
        rc, out = T.run("report_check.py", _stop(), d)
        check("R3 report_check: 10 calls -> reason lists 8 ids and '+2 more'",
              out.get("reason") == STOP_REASON + " Detected push-bearing tool calls this turn: "
              "10 (" + ", ".join(f"toolu_{k}" for k in range(1, 9)) + "; +2 more).",
              out.get("reason"))
        T.run("turn_reset.py", _prompt("p2"), d)
        st = _state(d)
        check("R3 turn_reset: turn facts cleared, outward_seen carried",
              "outward_calls" not in st and "outward_ids" not in st
              and "outward_attempt" not in st
              and len(st.get("outward_seen", [])) == 10
              and st.get("prompt_id") == "p2", str(st))
        T.run("bash_guard.py", _pre_bash("git push", "toolu_x", pid="p3"), d)
        st = _state(d)
        check("R3 bash_guard lazy turn reset: new turn facts, session id list carried",
              st.get("prompt_id") == "p3" and st.get("outward_calls") == 1
              and st.get("outward_ids") == ["toolu_x"] and "push_calls" not in st
              and len(st.get("outward_seen", [])) == 11, str(st))
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

        # --- 2.34: stale 2.33.0 state keys load; id lists carried, pilot keys not
        lists = {"review_ids": ["r1"], "outward_seen": ["o1"], "push_returned_ids": ["o1"]}
        pilot = {"handbacks": 4, "review_runs": 2, "push_calls": 3,
                 "handback_ids": ["t1"], "checklist_assigned": True}
        d = fresh()
        _seed(d, dict(lists, **pilot, prompt_id="p1", files=["/repo/a.py"],
                      outward_calls=2, outward_ids=["x"], outward_attempt=True,
                      updated_at=time.time()))
        T.run("gate.py", T.edit_payload("/repo/src/q.py", sid=SID, pid="p2"), d)
        st = _state(d)
        check("2.34 gate.py lazy reset: id lists carried, pilot keys dropped",
              all(st.get(k) == v for k, v in lists.items())
              and not any(k in st for k in pilot)
              and st.get("files") == ["/repo/src/q.py"] and "outward_ids" not in st, str(st))
        from gate import STALE_TURN_SECONDS
        _seed(d, dict(lists, **pilot, prompt_id="p2", files=["/repo/a.py"],
                      updated_at=time.time() - STALE_TURN_SECONDS - 60))
        T.run("gate.py", T.edit_payload("/repo/src/r.py", sid=SID, pid="p2"), d)
        st = _state(d)
        check("2.34 gate.py stale reset: id lists carried, pilot keys dropped",
              all(st.get(k) == v for k, v in lists.items())
              and not any(k in st for k in pilot)
              and st.get("files") == ["/repo/src/r.py"], str(st))
        _seed(d, dict(lists, **pilot, prompt_id="p2", files=["/repo/a.py", "/repo/b.py"],
                      updated_at=time.time()))
        rc, out = T.run("turn_reset.py", _prompt("p3"), d)
        st = _state(d)
        check("2.34 turn_reset on a stale 2.33.0 state: no context, files reset, id lists "
              "carried, pilot keys dropped, no turn_checklist record",
              silent(rc, out) and st.get("files") == [] and st.get("prompt_id") == "p3"
              and all(st.get(k) == v for k, v in lists.items())
              and not any(k in st for k in pilot) and not _obs(d, "turn_checklist"),
              str((out, st)))

        # gate.py decides the same on a state with and without the pilot keys
        def gate_decisions(extra):
            dd = fresh()
            _seed(dd, dict(extra, prompt_id="p1", files=["/repo/a.py"],
                           updated_at=time.time()))
            outs = [T.run("gate.py", T.edit_payload(f, sid=SID, pid="p1"), dd)[1]
                    for f in ("/repo/b.py", "/repo/c.py", "/repo/a.py")]
            return [T.decision(o) for o in outs]
        plain, stale = gate_decisions({}), gate_decisions(pilot)
        check("2.34 gate.py: a state carrying the pilot keys decides exactly as one without",
              plain == stale and stale[1] == "deny", str((plain, stale)))

        # --- 2.34: turn_reset emits nothing, records nothing ----------------
        for label, cfg in (("default config", None),
                           ("obsolete turn_checklist true", {"gate": {"turn_checklist": True}})):
            d = fresh(cfg)
            _seed(d, {"prompt_id": "p0", "files": ["/repo/a.py"], "handbacks": 3,
                      "review_runs": 1, "push_calls": 2})
            rc, out = T.run("turn_reset.py", _prompt("p1"), d)
            check(f"2.34 turn_reset ({label}): no output, reset done, no record",
                  silent(rc, out) and _state(d).get("files") == []
                  and _state(d).get("prompt_id") == "p1" and not _obs(d), str(out))
        dpf = fresh()
        os.makedirs(state_file(dpf, SID))  # the state path is a directory: save fails
        rc, out = T.run("turn_reset.py", _prompt("p1"), dpf)
        check("2.34 turn_reset: reset save failed -> exit 0, no output, no record",
              silent(rc, out) and not _obs(dpf), str(out))
        dsub = fresh()
        _seed(dsub, {"prompt_id": "p0", "files": ["/repo/a.py"]})
        rc, out = T.run("turn_reset.py", _prompt("p1", agent_id="a1", agent_type="w"), dsub)
        check("R3 turn_reset exempt (subagent): no output, state untouched (as before)",
              silent(rc, out) and _state(dsub).get("prompt_id") == "p0", str(out))

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
        check("2.34 post_bash: runner call without the probe marker -> one note, exact",
              _ctx(out, "PostToolUse") == RUNNER_T.format(runner="codex_consult.sh",
                                                          id="toolu_R"), str(out))
        check("2.34 post_bash: id claimed in review_ids, no review_runs counter",
              "review_runs" not in _state(d) and _state(d).get("review_ids") == ["toolu_R"],
              str(_state(d)))
        rc, out = T.run("post_bash.py", _post_bash(rcmd, "toolu_R"), d)
        check("R3 post_bash: the same tool_use_id again -> no line, no new claim",
              silent(rc, out) and _state(d).get("review_ids") == ["toolu_R"], str(out))
        for k, pcmd in enumerate((
                "HJW_PROBE=1 /p/haejwo/scripts/codex_consult.sh b.md > r.log 2>&1",
                "printf 'x' | env HJW_PROBE=1 /p/haejwo/scripts/claude_consult.sh -")):
            rc, out = T.run("post_bash.py", _post_bash(pcmd, f"toolu_PR{k}"), d)
            check(f"2.34 post_bash: setup probe form {k + 1} -> no note, no claim",
                  silent(rc, out) and _state(d).get("review_ids") == ["toolu_R"], str(out))
        dw = fresh()
        for k in range(65):
            T.run("post_bash.py", _post_bash(rcmd, f"toolu_rw{k}"), dw)
        rc, out = T.run("post_bash.py", _post_bash(rcmd, "toolu_rw0"), dw)
        ids_ = _state(dw).get("review_ids", [])
        check("R3 review dedupe: rolling window of 64 (evicted id replays)",
              _ctx(out, "PostToolUse") == RUNNER_T.format(runner="codex_consult.sh",
                                                          id="toolu_rw0")
              and len(ids_) == 64 and ids_[-1] == "toolu_rw0", str(ids_[-3:]))
        recs = [r for r in _obs(d, "post_bash") if r.get("via") == "review-invoked"]
        check("R3 post_bash observation: one note/review-invoked with chars",
              len(recs) == 1 and recs[0].get("chars") == len(RUNNER_T.format(
                  runner="codex_consult.sh", id="toolu_R")), str(recs))
        both = "/p/scripts/claude_consult.sh - < b.md && git push"
        T.run("bash_guard.py", _pre_bash(both, "toolu_B"), d)
        rc, out = T.run("post_bash.py", _post_bash(both, "toolu_B", stderr="done"), d)
        check("R3 post_bash: both on one call -> push line then review line",
              _ctx(out, "PostToolUse") == PUSH_T.format(id="toolu_B", tail="done",
                                                        interrupted="")
              + "\n" + RUNNER_T.format(runner="claude_consult.sh", id="toolu_B"), str(out))
        rc, out = T.run("post_bash.py", _post_bash("ls -la", "toolu_L", stdout="x"), d)
        check("R3 post_bash: an ordinary call -> no output", silent(rc, out), str(out))

        # --- post_bash exemptions ---------------------------------------------
        def post_exempt(label, data, env=None, root=None, payload=None):
            _seed(data, {"prompt_id": "p1", "files": [], "outward_ids": ["toolu_P"],
                         "outward_calls": 1, "outward_attempt": True})
            p = payload if payload is not None else _post_bash(rcmd + " ; git push", "toolu_P",
                                                                stderr="x")
            rc, out = T.run("post_bash.py", p, data, env_extra=env, root=root)
            check(f"R3 post_bash exempt ({label}): no output, no record, no claim",
                  silent(rc, out) and not _obs(data, "post_bash")
                  and "review_ids" not in _state(data)
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

        # --- 2.34: handback_nudge removed --------------------------------------
        hooks = json.load(open(os.path.join(T.PLUGIN, "hooks", "hooks.json"),
                               encoding="utf-8"))
        post = hooks.get("hooks", {}).get("PostToolUse") or []
        check("2.34 manifest: no PostToolUse Task/Agent entry",
              not [e for e in post if "Agent" in str(e.get("matcher"))
                   or "Task" in str(e.get("matcher"))], str(post))
        check("2.34 manifest and scripts: no handback_nudge.py",
              "handback_nudge" not in json.dumps(hooks)
              and not os.path.exists(os.path.join(T.SCRIPTS, "handback_nudge.py")))

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
        check("2.34 flow: the next prompt gets no context", silent(rc, out), str(out))

        # --- D6: the Stop block observation carries the reason's length ------
        d = fresh()
        _seed(d, {"prompt_id": "p1", "files": [], "outward_attempt": True,
                  "outward_calls": 1, "outward_ids": ["toolu_a"]})
        rc, out = T.run("report_check.py", _stop(), d)
        recs = _obs(d, "report_check")
        check("R3 report_check observation (D6): block with chars = len(reason)",
              len(recs) == 1 and recs[0].get("decision") == "block"
              and recs[0].get("chars") == len(out.get("reason", "")) > 0, str(recs))

        # --- D1: claim saves never move turn freshness -----------------------
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
        dpb = fresh()
        old = stale_seed(dpb)
        rc, out = T.run("post_bash.py", _post_bash(rcmd2, "toolu_DP", stderr="x"), dpb)
        st = _state(dpb)
        check("R3 D1: push claim + runner claim on a stale state emit both and keep updated_at",
              _ctx(out, "PostToolUse").count("[haejwo]") == 2 and st.get("updated_at") == old
              and st.get("push_returned_ids") == ["toolu_DP"]
              and st.get("review_ids") == ["toolu_DP"],
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
            rc2, out2 = T.run("post_bash.py", _post_bash(rcmd2, "toolu_LP", stderr="x"), dlk)
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()
        check("R3 D4 lock unavailable: post_bash silent, exit 0, state untouched",
              silent(rc2, out2)
              and open(state_file(dlk, SID), "rb").read() == before
              and not _obs(dlk, "post_bash"), str((rc2, out2)))
        dsv = fresh()
        _seed(dsv, {"prompt_id": "p1", "files": [], "updated_at": time.time(),
                    "outward_seen": ["toolu_SP"]})
        os.makedirs(state_file(dsv, SID) + ".tmp")  # the save's temp file: EISDIR
        rc2, out2 = T.run("post_bash.py", _post_bash(rcmd2, "toolu_SP", stderr="x"), dsv)
        rc3, out3 = T.run("post_bash.py", _post_bash(rcmd2, "toolu_SP", stderr="x"), dsv)
        check("R3 D4 save fails (EISDIR): no note, exit 0 — also on the repeat",
              silent(rc2, out2) and silent(rc3, out3) and not _obs(dsv, "post_bash"),
              str((rc2, out2, rc3, out3)))

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
            r_exc = H.claim_session_id(dex, SID, "review_ids", "t1")
            H.load_state, H.save_state = saved[0], eacces
            r_acc = H.claim_session_id(dex, SID, "review_ids", "t2")
            r_none = H.claim_session_id(dex, SID, "review_ids", None)
            H.save_state = saved[1]
            post_bash.save_state = eacces
            c_acc = post_bash.claim_push_return({"prompt_id": "p1"}, dex, SID, "toolu_XP")
            post_bash.save_state, post_bash.load_state = saved[3], boom
            c_exc = post_bash.claim_push_return({"prompt_id": "p1"}, dex, SID, "toolu_XP")
        finally:
            H.load_state, H.save_state, post_bash.load_state, post_bash.save_state = saved
        check("R3 D4 in-process: exception / EACCES save -> id claim (with or without an "
              "id) and push claim return False",
              (r_exc, r_acc, r_none, c_acc, c_exc) == (False,) * 5,
              str((r_exc, r_acc, r_none, c_acc, c_exc)))

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
            m2 = run_main(post_bash, _post_bash(rcmd2, "toolu_XP", stderr="x"))
        finally:
            H.load_state, H.save_state, post_bash.load_state, post_bash.save_state = saved
        check("R3 D4 exception inside the claim: post_bash exits 0 with no output",
              m2 == (0, ""), str(m2))
        check("R3 D4: nothing persisted or observed by the failed claims",
              "review_ids" not in _state(dex) and "push_returned_ids" not in _state(dex)
              and not _obs(dex, "post_bash"), str(_state(dex)))

        # --- 2.34: the gate `via` split (allow decision unchanged) -----------
        def gate_via(payload):
            dv = fresh()
            rc, out = T.run("gate.py", payload, dv)
            recs = _obs(dv, "gate")
            return (rc == 0 and T.decision(out) != "deny" and not _state(dv).get("files"),
                    recs[-1].get("via") if recs else None)

        tmp_py = os.path.join(tempfile.gettempdir(), "hjw-r3a-scratch", "s.py")
        cases = (
            ("a temp path outside the project", T.edit_payload(tmp_py, sid=SID),
             "temp-exempt"),
            ("a .git/ component path", T.edit_payload("/repo/.git/hooks/x.py", sid=SID),
             "metadata-exempt"),
            ("a .md path", T.edit_payload("/repo/notes.md", sid=SID), "non-code"),
            ("no path", dict(T.edit_payload("", sid=SID), tool_input={}), "non-code"),
            ("apply_patch, all paths metadata",
             T.patch_payload([("Add", "/repo/.git/a.py"), ("Update", "/repo/.codex/b.py")],
                             sid=SID), "metadata-exempt"),
            ("apply_patch, mixed reasons",
             T.patch_payload([("Update", "/repo/notes.md"), ("Add", "/repo/.git/a.py"),
                              ("Add", tmp_py)], sid=SID), "non-code"),
        )
        for label, payload, want in cases:
            allowed, via = gate_via(payload)
            check(f"2.34 gate via ({label}) -> {want!r}, allowed, nothing counted",
                  allowed and via == want, str((allowed, via)))
        allowed, via = gate_via(T.edit_payload("/repo/src/a.py", sid=SID))
        check("2.34 gate: a code path still counts (via 'ok')", via == "ok", str(via))

        # --- 2.34: retired observation schemas still parse -------------------
        import status_collect as sc
        ts = time.time()
        old_recs = [
            {"v": 2, "hook": "handback_nudge", "decision": "note", "via": "completed",
             "chars": 250, "sid": "s", "ts": ts},
            {"v": 2, "hook": "turn_checklist", "decision": "none", "via": "assigned-on",
             "sid": "s", "ts": ts},
            {"v": 2, "hook": "turn_checklist", "decision": "note", "via": "emitted",
             "chars": 240, "sid": "s", "ts": ts},
            {"v": 2, "hook": "gate", "decision": "allow", "via": "temp-exempt",
             "tool": "Edit", "path": "/tmp/x.py", "sid": "s", "ts": ts},
        ]
        an = sc.anomaly_lines(old_recs)
        check("2.34 status: 2.33.0 handback_nudge/turn_checklist records and a split "
              "gate via are no anomaly", an == [], str(an))
        an = sc.anomaly_lines([{"v": 2, "hook": "bogus", "decision": "note", "sid": "s",
                                "ts": ts}])
        check("2.34 status control: an unknown hook is still a new shape",
              any(a.startswith("new shape") for a in an), str(an))

        # --- 2.34: version ----------------------------------------------------
        vers = [json.load(open(os.path.join(T.PLUGIN, d_, "plugin.json"),
                               encoding="utf-8")).get("version")
                for d_ in (".claude-plugin", ".codex-plugin")]
        check("2.34 both plugin manifests at 2.34.0", vers == ["2.34.0", "2.34.0"], str(vers))

        # --- hooks.json and source canaries ---------------------------------
        hooks = json.load(open(os.path.join(T.PLUGIN, "hooks", "hooks.json"),
                               encoding="utf-8"))
        post = hooks.get("hooks", {}).get("PostToolUse") or []
        shape = [(e.get("matcher"), h.get("type"), h.get("command"), h.get("timeout"),
                  h.get("statusMessage")) for e in post for h in e.get("hooks", [])]
        check("R3 hooks.json PostToolUse: Bash -> post_bash.py only (2.34), the shared "
              "command shape",
              shape == [("Bash", "command", CMD_SHAPE.format("post_bash.py"), 10,
                         "haejwo post-bash")], str(shape))
        for fn in ("post_bash.py", "turn_reset.py"):
            src = open(os.path.join(T.SCRIPTS, fn), encoding="utf-8").read()
            check(f"R3 canary: {fn} never emits a permission decision or a block",
                  not re.search(r"""["'](permissionDecision|decision["']:\s*["']block|"""
                                r"""approve)["']""", src), fn)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
