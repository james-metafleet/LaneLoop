#!/usr/bin/env bash
# Waits for the verify loop's next terminal line after the current end of logs/loop.log and prints it: PAUSED (the owner
# must act; act on it at once), every test recorded, STOP, or the loop ending. With none of those, it still returns every
# LIVE_SYNC_MIN minutes (loop.conf; 0 = never) if the ledger changed, so the verify tab of the session page gets the
# progress (live_sync.py verify). Arm it whenever the loop runs: a pause nobody watches sits for hours. It keeps the computer
# awake while it waits; if the computer slept anyway, it returns on waking so you re-check the loop and the app.
# Times out after WAIT_HOURS (default 8) of awake time.
#   wait_verify.sh [from-line]
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/loop.conf"
source "$HERE/platform.sh"
keep_awake $$ "LaneLoop is watching the verify loop"
LOG="$HERE/logs/loop.log"
a="${1:-$(wc -l < "$LOG" 2>/dev/null || echo 0)}"
limit=$(( ${WAIT_HOURS:-8} * 3600 )); waited=0; poll="${WAIT_POLL_SECS:-30}"
every="${LIVE_SYNC_SECS:-$(( ${LIVE_SYNC_MIN:-10} * 60 ))}"
mkdir -p "$HERE/state"; mark="$HERE/state/wait_verify.$$"; touch "$mark"
tick=$(( $(date +%s) + every ))
until tail -n +$((a + 1)) "$LOG" 2>/dev/null | grep -aqE "PAUSED|every test is recorded|STOP|server stopped|interrupted"; do
    now=$(date +%s)
    if [ "$every" -gt 0 ] && [ "$now" -ge "$tick" ]; then
        if [ "$HERE/ledger.json" -nt "$mark" ]; then
            rm -f "$mark"; echo "== progress, nothing needs anyone: sync the session page's verify tab (live_sync.py verify)"
            (cd "$HERE" && "$PYTHON" ledger.py summary --short)
            echo "== next from-line: $a"; exit 0   # not the log's end: a stop logged since the check above is read next time
        fi
        tick=$(( now + every ))
    fi
    if [ "$waited" -ge "$limit" ]; then
        rm -f "$mark"; echo "TIMEOUT after ${WAIT_HOURS:-8}h; last lines:"; tail -n 5 "$LOG"; (cd "$HERE" && "$PYTHON" ledger.py summary --short); exit 3
    fi
    nap "$poll"; waited=$((waited + poll))
    if [ "$SLEPT" -gt 0 ]; then
        rm -f "$mark"
        echo "== the computer slept for $(duration "$SLEPT"): check the loop is still running and the app still answers, sync the session page, then re-arm this watcher"
        tail -n 3 "$LOG"; (cd "$HERE" && "$PYTHON" ledger.py summary --short)
        echo "== next from-line: $a"; exit 0
    fi
done
rm -f "$mark"
echo "== since line $a of loop.log:"; tail -n +$((a + 1)) "$LOG" | tail -n 8
(cd "$HERE" && "$PYTHON" ledger.py summary --short)
[ -f "$HERE/PAUSED.md" ] && { echo "== PAUSED.md:"; cat "$HERE/PAUSED.md"; }
echo "== next from-line: $(wc -l < "$LOG")"
