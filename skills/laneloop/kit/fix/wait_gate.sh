#!/usr/bin/env bash
# Waits for a branch's gate to finish and prints the verdict with every suite's line (PASS, FAIL, or the process gone).
# It keeps the computer awake while it waits; the 3-hour timeout counts awake time, and a sleep is noted with the verdict.
#   wait_gate.sh <slug>          (start the gate first: cd <worktree> && python <run>/gate.py --start)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/loop.conf"
source "$HERE/platform.sh"
cd "$WT_ROOT/$1" || { echo "no worktree for $1"; exit 2; }
keep_awake $$ "LaneLoop is waiting for the gate of $1"
sleep 20
limit=$(( ${WAIT_HOURS:-3} * 3600 )); waited=0; slept=0   # a full gate takes 20-40 minutes; a hung build or slot never ends
until "$PYTHON" "$HERE/gate.py" --status 2>&1 | grep -aqiE "^gate (PASS|FAIL)|process gone|no gate run|did not start"; do
    if [ "$waited" -ge "$limit" ]; then
        echo "TIMEOUT after ${WAIT_HOURS:-3}h, gate still running. Status, slot owners and the last log lines:"
        "$PYTHON" "$HERE/gate.py" --status; ls "$GATE_SLOTS" 2>/dev/null; cat "$GATE_SLOTS"/*/owner 2>/dev/null
        tail -n 5 .fixloop/logs/gate-*.log 2>/dev/null | tail -n 20; exit 3
    fi
    nap 60; waited=$((waited + 60)); slept=$((slept + SLEPT))
done
echo "== $1"; "$PYTHON" "$HERE/gate.py" --status
if [ "$slept" -gt 0 ]; then
    echo "== the computer slept for $(duration "$slept") during the gate: a FAIL from a service that timed out across it is worth one re-run"
fi
