#!/usr/bin/env python3
"""haejwo bash-guard — PreToolUse on Bash.

The rule: the MAIN agent never modifies code files via Bash
(sed -i / echo > / tee / heredoc redirects...) — that's the classic gate
bypass. This hook heuristically denies such commands regardless of the
edit budget. Known residual gap (undetectable by regex, accepted for a
delegation gate): `python -c`, `node -e`, project scripts that write files.
The injected rules text covers those by instruction.

Observation record (v2): the decision is computed FIRST, then observed
exactly once, then emitted — the audit trail can never claim an outcome the
hook didn't produce, and an observation failure never changes the decision.
`via` is the reason the decision was reached. Precedence is evaluated PER
SEGMENT, in this order within each segment (redirect/tee before in-place),
and the FIRST segment that denies wins — so a command whose earlier segment
only had an unresolved target still denies on a later literal one:
  subagent-exempt    the call came from inside a subagent (never gated)
  env-off            HAEJWO_GATE=off in the environment
  config-malformed   config.json is unparseable — allowed, guard fails open
  gate-off           config gate.enabled or gate.bash_guard is false
  redirect           DENIED: `>`/`>>` into a literal code file
  tee                DENIED: `tee` into a literal code file
  inplace            DENIED: an in-place editor with a literal code target
  fanout             DENIED: an in-place editor fanned out via find/xargs
  unresolved-target  allowed: the only write target(s) were shell-expanded
                     (see below) and nothing literal was denied
  no-command         the payload carried no command
  ok                 allowed, nothing matched
  fail-open          an internal error — allowed, as always (P4)
`target` carries the offending/exempted target (<=200 chars) for the
redirect / tee / inplace / unresolved-target cases: the stripped path for a
deny (what the deny text names), and for unresolved-target the FIRST
unresolved word seen, raw and verbatim.
"""
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    allow, deny, gate_disabled_by_env, is_code_file, is_subagent,
    load_config_with_status, malformed_note_once, observe, paths, read_payload,
)

SEGMENT_SPLIT = re.compile(r"\|\||&&|;|\|")
# capture redirect targets:  > file  >> file  2> file  &> file
# The capture is the whole shell WORD (\S+), not a pre-stripped path: the
# word is what carries provenance (quotes, $expansions) and classification
# happens on it — only the final code-file test sees the stripped path.
REDIRECT = re.compile(r"(?:^|\s)(?:\d?>>?|&>)\s*(\S+)")
TEE = re.compile(r"\btee\b\s+(?:-\w+\s+)*(\S+)")
SED_INPLACE = re.compile(r"\bsed\b[^|;&]*(?:\s-\w*i\w*\b|--in-place)")
AWK_INPLACE = re.compile(r"\bawk\b[^|;&]*-i\s*inplace")
PERL_INPLACE = re.compile(r"\bperl\b[^|;&]*\s-\w*i")
# fan-out executors: `find ... -exec sed -i` / `xargs sed -i` edit files whose
# names never appear in the command — treat in-place editor + fan-out as write
# intent even without a visible code-file token.
FANOUT = re.compile(r"\b(?:find|xargs)\b")


def protected_spans(text):
    """Per-character mask: True inside a `$( ... )` (balanced, nesting ok) or a
    backtick pair, delimiters included. Whitespace there does NOT split words —
    `$(printf /repo)/x.py` is ONE target, and splitting it would throw away the
    `$` that makes it unresolvable (origin 2026-09-14 re-review). An
    unterminated span protects to the end of the segment: the fail-open
    direction, since protection can only produce more unresolved-targets."""
    mask = [False] * len(text)
    n, i = len(text), 0
    while i < n:
        if text[i] == "$" and i + 1 < n and text[i + 1] == "(":
            depth, j = 0, i + 1
            while j < n:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            end = j if j < n else n - 1
        elif text[i] == "`":
            j = text.find("`", i + 1)
            end = j if j != -1 else n - 1
        else:
            i += 1
            continue
        for k in range(i, end + 1):
            mask[k] = True
        i = end + 1
    return mask


def _splits(text, idx, mask):
    return text[idx].isspace() and not mask[idx]


def expand_word(text, start, end, mask):
    """Widen a match to the whole shell WORD as typed — quotes and expansions
    included, across whitespace protected by `protected_spans`. Classify on
    the word and record provenance from the word; the word is never stripped
    (see the quoted-literal gap in `_decide`)."""
    i, j = start, end
    while i > 0 and not _splits(text, i - 1, mask):
        i -= 1
    while j < len(text) and not _splits(text, j, mask):
        j += 1
    return text[i:j]


def unresolved(word):
    """A write target this hook CANNOT resolve to a filesystem path.

    Two cases: (a) the word contains a shell expansion (`$VAR`, `$(...)`) or a
    backtick and this hook has no shell environment to expand it against;
    (b) the word opens a quote it does not close, i.e. the real target
    contains whitespace and this single word is only part of it.
    Origin 2026-09-12 field data: 9 of 12 denies were `$SP/x.py` scratch
    writes that the Write tool then performed freely — the gate was blocking
    a path it never actually knew to be project code.

    Deliberately quoting-agnostic: `'$SP/a.py'` is exempted too, a broader
    exemption than strictly necessary, because this hook does not model shell
    quoting and a false deny costs more than a missed heuristic here. The
    exemption is PER WORD, never command-wide — a literal code target in the
    same command still denies (see _decide's precedence).

    Quoted literal targets (`> 'src/app.py'`) are a known gap kept
    deliberately (P13: no field evidence for tightening) — a balanced-quoted
    word is classified raw, and the trailing quote means it is not a code
    file, exactly as in 2.10."""
    if "$" in word or "`" in word:
        return True
    if word[:1] in ("'", '"'):
        return not (len(word) >= 2 and word[-1] == word[0])
    if word[-1:] in ("'", '"'):
        # closes a quote it never opened: this word is only a FRAGMENT of the
        # real target (e.g. `"$(printf /repo)/x.py"` splits at the space
        # inside the substitution). Symmetric to the case above, and like it
        # strictly fail-open: it can only turn a deny into an allow.
        return True
    return False


def code_words(segment, cfg, cwd="", mask=None):
    """(literal code paths, unresolved words) for the in-place-editor scan:
    every code-extension match is first expanded to its whole shell word."""
    if mask is None:
        mask = protected_spans(segment)
    ext_alt = "|".join(re.escape(e) for e in cfg["code_extensions"])
    literal, unresolved_words, seen = [], [], set()
    for m in re.finditer(r"[^\s]*\.(?:%s)\b" % ext_alt, segment, re.I):
        word = expand_word(segment, m.start(), m.end(), mask)
        if word in seen:
            continue
        seen.add(word)
        if unresolved(word):
            unresolved_words.append(word)
        elif is_code_file(word, cfg, cwd):
            literal.append(word)
    return literal, unresolved_words


def _decide(payload, data):
    """Compute (decision, via, target, reason, context) without emitting."""
    if is_subagent(payload):
        return "allow", "subagent-exempt", None, None, None
    if gate_disabled_by_env():
        return "allow", "env-off", None, None, None

    cfg, cfg_status = load_config_with_status(data)
    if cfg_status == "malformed":
        # P4, origin 2026-09-21 audit item 1: never deny on a config we could
        # not parse. The one-time session note is SHARED with gate.py and
        # delegation_gate.py (hjw_common.malformed_note_once) — whichever
        # hook fires first emits it, so a session whose only tool call is a
        # Bash write still learns enforcement is off (origin 2026-09-21
        # review F2). An ABSENT config keeps the DEFAULT_CONFIG behavior.
        note = malformed_note_once(data, payload.get("session_id", "unknown"))
        return "allow", "config-malformed", None, None, note
    if not (cfg["gate"]["enabled"] and cfg["gate"]["bash_guard"]):
        return "allow", "gate-off", None, None, None

    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        return "allow", "no-command", None, None, None
    cwd = payload.get("cwd", "")

    cleaned = re.sub(r"2>&1", " ", command)
    cleaned = " ".join(cleaned.split())

    first_unresolved = None
    for segment in SEGMENT_SPLIT.split(cleaned):
        # 1) redirects / tee writing INTO a code file
        mask = protected_spans(segment)
        for pattern, label, via in ((REDIRECT, "output redirect", "redirect"),
                                    (TEE, "tee", "tee")):
            for m in pattern.finditer(segment):
                # re-expand from the captured start so a target spanning
                # protected whitespace stays ONE word
                word = expand_word(segment, m.start(1), m.start(1) + 1, mask)
                if unresolved(word):
                    # the raw word, verbatim (quotes included) — that's what
                    # the model typed and what an operator has to recognize
                    if first_unresolved is None:
                        first_unresolved = word
                    continue
                target = word
                if target != "/dev/null" and is_code_file(target, cfg, cwd):
                    return "deny", via, target, (
                        f"[haejwo gate] Bash {label} writes to a code file ({target}). "
                        f"The main agent must not modify code via Bash — use Edit/Write "
                        f"within the turn budget, or delegate to 'haejwo:default-worker'."
                    ), None
        # 2) in-place editors: explicit code-file target, OR fanned out via
        #    find/xargs where targets are invisible to regex (write intent).
        for pattern, label in (
            (SED_INPLACE, "sed -i"),
            (AWK_INPLACE, "awk -i inplace"),
            (PERL_INPLACE, "perl -i"),
        ):
            if pattern.search(segment):
                hits, skipped = code_words(segment, cfg, cwd, mask)
                if first_unresolved is None and skipped:
                    first_unresolved = skipped[0]
                if hits or FANOUT.search(segment):
                    shown = hits[:3] if hits else "files fanned out via find/xargs"
                    return "deny", ("inplace" if hits else "fanout"), \
                        (hits[0] if hits else None), (
                            f"[haejwo gate] Bash in-place edit ({label}) targets {shown}. "
                            f"The main agent must not modify code via Bash — use "
                            f"Edit/Write within budget, or delegate to 'haejwo:default-worker'."
                        ), None
    # Precedence: a literal code target always wins (it returned a deny
    # above); the unresolved exemption only applies when nothing literal did.
    if first_unresolved is not None:
        return "allow", "unresolved-target", first_unresolved, None, None
    return "allow", "ok", None, None, None


def main():
    payload = read_payload()
    if not payload:
        allow()

    root, data = paths(sys.argv)
    # Built defensively: a malformed payload must still produce a fail-open
    # RECORD, not a silent exit through the outer handler.
    record = {"v": 2, "hook": "bash_guard"}
    try:
        record.update({
            "agent_type": payload.get("agent_type"),
            "agent_id": payload.get("agent_id"),
            "sid": str(payload.get("session_id"))[:12],
        })
    except Exception:
        pass

    try:
        decision, via, target, reason, context = _decide(payload, data)
    except Exception:
        decision, via, target, reason, context = ("allow", "fail-open", None,
                                                  None, None)

    record["decision"] = decision
    record["via"] = via
    if target:
        record["target"] = str(target)[:200]
    # Observation failure must never change or block the decision.
    try:
        observe(data, record)
    except Exception:
        pass

    if decision == "deny":
        deny(reason)
    allow(context)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open
