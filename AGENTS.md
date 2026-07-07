# haejwo maintainer guide (for coding agents)

This repo is maintained largely BY coding agents — including, probably, you.
Read these BEFORE changing anything:

1. `haejwo/PHILOSOPHY.md` — the constitution: 12 principles with origin cases,
   the precedence order for conflicts, and the amendment rule.
2. `haejwo/PROMPTS.md` — style law for every LLM-facing string. Deny messages
   are a TESTED contract: changing them requires updating `tests/test_hooks.py`.

Hard rules (each earned by a real incident):
- Run `python3 tests/test_hooks.py` before any commit and gate the commit on
  the UNPIPED exit code — piped/tailed output hides failures.
- Editing any `haejwo/commands/*.md` requires regenerating the
  `haejwo/codex-skills/` mirrors; the drift canary fails otherwise.
- Version bumps update BOTH `haejwo/.claude-plugin/plugin.json` and
  `haejwo/.codex-plugin/plugin.json` (sync is tested). Patch = wording,
  minor = behavior.
- Scope is intentionally exactly TWO hosts (Claude Code + Codex CLI).
  Do not add host adapters without an explicit maintainer request.
- English for all prompts and instructions; Korean only for proper nouns
  (해줘) and localized READMEs.
- All codex-side behaviors here were measured live, not assumed. If you
  change a host-boundary assumption, re-measure before shipping.
