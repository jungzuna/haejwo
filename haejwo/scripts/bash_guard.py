#!/usr/bin/env python3
"""haejwo bash-guard — PreToolUse on Bash.

The rule: the MAIN agent never modifies code files via Bash
(sed -i / echo > / tee / heredoc redirects...) — that's the classic gate
bypass. This hook heuristically denies such commands regardless of the
edit budget. Known residual gap (undetectable by regex, accepted for a
delegation gate): `python -c`, `node -e`, project scripts that write files.
The injected rules text covers those by instruction.
"""
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from hjw_common import (  # noqa: E402
    allow, deny, gate_disabled_by_env, is_code_file, is_subagent,
    load_config, observe, paths, read_payload,
)

SEGMENT_SPLIT = re.compile(r"\|\||&&|;|\|")
# capture redirect targets:  > file  >> file  2> file  &> file
REDIRECT = re.compile(r"(?:^|\s)(?:\d?>>?|&>)\s*([^\s;|&<>]+)")
TEE = re.compile(r"\btee\b\s+(?:-\w+\s+)*([^\s;|&]+)")
SED_INPLACE = re.compile(r"\bsed\b[^|;&]*(?:\s-\w*i\w*\b|--in-place)")
AWK_INPLACE = re.compile(r"\bawk\b[^|;&]*-i\s*inplace")
PERL_INPLACE = re.compile(r"\bperl\b[^|;&]*\s-\w*i")
# fan-out executors: `find ... -exec sed -i` / `xargs sed -i` edit files whose
# names never appear in the command — treat in-place editor + fan-out as write
# intent even without a visible code-file token.
FANOUT = re.compile(r"\b(?:find|xargs)\b")


def code_tokens(segment, cfg, cwd=""):
    ext_alt = "|".join(re.escape(e) for e in cfg["code_extensions"])
    return [
        t for t in re.findall(r"[^\s;|&'\"()]+\.(?:%s)\b" % ext_alt, segment, re.I)
        if is_code_file(t, cfg, cwd)
    ]


def main():
    payload = read_payload()
    if not payload:
        allow()

    root, data = paths(sys.argv)
    observe(data, {
        "hook": "bash_guard",
        "agent_type": payload.get("agent_type"),
        "agent_id": payload.get("agent_id"),
        "sid": str(payload.get("session_id"))[:12],
    })

    if is_subagent(payload):
        allow()
    if gate_disabled_by_env():
        allow()

    cfg = load_config(data)
    if not (cfg["gate"]["enabled"] and cfg["gate"]["bash_guard"]):
        allow()

    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        allow()
    cwd = payload.get("cwd", "")

    cleaned = re.sub(r"2>&1", " ", command)
    cleaned = " ".join(cleaned.split())

    for segment in SEGMENT_SPLIT.split(cleaned):
        # 1) redirects / tee writing INTO a code file
        for pattern, label in ((REDIRECT, "output redirect"), (TEE, "tee")):
            for target in pattern.findall(segment):
                if target != "/dev/null" and is_code_file(target, cfg, cwd):
                    deny(
                        f"[haejwo gate] Bash {label} writes to a code file ({target}). "
                        f"The main agent must not modify code via Bash — use Edit/Write "
                        f"within the turn budget, or delegate to 'haejwo:default-worker'."
                    )
        # 2) in-place editors: explicit code-file target, OR fanned out via
        #    find/xargs where targets are invisible to regex (write intent).
        for pattern, label in (
            (SED_INPLACE, "sed -i"),
            (AWK_INPLACE, "awk -i inplace"),
            (PERL_INPLACE, "perl -i"),
        ):
            if pattern.search(segment):
                hits = code_tokens(segment, cfg, cwd)
                if hits or FANOUT.search(segment):
                    shown = hits[:3] if hits else "files fanned out via find/xargs"
                    deny(
                        f"[haejwo gate] Bash in-place edit ({label}) targets {shown}. "
                        f"The main agent must not modify code via Bash — use "
                        f"Edit/Write within budget, or delegate to 'haejwo:default-worker'."
                    )
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open
