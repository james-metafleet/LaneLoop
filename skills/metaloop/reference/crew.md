# The crew: any agent, any model, per role

A MetaLoop run does four kinds of work. The **crew** says which coding agent, model and effort does each one, so one
run can mix vendors: Gemini researching, Codex planning, Claude coding, for example.

| Role | What Its Sessions Do | Where It Runs |
|---|---|---|
| **research** | Answers a question read-only: how an area of the code works, what a library's docs say, whether a drafted defect is real. One fresh session per question, answer in `consults/<name>.md`. | `consult.py research` |
| **plan** | A design branch's sessions until their sign-off (mockups, schema pages); plan drafts and reviews on request (a second vendor reviewing a design catches what one model misses). | design chains, `consult.py plan` |
| **code** | The fix chains: red, green, docs, gate, security review, PR, CI, review proposals. | `fixloop.sh` |
| **verify** | The live-verify loop: runs the test plan against the running app in the browser. | `qa-loop.sh` |

The orchestrator (the session you talk to) is not in the crew: it's whatever agent you opened, and it can be any agent
that loads skills (see "Orchestrating from another agent" below).

## Pins

A pin is `agent/model[:effort][@window]`:

```text
claude/claude-opus-5-5[1m]:high          Claude Code, Opus 5.5 with its 1M window, high effort
codex/gpt-6-astra:xhigh                  Codex CLI, GPT-6 Astra, xhigh reasoning
agy/gemini-4-carbon:high                 Antigravity CLI, Gemini 4 Carbon
grok/grok-4.7                            Grok Build (no effort setting)
opencode/openrouter/qwen/qwen3-coder:high@200k   OpenCode: any provider's model; @ gives a window MetaLoop doesn't know
cursor/sonnet-5.5                        Cursor CLI
```

The effort must be one the agent takes (`crew.py models` lists them); a model id MetaLoop doesn't list still works, and
its window defaults to the agent's unless `@` gives it. The window matters: the context guard hands off at `SOFT_PCT`
and stops at `HARD_PCT` of it.

## Choosing a crew

```bash
python3 <kit>/common/crew.py presets          # every ready-made crew, role by role, with why
python3 <kit>/common/crew.py use mixed        # this repository's crew: writes .metaloop/crew.json (commit it)
python3 <kit>/common/crew.py set plan=codex/gpt-6-astra:xhigh code=claude/claude-opus-5-5[1m]:high
python3 <kit>/common/crew.py                  # the crew in effect, and which agents it needs
python3 <kit>/common/crew.py check            # each agent installed? each pin answering?
```

`use` and `set` act where you run them: in a repository they write `.metaloop/crew.json` (a preset plus the roles you
changed), which every run made there starts from; in a run folder they change that run's `loop.conf`, and the sessions
that start afterwards use the new pins. `init_run.py` takes the same choices as `CREW=<preset>` and `<ROLE>_PIN=<pin>`.
Roles also answer to the words people use: execution, build or coding is `code`; planning or design is `plan`; qa is
`verify`. The kit's default, when nothing chose, is the `claude` crew: MetaLoop's original Opus 5.5 set-up.

### When an agent isn't installed

Nothing is installed for you, and nothing fails halfway because an agent is missing:

- `crew.py presets` marks each crew **ready here** or **needs <agent>**, and `crew.py use auto` takes the first
  recommended crew whose agents are all installed.
- `crew.py use` and `set` save a crew that needs a missing agent, and say so: its install, its sign-in, and a crew this
  machine can run as it is.
- `init_run.py` with no crew chosen anywhere uses the `claude` crew; on a machine without Claude Code (an orchestrator on
  Codex, say) it starts on the first crew that can run there, and says which.
- `doctor.py` checks every agent the crew pins. A missing plan, code or verify agent blocks the run, with the install,
  who runs it, and the way out (`crew.py set <role>=<pin>` or `use <a ready crew>`). A missing research agent doesn't:
  the orchestrator researches with its own tools.
- The preflight refuses a pin whose agent isn't installed or doesn't answer (signed out, out of credit), before any
  chain starts.
- A session started on a missing agent anyway (after a `crew.py set` mid-run) ends at once with `agent_missing` and the
  install line: the chain stops as blocked with that ask, rather than counting it as a session without progress.

### The ready-made crews (November 2026)

| Crew | Research | Plan | Code | Verify |
|---|---|---|---|---|
| `frontier` | Opus 5.5 (high) | Fable 5.2 (high) | Opus 5.5 (high) | Opus 5.5 (xhigh) |
| `mixed` | Gemini 4 Carbon (high) | GPT-6 Astra (xhigh) | Opus 5.5 (high) | Opus 5.5 (xhigh) |
| `clean-code` | Opus 5.5 (high) | Fable 5.2 (high) | GPT-6 Astra (xhigh) | Opus 5.5 (xhigh) |
| `budget` | Gemini 4 Carbon (medium) | GPT-6.1 Sol (high) | GPT-6.1 Sol (medium) | Sonnet 5.5 (high) |
| `claude` | Opus 5.5 (high) | Opus 5.5 (xhigh) | Opus 5.5 (high) | Opus 5.5 (xhigh) |
| `codex` | GPT-6.1 Sol (high) | GPT-6 Astra (xhigh) | GPT-6 Astra (xhigh) | GPT-6 Astra (high) |
| `gemini` | Gemini 4 Carbon (high) | Gemini 4 Carbon (xhigh) | Gemini 4 Carbon (high) | Gemini 4 Carbon (xhigh) |
| `grok` | Grok 4.7 | Grok 4.7 | Grok 4.7 | Grok 4.7 |

The effort level for each role is in brackets. Claude models (Opus, Fable, Sonnet) run in Claude Code, GPT models in the
Codex CLI, Gemini models in the Antigravity CLI (`agy`) and Grok in Grok Build.

- **`frontier`**: the best measured model per role. All Claude Code today: Anthropic leads every role's benchmark.
- **`mixed`**: three vendors, each where it measured well; the design gets a view from a model that won't write the
  code.
- **`clean-code`**: least slop. SlopCodeBench puts Codex's Astra first for code that stays clean as it is extended.
- **`budget`**: most of the quality for a fraction of the cost.
- **`claude`**: one agent, one account and one bill. The default.
- **`codex`**, **`gemini`**, **`grok`**: one agent each. Grok trails the other vendors by 20+ points on
  Terminal-Bench 4.1.

### The evidence

All numbers as published on the date shown; re-check before relying on them.

| Benchmark | What It Measures | Leaders | Source |
|---|---|---|---|
| **SlopCodeBench** (v1.0, 26 Oct 2026) | An agent extends its own code through 196 checkpoints as the spec changes: solve rate, plus **erosion** (complexity piling into a few functions) and **verbosity** (redundant code). No agent solves a problem end to end; agent code is 2.3x more verbose and 2.0x more eroded than human code. | GPT 5.6 Sol xhigh (Codex) 32.1% isolated solve, $3.84 a checkpoint · **GPT-6 Astra xhigh (Codex) 31.6%, best strict solve 16.3%, least verbose 0.158** · **Opus 5.5 medium (Claude Code) 29.6% at $0.99** · GPT-6.1 Sol medium 29.1% at $0.91 · Opus 5.5 xhigh 27.6%, with the third-lowest erosion of the top ten (0.495) | [scbench.ai/leaderboard](https://www.scbench.ai/leaderboard), arXiv 2603.24755 |
| **Terminal-Bench 4.1** | Hard tasks in a real terminal, end to end. | Opus 5.5 max 65.8% · Fable 5.2 max 65.8% (3.5x the cost) · Gemini 4 Carbon high (agy) 64.6% · Sonnet 5.5 max 63.9% · GPT-6.1 Sol max 60.9% (about a seventh of Opus 5.5's cost) · GPT-6 Astra max 60.3% · Grok 4.7 38.8% | [tbench.ai](https://www.tbench.ai/leaderboard/terminal-bench/4.1) |
| **FrontierCode 2.0** (Artificial Analysis) | Is the change mergeable with no human edits; out-of-scope changes penalised. The nearest public measure of planning. | Fable 5.2 xhigh 49.2 / high 48.7 · Opus 5.5 max 44.0 · GPT-6 Astra 42.6 · GPT-6.1 Sol 42.5 | [artificialanalysis.ai](https://artificialanalysis.ai/agents/coding-agents) |
| **LibraryDesignBench** | An agent designs a library; scored by how simple and correct other agents' code on it becomes. | Opus 5.5 48.9 · Fable 5.1 47.5 · GPT-6 Astra 45.1 (human-written library: 46.6) | [snorkel.ai](https://snorkel.ai/leaderboard/librarydesignbench) |
| **SWE-Atlas-QnA** (Artificial Analysis) | Answering questions about real codebases: the nearest measure of research. | Opus 5.5 max (Claude Code) 65.9, the best single model in a stock CLI · Sonnet 5.5 64.2 · Gemini 4 Carbon 63.7 · Fable 5.2 62.1 · GPT-6 Astra 59.1 | [artificialanalysis.ai](https://artificialanalysis.ai/agents/coding-agents) |

Not used: SWE-bench Verified (no entries since February 2026), SWE-bench Pro (leaderboard unreadable, aggregators
disagree), METR time horizons (no longer updated), Aider Polyglot and LiveCodeBench (not updated since 2025), BrowseComp
(self-reported only). Gemini, Grok and Fable 5.2 have no SlopCodeBench entry yet.

To refresh: update `kit/common/agents.json` (models, windows, notes, `as_of`), the presets in `kit/crews/`, and this
table, in one change.

## What each agent supports

| Agent (`pin prefix`) | Context Guard | Permissions | Hand-Off and End Gate | MCP (Verify Loop) | Reads |
|---|---|---|---|---|---|
| Claude Code (`claude`) | in-session hook `ctx_guard.py`, the wrapper a point above as backstop | `settings.json` allow/deny | hooks (`ctx_guard.py`, `end_gate.py`) | `--mcp-config` | `CLAUDE.md` |
| Codex CLI (`codex`) | wrapper, estimated from the event stream | `--sandbox workspace-write` with the network on and the run folder writable (`CODEX_SANDBOX`), + guard rails | interrupt, then `codex exec resume` | `-c mcp_servers.*` (with `--trust`) | `AGENTS.md` |
| Antigravity (`agy`) | wrapper, estimated | `--dangerously-skip-permissions` + guard rails | `--conversation` | `.agents/mcp_config.json` | `GEMINI.md`, `AGENTS.md` |
| Gemini CLI (`gemini`) | wrapper, estimated | `--approval-mode yolo` + guard rails | `--resume latest` | `.gemini/settings.json` | `GEMINI.md` |
| Grok Build (`grok`) | wrapper, estimated | `--always-approve` + guard rails | the named session (`-s`) | `.grok/config.toml` | `AGENTS.md`, `CLAUDE.md` |
| OpenCode (`opencode`) | wrapper, **measured** from each step's tokens | a per-run config allowing edits and commands + guard rails | `--session` | the same config | `AGENTS.md` |
| Cursor CLI (`cursor`) | wrapper, estimated (Cursor reports no usage) | `--force` + guard rails | `--resume` | `.cursor/mcp.json` | `AGENTS.md`, `CLAUDE.md` |

How `agent_run.py` keeps the non-negotiables on agents without Claude Code's hooks:

- **Context.** It reads the agent's event stream as it runs. Where the agent reports tokens per request (OpenCode) it
  uses them; elsewhere it estimates from the prompt and every item since, as `ctx_guard.py` counts tool results. At
  `SOFT_PCT` it interrupts the session and resumes it with the same hand-off message the hook gives; at `HARD_PCT` it
  ends it and writes `state/last_stop.json` (kind `hard-cap`), so the chain's next session starts the way it does after
  a hook stop.
- **Permissions.** Each agent runs in its own unattended mode, and (on macOS and Linux) `git` and `gh` go through
  guard-rail shims built from the run's `settings.json` deny list: a force push, a merge, a PR comment or review, `gh api` writes and the rest exit
  126. They are guard rails, not a sandbox: the runbook stays binding and the orchestrator checks every branch.
- **The end gate.** A session that ends without updating its status and hand-off is resumed once with the end gate's
  ask.
- **The pin.** Claude Code's preflight checks every model a call used and the window. Other agents are checked with the
  session's own flags, so a flag the installed CLI doesn't know fails the preflight, not the run; the model is checked
  where the agent reports one (Antigravity, Gemini CLI, Cursor), and otherwise the flag is the pin.
- **Runaways.** `SESSION_TIMEOUT_MIN` (300 minutes of awake time) ends any session; `BUDGET_PER_SESSION` is enforced
  by Claude Code only.
- **The rules.** Claude Code gets the runbook as its system prompt; every other agent gets it ahead of the task, with a
  short note on what differs for it (`/security-review` is done by hand, no subagents, the guard rails).

A CLI that changed its flags doesn't need a kit release: `METALOOP_ARGS_<AGENT>` adds flags and `METALOOP_BIN_<AGENT>`
names another binary (Cursor's is `agent`, which Grok Build also installs).

### Known gaps

- Grok Build documents no `streaming-json` event schema: `agent_run.py` reads what is there and estimates the rest, and
  takes no effort setting for it.
- Antigravity has had a bug where `agy -p` drops stdout when it isn't writing to a terminal; update it (1.2.6 or later)
  if its preflight answers nothing.
- Codex's workspace sandbox can stop a gate that starts Docker or binds service ports. If doctor or the first gate says
  so, the owner may set `CODEX_SANDBOX=danger-full-access` (a hard rule in the runbook, named in the PR bodies).
- Claude Code says `--bare` will become the default for `-p`, and bare mode runs no hooks from `--settings`. The wrapper's
  backstop still ends a session at the ceiling; if that release lands, pass the flag that turns bare mode off through
  `METALOOP_ARGS_CLAUDE`.
- Gemini CLI now serves Google's Code Assist Standard and Enterprise plans; personal plans use Antigravity (`agy`).

## Orchestrating from another agent

The skill is a standard Agent Skill (`SKILL.md` with name and description), so any agent that loads skills can be the
orchestrator. Install it with the repository's `install.sh`: `./install.sh codex`, `agy`, `gemini`, `grok` or `all`
links `skills/metaloop` into that agent's user skills folder, and `./install.sh --project <repo>` into a repository's
`.agents/skills`, which Codex, Antigravity, Gemini CLI, OpenCode and Cursor all read. Claude Code installs the plugin.

What changes when the orchestrator isn't Claude Code:

- **`<kit>`** is the `kit` folder beside the installed `SKILL.md` (Claude Code passes it as `${CLAUDE_SKILL_DIR}/kit`).
- **Live pages** are claude.ai Artifacts. Elsewhere the pages are still built (`live/session.live.html`, `plan.html`) for
  the owner to open in a browser, read-only: the owner answers in the chat, and the orchestrator records each answer
  with `task.py answer` (chains) or in `plan.json` (plan rounds).
- **EGM** is a Claude Code plugin: plan with `PLANNER=grill`, and use `consult.py plan` for the outside review a
  Goldfish would give (a fresh session, on another model, with none of the conversation).
- **Signatures**: set `SIGN_TOOL` and `SIGN_MODEL` to the orchestrator's agent and model, so the replies it posts on
  PRs say who wrote them.
