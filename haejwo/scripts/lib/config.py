#!/usr/bin/env python3
"""Config reading for haejwo's two reviewer runners.

  values  <codex|claude> <config.json>   model / effort
  sandbox <config.json>                  codex.consult_sandbox, raw
  status  <config.json>                  ok | absent | malformed

HOST-RELATIVE: the `codex` block describes the reviewer of the host owning the data dir — under /.codex/
that is Claude (the codex runner ignores model/effort, the claude runner reads them), under /.claude/ the
reverse, decided from the selected path's TEXT. A vendorless custom root follows the runner kind: each
runner reads the block that describes it. The sandbox is read on either host and printed RAW (the caller's
allowlist decides); model/effort are whitespace-folded. Any parse failure is "no config"; unknown keys (a
stored `fallback_model`, retry removed in 2.22) are skipped; `models_codex` never selects this reviewer.
*[origin: a live smoke launched the claude reviewer with the codex host's own model name]*
*[origin: 2.17 config-ownership round]*
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
    # Custom plugin root: the runner kind names the host it reviews FOR.
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
    """The owner path's read status, disclosed beside the path: `absent` (no file), `malformed`
    (unparseable, unreadable, or not a JSON object), `ok` — same semantics as hjw_common.load_config.
    Never raises; unreadable is malformed, not absent (the operator's whole diagnosis)."""
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
