"""Runs one loop session on whichever coding agent a role's pin names, and reports it the same way for every agent.

  python3 agent_run.py session <role> --prompt F --log F --err F [--system F] [--settings F] [--mcp F] [--readonly]
                                      [--answer F] [--pin PIN]
        one headless session; prints subtype|cost_usd|turns|api_error|first 160 chars of the result (pipe-separated)
  python3 agent_run.py preflight [<role> | --pin PIN]
        prints "ok" when a one-line call on the pin answers, on that model where the agent reports it; else what it got
  python3 agent_run.py argv <role> [the session options]
        the command a session would run, as JSON, without running it
  python3 agent_run.py summary <log> [--agent A]
        the result line of a finished session log
  python3 agent_run.py pin <role>
        the role's pin, as loop.conf has it now

<role> is research, plan, code or verify: the pin comes from this run's loop.conf (<ROLE>_PIN; crew.py explains pins).
The run's rules (--system, the runbook) reach the agent as its system prompt where it takes one (Claude Code), else ahead
of the task. The prompt goes on stdin where the agent reads it there, else as its prompt argument (a long one as a file
the agent is told to read).

What stays the same on every agent:
- The context ceiling. Claude Code measures in-session with the ctx_guard.py hook (and this wrapper backs it up, a point
  above the hard stop). Every other agent is measured from outside: this wrapper reads its event stream as it runs, using
  the tokens the agent reports per request where it reports them (OpenCode) and otherwise an estimate from the prompt and
  every item since, counted the way ctx_guard.py counts tool results. At SOFT_PCT it interrupts the session and resumes it
  with the hand-off instruction (the message the hook would have given); at HARD_PCT it ends the session and writes
  state/last_stop.json (kind hard-cap), as the hook does.
- What a session may not do. Claude Code enforces settings.json's allow/deny. Every other agent runs in its own
  unattended mode (Codex in its workspace-write sandbox) plus guard-rail shims for git and gh, built from the same deny
  list, that refuse a force push, a merge, a comment, a review and the rest. They are guard rails, not a sandbox: the
  runbook stays binding and the orchestrator checks every branch.
- The end gate. Claude Code's Stop hook (end_gate.py) keeps a session from ending without its hand-off. Elsewhere, a
  session that ended without one is resumed once with the same ask.
- A time cap (SESSION_TIMEOUT_MIN, counted in awake time, so a sleep is no hang), the result line, the usage-limit
  detection (api_error usage_limit_reached / rate_limit_error) and the logs.

Escape hatches for a CLI that changed: METALOOP_BIN_<AGENT> names another binary, METALOOP_ARGS_<AGENT> adds flags
(shell words), e.g. METALOOP_ARGS_CODEX='-c model_verbosity=low'.
"""
import json, os, re, shlex, shutil, signal, subprocess, sys, threading, time, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import crew  # noqa: E402

WIN = os.name == "nt"
IMG = 1600
ARG_MAX_PROMPT = 100_000   # a longer prompt goes to an argument-prompt agent as a file it is told to read
LIMIT_RE = re.compile(r"usage limit|usage_limit|quota|insufficient_quota|out of credits|credit balance|plan limit|"
                      r"limit reached|exceeded your", re.I)
RATE_RE = re.compile(r"rate.?limit|\b429\b|too many requests|resource.?exhausted", re.I)
HANDOFF_ASK = ("[context guard] Context is at {pct:.0%} of the window (hand-off at {soft:.0%}, forced stop at {hard:.0%}, "
               "limit 75%). Start nothing new. Finish only the step in flight, record it as the runbook says, write the "
               "hand-off file, then end your reply.")
ENDGATE_ASK = ("[end gate] You ended without recording this session's progress or writing the hand-off file. Do both now, "
               "as the runbook says, then end your reply.")
AGENT_NOTES = """## Notes for {name}

This run's rules (above) were written for every agent MetaLoop drives; you are {name} running {model}.
- Where they name a Claude Code command (`/security-review`), do that work yourself: review this branch's diff the way a
  security reviewer would (injection, authorisation, secrets, unsafe input handling, unsafe defaults), fix what you find,
  and record the review with task.py as the phase asks.
- "No subagents" means: do every step yourself, in this session.
- git and gh refuse what a loop session may not do (a force push, a merge, a comment, a review, ...). Don't work around
  a refusal: it means the runbook gives that step to someone else.
- Keep the hand-off file current as you go: a session can be stopped when its context fills.
"""


def conf():
    return crew.read_conf(os.path.join(HERE, "loop.conf"))


def pin_for(role, c=None):
    c = c if c is not None else conf()
    role = crew.role_name(role)
    p = c.get(f"{role.upper()}_PIN")
    if not p:  # a run made before crews
        p = crew.legacy_pins(c, c.get("KIND", "fix")).get(role) or crew.preset_pins(c.get("CREW") or crew.DEFAULT_CREW)[role]
    return crew.parse(p)


def est(o):
    """Tokens a payload adds to the context, as ctx_guard.py counts them."""
    if isinstance(o, str):
        if len(o) > 4000 and " " not in o[:3000]:
            return IMG
        return len(o) // 3 + 1
    if isinstance(o, dict):
        if o.get("type") == "image":
            return IMG
        return sum(est(v) for v in o.values())
    if isinstance(o, list):
        return sum(est(x) for x in o)
    return 1


def api_error_of(text):
    if not text:
        return ""
    if LIMIT_RE.search(text):
        return "usage_limit_reached"
    if RATE_RE.search(text):
        return "rate_limit_error"
    return ""


def first_text(d, keys=("result", "response", "text", "content", "message")):
    """The first string under one of `keys`, looking one level into dicts and lists of text parts."""
    if isinstance(d, str):
        return d
    if isinstance(d, list):
        parts = [first_text(x, keys) for x in d]
        return "".join(p for p in parts if p)
    if isinstance(d, dict):
        for k in keys:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, (dict, list)):
                t = first_text(v, ("text", "content"))
                if t:
                    return t
    return ""


def resolve_bin(name):
    """The command to start a CLI with: its full path (on Windows that finds npm's .cmd shims, which a bare name doesn't),
    or, on Windows, a script with no extension run through bash (Git Bash stand-ins)."""
    p = shutil.which(name)
    if p:
        return [p]
    if WIN:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            f = os.path.join(d, name)
            if os.path.isfile(f):
                return [shutil.which("bash") or "bash", f]
    return [name]


def installed(name):
    r = resolve_bin(name)
    return len(r) > 1 or bool(shutil.which(r[0]))


def missing_line(ag, role):
    """The result line for a session whose agent isn't installed: the loops stop on it, with this ask, instead of
    counting it as a session that made no progress."""
    msg = (f"{ag.info['name']} (`{ag.bin}`) is not installed: {ag.info['install']}; then {ag.info['sign_in']}. "
           f"Or give the {role or 'role'} another agent: crew.py set {role or '<role>'}=<pin>")
    return "|".join(["agent_missing", "0", "0", "", msg.replace("|", "/")])


def load_mcp(path):
    try:
        return json.load(open(path, encoding="utf8")).get("mcpServers") or {}
    except (OSError, ValueError, AttributeError):
        return {}


# ------------------------------------------------------------------------------------------------------------ agents

class Agent:
    """One coding-agent CLI. Subclasses say how to call it and how to read its event stream."""
    name = ""
    hook_guard = False      # measures its own context in-session (Claude Code's hooks)
    system_flag = False     # takes the runbook as a system prompt
    prompt_arg = False      # takes the prompt as an argument, not on stdin
    resumable = True
    overhead = 12000        # the agent's own system prompt and tool definitions, before the task

    def __init__(self, p):
        self.p = p
        self.info = crew.registry()["agents"][p["agent"]]
        self.bin = os.environ.get(f"METALOOP_BIN_{p['agent'].upper()}") or self.info["bin"]
        self.price = (crew.model_info(p["agent"], p["model"]) or {}).get("price")
        self.ctx = 0            # the last context size the agent reported
        self.pending = 0        # estimated tokens since that report
        self.base = 0           # the task's own estimate (a floor before the first report)
        self.turns = 0
        self.text = ""
        self.errors = []
        self.models = set()
        self.session_id = ""
        self.cost = 0.0
        self.subtype = ""

    def cmd(self):
        return resolve_bin(self.bin)

    def extra(self):
        return shlex.split(os.environ.get(f"METALOOP_ARGS_{self.p['agent'].upper()}", ""))

    # how to call it
    def argv(self, o, prompt=""):
        raise NotImplementedError

    def resume_argv(self, o, message):
        return None

    def env(self, o):
        return {}

    def prepare(self, o, cwd):
        """Write any per-session config the agent reads from its folder (MCP servers)."""

    # how to read it
    def observe(self, ev):
        self.pending += int(est(ev) * 1.25) + 50

    def projected(self):
        return max(self.ctx, self.base) + self.pending

    def add_cost(self, inp, cached, out):
        if self.price:
            self.cost += ((inp - cached) * self.price[0] + cached * self.price[0] * 0.1 + out * self.price[1]) / 1e6

    def result(self, rc):
        err = " ".join(e for e in self.errors if e)
        sub = self.subtype or ("success" if rc == 0 and not self.errors else "error_during_execution")
        api = api_error_of(err) or (api_error_of(self.text) if rc else "")
        return sub, round(self.cost, 2), self.turns, api, (self.text or err)

    def model_ok(self):
        """Did the agent report only the pin's model? None: it reports none, so the flag is the only check."""
        if not self.models:
            return None
        want = (self.p["canonical"], self.p["model"])
        return all(any(m.startswith(w) or w.startswith(m) for w in want) for m in self.models)


class Claude(Agent):
    name = "claude"
    hook_guard = True
    system_flag = True
    overhead = 0

    def argv(self, o, prompt=""):
        a = self.cmd() + ["-p", "--model", self.p["model"]]
        if self.p["effort"]:
            a += ["--effort", self.p["effort"]]
        if o.get("preflight"):
            return a + ["--strict-mcp-config", "--output-format", "json", "--no-session-persistence"] + self.extra()
        if o.get("system"):
            a += ["--append-system-prompt-file", o["system"]]
        if o.get("settings"):
            a += ["--settings", o["settings"]]
        if o.get("mcp"):
            a += ["--mcp-config", o["mcp"]]
        a += ["--strict-mcp-config", "--permission-mode", "dontAsk"]
        if o.get("readonly"):
            a += ["--allowedTools", "Read", "Glob", "Grep", "WebFetch", "WebSearch",
                  "--disallowedTools", "Agent", "Task", "Workflow", "Edit", "Write", "Bash"]
        else:
            a += ["--disallowedTools", "Agent", "Task", "Workflow"]
        if o.get("budget"):
            a += ["--max-budget-usd", str(o["budget"])]
        return a + ["--output-format", "stream-json", "--verbose"] + self.extra()

    def env(self, o):
        # background calls run on the pinned model too, and the hook checks the pin
        m = self.p["canonical"]
        return {"ANTHROPIC_DEFAULT_HAIKU_MODEL": m, "ANTHROPIC_SMALL_FAST_MODEL": m, "LOOP_MODEL": m,
                "LOOP_EFFORT": self.p["effort"], "LOOP_CTX_WINDOW": str(self.p["window"])}

    def observe(self, ev):
        t = ev.get("type")
        if t == "assistant":
            m = ev.get("message") or {}
            u = m.get("usage")
            if ev.get("error"):
                self.errors.append(str(ev.get("error")))
            if u and m.get("model") != "<synthetic>" and not ev.get("isSidechain"):
                self.ctx = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                self.pending = u.get("output_tokens", 0)
                if m.get("model"):
                    self.models.add(m["model"])
        elif t == "user":
            self.pending += int(est((ev.get("message") or {}).get("content")) * 1.25) + 300
        elif t == "system" and ev.get("session_id"):
            self.session_id = ev["session_id"]
        elif t == "result":
            self.subtype = ev.get("subtype", "")
            self.cost = ev.get("total_cost_usd") or 0
            self.turns = ev.get("num_turns") or 0
            self.text = ev.get("result") or ""
            e = str(ev.get("api_error") or "") or ("rate_limit_error" if ev.get("api_error_status") == 429 else "")
            if e:
                self.errors.append(e)
            for k, v in (ev.get("modelUsage") or {}).items():
                self.models.add(v.get("canonicalModel") or k)

    def result(self, rc):
        e = " ".join(self.errors)
        api = e if e in ("usage_limit_reached", "rate_limit_error") else api_error_of(e)
        return self.subtype or "no-result", round(self.cost, 2), self.turns, api, self.text


class Codex(Agent):
    name = "codex"

    def args_for(self, o):
        a = self.cmd() + ["exec", "--json", "--skip-git-repo-check", "-m", self.p["model"]]
        if self.p["effort"]:
            a += ["-c", f"model_reasoning_effort={self.p['effort']}"]
        if o.get("readonly"):
            a += ["--sandbox", "read-only"]
        else:
            sb = o.get("codex_sandbox") or "workspace-write"
            a += ["--sandbox", sb]
            if sb == "workspace-write":
                a += ["-c", "sandbox_workspace_write.network_access=true"]
                if o.get("run"):
                    a += ["--add-dir", o["run"]]
        if o.get("preflight"):
            a += ["--ephemeral"]
        for name, s in load_mcp(o["mcp"]).items() if o.get("mcp") else []:
            k = f"mcp_servers.{name}"
            a += ["-c", f"{k}.command={json.dumps(s.get('command', ''))}", "-c", f"{k}.args={json.dumps(s.get('args', []))}"]
            if s.get("env"):
                a += ["-c", f"{k}.env={{" + ", ".join(f"{e} = {json.dumps(v)}" for e, v in s["env"].items()) + "}"]
        if o.get("mcp"):
            a += ["--trust"]   # Codex keeps MCP off in a folder it doesn't trust
        return a + self.extra()

    def argv(self, o, prompt=""):
        return self.args_for(o) + ["-"]

    def resume_argv(self, o, message):
        return self.args_for(o) + ["resume", self.session_id, message]

    def observe(self, ev):
        t = ev.get("type", "")
        if t == "thread.started":
            self.session_id = ev.get("thread_id", "") or self.session_id
        elif t == "item.completed":
            it = ev.get("item") or {}
            self.pending += int(est(it) * 1.25) + 50
            ty = it.get("type") or it.get("item_type")
            if ty == "agent_message":
                self.text = it.get("text") or self.text
            elif ty in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
                self.turns += 1
            elif ty == "error":
                self.errors.append(str(it.get("message", "")))
        elif t == "turn.completed":
            u = ev.get("usage") or {}   # the whole turn's totals: a cost, not this session's occupancy
            self.add_cost(u.get("input_tokens", 0), u.get("cached_input_tokens", 0), u.get("output_tokens", 0))
        elif t in ("turn.failed", "error"):
            self.errors.append(json.dumps(ev.get("error") or ev.get("message") or ev))


class Agy(Agent):
    name = "agy"
    prompt_arg = True

    def args_for(self, o, prompt):
        a = self.cmd() + ["-p", prompt, "--output-format", "stream-json", "--model", self.p["model"]]
        if self.p["effort"]:
            a += ["--effort", self.p["effort"]]
        if not o.get("readonly"):
            a += ["--dangerously-skip-permissions"]
        return a

    def argv(self, o, prompt=""):
        return self.args_for(o, prompt) + self.extra()

    def resume_argv(self, o, message):
        return self.args_for(o, message) + ["--conversation", self.session_id] + self.extra()

    def prepare(self, o, cwd):
        if o.get("mcp"):
            os.makedirs(os.path.join(cwd, ".agents"), exist_ok=True)
            json.dump({"mcpServers": load_mcp(o["mcp"])}, open(os.path.join(cwd, ".agents", "mcp_config.json"), "w"), indent=1)

    def observe(self, ev):
        e = ev.get("event", "")
        self.session_id = ev.get("conversation_id") or self.session_id
        if e == "init" and ev.get("model"):
            self.models.add(str(ev["model"]))
        elif e == "step_update":
            if ev.get("state") == "DONE":
                self.pending += int(est(ev) * 1.25) + 50
                if ev.get("step_type") == "tool":
                    self.turns += 1
                elif ev.get("step_type") == "agent_response":
                    self.text = first_text(ev) or self.text
        elif e == "result":
            self.text = first_text(ev, ("response", "result", "text")) or self.text
            st = str(ev.get("status", "SUCCESS"))
            if st != "SUCCESS":
                self.errors.append(f"{st}: {first_text(ev, ('error', 'message')) or ''}")


class Gemini(Agent):
    name = "gemini"

    def args_for(self, o):
        mode = "plan" if o.get("readonly") else "yolo"
        return self.cmd() + ["-m", self.p["model"], "--output-format", "stream-json", "--approval-mode", mode]

    def argv(self, o, prompt=""):
        # the task comes on stdin; -p is appended to it and makes the run headless
        return self.args_for(o) + ["-p", "Carry out the instructions above."] + self.extra()

    def resume_argv(self, o, message):
        return self.args_for(o) + ["--resume", "latest", "-p", message] + self.extra()

    def prepare(self, o, cwd):
        if o.get("mcp"):
            os.makedirs(os.path.join(cwd, ".gemini"), exist_ok=True)
            json.dump({"mcpServers": load_mcp(o["mcp"])}, open(os.path.join(cwd, ".gemini", "settings.json"), "w"), indent=1)

    def observe(self, ev):
        t = ev.get("type", "")
        if t == "init":
            self.session_id = ev.get("session_id") or self.session_id
            if ev.get("model"):
                self.models.add(str(ev["model"]))
        elif t == "message" and ev.get("role") == "assistant":
            chunk = first_text(ev, ("content", "text"))
            self.text = (self.text + chunk) if ev.get("delta") else chunk
            self.pending += est(chunk)
        elif t == "tool_use":
            self.turns += 1
            self.pending += int(est(ev) * 1.25) + 50
            self.text = ""
        elif t == "tool_result":
            self.pending += int(est(ev) * 1.25) + 300
        elif t == "error":
            self.errors.append(first_text(ev, ("message", "error")) or json.dumps(ev))
        elif t == "result" and str(ev.get("status", "success")).lower() != "success":
            self.errors.append(first_text(ev, ("error", "message")) or str(ev.get("status")))


class Grok(Agent):
    name = "grok"
    prompt_arg = True

    def __init__(self, p):
        super().__init__(p)
        self.session_id = str(uuid.uuid4())   # a named session: -s creates it, and resumes it later

    def args_for(self, o, prompt):
        a = self.cmd() + ["-p", prompt, "-m", self.p["model"], "--output-format", "streaming-json", "--no-auto-update",
                          "-s", self.session_id]
        if not o.get("readonly"):
            a += ["--always-approve"]
        return a + self.extra()

    def argv(self, o, prompt=""):
        return self.args_for(o, prompt)

    def resume_argv(self, o, message):
        return self.args_for(o, message)

    def prepare(self, o, cwd):
        if o.get("mcp"):
            os.makedirs(os.path.join(cwd, ".grok"), exist_ok=True)
            lines = []
            for name, s in load_mcp(o["mcp"]).items():
                lines += [f"[mcp_servers.{name}]", f"command = {json.dumps(s.get('command', ''))}",
                          f"args = {json.dumps(s.get('args', []))}", ""]
            open(os.path.join(cwd, ".grok", "config.toml"), "w").write("\n".join(lines))

    def observe(self, ev):
        # Grok Build documents no event schema: read what is there, and estimate the rest
        t = str(ev.get("type") or ev.get("event") or "")
        self.pending += int(est(ev) * 1.25) + 50
        if "tool" in t and ("result" in t or "end" in t or "complete" in t):
            self.turns += 1
        if t in ("result", "final", "done", "message", "assistant", "text") or "stopReason" in ev:
            self.text = first_text(ev) or self.text
        if ev.get("model"):
            self.models.add(str(ev["model"]))
        if ev.get("error") or t == "error":
            self.errors.append(first_text(ev, ("error", "message")) or json.dumps(ev)[:300])
        u = ev.get("usage")
        if isinstance(u, dict) and isinstance(ev.get("cost"), (int, float)):
            self.cost = float(ev["cost"])


class OpenCode(Agent):
    name = "opencode"
    prompt_arg = True

    def model_arg(self):
        return self.p["model"] + (f"#{self.p['effort']}" if self.p["effort"] else "")

    def argv(self, o, prompt=""):
        return self.cmd() + ["run", "--format", "json", "--model", self.model_arg()] + self.extra() + [prompt]

    def resume_argv(self, o, message):
        return self.cmd() + ["run", "--format", "json", "--model", self.model_arg(), "--session", self.session_id] \
            + self.extra() + [message]

    def env(self, o):
        return {"OPENCODE_CONFIG": os.path.join(o.get("state") or state_dir(), "opencode.json")}

    def prepare(self, o, cwd):
        allow = "deny" if o.get("readonly") else "allow"
        d = {"permission": {"edit": allow, "bash": allow, "webfetch": "allow"}}
        if o.get("mcp"):
            d["mcp"] = {n: {"type": "local", "command": [s.get("command", "")] + list(s.get("args", [])),
                            "environment": s.get("env", {}), "enabled": True} for n, s in load_mcp(o["mcp"]).items()}
        os.makedirs(o.get("state") or state_dir(), exist_ok=True)
        json.dump(d, open(os.path.join(o.get("state") or state_dir(), "opencode.json"), "w"), indent=1)

    def observe(self, ev):
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        self.session_id = ev.get("sessionID") or part.get("sessionID") or self.session_id
        t = ev.get("type", "")
        if t == "step_finish":
            tk = part.get("tokens") or {}
            cache = tk.get("cache") or {}
            self.ctx = tk.get("input", 0) + cache.get("read", 0) + cache.get("write", 0) + tk.get("output", 0)
            self.pending = 0
            self.cost += float(part.get("cost") or 0)
        elif t == "text":
            self.text = part.get("text") or self.text
        elif t == "tool_use":
            self.turns += 1
            self.pending += int(est(part) * 1.25) + 300
        elif t == "error":
            self.errors.append(first_text(ev, ("error", "message")) or json.dumps(ev)[:300])


class Cursor(Agent):
    name = "cursor"
    prompt_arg = True

    def args_for(self, o):
        a = self.cmd() + ["-p", "--output-format", "stream-json", "--model", self.p["model"]]
        if not o.get("readonly"):
            a += ["--force"]
        if o.get("mcp"):
            a += ["--approve-mcps"]
        return a + self.extra()

    def argv(self, o, prompt=""):
        return self.args_for(o) + [prompt]

    def resume_argv(self, o, message):
        return self.args_for(o) + ["--resume", self.session_id, message]

    def prepare(self, o, cwd):
        if o.get("mcp"):
            os.makedirs(os.path.join(cwd, ".cursor"), exist_ok=True)
            json.dump({"mcpServers": load_mcp(o["mcp"])}, open(os.path.join(cwd, ".cursor", "mcp.json"), "w"), indent=1)

    def observe(self, ev):
        t = ev.get("type", "")
        self.session_id = ev.get("session_id") or self.session_id
        if t == "system" and ev.get("model"):
            self.models.add(str(ev["model"]))
        elif t == "assistant":
            self.text = first_text(ev.get("message") or ev) or self.text
            self.pending += int(est(ev) * 1.25)
        elif t == "tool_call":
            self.pending += int(est(ev) * 1.25) + 50
            if ev.get("subtype") == "completed":
                self.turns += 1
        elif t == "result":
            self.subtype = "success" if not ev.get("is_error") else "error_during_execution"
            self.text = ev.get("result") or self.text
            if ev.get("is_error"):
                self.errors.append(str(ev.get("result") or "error"))


AGENTS = {"claude": Claude, "codex": Codex, "agy": Agy, "gemini": Gemini, "grok": Grok, "opencode": OpenCode,
          "cursor": Cursor}


def agent_for(p):
    cls = AGENTS.get(p["agent"])
    if not cls:
        raise crew.PinError(f"agent_run.py has no adapter for {p['agent']}")
    return cls(p)


# ------------------------------------------------------------------------------------------------- guard-rail shims

SHIM = r"""#!/usr/bin/env bash
# MetaLoop guard rail (made by agent_run.py): refuses what this run's settings.json denies a loop session, then runs the
# real command. The same deny list Claude Code enforces, for agents that have no allow/deny list of their own.
self="${0##*/}"; dir="$(cd "$(dirname "$0")" && pwd)"
real=""; OLDIFS="$IFS"; IFS=:
for d in $PATH; do  # the first one on PATH that is not this shim (compared as files: paths can be spelled two ways)
    [ -x "$d/$self" ] || continue; [ "$d/$self" -ef "$dir/$self" ] && continue; real="$d/$self"; break
done
IFS="$OLDIFS"
[ -n "$real" ] || { echo "$self: not found" >&2; exit 127; }
cmd="$self $*"
if [ -f "$dir/$self.allow" ]; then
    while IFS= read -r pat; do [ -n "$pat" ] || continue; case "$cmd" in $pat) exec "$real" "$@" ;; esac; done < "$dir/$self.allow"
fi
while IFS= read -r pat; do
    [ -n "$pat" ] || continue
    case "$cmd" in $pat) echo "MetaLoop: '$cmd' is not allowed in a loop session (the runbook says who does this instead)." >&2; exit 126 ;; esac
done < "$dir/$self.deny"
exec "$real" "$@"
"""
SHIM_TOOLS = ("git", "gh")
SHIM_ALLOW = {"gh": ["gh api --method GET *"]}   # pr_read.py's read-only calls


def make_shims(settings, state):
    """state/shims/{git,gh} from the Bash(git ...) / Bash(gh ...) deny rules of settings.json. Returns the folder."""
    try:
        deny = json.load(open(settings, encoding="utf8"))["permissions"]["deny"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    folder = os.path.join(state, "shims")
    os.makedirs(folder, exist_ok=True)
    made = False
    for tool in SHIM_TOOLS:
        pats = [m.group(1) for m in (re.fullmatch(r"Bash\((.*)\)", r) for r in deny)
                if m and (m.group(1) == tool or m.group(1).startswith((tool + " ", tool + "*")))]
        if not pats:
            continue
        open(os.path.join(folder, tool + ".deny"), "w", encoding="utf8", newline="\n").write("\n".join(pats) + "\n")
        if SHIM_ALLOW.get(tool):
            open(os.path.join(folder, tool + ".allow"), "w", encoding="utf8", newline="\n").write("\n".join(SHIM_ALLOW[tool]) + "\n")
        p = os.path.join(folder, tool)
        open(p, "w", encoding="utf8", newline="\n").write(SHIM)
        os.chmod(p, 0o755)
        made = True
    return folder if made else None


# -------------------------------------------------------------------------------------------------------- sessions

def loop_state():
    return os.environ.get("LOOP_STATE") or HERE


def state_dir():
    return os.path.join(loop_state(), "state")


def ctx_log(line):
    p = os.path.join(loop_state(), "logs", "ctx.log")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf8") as f:
        f.write(time.strftime("%H:%M:%S ") + line + "\n")


def write_stop(reason, kind, pct, ag):
    os.makedirs(state_dir(), exist_ok=True)
    json.dump({"reason": reason, "session": ag.session_id, "tool": "(wrapper)", "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "kind": kind, "pct": round(pct, 4), "agent": ag.p["agent"]},
              open(os.path.join(state_dir(), "last_stop.json"), "w", encoding="utf8"))


def terminate(proc):
    try:
        if WIN:
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            proc.kill() if WIN else os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def run_once(ag, argv, text, env, log, err, soft, hard, deadline):
    """Start the agent, feed it `text` on stdin, copy its stream to `log` while watching it. Returns (rc, why): why is ''
    (it ended by itself), 'soft' (interrupted at the hand-off threshold), 'hard' (ended at the hard stop) or 'timeout'."""
    kw = {} if WIN else {"start_new_session": True}
    with open(err, "ab") as ef:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=ef, env=env, **kw)
        except OSError as e:   # gone since the check, or not runnable: an error the result line reports
            ag.errors.append(f"could not start {argv[0]}: {e}")
            ef.write(f"could not start {argv[0]}: {e}\n".encode())
            return 126, ""

    def feed():
        try:
            proc.stdin.write(text.encode("utf8"))
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    threading.Thread(target=feed, daemon=True).start()
    timed_out = []

    def watchdog():   # deadline is on the monotonic clock: it stops while the computer sleeps, so a sleep is no hang
        while proc.poll() is None:
            if time.monotonic() > deadline:
                timed_out.append(1)
                terminate(proc)
                return
            time.sleep(5)
    threading.Thread(target=watchdog, daemon=True).start()
    why = ""
    window = ag.p["window"]
    with open(log, "ab") as lf:
        for raw in proc.stdout:
            lf.write(raw)
            lf.flush()
            try:
                ev = json.loads(raw)
            except ValueError:
                ag.pending += est(raw.decode("utf8", "replace"))
                continue
            if isinstance(ev, dict):
                ag.observe(ev)
            pct = ag.projected() / window
            if pct >= hard:
                ctx_log(f"{ag.session_id[:8]} {ag.p['agent']} ctx~{ag.projected()} -> {pct:.1%} HARD-STOP (wrapper)")
                write_stop(f"projected context {pct:.1%} >= hard stop {hard:.0%}", "hard-cap", pct, ag)
                why = "hard"
                terminate(proc)
                break
            if soft is not None and pct >= soft:
                ctx_log(f"{ag.session_id[:8]} {ag.p['agent']} ctx~{ag.projected()} -> {pct:.1%} HANDOFF (wrapper)")
                why = "soft"
                terminate(proc)
                break
    rc = proc.wait()
    if timed_out and not why:
        why = "timeout"
    return rc, why


def handoff_written(since):
    """Did this session record its progress and write its hand-off (the end gates' test)?"""
    st = loop_state()

    def fresh(name):
        p = os.path.join(st, name)
        return os.path.exists(p) and os.path.getmtime(p) >= since
    status = os.path.join(st, "status.json")
    if os.path.exists(status):   # a fix chain (LOOP_STATE is its .fixloop)
        try:
            if json.load(open(status, encoding="utf8")).get("status") in ("awaiting_owner", "needs_owner", "blocked", "done"):
                return True
        except (OSError, ValueError):
            pass
        return fresh("status.json") and fresh("HANDOFF.md")
    if os.path.exists(os.path.join(st, "ledger.json")):   # the verify loop
        r = subprocess.run([sys.executable, os.path.join(st, "ledger.py"), "remaining"], capture_output=True, text=True, cwd=st)
        if r.stdout.strip() == "0":
            return True
        return fresh("ledger.json") and fresh("HANDOFF.md")
    return True


def task_text(ag, prompt, system, state):
    """What the agent is given: the run's rules (unless it takes them as a system prompt), its notes, then the task. An
    argument-prompt agent gets a long one as a file to read."""
    parts = []
    if system and not ag.system_flag:
        parts += [system, AGENT_NOTES.format(name=ag.info["name"], model=ag.p["label"]), "---"]
    parts.append(prompt)
    text = "\n\n".join(parts)
    if ag.prompt_arg and len(text.encode("utf8")) > ARG_MAX_PROMPT:
        os.makedirs(state, exist_ok=True)
        path = os.path.join(state, f"task-{int(time.time())}.md")
        open(path, "w", encoding="utf8").write(text)
        text = (f"Read the file {path} in full before anything else: it holds this run's rules and your task for this "
                "session. Then carry it out.")
    return text


def session(role, o):
    c = conf()
    p = crew.parse(o["pin"]) if o.get("pin") else pin_for(role, c)
    ag = agent_for(p)
    if not installed(ag.bin):
        print(missing_line(ag, role))
        return 127
    soft = float(c.get("SOFT_PCT", "65")) / 100
    hard = float(c.get("HARD_PCT", "72")) / 100
    o.setdefault("budget", c.get("BUDGET_PER_SESSION"))
    o.setdefault("run", c.get("RUN") or HERE)
    o.setdefault("codex_sandbox", c.get("CODEX_SANDBOX") or "workspace-write")
    o.setdefault("state", state_dir())
    deadline = time.monotonic() + 60 * float(c.get("SESSION_TIMEOUT_MIN") or 300)   # awake time, not wall-clock time
    env = dict(os.environ)
    env.update(ag.env(o))
    env.update({"LOOP_CTX_WINDOW": str(p["window"]), "LOOP_SOFT_PCT": c.get("SOFT_PCT", "65"),
                "LOOP_HARD_PCT": c.get("HARD_PCT", "72")})
    if not ag.hook_guard and o.get("settings") and not o.get("readonly") and not WIN:
        shims = make_shims(o["settings"], o["state"])   # bash shims: macOS and Linux (Windows agents start commands elsewhere)
        if shims:
            env["PATH"] = shims + os.pathsep + env.get("PATH", "")
    ag.prepare(o, os.getcwd())
    prompt = open(o["prompt"], encoding="utf8").read()
    system = open(o["system"], encoding="utf8").read() if o.get("system") else ""
    text = task_text(ag, prompt, system, o["state"])
    ag.base = est(text) + ag.overhead
    start = time.time()
    for path in (o["log"], o["err"]):
        open(path, "wb").close()
    if ag.prompt_arg:
        argv, stdin = ag.argv(o, text), ""
    else:
        argv, stdin = ag.argv(o), text
    # Claude Code's own hook hands off and stops it; the wrapper only backs that up, a point above the hard stop
    rc, why = run_once(ag, argv, stdin, env, o["log"], o["err"], None if ag.hook_guard else soft,
                       min(hard + 0.01, 0.75) if ag.hook_guard else hard, deadline)
    # Without hooks: the hand-off threshold interrupts the session and resumes it with the hand-off ask, and a session
    # that ended without its hand-off is resumed once to write it.
    if not ag.hook_guard and ag.resumable and ag.session_id and not o.get("readonly"):
        ask = None
        if why == "soft":
            ask = HANDOFF_ASK.format(pct=ag.projected() / p["window"], soft=soft, hard=hard)
        elif why == "" and rc == 0 and not handoff_written(start):
            ask = ENDGATE_ASK
        if ask:
            ctx_log(f"{ag.session_id[:8]} {p['agent']} resumed: {ask[:40]}")
            rc, why = run_once(ag, ag.resume_argv(o, ask), "", env, o["log"], o["err"], None, hard, deadline)
    if why == "soft":   # it could not be resumed: the session ends here, as the hard stop would end it
        write_stop(f"projected context reached the hand-off threshold {soft:.0%}, and the session could not be resumed "
                   "to hand off", "hard-cap", ag.projected() / p["window"], ag)
    elif why == "timeout":
        write_stop(f"the session ran past SESSION_TIMEOUT_MIN ({c.get('SESSION_TIMEOUT_MIN') or 300} minutes)", "timeout",
                   ag.projected() / p["window"], ag)
    if o.get("answer"):
        open(o["answer"], "w", encoding="utf8").write((ag.text or "").strip() + "\n")
    sub, cost, turns, api, txt = ag.result(rc)
    print("|".join([sub, str(cost), str(turns), api, (txt or "")[:160].replace("\n", " ").replace("|", "/")]))
    return rc


def preflight(role, pin=None):
    """A one-line call with the session's own flags (so a flag the installed CLI doesn't know shows up here, not mid-run)."""
    c = conf()
    p = crew.parse(pin) if pin else pin_for(role, c)
    ag = agent_for(p)
    if not installed(ag.bin):
        return f"{ag.info['name']} (`{ag.bin}`) is not on PATH: {ag.info['install']}"
    o = {"preflight": True, "run": c.get("RUN") or HERE, "codex_sandbox": c.get("CODEX_SANDBOX") or "workspace-write",
         "state": state_dir()}
    env = dict(os.environ)
    env.update(ag.env(o))
    ask = "Reply with the single word READY."
    ag.prepare(o, HERE)
    argv, stdin = (ag.argv(o, ask), "") if ag.prompt_arg else (ag.argv(o), ask)
    try:
        r = subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env, timeout=600, cwd=HERE)
    except subprocess.TimeoutExpired:
        return f"{p['pin']}: no answer in 10 minutes"
    out = r.stdout or ""
    events = []
    try:
        events = [json.loads(out)]
    except ValueError:
        for line in out.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    for ev in events:
        if isinstance(ev, dict):
            ag.observe(ev)
    if ag.name == "claude":  # Claude Code reports every model a call used, and the window: both must be the pin's
        d = events[0] if events and isinstance(events[0], dict) else {}
        mu = d.get("modelUsage") or {}
        bad = [k for k, v in mu.items() if v.get("canonicalModel") != p["canonical"]]
        win = sorted({v.get("contextWindow") for v in mu.values()})
        res = (d.get("result") or "").strip()
        ok = res.upper().startswith("READY") and mu and not bad and win == [p["window"]]
        return "ok" if ok else f"models={list(mu)} windows={win} result={res[:80]!r}"
    said = (ag.text or "").strip()
    api = api_error_of(" ".join(ag.errors) + " " + (r.stderr or "")[-400:])
    if api:
        return f"{p['pin']}: {api} (a usage limit) {(r.stderr or '').strip()[-120:]!r}"
    if r.returncode != 0 or not said.upper().lstrip("*`_ \"'").startswith("READY"):
        return f"{p['pin']}: rc={r.returncode} said={said[:60]!r} err={(r.stderr or '').strip()[-160:]!r}"
    if ag.model_ok() is False:
        return f"{p['pin']}: answered as {sorted(ag.models)}, not {p['model']}"
    return "ok"


def summary(log, agent):
    ag = agent_for(crew.parse(f"{agent}/x"))
    for line in open(log, encoding="utf8", errors="replace"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            ag.observe(ev)
    sub, cost, turns, api, txt = ag.result(0)
    print("|".join([sub, str(cost), str(turns), api, (txt or "")[:160].replace("\n", " ").replace("|", "/")]))


def options(a):
    o, i = {}, 0
    flags = {"--prompt": "prompt", "--log": "log", "--err": "err", "--system": "system", "--settings": "settings",
             "--mcp": "mcp", "--pin": "pin", "--budget": "budget", "--answer": "answer", "--agent": "agent"}
    while i < len(a):
        if a[i] in flags and i + 1 < len(a):
            o[flags[a[i]]] = a[i + 1]
            i += 2
        elif a[i] == "--readonly":
            o["readonly"] = True
            i += 1
        else:
            raise crew.PinError(f"unknown option {a[i]}")
    return o


def main(a):
    try:
        if len(a) >= 2 and a[0] == "session":
            o = options(a[2:])
            missing = [k for k in ("prompt", "log", "err") if k not in o]
            if missing:
                raise crew.PinError(f"session needs --{', --'.join(missing)}")
            sys.exit(session(a[1], o))
        if a and a[0] == "preflight":
            if len(a) >= 3 and a[1] == "--pin":
                print(preflight(None, a[2]))
            else:
                print(preflight(a[1] if len(a) > 1 else "code"))
            return
        if len(a) >= 2 and a[0] == "argv":
            o = options(a[2:])
            c = conf()
            o.setdefault("run", c.get("RUN") or HERE)
            o.setdefault("codex_sandbox", c.get("CODEX_SANDBOX") or "workspace-write")
            p = crew.parse(o["pin"]) if o.get("pin") else pin_for(a[1], c)
            ag = agent_for(p)
            print(json.dumps({"argv": ag.argv(o, "<task>") if ag.prompt_arg else ag.argv(o),
                              "prompt": "argument" if ag.prompt_arg else "stdin", "env": ag.env(o),
                              "resume": ag.resume_argv(o, "<ask>") if ag.resumable and not ag.hook_guard else None,
                              "guard": "hook" if ag.hook_guard else "wrapper",
                              "rules": "system prompt" if ag.system_flag else "ahead of the task"}))
            return
        if len(a) == 2 and a[0] == "pin":
            print(pin_for(a[1])["pin"])
            return
        if len(a) >= 2 and a[0] == "summary":
            o = options(a[2:])
            summary(a[1], o.get("agent", "claude"))
            return
        print(__doc__)
        sys.exit(2)
    except crew.PinError as e:
        sys.exit(f"agent_run: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
