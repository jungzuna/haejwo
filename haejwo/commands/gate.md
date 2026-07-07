---
description: Inspect or change the enforcement gate on the fly — show status, set the per-turn file budget, or toggle on/off (emergency hatch for hotfixes).
argument-hint: "[on | off | <N files/turn> | bash on|off]"
---

You are the **haejwo host**. Operate the enforcement gate. The user's input: **$ARGUMENTS**

Data dir: `${CLAUDE_PLUGIN_DATA}` (if unsubstituted: `ls -d ~/.claude/plugins/data/*haejwo*`).

- **No argument** → show status: read `config.json` (gate.enabled, max_files_per_turn, bash_guard) plus the newest file in `state/` (this turn's counted files). One compact block.
- **`on` / `off`** → set `gate.enabled` accordingly in config.json via python3 (read-modify-write, preserve other keys). Confirm what changed.
- **A number N (1-10)** → set `gate.max_files_per_turn = N`. Confirm.
- **`bash on` / `bash off`** → set `gate.bash_guard`. Confirm.
- Anything else → show usage.

Notes: changes are effective immediately (hooks read config on every call). For a single-shot bypass without touching config there is also the env override `HAEJWO_GATE=off` on a command. If the user is disabling the gate, remind them to re-enable after the emergency (`/haejwo:gate on`).
