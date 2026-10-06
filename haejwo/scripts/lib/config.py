#!/usr/bin/env python3
"""Config reading for haejwo's two reviewer runners.

  values  <codex|claude> <config.json>   model / effort
  sandbox <config.json>                  codex.consult_sandbox, raw
  status  <config.json>                  ok | absent | malformed

HOST-RELATIVE reading: the `codex` block describes the reviewer of the HOST
that owns the data dir. On a CODEX host (config path under /.codex/) that
reviewer is CLAUDE, so the codex runner IGNORES codex.model/effort
there and the claude runner reads them; under /.claude/ it is
the other way round. That "only under /.codex/" rule covers VENDOR paths only
— a vendorless custom root is decided by runner kind instead (see below). The
host is decided from the SELECTED path's TEXT, never from its canonical target
— the caller chose that path and the caller's choice is what names the host.
*[origin: a live smoke launched the claude reviewer with the codex host's own
model name]*

CUSTOM PLUGIN ROOTS: a path carrying neither /.codex/ nor /.claude/ (an
install under a plugin root of the operator's own choosing) names no vendor,
and structural ownership alone does not establish one. There the host follows
the RUNNER KIND, which is the one thing still known: codex_consult.sh is the
reviewer of a Claude host, claude_consult.sh the reviewer of a Codex host —
so each runner reads the `codex` block that describes it, exactly as it would
on the matching vendor path. *[origin: 2.17 config-ownership round]*

The sandbox is read on EITHER host: it describes how THIS runner is launched,
not which vendor the model keys belong to. It is printed RAW — the caller's
allowlist decides, so the transport can never trim an exotic value into a
valid one. The model/effort values are whitespace-FOLDED (config values are
tolerant); an empty ENV value counts as unset, but that trimming belongs to
the caller.

Any parse failure is "no config" — a reviewer runner never guesses. Unknown
keys are skipped, so a stored `fallback_model` (retry removed in 2.22) is
ignored silently. NOT read here: `models_codex` belongs to codex-HOST worker tiers (spawn_agent
parameters) and never selects this reviewer.
"""
import json
import os
import sys

KEYS = ("model", "effort")


def _codex_block(path):
    with open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    return cfg.get("codex") if isinstance(cfg, dict) else None


def _is_codex_host(path, kind):
    if "/.codex/" in path:
        return True
    if "/.claude/" in path:
        return False
    # Custom plugin root: no vendor in the path, so the runner kind names the
    # host it is the reviewer FOR (see the module docstring).
    return kind == "claude"


def cmd_values(argv):
    kind, path = argv[0], argv[1]
    is_codex_host = _is_codex_host(path, kind)
    wanted = is_codex_host if kind == "claude" else not is_codex_host
    if not wanted or not path or not os.path.isfile(path):
        return
    vals = {"model": "", "effort": ""}
    ignored = []
    try:
        codex = _codex_block(path)
        if isinstance(codex, dict):
            for key in KEYS:
                if key not in codex:
                    continue
                v = codex.get(key)
                if isinstance(v, str):
                    vals[key] = " ".join(v.split())
                else:
                    ignored.append(key)
    except Exception:
        vals = {"model": "", "effort": ""}
        ignored = []
    sys.stdout.write("model=%s\neffort=%s\nignored=%s\n"
                     % (vals["model"], vals["effort"], ",".join(ignored)))


def cmd_sandbox(argv):
    path = argv[0]
    if not path or not os.path.isfile(path):
        return
    try:
        codex = _codex_block(path)
        v = codex.get("consult_sandbox") if isinstance(codex, dict) else None
        if isinstance(v, str):
            sys.stdout.write(v)
    except Exception:
        pass


def cmd_status(argv):
    """The OWNER path's read status — what the runner discloses next to the
    path itself, so "no config" can never again be confused with "another
    plugin's dir". Same semantics as hjw_common.load_config: `absent` (no
    file), `malformed` (present but unparseable, or a top-level value that is
    not a JSON object), `ok`. Never raises; an unreadable file is malformed,
    not absent — the difference is the operator's whole diagnosis."""
    path = argv[0] if argv else ""
    if not path:
        sys.stdout.write("none")
        return
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        sys.stdout.write("absent")
        return
    except Exception:
        try:
            sys.stdout.write("malformed" if os.path.exists(path) else "absent")
        except Exception:
            sys.stdout.write("absent")
        return
    sys.stdout.write("ok" if isinstance(cfg, dict) else "malformed")


MODES = {"values": cmd_values, "sandbox": cmd_sandbox, "status": cmd_status}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        sys.stderr.write("config.py: usage: config.py values|sandbox|status ...\n")
        sys.exit(2)
    MODES[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
