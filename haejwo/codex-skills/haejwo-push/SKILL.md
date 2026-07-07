---
name: haejwo-push
description: Show or set the per-repo push policy — outward actions are host-owned and ask-first until the user grants auto-push (a consent registry, not a gate).
---

<!-- MIRROR of commands/push.md for the Codex host — do not edit by hand;
     edit commands/push.md and regenerate. Drift is canary-tested. -->


You are the **haejwo host**. Manage push consent for the current repo. The user's input: **$ARGUMENTS**

Storage: `push.auto_repos` (list of repo roots) in `${CLAUDE_PLUGIN_DATA}/config.json` (if unsubstituted: `ls -d ~/.claude/plugins/data/*haejwo*`). Current repo root: `git rev-parse --show-toplevel`.

- **No argument** → report this repo's policy (**auto** if its root is in `push.auto_repos`, else **ask-first**) plus the full auto list.
- **`auto`** → confirm with the user ONCE that pushes/deploys from this repo may run without asking, then add the repo root to `push.auto_repos` (python3 read-modify-write on config.json; preserve all other keys). Report the change.
- **`ask`** → remove the repo root from the list (revoke). Report.
- Anything else → show usage.

Notes: this is a **consent registry, not a gate** — no hook blocks pushing. The orchestration rules make outward actions host-owned (workers never push) and ask-first by default; a grant here simply stops the asking for that repo. Revoke anytime with `/haejwo:push ask`.
