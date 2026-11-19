#!/usr/bin/env bash
# Prints "ok" when a one-line call on a role's pin answers on that pin; otherwise prints what it got. Claude Code reports
# every model a call used and its window, so there the answer must come ONLY from the pinned model, on the expected
# window (background calls are pinned too: a "haiku" helper call shows up as a second model and fails the check). Other
# agents are checked with the session's own flags, on the model they report where they report one.
#   preflight.sh            the run's main pin: code for a fix run, verify for a verify run
#   preflight.sh <role>     research, plan, code or verify (crew.py shows the pins)
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/loop.conf"
role="${1:-}"
[ -n "$role" ] || { [ "${KIND:-fix}" = verify ] && role=verify || role=code; }
mkdir -p "$HERE/state"
cd "$HERE" && "$PYTHON" "$HERE/agent_run.py" preflight "$role"
