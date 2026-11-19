"""The orchestrator session's ONE live page: a tab per running task (each chain of a fix run, and the verify loop), under
a dashboard of the whole session (its PR label, what is running, what needs the owner, PRs, cost). It is written into
<run>/live/session.live.html, outside the repo, and never committed (the repo ignores *.live.html). It draws itself from
its own database, which live_sync.py fills, so it is published once per session and stays current without a republish.

  python live_page.py [--title "<Title>"] <tab> [<tab> ...]   add tabs: a fix run's chain slugs, or `verify` in a verify
                                                              run folder; then (re)writes the page and prints its
                                                              capabilities
  python live_page.py --url <url>                             where the session's page is published (live/urls.json):
                                                              live_sync.py writes to it and the main window's lines link it
  python live_page.py --plan-url <url>                        where the plan page is: the dashboard links it

Publish the page ONCE per orchestrator session with the Artifact tool (file_path <run>/live/session.live.html, the
capabilities this prints, icon "pulse"), then --url with the URL the publish returns. A later run folder of the same
session (a fix run after the verify run, say) adds its tabs and records the SAME url with --url: it never publishes a
page of its own. New tabs reach the published page through live_sync.py, without a republish. Every task's conversation
with the owner happens on its tab: the main window only posts the link when a tab needs the owner.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = json.load(open(os.path.join(HERE, "loop.json"), encoding="utf8"))
LIVE = os.path.join(HERE, "live")
PAGE = "session.live.html"
# Owner-only writes: the tabs' state, lines, questions, review items and Claude's answers come from the owner's session
# (ArtifactData), and the answers and approvals are the owner's decisions, relayed to the chains as such. Anyone the page
# is shared with can read it and Ask Claude. Every collection is flat, with a `tab` field on each document: one page
# holds every task of the session, and a view may hold at most 64 subscriptions.
PATHS = ("state", "session", "events", "asks", "reviews", "replies", "answers", "approvals")
CAPS = {"db": {"rules": [{"path": p, "read": "view", "write": "owner"} for p in PATHS]}, "comments": {}, "user": {}}
TAB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")   # a db path segment, with `~` kept for the id separator


def jload(path, default):
    try:
        return json.load(open(path, encoding="utf8"))
    except (OSError, ValueError):
        return default


def tabs():
    """This run folder's tabs, in the order they were added."""
    return jload(os.path.join(LIVE, "tabs.json"), [])


def humanize(s):
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", s) if w)


def title_of_session():
    return humanize(CONF.get("RUN_NAME", "")) or "LaneLoop Session"


def render(title):
    config = {"title": title, "owner": CONF.get("OWNER", "the owner"), "syncMin": int(CONF.get("LIVE_SYNC_MIN", "10") or 0)}
    html = open(os.path.join(HERE, "live_page.html"), encoding="utf8").read()
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = (html.replace("__TITLE__", esc(title))
                .replace("/*__TWO_WAY__*/", open(os.path.join(HERE, "two_way.js"), encoding="utf8").read())
                .replace("__CONFIG__", json.dumps(config).replace("</", "<\\/")))
    out = os.path.join(LIVE, PAGE)
    open(out, "w", encoding="utf8", newline="\n").write(html)
    return out


def main(a):
    if not a:
        print(__doc__); sys.exit(2)
    sys.stdout.reconfigure(encoding="utf-8")   # redirected on Windows, stdout is cp1252
    os.makedirs(LIVE, exist_ok=True)
    if a[0] in ("--url", "--plan-url"):
        if len(a) != 2 or not a[1].startswith("https://"):
            print(__doc__); sys.exit(2)
        path = os.path.join(LIVE, "urls.json")
        known = jload(path, {})
        known["session" if a[0] == "--url" else "plan"] = a[1]
        json.dump(known, open(path, "w", encoding="utf8"), indent=1)
        print(f"session page: {a[1]} (then live_sync.py --all for this run's tabs)" if a[0] == "--url"
              else f"plan page: {a[1]} (the session page's dashboard links it from the next live_sync.py)")
        return
    title = None
    if a[0] == "--title":
        if len(a) < 2:
            print(__doc__); sys.exit(2)
        title, a = a[1], a[2:]
    verify_run = CONF.get("KIND") == "verify"
    for t in a:
        if not TAB_ID.match(t):
            sys.exit(f"{t}: a tab id is letters, digits, '.', '_' and '-' (up to 100), starting with a letter or digit")
        if verify_run and t != "verify":
            sys.exit(f"{t}: a verify run folder has one tab, `verify`")
        if not verify_run and t == "verify":
            sys.exit("`verify` is the verify loop's tab: a chain can't be called that (rename the branch in the plan)")
    have = tabs()
    have += [t for t in dict.fromkeys(a) if t not in have]
    json.dump(have, open(os.path.join(LIVE, "tabs.json"), "w", encoding="utf8"), indent=1)
    if title:
        open(os.path.join(LIVE, "title.txt"), "w", encoding="utf8").write(title)
    title = title or (open(os.path.join(LIVE, "title.txt"), encoding="utf8").read().strip()
                      if os.path.exists(os.path.join(LIVE, "title.txt")) else title_of_session())
    out = render(title)
    print(f"tabs: {', '.join(have) or '(none yet)'}")
    print(f"page: {out}")
    if jload(os.path.join(LIVE, "urls.json"), {}).get("session"):
        print("already published for this session: nothing to republish; live_sync.py --all adds the new tabs")
    else:
        print("publish it once for this orchestrator session (or, if this session's page is already up, only "
              "live_page.py --url <its url>), with these capabilities, icon \"pulse\", then live_page.py --url <url>:")
    print(json.dumps(CAPS))


if __name__ == "__main__":
    main(sys.argv[1:])
