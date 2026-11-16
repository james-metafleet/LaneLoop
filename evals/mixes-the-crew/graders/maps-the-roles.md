---
type: llm
---
PASS if the reply keeps the owner's split and expresses it as MetaLoop's crew: planning on Codex with GPT-6 Astra
(a pin like `codex/gpt-6-astra`), coding on Claude Code with Opus (`claude/claude-opus-5-5...`), research on Gemini
through Antigravity or Gemini CLI (`agy/gemini-...` or `gemini/...`), set with `crew.py use`/`set` or `init_run.py`
with `CREW=`/`<ROLE>_PIN=` (the `mixed` crew is this split). It may suggest a verify pin and checking the agents are
installed (`crew.py check`, `doctor.py`).
FAIL if it says the loop can only run on Claude, ignores one of the three roles, or swaps them.
