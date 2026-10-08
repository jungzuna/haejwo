---
description: Inspect or change the enforcement gate on the fly — show status, set the per-turn file budget, or toggle on/off (emergency hatch for hotfixes).
argument-hint: "[on | off | <N files/turn> | bash on|off]"
---

You are the **haejwo host**. Operate the enforcement gate. The user's input: **$ARGUMENTS**

Data dir: `${CLAUDE_PLUGIN_DATA}` (if unsubstituted, by host: Claude Code `~/.claude/plugins/data/haejwo-haejwo/`, Codex `~/.codex/plugins/data/haejwo-haejwo/`).

- **No argument** → show status: read `config.json` (gate.enabled, max_files_per_turn, bash_guard) plus this session's state file — `$CLAUDE_CODE_SESSION_ID`, every `[^A-Za-z0-9_-]` → `-`, first 80 chars → `state/<that>.json` (this turn's counted files); unknown session id → the newest `state/*.json`, labeled "may be another session's". One compact block.
- **`on` / `off`** → set `gate.enabled` accordingly in config.json via python3 (read-modify-write, preserve other keys). Confirm what changed.
- **A number N (1-10)** → set `gate.max_files_per_turn = N`. Confirm.
- **`bash on` / `bash off`** → set `gate.bash_guard`. Confirm.
- Anything else → show usage.

Notes: changes are effective immediately (hooks read config on every call). There is no per-command bypass: `HAEJWO_GATE=off` works only in the environment of the Claude Code / Codex process itself, set before it starts. If the user is disabling the gate, remind them to re-enable after the emergency (`/haejwo:gate on`).
