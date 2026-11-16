"""The crew: which coding agent, model and effort does each kind of work in a MetaLoop run.

Four roles, one pin each. A pin is `agent/model[:effort][@window]`, for example

  research  agy/gemini-4-carbon:high              read docs, the web and the code; answer questions (consult.py)
  plan      codex/gpt-6-astra:xhigh              design branches (mockups, schema pages), plan drafts and reviews
  code      claude/claude-opus-5-5[1m]:high      the fix chains: red, green, docs, gate, PR, CI
  verify    claude/claude-opus-5-5[1m]:xhigh     the live-verify loop

Any role may use any agent MetaLoop can drive (agents.json): Claude Code, Codex, Antigravity (agy), Gemini CLI, Grok
Build, OpenCode (any provider's model, open models included) and Cursor. A crew is a ready-made set of four pins (kit/crews/*.json); a
repository picks one in .metaloop/crew.json and may override single roles; a run keeps the pins it was made with in
loop.conf (<ROLE>_PIN), where they can be changed for the sessions that start after.

  python3 crew.py                          the crew in effect here (in a run folder: the run's; else this repository's)
  python3 crew.py presets                  every ready-made crew, role by role
  python3 crew.py use <preset>             base this repository's (or this run's) crew on a preset
  python3 crew.py use auto                 the first recommended crew whose agents are all installed here
  python3 crew.py set <role>=<pin> ...     give a role another agent or model, e.g. plan=codex/gpt-6-astra:xhigh
  python3 crew.py models [agent]           the agents and models MetaLoop knows: windows, efforts, what each is good at
  python3 crew.py check                    is each agent's CLI installed; does each pin answer a one-line call
  python3 crew.py --json                   the crew in effect, for scripts

Roles also answer to the words people use for them: execution/exec/build/coding = code, planning/design = plan,
qa/test = verify.
"""
import glob, json, os, re, shlex, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROLES = ("research", "plan", "code", "verify")
ALIASES = {"execution": "code", "exec": "code", "execute": "code", "build": "code", "coding": "code", "coder": "code",
           "planning": "plan", "planner": "plan", "design": "plan", "researcher": "research", "qa": "verify",
           "test": "verify", "verifier": "verify"}
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
DEFAULT_CREW = "claude"   # what a run gets when nothing chose a crew: every role on Claude Code, as MetaLoop always ran
DEFAULT_WINDOW = 200000


class PinError(ValueError):
    pass


def registry():
    return json.load(open(os.path.join(HERE, "agents.json"), encoding="utf8"))


def kit_dir():
    """The kit: this file's parent in the kit itself; in a run folder, the KIT its loop.json names."""
    if os.path.isdir(os.path.join(HERE, "..", "crews")):
        return os.path.normpath(os.path.join(HERE, ".."))
    try:
        return json.load(open(os.path.join(HERE, "loop.json"), encoding="utf8"))["KIT"]
    except (OSError, ValueError, KeyError):
        return os.path.normpath(os.path.join(HERE, ".."))


ORDER = ("frontier", "mixed", "clean-code", "budget", "claude", "codex", "gemini", "grok")


def presets():
    """Every ready-made crew, the recommended ones first."""
    out = {}
    for p in glob.glob(os.path.join(kit_dir(), "crews", "*.json")):
        d = json.load(open(p, encoding="utf8"))
        out[d.get("name") or os.path.basename(p)[:-5]] = d
    rank = {n: i for i, n in enumerate(ORDER)}
    return dict(sorted(out.items(), key=lambda kv: (rank.get(kv[0], len(ORDER)), kv[0])))


def role_name(r):
    r = r.strip().lower()
    r = ALIASES.get(r, r)
    if r not in ROLES:
        raise PinError(f"no role {r!r}: the roles are {', '.join(ROLES)}")
    return r


def parse(pin, reg=None):
    """agent/model[:effort][@window] -> dict(agent, model, effort, window, pin). The model may itself hold '/' and ':'
    (opencode/openrouter/qwen/qwen3-coder, opencode/ollama/qwen3-coder:30b): only a known effort word after the last ':'
    is read as the effort."""
    reg = reg or registry()
    s = (pin or "").strip()
    if "/" not in s:
        raise PinError(f"{pin!r} is not a pin: write agent/model[:effort], e.g. codex/gpt-6-astra:high")
    window = None
    m = re.search(r"@(\d+(?:\.\d+)?)([kKmM]?)$", s)
    if m:
        n = float(m.group(1)) * {"": 1, "k": 1000, "m": 1000000}[m.group(2).lower()]
        window, s = int(n), s[: m.start()]
    agent, model = s.split("/", 1)
    agent = agent.lower()
    effort = ""
    if ":" in model and model.rsplit(":", 1)[1].lower() in EFFORTS:
        model, effort = model.rsplit(":", 1)
        effort = effort.lower()
    info = reg["agents"].get(agent)
    if not info:
        raise PinError(f"{pin!r}: MetaLoop can't drive an agent called {agent!r} (it knows {', '.join(reg['agents'])})")
    if not model:
        raise PinError(f"{pin!r}: no model after {agent}/")
    if effort and effort not in info.get("efforts", []):
        if info.get("efforts"):
            raise PinError(f"{pin!r}: {info['name']} takes the efforts {', '.join(info['efforts'])}, not {effort}")
        raise PinError(f"{pin!r}: {info['name']} has no effort setting; drop :{effort}")
    known = model_info(agent, model, reg)
    if window is None:
        window = known.get("window") or info.get("window") or DEFAULT_WINDOW
    if not effort and info.get("efforts"):
        effort = known.get("effort") or info.get("default_effort") or ""
    return {"agent": agent, "model": model, "effort": effort, "window": int(window),
            "canonical": known.get("canonical") or model, "label": known.get("label") or model,
            "pin": f"{agent}/{model}" + (f":{effort}" if effort else ""), "known": bool(known)}


def model_info(agent, model, reg=None):
    reg = reg or registry()
    key = f"{agent}/{model}"
    for m in reg["models"]:
        if m["pin"] == key:
            return m
    return {}


def bin_of(agent, reg=None):
    reg = reg or registry()
    return os.environ.get(f"METALOOP_BIN_{agent.upper()}") or reg["agents"][agent]["bin"]


def installed(agent, reg=None):
    """Is this agent's CLI on PATH? (On Windows, a script with no extension counts: Git Bash runs it.)"""
    b = bin_of(agent, reg)
    if shutil.which(b):
        return True
    return os.name == "nt" and any(os.path.isfile(os.path.join(d, b)) for d in os.environ.get("PATH", "").split(os.pathsep))


def missing_agents(pins, reg=None):
    """The agents these pins need that aren't installed, in role order."""
    reg = reg or registry()
    out = []
    for r in ROLES:
        a = parse(pins[r], reg)["agent"]
        if a not in out and not installed(a, reg):
            out.append(a)
    return out


def first_runnable(reg=None):
    """The first crew, recommended ones first, whose agents are all installed here (None: no crew can run)."""
    for name, d in presets().items():
        if not missing_agents({r: d[r] for r in ROLES}, reg):
            return name
    return None


def install_hint(agent, reg=None):
    info = (reg or registry())["agents"][agent]
    return f"{info['name']} isn't installed here (`{bin_of(agent, reg)}`): {info['install']}; then {info['sign_in']}"


def preset_pins(name):
    ps = presets()
    if name not in ps:
        raise PinError(f"no crew called {name!r}: the crews are {', '.join(ps)}")
    return {r: ps[name][r] for r in ROLES}


def from_crew_file(path):
    """.metaloop/crew.json: {"crew": "<preset>", "<role>": "<pin>", ...}: the preset, then each role it overrides."""
    d = json.load(open(path, encoding="utf8"))
    pins = preset_pins(d.get("crew") or DEFAULT_CREW)
    for k, v in d.items():
        if k in ("crew", "about", "$comment"):
            continue
        pins[role_name(k)] = v
    return d.get("crew") or DEFAULT_CREW, pins


def legacy_pins(conf, kind):
    """A run made before crews: its MODEL_ARG/EFFORT (and PLAN_*) pins become Claude Code pins."""
    pins = {}
    if conf.get("MODEL_ARG"):
        pins["verify" if kind == "verify" else "code"] = f"claude/{conf['MODEL_ARG']}:{conf.get('EFFORT', 'high')}"
    if conf.get("PLAN_MODEL_ARG"):
        pins["plan"] = f"claude/{conf['PLAN_MODEL_ARG']}:{conf.get('PLAN_EFFORT', 'xhigh')}"
    return pins


def derived(pins, reg=None):
    """What loop.conf carries for each pin, so shell scripts and logs need no parsing: <ROLE>_AGENT, _MODEL, _EFFORT,
    _WINDOW. Plus the pre-crew keys (MODEL, MODEL_ARG, EFFORT, PLAN_*, CTX_WINDOW), still read by older scripts."""
    reg = reg or registry()
    out = {}
    for r in ROLES:
        p = parse(pins[r], reg)
        R = r.upper()
        out.update({f"{R}_PIN": p["pin"] + (f"@{p['window']}" if "@" in pins[r] else ""), f"{R}_AGENT": p["agent"],
                    f"{R}_MODEL": p["model"], f"{R}_EFFORT": p["effort"], f"{R}_WINDOW": str(p["window"])})
    c, pl = parse(pins["code"], reg), parse(pins["plan"], reg)
    out.update(MODEL=c["canonical"], MODEL_ARG=c["model"], EFFORT=c["effort"], CTX_WINDOW=str(c["window"]),
               PLAN_MODEL=pl["canonical"], PLAN_MODEL_ARG=pl["model"], PLAN_EFFORT=pl["effort"])
    return out


def run_pins(kind, conf):
    """The verify loop runs on the verify pin: its MODEL/EFFORT/CTX_WINDOW (the guard's and the preflight's) are that pin's."""
    if kind != "verify":
        return {}
    v = parse(conf["VERIFY_PIN"])
    return {"MODEL": v["canonical"], "MODEL_ARG": v["model"], "EFFORT": v["effort"], "CTX_WINDOW": str(v["window"])}


def sign_line(pins, role="code", reg=None):
    """SIGN_TOOL / SIGN_MODEL for the lines the orchestrator signs PR threads with."""
    reg = reg or registry()
    p = parse(pins[role], reg)
    return reg["agents"][p["agent"]]["name"], p["label"]


# ---- where the crew in effect lives: a run folder's loop.conf, else the repository's .metaloop/crew.json ----

def read_conf(path):
    conf = {}
    try:
        for line in open(path, encoding="utf-8"):
            m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.rstrip("\n"))
            if m:
                conf[m.group(1)] = (shlex.split(m.group(2)) or [""])[0]
    except (OSError, ValueError):
        pass
    return conf


def write_conf(path, updates):
    """Change keys of a run's loop.conf in place (comments and order kept), and its loop.json to match."""
    lines = open(path, encoding="utf-8").read().splitlines()
    done = set()
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if m and m.group(1) in updates:
            lines[i] = f"{m.group(1)}={shlex.quote(updates[m.group(1)])}"
            done.add(m.group(1))
    lines += [f"{k}={shlex.quote(v)}" for k, v in sorted(updates.items()) if k not in done]
    open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    j = os.path.join(os.path.dirname(path), "loop.json")
    if os.path.exists(j):
        d = json.load(open(j, encoding="utf-8"))
        d.update(updates)
        json.dump(d, open(j, "w", encoding="utf-8"), indent=1)


def repo_root():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else os.getcwd()


def where():
    """('run', loop.conf) when run from a run folder, else ('repo', .metaloop/crew.json)."""
    conf = os.path.join(os.getcwd(), "loop.conf")
    if not os.path.exists(conf) and os.path.exists(os.path.join(HERE, "loop.conf")):
        conf = os.path.join(HERE, "loop.conf")
    if os.path.exists(conf):
        return "run", conf
    return "repo", os.path.join(repo_root(), ".metaloop", "crew.json")


def current():
    """(source, crew name, pins) in effect here."""
    kind, path = where()
    if kind == "run":
        conf = read_conf(path)
        pins = preset_pins(conf.get("CREW") or DEFAULT_CREW)
        pins.update(legacy_pins(conf, conf.get("KIND", "fix")))
        pins.update({r: conf[f"{r.upper()}_PIN"] for r in ROLES if conf.get(f"{r.upper()}_PIN")})
        return f"run {os.path.dirname(path)}", conf.get("CREW") or "(custom)", pins
    if os.path.exists(path):
        name, pins = from_crew_file(path)
        return path, name, pins
    return "kit default (no .metaloop/crew.json)", DEFAULT_CREW, preset_pins(DEFAULT_CREW)


def table(pins, reg=None, base=None):
    reg = reg or registry()
    rows = []
    for r in ROLES:
        p = parse(pins[r], reg)
        a = reg["agents"][p["agent"]]
        mark = "" if base is None or base.get(r) == pins[r] else "  (changed)"
        rows.append(f"  {r:<9} {p['pin']:<44} {a['name']} · {p['label']} · {p['window'] // 1000}k window{mark}")
    return "\n".join(rows)


def cmd_show(as_json=False):
    src, name, pins = current()
    if as_json:
        print(json.dumps({"source": src, "crew": name, "pins": pins,
                          "parsed": {r: parse(pins[r]) for r in ROLES}}, indent=1))
        return
    base = presets().get(name, {})
    print(f"Crew: {name}   ({src})\n{table(pins, base=base)}")
    agents = sorted({parse(p)["agent"] for p in pins.values()})
    print(f"\nAgents this crew needs: {', '.join(registry()['agents'][a]['name'] for a in agents)}"
          "   (crew.py check: installed and answering?)")


def cmd_presets():
    reg = registry()
    ps = presets()
    for group in ("recommended", "one agent"):
        print(f"== {group}\n")
        for name, d in ps.items():
            if d.get("group", "recommended") != group:
                continue
            miss = missing_agents({r: d[r] for r in ROLES}, reg)
            here = "ready here" if not miss else "needs " + ", ".join(reg["agents"][a]["name"] for a in miss)
            print(f"{name} ({here}): {d.get('about', '')}")
            print(table({r: d[r] for r in ROLES}, reg))
            why = d.get("why") or {}
            for r in ("research", "plan", "code", "verify", "all"):
                if why.get(r):
                    print(f"    why {r}: {why[r]}")
            print()
    ready = [n for n, d in ps.items() if not missing_agents({r: d[r] for r in ROLES}, reg)]
    print(f"Ready on this machine: {', '.join(ready) or 'none (install an agent: crew.py models lists them)'}.")
    print(f"Pick one: crew.py use <name> (or use auto: the first ready one). Change one role: crew.py set code=<pin>. "
          f"(as of {reg.get('as_of', '?')}; reference/crew.md has the benchmarks and sources)")


def save(pins, crew_name):
    kind, path = where()
    if kind == "run":
        conf = read_conf(path)
        up = derived(pins)
        up.update(run_pins(conf.get("KIND", "fix"), {**conf, **up}))
        up["CREW"] = crew_name
        write_conf(path, up)
        print(f"Updated {path}: sessions that start from now on use these pins.")
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        base = preset_pins(crew_name) if crew_name in presets() else {}
        d = {"crew": crew_name if crew_name in presets() else DEFAULT_CREW}
        d.update({r: pins[r] for r in ROLES if base.get(r) != pins[r]})
        json.dump(d, open(path, "w", encoding="utf8"), indent=1)
        open(path, "a", encoding="utf8").write("\n")
        print(f"Wrote {path}: commit it, and every run made in this repository starts with this crew.")


def warn_missing(pins):
    """Say which agents the crew needs that this machine lacks: the run won't start until they are installed."""
    miss = missing_agents(pins)
    for a in miss:
        roles = [r for r in ROLES if parse(pins[r])["agent"] == a]
        if roles == ["research"]:
            print(f"note: {install_hint(a)}. Until it is, research questions (consult.py) won't run and the orchestrator "
                  "researches with its own tools; or give research another agent: crew.py set research=<pin>.")
        else:
            print(f"note: {install_hint(a)}. Until it is, {', '.join(roles)} sessions can't start "
                  f"(doctor.py and the preflight stop the run); or give {roles[0]} another agent: crew.py set {roles[0]}=<pin>.")
    if miss:
        alt = first_runnable()
        if alt:
            print(f"note: '{alt}' is the first crew this machine can run as it is (crew.py use {alt}).")


def cmd_use(name):
    if name == "auto":
        name = first_runnable()
        if not name:
            raise PinError("no crew can run here: none of its agents are installed (crew.py models lists them, with installs)")
        print(f"auto: '{name}' is the first recommended crew whose agents are all installed here.")
    pins = preset_pins(name)
    for r in ROLES:
        parse(pins[r])
    save(pins, name)
    print(table(pins))
    warn_missing(pins)


def cmd_set(assignments):
    _, name, pins = current()
    for a in assignments:
        if "=" not in a:
            raise PinError(f"{a!r}: write role=agent/model[:effort], e.g. plan=codex/gpt-6-astra:xhigh")
        r, pin = a.split("=", 1)
        r = role_name(r)
        parse(pin)
        pins[r] = pin.strip()
    save(pins, name)
    print(table(pins, base=presets().get(name)))
    warn_missing(pins)


def cmd_models(agent=None):
    reg = registry()
    print(f"Agents and models MetaLoop knows (as of {reg.get('as_of', '?')}). Any other model id works too: give its "
          "window with @, e.g. opencode/ollama/qwen3-coder:30b@256k.\n")
    for a, info in reg["agents"].items():
        if agent and a != agent:
            continue
        eff = ", ".join(info.get("efforts", [])) or "none"
        print(f"{a}: {info['name']} (`{info['bin']}`) · efforts: {eff} · context guard: {info['guard']}")
        for m in reg["models"]:
            if m["pin"].split("/", 1)[0] == a:
                good = ", ".join(m.get("good_for", []))
                print(f"  {m['pin']:<46} {m.get('window', 0) // 1000:>5}k  {m.get('label', '')}"
                      + (f" · best at: {good}" if good else ""))
                if m.get("notes"):
                    print(f"  {'':<46}        {m['notes']}")
        print()


def cmd_check():
    """Each agent the crew needs: on PATH, its version; each pin: a one-line call (agent_run.py preflight)."""
    _, name, pins = current()
    reg = registry()
    bad = 0
    for a in sorted({parse(p)["agent"] for p in pins.values()}):
        info = reg["agents"][a]
        exe = shutil.which(os.environ.get(f"METALOOP_BIN_{a.upper()}") or info["bin"])
        if not exe:
            bad += 1
            print(f"MISSING  {info['name']}: `{info['bin']}` is not on PATH. Install: {info['install']}; then {info['sign_in']}")
            continue
        v = subprocess.run([exe] + info.get("version_args", ["--version"]), capture_output=True, text=True, timeout=60)
        print(f"ok       {info['name']}: {(v.stdout or v.stderr).strip().splitlines()[0][:60] if (v.stdout or v.stderr).strip() else exe}")
    runner = os.path.join(HERE, "agent_run.py")
    for r in ROLES:
        p = parse(pins[r])
        if not shutil.which(reg["agents"][p["agent"]]["bin"]):
            continue
        out = subprocess.run([sys.executable, runner, "preflight", "--pin", pins[r]], capture_output=True, text=True,
                             timeout=600).stdout.strip()
        if out != "ok":
            bad += 1
        print(f"{'ok' if out == 'ok' else 'REFUSED':<8} {r}: {p['pin']}" + ("" if out == "ok" else f" -> {out}"))
    sys.exit(1 if bad else 0)


def main(a):
    try:
        if not a or a[0] in ("show",):
            cmd_show()
        elif a[0] == "--json":
            cmd_show(as_json=True)
        elif a[0] == "presets":
            cmd_presets()
        elif a[0] == "use" and len(a) == 2:
            cmd_use(a[1])
        elif a[0] == "set" and len(a) > 1:
            cmd_set(a[1:])
        elif a[0] == "models":
            cmd_models(a[1] if len(a) > 1 else None)
        elif a[0] == "check":
            cmd_check()
        elif a[0] == "parse" and len(a) == 2:   # for scripts and the self-test
            print(json.dumps(parse(a[1])))
        else:
            print(__doc__)
            sys.exit(2)
    except PinError as e:
        sys.exit(f"crew: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
