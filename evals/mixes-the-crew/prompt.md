---
description: "A request to split the work across vendors: the skill loads and sets the crew per role (plan on Codex, code on Claude, research on Gemini), starting nothing."
tags: [trigger, crew]
runs: 3
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

QA found 11 bugs across checkout, search and accounts. I'd like them fixed overnight as a loop, one PR each, but split
the work: Codex with GPT-6 Astra should do the planning, Claude Opus should write the code, and Gemini should do the
research. How would you set that up?
