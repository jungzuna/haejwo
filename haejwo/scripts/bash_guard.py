#!/usr/bin/env python3
"""haejwo bash-guard — PreToolUse on Bash.

The rule: the MAIN agent never modifies code files via Bash
(sed -i / echo > / tee / heredoc redirects...) — that's the classic gate
bypass. This hook heuristically denies such commands regardless of the
edit budget. Known residual gap (undetectable by regex, accepted for a
delegation gate): `python -c`, `node -e`, project scripts that write files.
The injected rules text covers those by instruction.

Observation record (v2): one record per well-formed call; malformed input
(an empty or non-JSON payload) exits before observing. The decision is
computed FIRST, then observed exactly once, then emitted — the audit trail can never claim an outcome the
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
  heredoc-data       allowed: the whole command is one `cat` writing a
                     quoted heredoc into a literal non-code file (below)
  unresolved-target  allowed: the only write target(s) were shell-expanded
                     (see below) and nothing literal was denied
  no-command         the payload carried no command
  ok                 allowed, nothing matched
  fail-open          an internal error — allowed, as always (P4)
`target` carries the offending/exempted target (<=200 chars) for the
redirect / tee / inplace / heredoc-data / unresolved-target cases: the stripped path for a
deny (what the deny text names), and for unresolved-target the FIRST
unresolved word seen, raw and verbatim.

Deferred on purpose (2.20): two false-positive
classes stay — a relative target after `cd` (origin 2026-10-03, project M field
session: `cd $SP/smoke && … && cat > smoke.mjs`, a scratchpad write
tested against the payload cwd) and redirect examples inside a heredoc body
or quoted string. Five relaxations were rejected in cross-vendor review, each
with a reproduced deny->allow regression (full record: the 2.20.0 commit
message, 22ba141): placing a relative path or classifying a heredoc body
needs the shell's parser, which this heuristic is not. Reopen only with field
evidence that a false positive costs more than one retry, and only with a
shape that leaves nothing else in the command unclassified.

Reopened for ONE shape (2.26, cold-loop cycle 2 S-F; field evidence: four
heredoc-body denies in one day, one costing two retries): the WHOLE command
is exactly `cat > T <<'D'` (`>>`, `<<"D"`, `<<-'D'` too; the heredoc may come
first), first line `[ \t]` between tokens and nothing else on it; T a plain
literal word (no quote, `$`, backtick, glob, `~`) that is not a code file by
the ordinary test; the body ends at the FIRST line the shell itself takes
as the delimiter (exact; `<<-` strips leading tabs), and only spaces/tabs
follow. Then the body is data and is not scanned. Any other command — a
second command on any line, an unquoted delimiter, a code-file target,
`tee`, an extra redirect, a file operand — runs the scan below unchanged.
Signed stop condition (reviewer): withdraw, never patch around, on any
reproduced allow outside this grammar, any previously denied executable
code-write becoming allowed, any other decision changing, or a new parser
dependency. Evidence: tests/test_hooks.py, the 54-row matrix and the
seeded differential fuzzer in the private notes (guard-fuzz-20261004).

Assignment resolution — withdrawn (2.32). Origin: field round 2 (project M, one
day): 42-45 of ~340 main-agent Bash records were `$E/...`, `$D/...`,
`$OUT/...` writes whose literal assignment sat in the same command
(`D=/repo; cat > $D/a.py`), all allowed as unresolved-target. A point-of-use
resolver was built and withdrawn under its own signed stop condition: Codex
reproduced two WRONG DENIES, `declare -n D=T; D=/repo/a.py; T=/dev/null;
printf x > $D` and `D=/repo/a.py; trap 'D=/dev/null' DEBUG; printf x > $D`
(bash writes /dev/null in both). Reopen only with a shape that models
variable attributes and traps — a real shell parser, which this heuristic is
not; never with a blacklist of the counterexamples.

Outward note (2.32). A main-agent `git push` attempt (command word `git` or
`*/git` after `NAME=VALUE` words, a few reserved/wrapper words and `env`'s
options; git global options skipped; first non-option word `push`; no
`--dry-run` and no short option cluster containing `n`, e.g. `-n`, `-fn`)
in a command whose decision is ALLOW (ok, unresolved-target, heredoc-data;
never env-off, gate-off or config-malformed: haejwo is off there and
report_check does not run) sets the per-turn `outward_attempt` flag in the
session state (skipped when the state lock is not taken), appends a SECOND
record `via: outward-attempt` (target `git push`) after the decision record,
and adds the record-line note to the allow context — never a permission
decision. A deny anywhere in the command wins: no flag, no record, no note.
Quote-aware: `echo git push` and `printf "%s" "git push"` do not match.
Heredocs (field round 3; project M session e1ffebe1, 2026-10-22: 14 push-bearing
commands, 4 detected, 10 missed): literal body text of a quoted-delimiter heredoc (`<<'EOF'`,
`<<"EOF"`, `<<\\EOF`, `<<-`) is never classified as a push, and commands
after its terminator line are. Known misses (fail-open): an unquoted-delimiter
body and everything after it (the scan ends at its operator line: the body
may join lines with backslash-newline and expand `$(...)`), continued or
multiline heredoc headers, `$(...)` inside bodies, `EOF)` terminators inside
substitutions, `sudo git push` and other wrappers not named in
_PUSH_PREFIX_WORDS.

Bundle B (2.33.0, field round 3): the outward-attempt state write also keeps
the turn's `outward_calls` / `outward_ids` (tool_use ids for display, cap 8)
and the session dedupe list `outward_seen` (cap 64; the session counter
`push_calls` was removed with the 2.33.0 checklist in 2.34.0) — no command
text is stored; post_bash.py and report_check.py read them.
`review_runner_invoked` (the same segment grammar, plus `timeout
<duration>`; setup probes marked `HJW_PROBE=` excluded) names a
reviewer-runner invocation for post_bash.py; it never affects a decision
here.
"""
import os
import re
import shlex
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    CODEX_SPAWN_WORKER, allow, carry_session_flags, command_name,
    config_ignored_note_once, deny, gate_disabled_by_env, is_code_file,
    is_subagent, load_config_with_status, load_state, malformed_note_once,
    SESSION_IDS_CAP, observe, on_codex_host, paths, read_payload, save_state,
    state_lock, with_note,
)
from gate import STALE_TURN_SECONDS  # noqa: E402  same turn reset as gate.py

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
# heredoc-data (header): the first line of the whole-command shape, both
# operand orders. Groups: T, then `-` of `<<-`, then the delimiter (one of
# two alternatives). ASCII-only classes on purpose: `\s`/`\w` would accept
# `\r` and non-ASCII (the 2.20 `cat\r` finding).
_HD_T = r"([A-Za-z0-9_./@%+,:=-]+)"
_HD_OP = r"<<(-?)(?:'([A-Za-z0-9_]+)'|\"([A-Za-z0-9_]+)\")"
HEREDOC_DATA_HEADS = (
    re.compile(r"[ \t]*cat[ \t]+>>?[ \t]*" + _HD_T + r"[ \t]+" + _HD_OP + r"[ \t]*"),
    re.compile(r"[ \t]*cat[ \t]+" + _HD_OP + r"[ \t]+>>?[ \t]*" + _HD_T + r"[ \t]*"),
)


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
    if not cfg["code_extensions"]:
        return [], []  # nothing counts as code (hjw_common._validate_types)
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


def heredoc_data_target(command, cfg, cwd=""):
    """T when the WHOLE raw command is the header's heredoc-data shape, else
    None. Runs on the RAW command (before any normalization), and the body
    ends at the first line the shell itself ends it on, so no executed line
    is ever treated as data."""
    first, nl, rest = command.partition("\n")
    if not nl:
        return None
    for order, head in enumerate(HEREDOC_DATA_HEADS):
        m = head.fullmatch(first)
        if m:
            break
    else:
        return None
    if order == 0:
        target, dash, delim = m.group(1), m.group(2), m.group(3) or m.group(4)
    else:
        dash, delim, target = m.group(1), m.group(2) or m.group(3), m.group(4)
    lines = rest.split("\n")
    for k, line in enumerate(lines):
        if (line.lstrip("\t") if dash else line) == delim:
            if any(after.strip(" \t") for after in lines[k + 1:]):
                return None
            break
    else:
        return None  # no delimiter line: not the shape
    if is_code_file(target, cfg, cwd):
        return None
    return target


# --- Outward note (2.32; module docstring) ----------------------------------
OUTWARD_NOTE = ("[haejwo] outward action requested (git push): the report "
                "carries `Outward actions: <repo; branch; action; result> - "
                "authorized by: <user words, time>`.")
_ASSIGN_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_PUSH_PREFIX_WORDS = frozenset({"!", "{", "if", "then", "do", "else", "elif",
                                "while", "until", "time", "command", "exec",
                                "nohup"})
_ENV_OPTS_WITH_ARG = frozenset({"-u", "--unset", "-C", "--chdir", "-S",
                                "--split-string"})
_GIT_OPTS_WITH_ARG = frozenset({"-C", "-c", "--git-dir", "--work-tree",
                                "--namespace", "--super-prefix", "--config-env"})
_DRY_RUN_CLUSTER = re.compile(r"-[A-Za-z]*n[A-Za-z]*")


def _odd_backslashes_before(text, k):
    j = k
    while j > 0 and text[j - 1] == "\\":
        j -= 1
    return (k - j) % 2 == 1


def _comment_starts(text, i):
    """True when the unquoted `#` at `i` starts a comment: the previous
    significant character is a word boundary (none, unescaped whitespace or
    one of `;|&()`). A backslash-newline before it is a removed line
    continuation and is skipped (`: \\<newline># x` is a comment); an
    escaped space is a word character (`a\\ #x` is not). Callers only
    reach it outside quotes."""
    k = i - 1
    while k >= 1 and text[k] == "\n" and _odd_backslashes_before(text, k):
        k -= 2
    if k < 0:
        return True
    return ((text[k].isspace() or text[k] in ";|&()")
            and not _odd_backslashes_before(text, k))


def _shell_segments(text):
    """Split at `; | & ( )` and newlines OUTSIDE quotes; a word-start `#`
    comments out the rest of its line."""
    segs, cur, quote, i, n = [], [], None, 0, len(text)
    while i < n:
        ch = text[i]
        if quote == "'":
            cur.append(ch)
            if ch == "'":
                quote = None
        elif ch == "\\" and i + 1 < n:
            cur.append(text[i:i + 2])
            i += 1
        elif quote == '"':
            cur.append(ch)
            if ch == '"':
                quote = None
        elif ch in "'\"":
            quote = ch
            cur.append(ch)
        elif ch == "#" and _comment_starts(text, i):
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        elif ch in ";|&()\n":
            segs.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    segs.append("".join(cur))
    return segs


_TIMEOUT_OPTS_WITH_ARG = frozenset({"-s", "--signal", "-k", "--kill-after"})


def _command_word_index(words, timeout=False):
    """Index of the command word: past `NAME=VALUE` words, the reserved and
    wrapper words in _PUSH_PREFIX_WORDS and `env` with its options; with
    `timeout`, also past `timeout`, its options and its duration (the
    review-runner matcher only — the push matcher never skipped it)."""
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        if _ASSIGN_WORD.match(w) or w in _PUSH_PREFIX_WORDS:
            i += 1
        elif w == "env":
            i += 1
            while i < n and words[i].startswith("-"):
                i += 2 if words[i] in _ENV_OPTS_WITH_ARG else 1
        elif timeout and w == "timeout":
            i += 1
            while i < n and words[i].startswith("-"):
                i += 2 if words[i] in _TIMEOUT_OPTS_WITH_ARG else 1
            i += 1  # the duration
        else:
            break
    return i


def _is_push(words):
    n = len(words)
    i = _command_word_index(words)
    if i >= n or not (words[i] == "git" or words[i].endswith("/git")):
        return False
    j = i + 1
    while j < n and words[j].startswith("-"):
        j += 2 if words[j] in _GIT_OPTS_WITH_ARG else 1
    if j >= n or words[j] != "push":
        return False
    return not any(a == "--dry-run" or _DRY_RUN_CLUSTER.fullmatch(a)
                   for a in words[j + 1:])


_HEREDOC_WORD_END = frozenset(" \t\n;|&()<>")


def _heredoc_delimiter(text, i):
    """(value, quoted, end) for the delimiter word starting at `i`, or None
    when this heuristic does not model it (empty word, `$`/backtick, a quote
    left open). Quote removal only: bash expands nothing in a delimiter."""
    val, quoted, quote, j, n = [], False, None, i, len(text)
    while j < n:
        ch = text[j]
        if ch == "\n" and quote:
            return None  # a delimiter spanning lines: not modeled
        if quote == "'":
            if ch == "'":
                quote = None
            else:
                val.append(ch)
        elif quote == '"':
            if ch == '"':
                quote = None
            elif ch == "\\" and j + 1 < n and text[j + 1] in '"\\$`':
                val.append(text[j + 1])
                j += 1
            else:
                val.append(ch)
        elif ch in "$`":
            return None
        elif ch in "'\"":
            quote, quoted = ch, True
        elif ch == "\\" and j + 1 < n and text[j + 1] != "\n":
            quoted = True
            val.append(text[j + 1])
            j += 1
        elif ch in _HEREDOC_WORD_END or ch == "\\":
            break
        else:
            val.append(ch)
        j += 1
    if (quote is not None or not val or "$" in text[i:j]
            or "`" in text[i:j]):
        return None  # open quote, empty delimiter (`<<''`), expansion
    return "".join(val), quoted, j


def _skip_quoted_heredocs(text):
    """`text` with the body and terminator line of every QUOTED-delimiter
    heredoc (`<<'D'`, `<<"D"`, `<<\\D`, `<<-` too) removed; the operator line
    and its newline stay, so an executable opening line is still scanned.
    Same quote/escape/comment tracking as `_shell_segments`; `<<<` is not an
    operator. A body ends at the first later physical line equal to the
    delimiter (`<<-`: after stripping leading tabs only; no CR or space
    normalization); several heredocs on one line take their bodies in order.
    The scan ENDS (the rest is never classified, fail-open) at the end of the
    line of an UNQUOTED delimiter (its body may join lines and expand
    `$(...)`), at an unterminated quoted body, at a delimiter this does not
    model, and at a header continued past its line (a newline inside quotes
    or after a backslash once an operator was seen)."""
    out, quote, i, n = [], None, 0, len(text)
    pending, stop = [], False  # quoted (delimiter, dash) on this line
    while i < n:
        ch = text[i]
        if ch == "\n" and (pending or stop) and (quote or stop):
            break
        if quote == "'":
            out.append(ch)
            if ch == "'":
                quote = None
        elif ch == "\\" and i + 1 < n:
            if text[i + 1] == "\n" and (pending or stop):
                break
            out.append(text[i:i + 2])
            i += 1
        elif quote == '"':
            out.append(ch)
            if ch == '"':
                quote = None
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        elif ch == "#" and _comment_starts(text, i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(text[i:j])
            i = j
            continue
        elif text.startswith("<<<", i):
            out.append("<<<")
            i += 3
            continue
        elif text.startswith("<<", i):
            dash = text.startswith("<<-", i)
            j = i + (3 if dash else 2)
            while j < n and text[j] in " \t":
                j += 1
            word = _heredoc_delimiter(text, j)
            if word is not None:
                j = word[2]
            if word is None or not word[1]:
                stop = True  # unquoted or unmodeled: end at this line
            else:
                pending.append((word[0], dash))
            out.append(text[i:j])
            i = j
            continue
        elif ch == "\n" and pending:
            out.append("\n")
            pos = i + 1
            for delim, dash in pending:
                while True:
                    if pos >= n:
                        return "".join(out)  # unterminated: the scan ends
                    nl = text.find("\n", pos)
                    end = n if nl == -1 else nl
                    line = text[pos:end]
                    pos = end + 1
                    if (line.lstrip("\t") if dash else line) == delim:
                        break
            pending = []
            i = pos
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def git_push_attempt(command):
    """True when some shell segment of `command` is a `git push` attempt.

    Guarantee: literal body text of a quoted-delimiter heredoc is never
    classified as a push; commands after its terminator line are
    (`_skip_quoted_heredocs`). Known misses (fail-open): an unquoted-delimiter
    heredoc's body and everything after it, a continued or multiline heredoc
    header, `$(...)` inside a body, an `EOF)` terminator inside a
    substitution, and wrappers not in _PUSH_PREFIX_WORDS (`sudo git push`)."""
    for seg in _shell_segments(_skip_quoted_heredocs(command)):
        try:
            words = shlex.split(seg)
        except ValueError:
            continue
        if _is_push(words):
            return True
    return False


REVIEW_RUNNERS = ("codex_consult.sh", "claude_consult.sh")
# The setup-probe marker (2.34.0, plan ledger #5): an assignment word
# `HJW_PROBE=<anything>` before a runner's command word marks a probe call.
PROBE_MARKER = "HJW_PROBE="
# Words after which a `NAME=VALUE` word is still a shell assignment (bash
# reserved words, recognized only at the start of a simple command).
_RESERVED_WORDS = frozenset({"!", "{", "if", "then", "do", "else", "elif",
                             "while", "until", "time"})
# GNU coreutils env options. Long options: name -> True when it takes a
# REQUIRED operand (the next word unless spelled `--opt=value`); the signal
# options take an optional `=SIG` only, never the next word. getopt_long
# accepts any unambiguous prefix (`--chd` = `--chdir`). Short options: the
# letters that take no operand; any other letter is an operand letter
# (`uCSa`) or unknown. An operand is never an assignment, whatever it looks
# like.
_ENV_LONG_OPTS = {"unset": True, "chdir": True, "split-string": True,
                  "argv0": True, "ignore-environment": False, "null": False,
                  "debug": False, "block-signal": False,
                  "default-signal": False, "ignore-signal": False,
                  "list-signal-handling": False, "help": False,
                  "version": False}
_ENV_SHORT_FLAGS = "i0v"


def _env_long_takes_next(opt):
    """True when the `--name` word `opt` (no `=`) consumes the next word:
    the long option it names (exact, or an unambiguous prefix as
    getopt_long resolves it) takes a required operand. An unknown or
    ambiguous name is treated as consuming it too — conservative: the next
    word is never read as a probe marker (a wrong guess keeps the note)."""
    name = opt[2:]
    if name in _ENV_LONG_OPTS:
        return _ENV_LONG_OPTS[name]
    hits = [k for k in _ENV_LONG_OPTS if k.startswith(name)] if name else []
    return _ENV_LONG_OPTS[hits[0]] if len(hits) == 1 else True


def _probe_marked(words, end):
    """True when a word before `end` (the command word's index) is
    `HJW_PROBE=<anything>` in an ASSIGNMENT role: a leading bare `NAME=VALUE`
    prefix word (at the start of the simple command or after a reserved
    word), or a `NAME=VALUE` word `env` reads as an assignment once its
    options and their operands (`-u NAME`, `-C dir`, `-S str`, `-a name`,
    attached, `--opt=value` and abbreviated `--chd dir` forms, `--`) are
    consumed (_env_long_takes_next; an unknown option letter or name is
    taken to consume the next word, so that word is never a marker). An
    option operand (`env -C HJW_PROBE=1 …`, `env --chd HJW_PROBE=1 …`)
    is not a marker; once a split-string option appears in any spelling
    (`env -S …`, `--spl=-C …`) no later word is one either, since the split
    can change every later word's role. Nor is a `NAME=VALUE` word after `nohup`, `command`, `exec` or
    `timeout <duration>` — the shell does not assign it; that word is their
    command. Separate from _command_word_index on purpose: the push grammar
    stays unchanged. Never raises."""
    try:
        i, assign_ok, fresh = 0, True, True
        while i < end:
            w = words[i]
            if _ASSIGN_WORD.match(w):
                if assign_ok and w.startswith(PROBE_MARKER):
                    return True
                fresh = False
                i += 1
            elif w in _RESERVED_WORDS and fresh:
                i += 1
            elif w == "env":
                assign_ok, fresh = True, False
                i += 1
                while i < end and words[i].startswith("-"):
                    o = words[i]
                    i += 1
                    if o == "--":
                        break
                    # split-string (-S, a cluster holding S, --split-string
                    # or any prefix of it, with or without =) can rewrite
                    # the role of every later word: nothing after it is a
                    # marker (a kept note is the safe failure)
                    if (o.startswith("--") and len(o) > 2
                            and "split-string".startswith(
                                o[2:].split("=", 1)[0])) \
                            or (not o.startswith("--") and "S" in o):
                        return False
                    if o.startswith("--"):
                        if "=" not in o and _env_long_takes_next(o):
                            i += 1  # its operand
                    else:
                        # a short cluster: the first letter that is not a
                        # known flag (an operand letter, or unknown:
                        # conservative) takes the rest of the word, or the
                        # next word when it ends the cluster
                        for k, ch in enumerate(o[1:], 1):
                            if ch not in _ENV_SHORT_FLAGS:
                                if k == len(o) - 1:
                                    i += 1
                                break
            else:
                assign_ok, fresh = False, False
                i += 1
    except Exception:
        pass
    return False


def review_runner_invoked(command):
    """The runner basename when some shell segment of `command` invokes a
    reviewer runner, else None (field round 3 bundle B; post_bash.py).

    A segment invokes one when its command word (`_command_word_index` with
    `timeout`: past `NAME=VALUE` words, `nohup` and the other prefix words,
    `env` with its options, `timeout` with its options and duration) has
    basename `codex_consult.sh` or `claude_consult.sh` and no word of the
    segment is `--help`. A segment carrying `HJW_PROBE=<anything>` as an
    assignment before the command word (_probe_marked: a bare prefix
    assignment or an `env` assignment, never an option operand) is a setup
    probe (/haejwo:setup) and does not count: no note, no id claim. The
    marker is read from syntax only, never by substring, so a probe brief's
    text or an `HJW_PROBE=` after the command word does not exempt a call.
    Quoted-delimiter heredoc bodies are skipped exactly
    as git_push_attempt skips them. Known misses (no line, fail-open):
    `bash -c '...'`, an interpreter word (`bash <path>/codex_consult.sh`),
    `sudo` and other wrappers not skipped above, and the push matcher's
    heredoc misses."""
    try:
        for seg in _shell_segments(_skip_quoted_heredocs(command)):
            try:
                words = shlex.split(seg)
            except ValueError:
                continue
            i = _command_word_index(words, timeout=True)
            if _probe_marked(words, i):
                continue  # a setup probe: not a review call
            if i < len(words) and "--help" not in words:
                base = os.path.basename(words[i])
                if base in REVIEW_RUNNERS:
                    return base
    except Exception:
        pass
    return None


OUTWARD_IDS_CAP = 8


def record_outward_attempt(payload, data, record):
    """Per-turn flag (under the session lock; skipped when not taken) and the
    outward-attempt record. Never raises.

    Bundle B (2.33.0): the same state write also keeps, on the FIRST sight
    of a tool_use_id, `outward_calls` +1 (turn) and the id in `outward_ids`
    (turn, display only, cap OUTWARD_IDS_CAP).
    First sight is decided by the session list `outward_seen` (cap
    SESSION_IDS_CAP; it stops growing there, so a later id is counted on
    every replay — no dedupe — and post_bash.py gives it no return note);
    an id already in `outward_seen` is a replay and changes no count (diff
    review D2: the capped display list was the dedupe set, so a replayed 9th
    id counted twice). No id: always counted. No command text
    is stored. The turn boundary is applied before the save, as in gate.py,
    so refreshing `updated_at` here is gate.py's own semantics."""
    sid = payload.get("session_id", "unknown")
    tuid = payload.get("tool_use_id")
    tuid = tuid if isinstance(tuid, str) and tuid else None
    try:
        with state_lock(data, sid) as lk:
            if lk.acquired:
                state = load_state(data, sid)
                # the same turn boundary gate.py applies (gate._decide_loaded)
                pid = payload.get("prompt_id") or payload.get("turn_id")
                if pid and state.get("prompt_id") != pid:
                    state = carry_session_flags(state, {"prompt_id": pid, "files": []})
                elif (state.get("updated_at")
                      and time.time() - state["updated_at"] > STALE_TURN_SECONDS):
                    state = carry_session_flags(state, {"prompt_id": pid, "files": []})
                state["outward_attempt"] = True
                ids, seen = ([x for x in v if isinstance(x, str)]
                             if isinstance(v, list) else []
                             for v in (state.get("outward_ids"),
                                       state.get("outward_seen")))
                if tuid is None or tuid not in seen:
                    if tuid is not None:
                        if len(seen) < SESSION_IDS_CAP:
                            seen.append(tuid)
                        if len(ids) < OUTWARD_IDS_CAP and tuid not in ids:
                            ids.append(tuid)
                    state["outward_ids"] = ids
                    state["outward_seen"] = seen
                    cur = state.get("outward_calls")
                    state["outward_calls"] = (cur if isinstance(cur, int)
                                              and not isinstance(cur, bool)
                                              and cur >= 0 else 0) + 1
                save_state(data, sid, state)
    except Exception:
        pass
    try:
        rec = {k: record[k] for k in ("v", "hook", "agent_type", "agent_id", "sid")
               if k in record}
        rec.update({"decision": "allow", "via": "outward-attempt",
                    "target": "git push"})
        observe(data, rec)
    except Exception:
        pass


def _decide(payload, data, root=""):
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
        note = malformed_note_once(data, payload.get("session_id", "unknown"),
                                   on_codex_host(root, data))
        return "allow", "config-malformed", None, None, note
    # cold-loop B2: wrong-typed values fell back to defaults at load; said
    # once per session, on whatever this call emits.
    note = config_ignored_note_once(
        data, payload.get("session_id", "unknown"), cfg)
    decision, via, target, reason, context = _decide_loaded(
        payload, cfg, on_codex_host(root, data))
    context, reason = with_note(note, context, reason)
    return decision, via, target, reason, context


def _decide_loaded(payload, cfg, on_codex):
    """_decide past the config load: the guard decision proper."""
    # Codex spells the commands as skills (cold-loop F11) and delegates via
    # spawn_agent — its manifest ships no agents (cycle 3 F4); Claude text as
    # was.
    override = command_name("gate off", on_codex)
    worker = CODEX_SPAWN_WORKER if on_codex else "'haejwo:default-worker'"
    edit_tools = "apply_patch" if on_codex else "Edit/Write"  # cycle 3 F16
    if not (cfg["gate"]["enabled"] and cfg["gate"]["bash_guard"]):
        return "allow", "gate-off", None, None, None

    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        return "allow", "no-command", None, None, None
    cwd = payload.get("cwd", "")

    data_target = heredoc_data_target(command, cfg, cwd)
    if data_target is not None:
        return "allow", "heredoc-data", data_target, None, None

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
                        f"Bash code edits signal delegation. Delegate project-exercising "
                        f"scripts, scratch implementations and other delegated work to "
                        f"{worker}. For eligible direct work (never scratch, probe or "
                        f"project-exercising scripts), use {edit_tools} within the "
                        f"turn budget. Emergency override: {override}."
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
                # An empty code_extensions means nothing counts as code
                # (incl. an invalid list, cycle 3 B2): no fan-out deny either.
                if hits or (FANOUT.search(segment) and cfg["code_extensions"]):
                    shown = hits[:3] if hits else "files fanned out via find/xargs"
                    return "deny", ("inplace" if hits else "fanout"), \
                        (hits[0] if hits else None), (
                            f"[haejwo gate] Bash in-place edit ({label}) targets {shown}. "
                            f"Bash code edits signal delegation. Delegate "
                            f"project-exercising scripts, scratch implementations and "
                            f"other delegated work to {worker}. For eligible direct work "
                            f"(never scratch, probe or project-exercising "
                            f"scripts), use {edit_tools} within the turn budget. "
                            f"Emergency override: {override}."
                        ), None
    # Precedence: a literal code target always wins (it returned a deny
    # above); the unresolved exemption only applies when nothing literal did.
    if first_unresolved is not None:
        return "allow", "unresolved-target", first_unresolved, None, None
    return "allow", "ok", None, None, None


def main():
    os.umask(0o077)  # cold-loop F15: state and observations are per-user
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
        decision, via, target, reason, context = _decide(payload, data, root)
    except Exception:
        decision, via, target, reason, context = ("allow", "fail-open", None,
                                                  None, None)

    record["decision"] = decision
    record["via"] = via
    if target:
        record["target"] = str(target)[:200]
    # Observation failure must never change or block the decision.
    try:
        observe(data, dict(record))
    except Exception:
        pass

    # Outward note: main-agent allows only (a deny wins). Not on
    # subagent-exempt / fail-open, nor on env-off (the emergency hatch:
    # haejwo is off), gate-off or config-malformed (report_check.py exits
    # silently there, so the note would promise a check that never runs).
    if decision == "allow" and via not in ("subagent-exempt", "fail-open", "env-off",
                                           "gate-off", "config-malformed"):
        try:
            command = (payload.get("tool_input") or {}).get("command") or ""
            if isinstance(command, str) and git_push_attempt(command):
                record_outward_attempt(payload, data, record)
                context, _ = with_note(OUTWARD_NOTE, context)
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
