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

# Field round 3 (heredoc grammar). Provenance: project M session e1ffebe1 rows
# 0-4536, 2026-10-22: 14 push-bearing commands, 4 detected, 10 missed. S1/S1b/
# S2 are the field shapes (identifiers anonymized); the two LONG fixtures are
# representative field-length fixtures (>= 600 chars, multi-line commit
# bodies), not pathology tests. Of the 53 cases, 13 distinguish this grammar
# from HEAD (the five field shapes, the post-terminator / multi-heredoc /
# escaped-delimiter / quoted-or-commented-operator positives, and the
# continuation-comment negative); the rest are preservation checks.
S1 = ("git -c user.name=x -c user.email=y commit -q -F - <<'EOF'\nmsg line\n\n"
      "Co-Authored-By: Z\nEOF\ngit log --oneline -1 && git push -q origin main "
      "2>&1 | tail -3; git status -sb | head -1")
S1B = ("git branch --show-current && git add -A && git reset -q -- "
       "tools/reports/*.jsonl && git status --short | grep -c . && git commit -q "
       "-F - <<'EOF'\nmsg\nEOF\ngit log --oneline -1 && git push -q origin HEAD "
       "2>&1 | tail -3")
S2 = ("cat >> docs/design/X.md <<'EOF'\n\n### G-12 title\n- line with git push "
      "words in prose\nEOF\ngit add docs/design/X.md && git -c user.name=x -c "
      "user.email=y commit -q -m \"docs: t\n\nCo-Authored-By: Z\" && git push -q "
      "origin main 2>&1 | tail -1; git log --oneline -1")
S1_LONG = (
    "git -c user.name=field-bot -c user.email=bot@example.invalid commit -q -F - "
    "<<'EOF'\nfeat(api): move the report endpoints under /api and pin the schema\n\n"
    "- routes: every /v1_old/* handler is served from /api/* with the same\n"
    "  request and response shapes; the old prefix answers 308 for one release\n"
    "- schema: the report payload gains an explicit version field and the\n"
    "  validator rejects unknown keys instead of dropping them silently\n"
    "- tests: the contract suite runs against both prefixes until removal;\n"
    "  never git push from CI without the release tag (prose, not a command)\n\n"
    "Co-Authored-By: Field Agent <noreply@example.invalid>\nEOF\n"
    "git log --oneline -1 && git push -q origin main 2>&1 | tail -3; "
    "git status -sb | head -1")
S2_LONG = (
    "cat >> docs/design/report-pipeline.md <<'EOF'\n\n### G-14 nightly export "
    "ownership\n\n- The exporter writes one JSONL file per run under "
    "tools/reports/ and never\n  commits it; the reviewer checks the row count "
    "against the source table.\n- Rollback: revert the export commit, then git "
    "push the revert (prose).\n- Open question: whether the weekly digest reuses "
    "the nightly files or\n  recomputes them; decided at the next design "
    "review.\nEOF\ngit add docs/design/report-pipeline.md && git -c "
    "user.name=field-bot -c user.email=bot@example.invalid commit -q -m \"docs: "
    "G-14 export ownership\n\nRecords the owner, rollback path and the open "
    "digest question.\n\nCo-Authored-By: Field Agent <noreply@example.invalid>\" "
    "&& git push -q origin main 2>&1 | tail -1; git log --oneline -1")
assert len(S1_LONG) >= 600 and len(S2_LONG) >= 600

# Codex diff review B1 (reproduced in bash 5.1, no push runs): `#` after a
# backslash-newline still starts a comment, so the commented `<<'EOF'` is
# no operator and `git push` sits inside the real quoted body.
B1_CONT_COMMENT = ": \\\n# <<'EOF'\n: <<'Q'\nEOF\ngit push\nQ"

PUSH_CASES += [
    (S1, True, "field S1: commit -F - <<'EOF' then push"),
    (S1B, True, "field S1b: add/reset/commit <<'EOF' then push HEAD"),
    (S2, True, "field S2: cat >> md <<'EOF' then commit -m and push"),
    (S1_LONG, True, "field S1 long verbatim-length"),
    (S2_LONG, True, "field S2 long verbatim-length"),
    ("cat <<'EOF'\ngit push origin main\nEOF", False, "push only inside a quoted body"),
    ("cat <<'EOF'\nEOF_NOT\nxEOF\ngit push origin main\nEOF", False,
     "body lines containing the delimiter as a substring"),
    ("cat <<'EOF'\nEOF_NOT\nxEOF\nEOF\ngit push", True,
     "push after the exact terminator, substring lines before it"),
    ("cat <<EOF\nx\nEOF\ngit push", False,
     "unquoted delimiter: scan ends at the operator line (documented fail-open)"),
    (": <<EOF\nx\\\nEOF\ngit push\nEOF", False,
     "unquoted body joined by backslash-newline"),
    ('echo "git push"', False, "echo of a quoted push"),
    ("printf '%s' \"git push\"", False, "printf of a quoted push"),
    ('cat <<< "git push"', False, "here-string is not a heredoc"),
    ("# git push", False, "whole-line comment"),
    ("git push && cat <<'EOF'\nbody\nEOF", True, "push before a heredoc"),
    ("git push <<'EOF'\npayload\nEOF", True, "push on the operator line"),
    ("cat <<'A'\na\nA\ncat <<\"B\"\nb\nB\ngit push", True,
     "two quoted heredocs then push"),
    ("cat <<-'EOF'\n\tbody\n\tEOF\ngit push", True,
     "<<- with a tab-indented terminator then push"),
    ("cat <<\\EOF\nbody\nEOF\ngit push", True, "escaped delimiter then push"),
    ("cat <<'A' <<'B'\ngit push\nA\ngit push\nB\ngit push", True,
     "two heredocs on one line: bodies in declaration order"),
    ("cat <<'A' <<'B'\na\nA\ngit push\nB", False,
     "two heredocs on one line: second body not scanned"),
    ("cat <<'A' <<B\na\nA\nb\nB\ngit push", False,
     "a quoted and an unquoted heredoc on one line: scan ends"),
    ("cat <<'EOF'\nbody\ngit push", False, "unterminated quoted heredoc"),
    ("cat <<'EOF'\nx\nEOF\r\ngit push", False,
     "CR after the delimiter is not the terminator"),
    ("cat <<-'EOF'\nx\n  EOF\ngit push", False, "<<- strips tabs only, not spaces"),
    ("echo '<<EOF'\ngit push", True, "<< inside quotes is not an operator"),
    ("echo x # <<'EOF'\ngit push", True, "<< after a comment is not an operator"),
    ("cat <<'EOF'; echo \"a\nEOF\ngit push\"\nEOF", False,
     "header continued past its line: scan ends"),
    (B1_CONT_COMMENT, False, "B1: # after a line continuation is a comment"),
    ("echo a\\ #not-a-comment; git push", True, "escaped space: # is not a comment"),
    ("git status \\\n# ; git push", False, "continuation then comment: ; is commented"),
    ("cat <<''\nx\n\ngit push", False, "empty delimiter is not modeled: scan ends"),
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

        # integrated (field round 3): a push after a quoted heredoc's
        # terminator reaches the Stop check; the Codex exemption holds
        both = ("Pushed.\nOutward actions: repo x; main; git push; ok - authorized "
                "by: \"push it\", 10:00\nFinal-diff review: not run because: docs only")
        cx_root = "/home/u/.codex/plugins/cache/haejwo/haejwo"
        for label, root, msg in (("no labels", None, "Pushed the docs."),
                                 ("both labels", None, both),
                                 ("Codex host", cx_root, "Pushed the docs.")):
            d = tempfile.mkdtemp(prefix="hjw-test-r2a-int-")
            try:
                sid = "s-R2Pint"
                rc, out = T.run("bash_guard.py", T.bash_payload(S2, sid=sid), d, root=root)
                recs = _sid_recs(d, sid)
                T.check(f"R2P integrated S2 ({label}): allow, outward-attempt record, flag",
                        rc == 0 and T.no_decision_key(out)
                        and [r.get("via") for r in recs] == ["ok", "outward-attempt"]
                        and recs[1].get("target") == "git push"
                        and _state(d, sid).get("outward_attempt") is True,
                        str(out) + str(recs))
                if root is None:
                    T.check(f"R2P integrated S2 ({label}): note in additionalContext",
                            T.ctx_of(out) == OUTWARD_NOTE, str(out))
                stop = {"session_id": sid, "prompt_id": "p1", "hook_event_name": "Stop",
                        "stop_hook_active": False, "last_assistant_message": msg}
                rc, out = T.run("report_check.py", stop, d, root=root)
                if label == "no labels":
                    T.check("R2P integrated Stop: blocks once without the record lines",
                            rc == 0 and out.get("decision") == "block"
                            and _state(d, sid).get("report_nudged") is True, str(out))
                    rc, out = T.run("report_check.py", stop, d, root=root)
                    T.check("R2P integrated Stop: second Stop in the turn passes",
                            rc == 0 and out == {}, str(out))
                else:
                    T.check(f"R2P integrated Stop ({label}): passes silently",
                            rc == 0 and out == {}
                            and not _state(d, sid).get("report_nudged"), str(out))
            finally:
                shutil.rmtree(d, ignore_errors=True)

        # B1 (Codex diff review): a commented `<<'EOF'` after a continuation
        # runs no push -> no record, no flag, no note, and no Stop block
        d = tempfile.mkdtemp(prefix="hjw-test-r2a-b1-")
        try:
            sid = "s-R2Pb1"
            rc, out = T.run("bash_guard.py", T.bash_payload(B1_CONT_COMMENT, sid=sid), d)
            recs = _sid_recs(d, sid)
            T.check("R2P B1 continuation comment: allow, no outward record/flag/note",
                    rc == 0 and T.no_decision_key(out) and T.ctx_of(out) == ""
                    and [r.get("via") for r in recs] == ["ok"]
                    and not _state(d, sid).get("outward_attempt"), str(out) + str(recs))
            rc, out = T.run("report_check.py",
                            {"session_id": sid, "prompt_id": "p1", "hook_event_name": "Stop",
                             "stop_hook_active": False,
                             "last_assistant_message": "Done."}, d)
            T.check("R2P B1 continuation comment: report_check does not block",
                    rc == 0 and out == {}, str(out))
        finally:
            shutil.rmtree(d, ignore_errors=True)

        # deny text (field round 3): four paths x two host renderings, byte-exact
        mid = ("Bash code edits signal delegation. Delegate project-exercising "
               "scripts, scratch implementations and other delegated work to {w}. "
               "For eligible direct work, use {e} within the turn budget. "
               "Emergency override: {o}.")
        hosts = (("Claude", None, "'haejwo:default-worker'", "Edit/Write", "/haejwo:gate off"),
                 ("Codex", cx_root, "spawn_agent with the configured worker model and "
                  "reasoning_effort (see @haejwo-setup)", "apply_patch", "@haejwo-gate off"))
        paths_ = (("redirect", "echo x > src/app.py",
                   "[haejwo gate] Bash output redirect writes to a code file (src/app.py). "),
                  ("tee", "echo x | tee src/app.py",
                   "[haejwo gate] Bash tee writes to a code file (src/app.py). "),
                  ("inplace", "sed -i s/a/b/ src/app.py",
                   "[haejwo gate] Bash in-place edit (sed -i) targets ['src/app.py']. "),
                  ("fanout", "find . -exec sed -i s/a/b/ {} +",
                   "[haejwo gate] Bash in-place edit (sed -i) targets files fanned out "
                   "via find/xargs. "))
        for host, root, w, e, o in hosts:
            for via, cmd, head in paths_:
                sid = f"s-D{host[:2]}{via[:4]}"  # <= 12 chars (record sid cap)
                rc, out = T.run("bash_guard.py", T.bash_payload(cmd, sid=sid), data, root=root)
                want = head + mid.format(w=w, e=e, o=o)
                T.check(f"R2D deny text byte-exact: {via} on {host}",
                        rc == 0 and T.decision(out) == "deny" and T.reason_of(out) == want
                        and [r.get("via") for r in _sid_recs(data, sid)] == [via],
                        f"got={T.reason_of(out)!r}")

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
