#!/usr/bin/env python3
"""Wall-clock bound for a reviewer runner's python helpers and the reviewer CLI;
not coreutils, bootstrap python, or stdin reads.

`bounded.py <seconds> <cmd> [args...]` runs the command in its OWN session (killing only the direct child
would leak the expensive descendants) and exits 124 when the bound expires, matching timeout(1), which the
runners' failure classifiers read. A SIGINT/SIGTERM here kills the child's group too, then is re-raised (130/143).
"""
import os, signal, subprocess, sys

child, pending = None, []

def on_signal(signum, _frame):
    pending.append(signum)
    if child is not None:
        raise KeyboardInterrupt  # out of child.wait(); SIGTERM takes the same path

def reap():
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, signal.SIG_IGN)  # a second signal must not abort the sweep
    for sig, grace in ((signal.SIGTERM, 2), (signal.SIGKILL, 1)):
        try:
            os.killpg(child.pid, sig)
        except Exception:
            pass
        try:
            child.wait(timeout=grace)
        except Exception:
            pass

try:
    secs = float(sys.argv[1])
except Exception:
    sys.exit(2)
cmd = sys.argv[2:]
if not cmd:
    sys.exit(2)
for _sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(_sig, on_signal)
try:
    child = subprocess.Popen(cmd, start_new_session=True)
except FileNotFoundError:
    sys.exit(127)
except Exception:
    sys.exit(126)
try:
    if pending:
        raise KeyboardInterrupt  # signalled while the child was being spawned
    sys.exit(child.wait(timeout=secs))
except KeyboardInterrupt:
    reap()
    signal.signal(pending[0], signal.SIG_DFL)
    os.kill(os.getpid(), pending[0])
    sys.exit(128 + pending[0])
except subprocess.TimeoutExpired:
    reap()
    sys.exit(124)
except Exception:
    sys.exit(126)
