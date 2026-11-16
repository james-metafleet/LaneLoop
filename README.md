# MetaLoop

MetaLoop is a skill for running large engineering work as loops of fresh, headless coding-agent sessions. The spec is
engineered up front, branches run in parallel in isolated worktrees, every session stays well inside its context
window, and the owner decides at fixed gates. Each kind of work can run on a different agent and model: research on
Gemini, planning on Codex, coding on Claude, or any mix you choose.

## Contents

- [Key Features](#key-features)
- [Crews](#crews)
- [Planning with EGM](#planning-with-egm)
- [Installation](#installation)
- [Supported Stacks and Services](#supported-stacks-and-services)
- [Repository Layout](#repository-layout)
- [Development](#development)

## Key Features

- **Spec first.** Nothing is built before the owner signs off a plan: every branch broken into tasks, each with the
  test that proves it.
- **Any agent, per role.** A run's crew pins an agent, model and effort for each role: research, plan, code and
  verify. Claude Code, Codex, Antigravity, Gemini CLI, Grok Build, OpenCode (any provider's model) and Cursor are all
  driven the same way. The ready-made crews are chosen from current benchmarks, SlopCodeBench among them.
- **Parallel.** Each branch is its own chain of headless sessions in its own git worktree, five at a time by default,
  each with a live page where the owner follows its progress and answers its questions.
- **Context-efficient.** A guard hands a session off at 65% of its context window and stops it at 72%, on every agent:
  through a hook in Claude Code and through the session's event stream elsewhere. Sessions hand over through files,
  never memory, so every session starts sharp.
- **Any stack.** Nothing in MetaLoop is tied to a language, framework or database. A project describes itself in one
  file, `.metaloop/project.json`: how it builds and tests, which services its checks need and how its app is served.

## Crews

### Roles

| Role | Responsibility | Run By |
|---|---|---|
| Research | Answers questions read-only, many at once, each in its own file. | `consult.py research` |
| Plan | Runs design branches (mockups, schema pages) and reviews plans. | Design chains, `consult.py plan` |
| Code | Runs the fix chains: failing test, fix, docs, gate, security review, pull request and CI. | `fixloop.sh` |
| Verify | Runs the live-verify loop against the running app. | `qa-loop.sh` |

### Choosing a Crew

Pick a ready-made crew, then change any role you want. Each role is set by a pin of the form
`agent/model[:effort]`, for example `codex/gpt-6-astra:xhigh`.

| Command | Purpose |
|---|---|
| `crew.py presets` | Lists every crew role by role, with the reasons, and marks which are ready on this machine. |
| `crew.py use auto` | Selects the first recommended crew this machine can run. |
| `crew.py use <crew>` | Selects a crew and writes `.metaloop/crew.json`, which you commit. |
| `crew.py set <role>=<pin>` | Changes one or more roles, for example `plan=codex/gpt-6-astra:xhigh`. |
| `crew.py check` | Confirms that each agent is installed and each pin answers. |

Run each command as `python3 <kit>/common/crew.py`, where `<kit>` is the kit folder of the installed skill.

### Recommended Crews

| Crew | Research | Plan | Code | Verify | Best For |
|---|---|---|---|---|---|
| `frontier` | Opus 5.5 | Fable 5.2 | Opus 5.5 | Opus 5.5 | Top score per role |
| `mixed` | Gemini 4 Carbon | GPT-6 Astra | Opus 5.5 | Opus 5.5 | Three vendors |
| `clean-code` | Opus 5.5 | Fable 5.2 | GPT-6 Astra | Opus 5.5 | Least slop |
| `budget` | Gemini 4 Carbon | GPT-6.1 Sol | GPT-6.1 Sol | Sonnet 5.5 | Lowest cost |

- **`frontier`** puts the best measured model in each role; today every role goes to Claude Code.
- **`mixed`** gives each role to the vendor that measured well in it, so the design is reviewed by a model that will
  not write the code.
- **`clean-code`** codes with GPT-6 Astra, which SlopCodeBench ranks first for code that stays clean as it grows.
- **`budget`** keeps most of the quality at a fraction of the cost.

Claude models (Opus, Fable and Sonnet) run in Claude Code, GPT models in the Codex CLI and Gemini models in the
Antigravity CLI. `crew.py presets` shows the effort level for each role.

### Single-Agent Crews

| Crew | Agent | Model |
|---|---|---|
| `claude` (default) | Claude Code | Opus 5.5 |
| `codex` | Codex CLI | GPT-6 Astra, with GPT-6.1 Sol for research |
| `gemini` | Antigravity CLI | Gemini 4 Carbon |
| `grok` | Grok Build | Grok 4.7 |

[reference/crew.md](skills/metaloop/reference/crew.md) sets out the benchmarks behind each choice (SlopCodeBench,
Terminal-Bench 4.1, FrontierCode, LibraryDesignBench and SWE-Atlas-QnA, as of November 2026), what each agent supports,
and how the context guard, permissions and hand-offs work on agents without Claude Code's hooks.

## Planning with EGM

Planning uses Google's [Elephant-Goldfish Model](https://github.com/google/elephant-goldfish-model) (EGM). The main
session is EGM's **Elephant**: it designs the work with the owner. EGM's **Goldfish**, fresh subagents with none of the
conversation, review the design. MetaLoop then splits the approved design into branches, has the Goldfish review that
split, and orchestrates the chains that build it.

| Stage | Steps |
|---|---|
| Design (EGM) | `/egm:egm-brainstorm` → `/egm:egm-draft` → `/egm:egm-review` and `/egm:egm-resolve` → the owner approves and the design is merged |
| Hand-off | `egm_plan.py import` → branches drawn up → `egm_plan.py check` → fan-out Goldfish review → the owner signs off |
| Build (MetaLoop) | `make_briefs.py` → parallel chains (red, green, docs, gate, security, PR, CI) → review items → the owner merges |
| After merge | `/egm:egm-drift-report` → the owner updates the design doc or the code → re-verify |

The full procedure is in [reference/egm.md](skills/metaloop/reference/egm.md). `PLANNER=grill` plans the original way,
with grilling rounds on the plan page, and uses the `grilling` and `domain-modeling` companion skills where the
repository or the user has them.

## Installation

### Claude Code

MetaLoop installs as a Claude Code plugin, once per machine, and works in any repository:

```bash
claude plugin install metaloop --marketplace jomitiron/MetaLoop
```

This adds the repository as the `metaloop` marketplace and installs `metaloop@metaloop`. In a new session, Claude picks
the skill up when the work fits, or you can run `/metaloop:metaloop`.

To plan with EGM, install Google's plugin as well:

```bash
claude plugin install egm --marketplace google/elephant-goldfish-model
```

### Other Agents

Any agent that loads Agent Skills can orchestrate a run. From a clone of this repository, link the skill into the
agent's skills folder; a `git pull` in the clone then updates it:

```bash
./install.sh codex
```

| Argument | Installs Into | Read By |
|---|---|---|
| `codex` | `~/.agents/skills` | Codex CLI, Grok Build |
| `agy` or `gemini` | `~/.gemini/skills` | Antigravity CLI, Gemini CLI |
| `grok` | `~/.grok/skills` | Grok Build (which also reads Claude Code plugins) |
| `all` | All three folders above | All of the above |
| `--project <repo>` | `<repo>/.agents/skills` | Codex CLI, Antigravity CLI, Gemini CLI, OpenCode, Cursor |

Add `--copy` to copy the skill instead of linking it, for example to commit it to a repository for the whole team.

The agents in a run's crew only need to be installed and signed in; `crew.py check` and the run's `doctor.py` report
any that are missing. When the orchestrator is an agent other than Claude Code, the live pages are files the owner opens
in a browser and answers in the chat, and planning uses the grilling rounds, since EGM is a Claude Code plugin. See
[reference/crew.md](skills/metaloop/reference/crew.md).

### Updates

Turn on auto-update once: in Claude Code, open `/plugin` → **Marketplaces** → `metaloop` → **Enable auto-update**.
Claude Code then checks in the background during a session and installs any newer MetaLoop, which loads at the next
start or after `/reload-plugins`. Auto-update is off by default for marketplaces outside Anthropic's own. Without it,
update by hand:

```bash
claude plugin update metaloop@metaloop
```

A run already under way keeps the copy of the kit it was created with. Running `init_run.py` on it again brings in the
new kit and keeps the run's settings and any runbook or settings file you edited; the new render is written beside them
with a `.new` suffix.

### Team Repositories

To give everyone who works in a repository MetaLoop and EGM, with MetaLoop updating itself, commit the following to
the repository's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "metaloop": {
      "source": { "source": "github", "repo": "jomitiron/MetaLoop" },
      "autoUpdate": true
    },
    "egm": {
      "source": { "source": "github", "repo": "google/elephant-goldfish-model" }
    }
  },
  "enabledPlugins": {
    "metaloop@metaloop": true,
    "egm@egm": true
  }
}
```

Claude Code registers both marketplaces for each person once they trust the folder. Enabling a plugin there does not
install it, so each person runs these commands once in the repository:

```bash
claude plugin install metaloop@metaloop --scope project
claude plugin install egm@egm --scope project
```

### Project Setup

Describe the project once from the presets and commit the file this writes, `.metaloop/project.json`. Ask Claude to
set MetaLoop up for the repository and it runs this from the installed kit (`--list` shows every preset):

```bash
python3 <kit>/init_project.py --stack node --db postgres
```

Claude then starts a run (`init_run.py fix|verify <run folder> OWNER=<you>`) and uses the run's `doctor.py` to report
anything else the machine needs. `<kit>` is the kit folder inside the installed plugin; Claude knows its path. Every
session follows the repository's own instructions (`CLAUDE.md`, `AGENTS.md`).

## Supported Stacks and Services

### Stacks

| Stack | Preset (`--stack`) | Toolchain and Tests |
|---|---|---|
| Node.js | `node` | npm; Jest by default, or Vitest |
| Python | `python` | A `.venv` virtualenv; pytest |
| Ruby on Rails | `ruby` | Bundler; RSpec by default, or Minitest |
| Go | `go` | Go modules; `go vet` and `go test` |
| Rust | `rust` | Cargo; Clippy and `cargo test` |
| Java (Maven) | `java-maven` | Maven, using `./mvnw` when present; JUnit through Surefire |
| Java or Kotlin (Gradle) | `java-gradle` | Gradle; the gate goes by the exit code |
| .NET | `dotnet` | The dotnet CLI |
| PHP | `php` | Composer; PHPUnit |
| Anything else | `custom` | Every command filled in by hand |

### Databases and Services

| Service | Preset (`--db`) | Details | Exports |
|---|---|---|---|
| PostgreSQL | `postgres` | Version 16 | `DATABASE_URL` |
| MySQL | `mysql` | Version 8.4 | `DATABASE_URL` |
| MongoDB | `mongodb` | Version 7, as a single-node replica set so transactions work | `MONGO_URL` |
| Redis | `redis` | Version 7 | `REDIS_URL` |
| SQLite | `sqlite` | No server; a fresh database file per gate slot | `DATABASE_URL` |

Services run in Docker by default, or natively with `--via native`.

A preset fills in the gate's steps with the right test-output parser, the services those steps need (a fresh instance
per gate slot, on its own port, removed afterwards), how the verify loop builds, starts and health-checks the app (with
its own services, and data kept between runs and seeded once), the tools the machine needs, and the commands sessions
may run. Whatever a preset cannot know, such as how this particular app starts, it marks `FILL IN`, and the run will
not start until each one is filled in. Anything the presets do not cover takes a few lines of JSON: any command, any
test runner (given a summary pattern) and any service with a start command and a ready check.
[reference/project.md](skills/metaloop/reference/project.md) documents every field.

## Repository Layout

This repository is a Claude Code plugin, the marketplace that lists it, and a plain Agent Skill folder that any other
agent can load through `install.sh`.

| Path | Contents |
|---|---|
| [.claude-plugin/](.claude-plugin/) | `plugin.json` (the plugin) and `marketplace.json` (the catalog Claude Code installs it from). |
| [skills/metaloop/SKILL.md](skills/metaloop/SKILL.md) | The skill: when to offer a loop, the roles, the non-negotiables and the lifecycle. |
| [skills/metaloop/reference/](skills/metaloop/reference/) | The orchestrator's procedures, the project profile, the fix and verify loops, the EGM hand-off, and pitfalls met in practice. |
| [skills/metaloop/kit/](skills/metaloop/kit/) | The scripts `init_run.py` copies into a run folder (see the [kit README](skills/metaloop/kit/README.md)). |
| [skills/metaloop/kit/presets/](skills/metaloop/kit/presets/) | Ready-made project profiles: stacks, and the databases and services their checks need. |
| [skills/metaloop/kit/crews/](skills/metaloop/kit/crews/) | Ready-made crews: which agent and model researches, plans, codes and verifies. |
| [skills/metaloop/kit/common/agents.json](skills/metaloop/kit/common/agents.json) | The agents the kit drives and the models it knows, with dated benchmark notes. |
| [install.sh](install.sh) | Installs the skill for Codex, Antigravity, Gemini CLI, Grok Build, OpenCode or Cursor. |
| [evals/](evals/) | Behaviour evals for the skill (`claude plugin eval .`). |
| [.github/workflows/check.yml](.github/workflows/check.yml) | Validates the plugin and runs the kit's self-test on every pull request. |

## Development

### Releasing Changes

Every commit on `main` is a release. `plugin.json` has no `version`, so Claude Code versions MetaLoop by commit: people
with auto-update on receive a merged change at their next session, and everyone else on `claude plugin update`.

- **Merge only what is ready.** [check.yml](.github/workflows/check.yml) validates the plugin and runs the kit's
  self-test on every pull request. Run the [evals](#evals) before merging a change to `SKILL.md` or the references.
- **Try a change before it ships.** From a clone, `claude --plugin-dir .` loads the working copy for that session
  instead of the installed plugin.
- **Numbered releases.** To hold users on numbered releases instead, add `"version": "1.0.0"` to
  `.claude-plugin/plugin.json` and raise it for each release (`claude plugin tag --push` tags it). Users then receive
  nothing new until the version changes.

### Evals

[evals/](evals/) checks that the skill steers Claude as intended, across nine cases: from offering a loop only for wide
work, to planning with EGM before any chain starts, to giving each role the agent the owner names, to leaving merges
and sign-offs to the owner. Each case runs with and without the skill, so the report shows the difference it makes.

```bash
claude plugin eval . --judge-model sonnet
```

### Self-Test

From a clone of this repository:

```bash
bash skills/metaloop/kit/selftest.sh
```

The self-test renders a fix run and a verify run in a temporary folder from a test profile and runs the scripts against
stand-ins (a stub toolchain and a stub service), every test-output parser against sample output, and every stack
preset. Where PostgreSQL or MongoDB is installed, it also starts the native presets for real. Nothing outside the
temporary folder is touched.
