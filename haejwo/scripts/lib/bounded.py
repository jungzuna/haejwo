#!/usr/bin/env python3
"""Wall-clock bound for every helper and CLI call a reviewer runner makes.

`bounded.py <seconds> <cmd> [args...]` runs the command in its OWN session and exits 124 when the
bound expires, matching timeout(1), which the runners' failure classifiers read.
"""
import os, signal, subprocess, sys

try:
    secs = float(sys.argv[1])
except Exception:
    sys.exit(2)
cmd = sys.argv[2:]
if not cmd:
    sys.exit(2)
try:
    # Own session/process group: killing only the direct child would leak the expensive descendants
    # this bound exists to cap.
    child = subprocess.Popen(cmd, start_new_session=True)
except FileNotFoundError:
    sys.exit(127)
except Exception:
    sys.exit(126)
try:
    sys.exit(child.wait(timeout=secs))
except subprocess.TimeoutExpired:
    for sig, grace in ((signal.SIGTERM, 2), (signal.SIGKILL, 1)):
        try:
            os.killpg(child.pid, sig)
        except Exception:
            pass
        try:
            child.wait(timeout=grace)
            if sig is signal.SIGTERM:
                continue  # still sweep the group with SIGKILL
        except Exception:
            pass
    sys.exit(124)
except Exception:
    sys.exit(126)
