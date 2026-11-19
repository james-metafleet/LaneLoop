"""Keep the session's live page current from this run folder's state: one tab per task (live_page.py adds them). Prints
the writes for the ArtifactData tool; nothing reaches the page until the main session writes them, and then marks them
written with --done.

  python live_sync.py --all | <tab> [<tab> ...]   the pending writes, as JSON {"url", "writes", "attention"}. Write
                                                 "writes" with ArtifactData (action batch, that url, at most 50 writes
                                                 per call, in order)
  python live_sync.py <...> --done                after every batch succeeded: mark those writes as written
  python live_sync.py --attention                 the main window's lines, one per thing a tab needs the owner for, each
                                                  with the page's link: post these, and nothing else about a task, in
                                                  the chat

--all is every tab of this run folder. The page's collections are flat and every document names its `tab`:
state/<tab> (one per tab, replaced as the task moves; only the main session writes it), session/<run> (the run's PR
label, repository, base and plan page), and events, asks, reviews and replies keyed <tab>~<id>. The owner's answers and
approvals come back as answers/<tab>~<question id> and approvals/<tab>~r<n>. Events, questions and review items each
create a new document, so none can race the owner's answers on the page. The writes are kept in live/pending.json and
offered again until --done; live/sync.json is what each tab has been sent.
"""
import hashlib, json, os, re, sys
from urllib.parse import quote_plus

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = json.load(open(os.path.join(HERE, "loop.json"), encoding="utf8"))
LIVE = os.path.join(HERE, "live")
OWNER = CONF.get("OWNER", "the owner")
DOC_WARN = 20000   # an artifact's database holds at most 25,000 documents
TS = r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)"
BUILD = [("red", "Failing test"), ("green", "Fix"), ("docs", "Docs"), ("gate", "Full test run"),
         ("security", "Security review"), ("pr", "Pull request"), ("ci", "CI and review")]
DESIGN = [("design", "Draft the page")]
READY = (None, "Ready for you")


def jload(path, default):
    try:
        return json.load(open(path, encoding="utf8"))
    except (OSError, ValueError):
        return default


def read(path):
    try:
        return open(path, encoding="utf8").read()
    except OSError:
        return ""


def urls():
    return jload(os.path.join(LIVE, "urls.json"), {})


def humanize(slug):
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", slug) if w)


def plain(text):
    """A task.py note, as the owner reads it on the page."""
    m = re.match(r"\[(\w+)\]\s*(.*)", text, re.S)
    if m:
        name = dict(BUILD + DESIGN).get(m.group(1), m.group(1))
        return (m.group(2).strip() or f"Started: {name}"), ""
    for pre, say, tone in (("awaiting_owner: ", "Ready for you: ", "ok"), ("needs_owner: ", "Needs you: ", "warn"),
                           ("blocked: ", "Stopped: ", "warn"), ("requeued: ", "Back in the queue: ", "")):
        if text.startswith(pre):
            return say + text[len(pre):], tone
    m = re.match(r"asked (Q\d+): (.*)", text)
    if m:
        return f"Asked you {m.group(1)}: {m.group(2)}", "warn"
    m = re.match(r"OWNER ANSWER (\S+) \([^)]*\): (.*?)(\. If that isn't what you carried on with.*)?$", text, re.S)
    if m:
        return f"You answered {m.group(1)}: {m.group(2)}", "ok"
    m = re.match(r"SIGNED OFF (\([^)]*\)): (.*?)\. Mode is now BUILD", text, re.S)
    if m:
        return f"Design signed off {m.group(1)}: {m.group(2)}", "ok"
    m = re.match(r"APPROVED REVIEW ITEMS (.*?) (\([^)]*\))", text)
    if m:
        return f"Review items {m.group(1)} approved {m.group(2)}", "ok"
    if text.startswith("REVIEW ITEMS DECIDED"):
        return "Review comments decided: nothing to change", "ok"
    m = re.match(r"task (T\d+) done: (.*)", text)
    if m:
        return f"Task {m.group(1)} done" + (f": {m.group(2)}" if m.group(2) else ""), "ok"
    return text, ""


def chain(slug, recs=None):
    """The page's content for one chain: its state, its lines, its questions and its review items (each with a key)."""
    fl = os.path.join(CONF["WT_ROOT"], slug, ".fixloop")
    merged = False
    if not os.path.exists(os.path.join(fl, "status.json")) and os.path.exists(os.path.join(HERE, "retired", slug, "status.json")):
        fl, merged = os.path.join(HERE, "retired", slug), True   # retire.sh removed the worktree and kept its state here
    brief = read(os.path.join(fl, "brief.md")) or read(os.path.join(HERE, "briefs", f"{slug}.md"))
    d = jload(os.path.join(fl, "status.json"), {"status": "todo", "phase": None, "pr": None, "notes": [], "reviews": []})
    notes, st = d.get("notes", []), d.get("status", "todo")
    design = re.search(r"^Mode: design\.", brief, re.M) and not any(" SIGNED OFF (" in n for n in notes)
    defs = (DESIGN if design else BUILD) + [READY]
    when = {}
    for n in notes:
        m = re.match(TS + r"\s+\[(\w+)\]", n)
        if m:
            when[m.group(2)] = m.group(1)
    phases = [p for p, _ in defs][:-1]   # READY has no phase: a chain with none yet hasn't started
    cur = (len(defs) if st == "done" else len(defs) - 1 if st == "awaiting_owner"
           else phases.index(d["phase"]) if d.get("phase") in phases else -1)
    steps = [{"name": name, "t": when.get(p, "") if p else "",
              "state": "done" if i < cur else ("held" if st in ("blocked", "needs_owner") else "now") if i == cur else "todo"}
             for i, (p, name) in enumerate(defs)]
    step = defs[cur][1] if 0 <= cur < len(defs) else ""
    asks = d.get("asks", [])
    waiting = [q for q in asks if q["status"] == "open"]
    summary = d.get("summary", "")
    holding = summary.startswith("holding the pull request")
    badge = {"todo": ("draft", "Back in the queue" if notes else "Queued"), "needs_owner": ("warn", "Needs you"),
             "awaiting_owner": ("warn", "Ready for you"), "blocked": ("draft", "Stopped"), "done": ("ok", "Done")}.get(st)
    if st == "in_progress":
        at = f" · {step}" if step else ""
        badge = ("warn", f"Needs you{at}") if waiting else ("info", f"Working{at}")
    badge = badge or ("draft", st)
    nxt = defs[cur + 1][1] if cur < len(defs) - 1 else ""
    quiet = {"todo": "Waiting for a free slot.", "blocked": f"Stopped: {summary}. The main session is looking at it.",
             "done": "Done.", "awaiting_owner": f"Ready for you: {summary}"}.get(st)
    if st == "in_progress" and not waiting:
        quiet = f"Nothing needs you. Next: {nxt}." if nxt else "Nothing needs you."
    done = set(d.get("tasks_done", []))
    tasks = [{"id": t, "text": x, "test": p, "done": t in done}
             for t, x, p in re.findall(r"^- (T\d+) · (.+?) · proved by: (.+)$", brief, re.M)]
    runs = [l.split("|") for l in read(os.path.join(fl, "state", "sessions.txt")).splitlines() if l.count("|") >= 3]
    cost = sum(float(r[2]) for r in runs if re.match(r"^\d+(\.\d+)?$", r[2] or ""))
    title = (re.search(r"^Title: (.+)$", brief, re.M) or [None, humanize(slug)])[1].strip()
    times = [m.group(1) for m in (re.match(TS, n) for n in notes) if m]
    answered = {q["id"]: q.get("answer", "") for q in asks if q["status"] == "answered"}
    cards = [dict(id=q["id"], kind="ask", title=q["title"], question=q["question"], options=q["options"],
                  recommend=q.get("recommend", "a"), at=q["at"]) for q in asks]
    for i, n in enumerate(notes):   # a stop with `needs` that isn't the hold for an open question: its summary is the ask
        m = re.match(TS + r"\s+needs_owner: (.*)", n, re.S)
        if m and not m.group(2).startswith("holding the pull request"):
            cards.append(dict(id=f"needs-{i}", kind="needs", title="The Chain Needs You", question=m.group(2), options=[],
                              recommend="", at=m.group(1)))
            if st != "needs_owner" or any(o.startswith(f"OWNER ANSWER needs-{i}") for o in (x[20:] for x in notes[i + 1:])):
                answered.setdefault(f"needs-{i}", "answered")
    items = []
    for k, r in enumerate(d.get("reviews", []), 1):
        rec = (recs or {}).get(str(k))
        if rec is None:
            continue   # each item goes up with the main session's recommendation, in plain words
        rec = rec if isinstance(rec, dict) else {"rec": rec}
        who, _, what = r["comment"].partition(":")
        items.append(dict(id=f"r{k}", n=k, who=re.sub(r"\s*#\d+$", "", who.strip()), what=rec.get("what") or what.strip(),
                          proposal=rec.get("proposal") or r.get("proposal", ""), rec=rec.get("rec", "Owner's call")))
    state = dict(title=title, kind="chain", branch=d.get("branch") or f"{CONF.get('BRANCH_PREFIX', '')}{CONF.get('SLUG_PREFIX', '')}{slug}",
                 pr=d.get("pr"), badge={"tone": badge[0], "text": badge[1]}, quiet=quiet, steps=steps, tasks=tasks,
                 counts=None, started=times[0] if times else "", at=times[-1] if times else "", sessions=len(runs),
                 cost=round(cost, 2), answered=answered, holding=holding, merged=merged, status=st)
    events = []
    for i, n in enumerate(notes):
        m = re.match(TS + r"\s+(.*)", n, re.S)
        events.append((f"n{i}", m.group(1) if m else "", *plain(m.group(2) if m else n)))
    lines = []
    for q in waiting:
        lines.append(f"Needs you · {slug}: {q['title']}")
    if st == "needs_owner" and not holding:
        lines.append(f"Needs you · {slug}: {summary}")
    if st == "awaiting_owner":
        lines.append(f"Ready · {slug}: {summary}")
    return state, events, cards, items, lines


def verify():
    d = jload(os.path.join(HERE, "ledger.json"), {"tests": []})
    tests = d.get("tests", [])
    c = {"pass": 0, "fail": 0, "blocked": 0, "running": 0, "todo": 0}
    for t in tests:
        k = {"in_progress": "running", "resumed": "todo", "awaiting_human": "running"}.get(t["status"], t["status"])
        c[k] = c.get(k, 0) + 1
    paused = os.path.exists(os.path.join(HERE, "PAUSED.md")) or any(t["status"] == "awaiting_human" for t in tests)
    now = next((t for t in tests if t["status"] in ("in_progress", "awaiting_human")), None)
    needs = [t for t in tests if t["status"] == "blocked" and t.get("needs_owner")]
    if paused:
        badge = ("warn", "Needs you · sign-in")
    elif not c["todo"] and not c["running"]:
        badge = ("ok", "Done")
    else:
        badge = ("warn" if needs else "info", f"Running · {now['id']}" if now else "Running")
    nxt = next((t["id"] for t in tests if t["status"] == "todo"), "")
    quiet = None if paused else ("Every test is recorded." if badge[1] == "Done" else f"Nothing needs you. Next: {nxt}." if nxt else "")
    times = sorted(x for t in tests for x in (t.get("started"), t.get("finished")) if x)
    state = dict(title=f"{humanize(CONF.get('RUN_NAME', 'verify'))} Verify Loop", kind="verify", branch=CONF.get("BASE", ""),
                 pr=None, badge={"tone": badge[0], "text": badge[1]}, quiet=quiet, steps=[], tasks=[], counts=c,
                 started=times[0] if times else "", at=times[-1] if times else "", sessions=len(d.get("sessions", [])),
                 cost=round(sum(float(s.get("cost") or 0) for s in d.get("sessions", []) if isinstance(s, dict)), 2),
                 answered={}, holding=False, merged=False, status="done" if badge[1] == "Done" else "in_progress")
    said = {"pass": ("passed", "ok"), "fail": ("failed", "warn"), "blocked": ("set aside", "")}
    events = []
    for t in tests:
        if t.get("started"):
            events.append((f"{t['id']}@s{t['started']}", t["started"], f"Started {t['id']} · {t['title']}", ""))
        if t["status"] in said and t.get("finished"):
            word, tone = said[t["status"]]
            events.append((f"{t['id']}@{t['finished']}", t["finished"], f"{t['id']} {word}: {t.get('result') or ''}".rstrip(": "), tone))
    events.sort(key=lambda e: e[1])
    cards = [dict(id=f"{t['id']}@{t['finished']}", kind="needs", title=f"{t['id']} · {t['title']}", question=t["ask"],
                  options=[], recommend="", at=t["finished"]) for t in needs]
    if now and now["status"] == "awaiting_human":
        cards.append(dict(id=f"{now['id']}@signin@{now.get('started')}", kind="signin", title=f"Sign In For {now['id']}",
                          question=now.get("ask") or read(os.path.join(HERE, "PAUSED.md")), options=[], recommend="",
                          at=now.get("started") or ""))
    lines = [f"Needs you · verify: sign in for {now['id']}: {now.get('ask') or ''}".rstrip(": ")] if paused and now else []
    lines += [f"Needs you · verify: {t['id']}: {t['ask']}" for t in needs]
    return state, events, cards, [], lines


def gh_url(label=None):
    repo = CONF.get("GH_REPO", "")
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo):
        return ""
    base = f"https://github.com/{repo}"
    return f"{base}/pulls?q=" + quote_plus(f'is:pr label:"{label}"', safe="") if label else base


def session_doc():
    """What the dashboard shows of this run: its PR label (a fix run's), repository, base and the plan page."""
    label = CONF.get("PR_LABEL", "") if CONF.get("KIND") == "fix" else ""
    return dict(run=CONF.get("RUN_NAME", ""), kind=CONF.get("KIND", ""), label=label, labelUrl=gh_url(label) if label else "",
                repo=CONF.get("GH_REPO", ""), repoUrl=gh_url(), base=CONF.get("BASE", ""), owner=OWNER,
                plan=urls().get("plan", ""), tabs=tabs())


def tabs():
    return jload(os.path.join(LIVE, "tabs.json"), [])


def content(tab, order):
    if tab == "verify" and CONF.get("KIND") == "verify":
        state, events, cards, items, lines = verify()
    else:
        state, events, cards, items, lines = chain(tab, jload(os.path.join(LIVE, f"{tab}.recs.json"), {}))
    state.update(tab=tab, order=-1 if state["kind"] == "verify" else order, run=CONF.get("RUN_NAME", ""))
    return state, events, cards, items, lines


def digest(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode("utf8")).hexdigest()


def tab_writes(tab, order, sync):
    """The writes this tab still needs, from what was last marked written, and its attention lines."""
    sync = dict({"events": 0, "keys": [], "state": "", "asks": [], "reviews": []}, **sync)
    state, events, cards, items, lines = content(tab, order)
    writes, new = [], json.loads(json.dumps(sync))
    keys = set(sync["keys"])
    for key, at, text, tone in events:
        if key in keys:
            continue
        new["events"] += 1; new["keys"].append(key)
        writes.append({"op": "set", "collection": "events", "doc_id": f"{tab}~e{new['events']:06d}",
                       "data": {"tab": tab, "seq": new["events"], "at": at, "text": text, "tone": tone}})
    for q in cards:
        if q["id"] not in sync["asks"]:
            new["asks"].append(q["id"])
            writes.append({"op": "set", "collection": "asks", "doc_id": f"{tab}~{q['id']}", "data": dict(q, tab=tab)})
    current = {q["id"] for q in cards}
    for qid in sync["asks"]:   # a card the run no longer needs (a sign-in done, a set-aside test requeued) leaves the tab
        if qid not in current:
            state["answered"].setdefault(qid, "signed in" if "@signin@" in qid else "answered")
    for r in items:
        if r["id"] not in sync["reviews"]:
            new["reviews"].append(r["id"])
            writes.append({"op": "set", "collection": "reviews", "doc_id": f"{tab}~{r['id']}", "data": dict(r, tab=tab)})
    h = digest(state)
    if h != sync["state"]:
        new["state"] = h
        writes.append({"op": "set", "collection": "state", "doc_id": tab, "data": state})
    return writes, new, lines


def documents(sync):
    return sum(1 + v.get("events", 0) + len(v.get("asks", [])) + len(v.get("reviews", []))
               for k, v in sync.items() if k != "_session") + 1


def main(a):
    if not a:
        print(__doc__); sys.exit(2)
    sys.stdout.reconfigure(encoding="utf-8")   # redirected on Windows, stdout is cp1252: no "→", no "·" in some notes
    os.makedirs(LIVE, exist_ok=True)
    url = urls().get("session", "")
    order = {t: i for i, t in enumerate(tabs())}
    sync_path, pend = os.path.join(LIVE, "sync.json"), os.path.join(LIVE, "pending.json")
    sync = jload(sync_path, {})
    if a[0] == "--attention":
        for tab in order:
            for line in tab_writes(tab, order[tab], sync.get(tab, {}))[2]:
                print(f"{line} → {url or '(the session page is not published yet)'}")
        return
    if "--done" in a:
        p = jload(pend, None)
        if p:
            json.dump(p["sync"], open(sync_path, "w", encoding="utf8"), indent=1)
            os.remove(pend)
            print(f"{len(p['writes'])} writes marked written")
        return
    want = list(order) if "--all" in a else [t for t in a if not t.startswith("--")]
    unknown = [t for t in want if t not in order]
    if unknown:
        sys.exit(f"not a tab of this run: {', '.join(unknown)} (live_page.py {' '.join(unknown)} adds it)")
    writes, new, lines = [], json.loads(json.dumps(sync)), []
    sess = session_doc()
    if digest(sess) != sync.get("_session"):
        writes.append({"op": "set", "collection": "session", "doc_id": sess["run"] or "run", "data": sess})
        new["_session"] = digest(sess)
    for tab in want:
        w, s, l = tab_writes(tab, order[tab], sync.get(tab, {}))
        writes += w; new[tab] = s; lines += l
    json.dump({"writes": writes, "sync": new}, open(pend, "w", encoding="utf8"), indent=1, ensure_ascii=False)
    print(json.dumps({"url": url, "writes": writes, "attention": [f"{x} → {url}" for x in lines]}, ensure_ascii=False, indent=1))
    if not url:
        print("the session page is not published yet: live_page.py, publish it, then live_page.py --url <url> "
              "(or, if this session's page is already up, only --url)", file=sys.stderr)
    n = documents(new)
    if n >= DOC_WARN:
        print(f"warning: this run has written {n} documents to the session page; an artifact holds at most 25,000. "
              f"Start the next run of this session on a page of its own", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
