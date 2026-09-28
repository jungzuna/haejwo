#!/usr/bin/env python3
"""Config reading for haejwo's two reviewer runners.

  values  <codex|claude> <config.json>   model / effort / fallback_model
  sandbox <config.json>                  codex.consult_sandbox, raw

HOST-RELATIVE reading: the `codex` block describes the reviewer of the HOST
that owns the data dir. On a CODEX host (config path under /.codex/) that
reviewer is CLAUDE, so the codex runner IGNORES codex.model/effort/
fallback_model there and the claude runner reads them ONLY there. The host is
decided from the SELECTED path's TEXT, never from its canonical target — the
caller chose that path and the caller's choice is what names the host.
*[origin: a live smoke launched the claude reviewer with the codex host's own
model name]*

The sandbox is read on EITHER host: it describes how THIS runner is launched,
not which vendor the model keys belong to. It is printed RAW — the caller's
allowlist decides, so the transport can never trim an exotic value into a
valid one. The model/effort values are whitespace-FOLDED (config values are
tolerant); an empty ENV value counts as unset, but that trimming belongs to
the caller.

Any parse failure is "no config" — a reviewer runner never guesses. NOT read
here: `efforts_codex` / `models_codex` belong to codex-HOST worker tiers
(spawn_agent parameters) and never select this reviewer.
"""
import json
import os
import sys

KEYS = ("model", "effort", "fallback_model")


def _codex_block(path):
    with open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    return cfg.get("codex") if isinstance(cfg, dict) else None


def cmd_values(argv):
    kind, path = argv[0], argv[1]
    is_codex_host = "/.codex/" in path
    wanted = is_codex_host if kind == "claude" else not is_codex_host
    if not wanted or not path or not os.path.isfile(path):
        return
    vals = {"model": "", "effort": "", "fallback_model": ""}
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
        vals = {"model": "", "effort": "", "fallback_model": ""}
        ignored = []
    sys.stdout.write("model=%s\neffort=%s\nfallback_model=%s\nignored=%s\n"
                     % (vals["model"], vals["effort"], vals["fallback_model"], ",".join(ignored)))


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


MODES = {"values": cmd_values, "sandbox": cmd_sandbox}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        sys.stderr.write("config.py: usage: config.py values|sandbox ...\n")
        sys.exit(2)
    MODES[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
