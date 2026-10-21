"""Field round 2, Worker A: bash_guard.py assignment resolution and the
outward (`git push`) note. Wired into test_hooks.main() by the host."""
import json
import os
import shutil
import tempfile

import test_hooks as T
from hjw_common import state_file

OUTWARD_NOTE = ("[haejwo] outward action requested (git push): the report "
                "carries `Outward actions: <repo; branch; action; result> - "
                "authorized by: <user words, time>`.")


def _recs(data):
    path = os.path.join(data, "state", "observations.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path)]


def _sid_recs(data, sid):
    return [r for r in _recs(data) if r.get("sid") == sid]


def _state(data, sid):
    try:
        with open(state_file(data, sid)) as f:
            return json.load(f)
    except Exception:
        return {}


# (command, is_attempt, name)
PUSH_CASES = [
    ("git push", True, "plain"),
    ("git -C /repo push origin main", True, "-C <path>"),
    ("git --no-optional-locks push", True, "option without argument"),
    ("git -c a=b push", True, "-c <k=v>"),
    ("GIT_SSH_COMMAND=x git push", True, "leading assignment word"),
    ("cd /repo && git push", True, "after &&"),
    ("/usr/bin/git push origin HEAD", True, "path to git"),
    ("git push --dry-run", False, "--dry-run"),
    ("git push -n", False, "-n"),
    ("git push -fn origin HEAD", False, "bundled -fn"),
    ("git push -nf origin HEAD", False, "bundled -nf"),
    ("git push --dry-run origin HEAD", False, "--dry-run with args"),
    ("git push -f origin HEAD", True, "-f is not a dry run"),
    ("env -u GIT_DIR git push origin HEAD", True, "env with -u NAME"),
    ("env -i PATH=/usr/bin git push", True, "env option then assignment"),
    ('printf "%s" "git push"', False, "quoted inside printf"),
    ("echo git push", False, "echo argument"),
    ("git pushx", False, "pushx"),
    ('echo "a; git push origin"', False, "separator inside quotes"),
    ("git status # ; git push", False, "comment"),
    ("cat > notes.txt <<'EOF'\ngit push\nEOF", False, "heredoc body not scanned"),
]


def run_section():
    print("== bash_guard.py outward note on git push (2.32) ==")
    data = tempfile.mkdtemp(prefix="hjw-test-r2a-")
    try:
        # the assignment resolver is withdrawn: same-command literal
        # assignments stay unresolved (HEAD behavior), no resolved_from
        for i, cmd in enumerate(("D=/repo; cat > $D/a.py",
                                 "declare -n D=T; D=/repo/a.py; T=/dev/null; printf x > $D",
                                 "D=/repo/a.py; trap 'D=/dev/null' DEBUG; printf x > $D")):
            sid = f"s-R2W{i}"
            rc, out = T.run("bash_guard.py", T.bash_payload(cmd, sid=sid), data)
            recs = _sid_recs(data, sid)
            T.check(f"R2W resolver withdrawn -> allow unresolved-target: {cmd}",
                    rc == 0 and T.no_decision_key(out) and len(recs) == 1
                    and recs[0].get("via") == "unresolved-target"
                    and "resolved_from" not in recs[0], str(out) + str(recs))
        for i, (cmd, attempt, name) in enumerate(PUSH_CASES):
            sid = f"sess-R2P{i}"
            rc, out = T.run("bash_guard.py", T.bash_payload(cmd, sid=sid), data)
            recs = _sid_recs(data, sid)
            outward = [r for r in recs if r.get("via") == "outward-attempt"]
            st = _state(data, sid)
            if attempt:
                T.check(f"R2P push note exact: {name}",
                        rc == 0 and T.ctx_of(out) == OUTWARD_NOTE, str(out))
                T.check(f"R2P no permissionDecision: {name}", T.no_decision_key(out),
                        str(out))
                T.check(f"R2P outward_attempt flag + prompt_id: {name}",
                        st.get("outward_attempt") is True and st.get("prompt_id") == "p1",
                        str(st))
                T.check(f"R2P records: decision then outward-attempt: {name}",
                        len(recs) == 2 and recs[0].get("via") == "ok"
                        and len(outward) == 1 and outward[0].get("decision") == "allow"
                        and outward[0].get("target") == "git push"
                        and outward[0].get("hook") == "bash_guard", str(recs))
            else:
                T.check(f"R2P not an attempt: {name}",
                        rc == 0 and T.ctx_of(out) == "" and not outward
                        and not st.get("outward_attempt") and T.no_decision_key(out),
                        str(out) + str(recs) + str(st))

        # a deny elsewhere in the command wins: no flag, no record, no note
        sid = "s-R2Pdeny"
        rc, out = T.run("bash_guard.py",
                        T.bash_payload("git push; echo > src/a.py", sid=sid), data)
        recs = _sid_recs(data, sid)
        T.check("R2P deny wins over the outward note",
                rc == 0 and T.decision(out) == "deny" and OUTWARD_NOTE not in T.reason_of(out)
                and not _state(data, sid).get("outward_attempt")
                and [r.get("via") for r in recs] == ["redirect"],
                str(out) + str(recs))

        # subagent payloads: untouched
        sid = "s-R2Psub"
        rc, out = T.run("bash_guard.py",
                        T.bash_payload("git push", sid=sid, agent="task-worker"), data)
        T.check("R2P subagent git push: no note, no flag, no outward record",
                rc == 0 and T.ctx_of(out) == "" and not _state(data, sid).get("outward_attempt")
                and not [r for r in _sid_recs(data, sid) if r.get("via") == "outward-attempt"],
                str(out))

        # a new prompt_id resets the turn the way gate.py does (files cleared)
        sid = "s-R2Pturn"
        path = state_file(data, sid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"prompt_id": "p0", "files": ["/repo/x.py"],
                       "cfg_ignored_noted": True, "updated_at": 1}, f)
        rc, out = T.run("bash_guard.py", T.bash_payload("git push", sid=sid), data)
        st = _state(data, sid)
        T.check("R2P new turn: flag set, files reset, session flags carried",
                st.get("outward_attempt") is True and st.get("prompt_id") == "p1"
                and st.get("files") == [] and st.get("cfg_ignored_noted") is True,
                str(st))

        # enforcement off: no note, no flag, no outward record
        for label, sub, env_extra, cfg, want_via in (
                ("env-off", "s-R2Penv", {"HAEJWO_GATE": "off"}, None, "env-off"),
                ("gate-off", "s-R2Pgoff", None, {"gate": {"enabled": False}}, "gate-off"),
                ("bash_guard off", "s-R2Pbgoff", None, {"gate": {"bash_guard": False}}, "gate-off"),
                ("config-malformed", "s-R2Pmal", None, "{not json", "config-malformed")):
            d = tempfile.mkdtemp(prefix="hjw-test-r2a-off-")
            try:
                if cfg is not None:
                    with open(os.path.join(d, "config.json"), "w") as f:
                        f.write(cfg if isinstance(cfg, str) else json.dumps(cfg))
                rc, out = T.run("bash_guard.py", T.bash_payload("git push", sid=sub), d,
                                env_extra=env_extra)
                recs = _sid_recs(d, sub)
                T.check(f"R2P {label}: no note, no flag, no outward record",
                        rc == 0 and OUTWARD_NOTE not in T.ctx_of(out)
                        and T.no_decision_key(out)
                        and not _state(d, sub).get("outward_attempt")
                        and not [r for r in recs if r.get("via") == "outward-attempt"]
                        and [r.get("via") for r in recs] == [want_via],
                        str(out) + str(recs))
            finally:
                shutil.rmtree(d, ignore_errors=True)

        # ok and unresolved-target allows carry the note
        for label, sub, cmd, via in (
                ("ok allow", "s-R2Pok", "cat > notes.md; git push", "ok"),
                ("unresolved-target", "s-R2Punr", "cat > $SP/a.py; git push", "unresolved-target")):
            rc, out = T.run("bash_guard.py", T.bash_payload(cmd, sid=sub), data)
            recs = _sid_recs(data, sub)
            T.check(f"R2P {label}: note emitted",
                    rc == 0 and T.ctx_of(out) == OUTWARD_NOTE
                    and [r.get("via") for r in recs] == [via, "outward-attempt"],
                    str(out) + str(recs))

        # a lock not acquired: the write is skipped, the note still emitted
        sid = "s-R2Plock"
        import fcntl
        lock_path = state_file(data, sid) + ".lock"
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fh = open(lock_path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            rc, out = T.run("bash_guard.py", T.bash_payload("git push", sid=sid), data)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
        T.check("R2P lock busy: note emitted, state untouched",
                rc == 0 and T.ctx_of(out) == OUTWARD_NOTE
                and not os.path.exists(state_file(data, sid)), str(out))
    finally:
        shutil.rmtree(data, ignore_errors=True)
