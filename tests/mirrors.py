#!/usr/bin/env python3
"""Codex-skill mirrors of haejwo's commands: check or regenerate.

  python3 tests/mirrors.py            print drift, exit 1 on any
  python3 tests/mirrors.py --write    regenerate every mirror from its command
  (optional trailing args: <commands_dir> <codex_skills_dir>)

A mirror `codex-skills/haejwo-<name>/SKILL.md` is its command
`commands/<name>.md` with the frontmatter rewritten for the Codex host (a
`name:` line, the same `description:` line, no `argument-hint:`) and a
do-not-edit banner; the body is the command's with ONE host substitution:
every `/haejwo:<cmd>` reference becomes `@haejwo-<cmd>`, the Codex skill name
(never inside a URL).
`${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}` and `$ARGUMENTS` are NOT
substituted (their Codex behaviour is unmeasured; the commands handle an
unresolved placeholder themselves). Drift is a differing mirror,
a missing mirror, or an ORPHAN mirror with no command. `--write` never deletes
an orphan: it reports it and exits 1, and the maintainer removes it.
The test suite's drift canary calls `drift()` from here — never `--write`.

Files are read and written in BINARY mode and compared as bytes: text mode
translates line endings, so a CRLF-only drift would compare equal. Because
the canary and the generator share `expected_mirror()`, the suite ALSO pins
it against an independent fixture (tests/fixtures/mirror-gate/: a frozen
command and its byte-exact expected mirror) — a generator bug cannot
validate its own output.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "haejwo")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
# A slash command at a token start; a path or agent name (`x/haejwo:y`, `haejwo:default-worker`) is not one.
SLASH_CMD = re.compile(r"(?<![\w./~-])/haejwo:([a-z][a-z0-9-]*)")


def rewrite_commands(text):
    """Every `/haejwo:<cmd>` -> `@haejwo-<cmd>`, except inside a URL: a match
    whose whitespace-delimited token has a `://` before it is left as written
    (`https://[::1]/haejwo:setup`, `?next=/haejwo:plan`)."""
    def sub(m):
        i = m.start()
        while i > 0 and not text[i - 1].isspace():
            i -= 1
        return m.group(0) if "://" in text[i:m.start()] else "@haejwo-" + m.group(1)
    return SLASH_CMD.sub(sub, text)


def expected_mirror(name, cmd_text):
    m = FRONTMATTER.match(cmd_text)
    if not m:
        raise ValueError("commands/%s.md: no frontmatter" % name)
    desc = [l for l in m.group(1).split("\n") if l.startswith("description:")]
    if len(desc) != 1:
        raise ValueError("commands/%s.md: expected exactly one description: line" % name)
    return ("---\nname: haejwo-%s\n%s\n---\n\n"
            "<!-- MIRROR of commands/%s.md for the Codex host — do not edit by hand;\n"
            "     edit commands/%s.md and run `python3 tests/mirrors.py --write`.\n"
            "     Drift is canary-tested. -->\n\n"
            % (name, desc[0], name, name)) + rewrite_commands(cmd_text[m.end():])


def expected_mirror_bytes(name, cmd_bytes):
    """expected_mirror() over raw bytes: no newline translation either way."""
    return expected_mirror(name, cmd_bytes.decode("utf-8")).encode("utf-8")


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _commands(commands_dir):
    return sorted(fn[:-3] for fn in os.listdir(commands_dir) if fn.endswith(".md"))


def _skill_path(skills_dir, name):
    return os.path.join(skills_dir, "haejwo-%s" % name, "SKILL.md")


def drift(commands_dir=None, skills_dir=None):
    """Every problem as one line; an empty list means no drift."""
    commands_dir = commands_dir or os.path.join(PLUGIN, "commands")
    skills_dir = skills_dir or os.path.join(PLUGIN, "codex-skills")
    problems = []
    names = _commands(commands_dir)
    for name in names:
        want = expected_mirror_bytes(name, _read(os.path.join(commands_dir, name + ".md")))
        path = _skill_path(skills_dir, name)
        if not os.path.isfile(path):
            problems.append("missing mirror: codex-skills/haejwo-%s/SKILL.md" % name)
            continue
        if _read(path) != want:
            problems.append("drift: codex-skills/haejwo-%s/SKILL.md differs from commands/%s.md"
                            % (name, name))
    wanted = set("haejwo-%s" % n for n in names)
    if os.path.isdir(skills_dir):
        for entry in sorted(os.listdir(skills_dir)):
            if entry not in wanted:
                problems.append("orphan mirror: codex-skills/%s has no command" % entry)
    return problems


def write(commands_dir=None, skills_dir=None):
    commands_dir = commands_dir or os.path.join(PLUGIN, "commands")
    skills_dir = skills_dir or os.path.join(PLUGIN, "codex-skills")
    for name in _commands(commands_dir):
        data = expected_mirror_bytes(name, _read(os.path.join(commands_dir, name + ".md")))
        path = _skill_path(skills_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)


def main(argv):
    do_write = "--write" in argv
    rest = [a for a in argv if a != "--write"]
    if len(rest) not in (0, 2):
        sys.stderr.write("usage: mirrors.py [--write] [<commands_dir> <codex_skills_dir>]\n")
        return 2
    dirs = rest or [None, None]
    if do_write:
        write(*dirs)
    problems = drift(*dirs)
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
