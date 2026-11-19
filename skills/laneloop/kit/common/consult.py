"""Ask a crew role a question in a fresh, read-only session, and get the answer as a file.

The research role (the crew's research pin) gathers facts: how an area of the code works, what a library's docs say,
what changed upstream, whether a drafted defect is real. The plan role answers design questions and reviews plans: a
model from another vendor reviewing a design or a fan-out that a different model drafted catches what one model's blind
spots miss. Each question is its own session on that role's agent and model, read-only: it reads the repository (and
the web, where its agent can) and answers in its final message, which lands in consults/<name>.md. Questions run in
parallel; the orchestrator reads the answers instead of reading the code itself.

  python3 consult.py <role> <name> "<question>" [--file PATH ...] [--cwd DIR]
  python3 consult.py <role> --batch questions.json [--par N] [--cwd DIR]
        questions.json: [{"name": "auth-flow", "question": "...", "files": ["docs/designs/x.md"]}, ...]
  python3 consult.py list                          every answer so far: role, pin, cost, first line

<role>: research or plan (code and verify work too). --cwd: the folder the session reads (default: the repository's
main checkout, REPO in loop.conf). Prints one line per question: the answer's path, the pin and the cost.
"""
import concurrent.futures, glob, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import crew  # noqa: E402

PROMPT = """# A question from the LaneLoop orchestrator ({role})

Folder: {cwd} (read it; change nothing). Now: {now}.

## Question

{question}
{material}
## How to answer

- Read-only: don't edit, create or delete any file, and run nothing that changes anything (no installs, no git
  writes, no network calls that post or modify).
- Back every claim about the code with file:line, and every claim about a library, API or service with its source.
- Say plainly what you could not verify, and what you assumed.
- Your final message is the answer, in Markdown, for an engineer who hasn't seen your session: the answer first, then the
  evidence, then the open points. It is saved as a file; nothing else you write is kept.
"""


def conf():
    return crew.read_conf(os.path.join(HERE, "loop.conf"))


def ask(role, name, question, files, cwd):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", name):
        return f"{name}: not a usable name (letters, digits, . _ -)", None
    folder = os.path.join(HERE, "consults", name)
    os.makedirs(folder, exist_ok=True)
    material = ""
    if files:
        material = "\n## Material (read each in full)\n\n" + "\n".join(f"- {os.path.abspath(f)}" for f in files) + "\n"
    prompt = os.path.join(folder, "prompt.md")
    open(prompt, "w", encoding="utf8").write(PROMPT.format(role=f"{role} role", cwd=cwd, now=time.strftime("%Y-%m-%d %H:%M"),
                                                           question=question.strip(), material=material))
    answer = os.path.join(HERE, "consults", f"{name}.md")
    env = dict(os.environ, LOOP_STATE=folder)
    r = subprocess.run([sys.executable, os.path.join(HERE, "agent_run.py"), "session", role, "--readonly",
                        "--prompt", prompt, "--answer", answer, "--log", os.path.join(folder, "session.jsonl"),
                        "--err", os.path.join(folder, "session.err")], cwd=cwd, env=env, capture_output=True, text=True)
    line = (r.stdout.strip().splitlines() or [""])[-1]
    sub, cost, _turns, api, text = (line.split("|", 4) + ["", "", "", "", ""])[:5]
    pin = subprocess.run([sys.executable, os.path.join(HERE, "agent_run.py"), "pin", role], capture_output=True,
                         text=True).stdout.strip()
    meta = {"name": name, "role": role, "pin": pin, "subtype": sub, "cost": cost, "api_error": api,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "question": question.strip()[:300]}
    json.dump(meta, open(os.path.join(folder, "meta.json"), "w", encoding="utf8"), indent=1)
    ok = os.path.exists(answer) and os.path.getsize(answer) > 2 and not api
    status = f"consults/{name}.md · {pin} · ${cost}" if ok else \
        f"{name}: no answer ({api or sub or 'rc ' + str(r.returncode)}: {text[:100]}) · see consults/{name}/session.err"
    return status, meta


def cmd_list():
    for m in sorted(glob.glob(os.path.join(HERE, "consults", "*", "meta.json"))):
        d = json.load(open(m, encoding="utf8"))
        ans = os.path.join(HERE, "consults", f"{d['name']}.md")
        first = ""
        if os.path.exists(ans):
            first = next((l.strip() for l in open(ans, encoding="utf8") if l.strip()), "")[:80]
        print(f"{d['name']:<24} {d['role']:<8} {d['pin']:<40} ${d['cost']:<6} {first}")


def main(a):
    if a and a[0] == "list":
        return cmd_list()
    if len(a) < 2:
        print(__doc__)
        sys.exit(2)
    try:
        role = crew.role_name(a[0])
    except crew.PinError as e:
        sys.exit(f"consult: {e}")
    rest = a[1:]
    files, cwd, par, batch = [], conf().get("REPO") or os.getcwd(), 4, None
    pos = []
    i = 0
    while i < len(rest):
        if rest[i] == "--file" and i + 1 < len(rest):
            files.append(rest[i + 1]); i += 2
        elif rest[i] == "--cwd" and i + 1 < len(rest):
            cwd = os.path.abspath(rest[i + 1]); i += 2
        elif rest[i] == "--par" and i + 1 < len(rest):
            par = max(1, int(rest[i + 1])); i += 2
        elif rest[i] == "--batch" and i + 1 < len(rest):
            batch = rest[i + 1]; i += 2
        else:
            pos.append(rest[i]); i += 1
    if batch:
        qs = json.load(open(batch, encoding="utf8"))
    elif len(pos) == 2:
        qs = [{"name": pos[0], "question": pos[1], "files": files}]
    else:
        print(__doc__)
        sys.exit(2)
    with concurrent.futures.ThreadPoolExecutor(max_workers=par) as ex:
        futs = [ex.submit(ask, role, q["name"], q["question"], q.get("files", []), cwd) for q in qs]
        bad = 0
        for f in futs:
            status, meta = f.result()
            bad += 0 if status.startswith("consults/") else 1
            print(status, flush=True)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
