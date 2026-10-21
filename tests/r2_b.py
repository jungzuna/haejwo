"""Field round 2, Worker B: report_check.py (Stop) — presence check of the
record lines on a push turn. Wired into test_hooks.main() by the host."""
import json
import os
import re
import shutil
import tempfile

import test_hooks as T
from hjw_common import state_file

SID = "sess-RC"
BOTH = ("Pushed.\nOutward actions: repo x; branch b; git push; ok - authorized "
        "by: \"push it\", 10:00\nFinal-diff review: not run because: docs only")


def _stop(msg, sid=SID, **extra):
    d = {"session_id": sid, "hook_event_name": "Stop",
         "stop_hook_active": False, "last_assistant_message": msg}
    d.update(extra)
    return d


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


def _obs(data):
    path = os.path.join(data, "state", "observations.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def _cfg(data, cfg):
    with open(os.path.join(data, "config.json"), "w") as f:
        f.write(cfg if isinstance(cfg, str) else json.dumps(cfg))


def run_section():
    print("== report_check.py (Stop) ==")
    tmp = tempfile.mkdtemp(prefix="hjw-r2b-")
    n = [0]

    def fresh(outward=True):
        n[0] += 1
        d = os.path.join(tmp, f"d{n[0]}")
        os.makedirs(d)
        if outward:
            _seed(d, {"prompt_id": "p1", "files": [], "outward_attempt": True})
        return d

    def silent(rc, out):
        return rc == 0 and out == {}

    try:
        d = fresh(outward=False)
        rc, out = T.run("report_check.py", _stop("done"), d)
        T.check("RC1 no state -> exit 0, no output", silent(rc, out), str((rc, out)))

        d = fresh()
        rc, out = T.run("report_check.py", _stop("done", stop_hook_active=True), d)
        T.check("RC2 stop_hook_active -> silent, no nudge flag",
                silent(rc, out) and not _state(d).get("report_nudged"), str(out))

        d = fresh()
        rc, out = T.run("report_check.py", _stop("Pushed the branch."), d)
        reason = out.get("reason", "")
        T.check("RC3 push turn, no labels -> block naming both labels",
                rc == 0 and out.get("decision") == "block"
                and "Outward actions:" in reason and "Final-diff review:" in reason
                and reason.startswith("[haejwo] This turn requested git push.")
                and "not run because:" in reason, str(out))
        T.check("RC3 the once-flag is persisted before the block",
                _state(d).get("report_nudged") is True, str(_state(d)))
        recs = [r for r in _obs(d) if r.get("hook") == "report_check"]
        T.check("RC3 observation: decision block via missing-both",
                len(recs) == 1 and recs[0].get("decision") == "block"
                and recs[0].get("via") == "missing-both" and recs[0].get("v") == 2,
                str(recs))
        T.check("RC3 the block never carries a permission decision",
                "hookSpecificOutput" not in out and T.no_decision_key(out), str(out))
        rc, out = T.run("report_check.py", _stop("Pushed the branch."), d)
        T.check("RC4 second Stop in the same turn -> silent (once per turn)",
                silent(rc, out), str(out))

        d = fresh()
        rc, out = T.run("report_check.py",
                        _stop("Outward actions: repo; main; push; ok - authorized by: x"), d)
        recs = [r for r in _obs(d) if r.get("hook") == "report_check"]
        T.check("RC5 only `Outward actions:` -> block via missing-review",
                out.get("decision") == "block" and recs and recs[-1].get("via") == "missing-review",
                str((out, recs)))

        d = fresh()
        rc, out = T.run("report_check.py", _stop("Final-diff review: not run because: x"), d)
        recs = [r for r in _obs(d) if r.get("hook") == "report_check"]
        T.check("RC5b only `Final-diff review:` -> block via missing-outward",
                out.get("decision") == "block" and recs and recs[-1].get("via") == "missing-outward",
                str((out, recs)))

        d = fresh()
        rc, out = T.run("report_check.py", _stop(BOTH), d)
        T.check("RC6 both labels (review `not run because:`) -> silent, no nudge flag",
                silent(rc, out) and not _state(d).get("report_nudged")
                and not _obs(d), str((out, _state(d))))

        d = fresh()
        rc, out = T.run("report_check.py", _stop("done"), d, env_extra={"HAEJWO_GATE": "off"})
        T.check("RC7 HAEJWO_GATE=off -> silent", silent(rc, out), str(out))

        d = fresh()
        _cfg(d, {"gate": {"report_check": False}})
        rc, out = T.run("report_check.py", _stop("done"), d)
        T.check("RC8 gate.report_check false -> silent", silent(rc, out), str(out))

        d = fresh()
        _cfg(d, {"gate": {"report_check": "yes"}})
        rc, out = T.run("report_check.py", _stop("done"), d)
        T.check("RC8b invalid gate.report_check -> check OFF (fail open, like every "
                "enforcement flag)", silent(rc, out), str(out))

        d = fresh()
        _cfg(d, "{not json")
        rc, out = T.run("report_check.py", _stop("done"), d)
        T.check("RC9 malformed config.json -> silent", silent(rc, out), str(out))

        d = fresh()
        rc, out = T.run("report_check.py", _stop("done", agent_id="a1", agent_type="w"), d)
        T.check("RC10 subagent payload -> silent", silent(rc, out), str(out))

        d = fresh()
        rc, out = T.run("report_check.py", "{not json", d)
        rc2, out2 = T.run("report_check.py", "", d)
        T.check("RC11 malformed / empty stdin -> exit 0, no output",
                silent(rc, out) and silent(rc2, out2), str((rc, out, rc2, out2)))

        d = fresh()
        _cfg(d, {"gate": {"enabled": False}})
        rc, out = T.run("report_check.py", _stop("done"), d)
        T.check("RC12 gate.enabled false -> silent", silent(rc, out), str(out))

        d = fresh()
        rc, out = T.run("report_check.py", {"session_id": SID, "hook_event_name": "Stop",
                                            "stop_hook_active": False}, d)
        T.check("RC12b last_assistant_message absent -> silent", silent(rc, out), str(out))

        d = fresh()
        rc, out = T.run("report_check.py", _stop("done", prompt_id="p2"), d)
        T.check("RC12c state from an earlier prompt_id -> silent",
                silent(rc, out), str(out))

        d = fresh()
        codex_root = os.path.join(tmp, ".codex", "plugins", "haejwo")
        rc, out = T.run("report_check.py", _stop("done"), d, root=codex_root)
        T.check("RC12d Codex host (Stop contract unmeasured) -> silent",
                silent(rc, out), str(out))

        src = open(os.path.join(T.SCRIPTS, "report_check.py"), encoding="utf-8").read()
        T.check("RC13 canary: report_check.py never emits permissionDecision",
                not re.search(r"""["'](permissionDecision|hookSpecificOutput|approve)["']""", src),
                "a quoted permission key in the source")

        hooks = json.load(open(os.path.join(T.PLUGIN, "hooks", "hooks.json"), encoding="utf-8"))
        stop = hooks.get("hooks", {}).get("Stop") or []
        cmds = [h for e in stop for h in e.get("hooks", [])]
        T.check("RC14 hooks.json Stop entry: report_check.py, the shared command shape",
                len(cmds) == 1 and cmds[0].get("type") == "command"
                and cmds[0].get("command") == (
                    'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_check.py" '
                    '"${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}"')
                and cmds[0].get("timeout") == 10
                and cmds[0].get("statusMessage") == "haejwo record", str(cmds))

        d = fresh()
        T.run("report_check.py", _stop("done"), d)
        nudged = _state(d).get("report_nudged") is True
        T.run("turn_reset.py", {"session_id": SID, "prompt_id": "p2",
                                "hook_event_name": "UserPromptSubmit"}, d)
        st = _state(d)
        T.check("RC15 turn_reset clears outward_attempt and report_nudged",
                nudged and "outward_attempt" not in st and "report_nudged" not in st
                and st.get("prompt_id") == "p2", str(st))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
