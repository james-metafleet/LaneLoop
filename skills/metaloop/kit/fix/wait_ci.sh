#!/usr/bin/env bash
# Waits until no check on the PR(s) is pending, then prints every check and the merge state. `gh pr checks --watch` can
# exit while a job is still pending; this polls until nothing is. It keeps the computer awake while it waits; the checks
# run on GitHub regardless, and a sleep only delays the answer (noted with it).
#   wait_ci.sh <PR> [<PR> ...]
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/loop.conf"
source "$HERE/platform.sh"
keep_awake $$ "MetaLoop is waiting for CI on PR $*"
sleep 45   # a push takes a moment to register its checks
slept=0
for _ in $(seq 1 90); do
    p=0
    for n in "$@"; do
        out=$(gh pr checks "$n" --repo "$GH_REPO" 2>&1); rc=$?
        # gh exits 8 while checks are pending; any other failure (network, auth) is "don't know yet", never "done"
        if [ "$rc" -ne 0 ] && [ "$rc" -ne 8 ] && ! echo "$out" | grep -q $'\t'; then p=$((p + 1)); continue; fi
        c=$(echo "$out" | awk -F'\t' '$2=="pending"' | wc -l); p=$((p + c))
    done
    [ "$p" -eq 0 ] && break
    nap 60; slept=$((slept + SLEPT))
done
if [ "$slept" -gt 0 ]; then echo "== the computer slept for $(duration "$slept") while waiting"; fi
for n in "$@"; do
    echo "== #$n"
    gh pr checks "$n" --repo "$GH_REPO" 2>&1 | awk -F'\t' '{print $1, $2, $3}'
    gh pr view "$n" --repo "$GH_REPO" --json state,mergeable,mergeStateStatus,headRefOid --jq '"\(.state) \(.mergeable) \(.mergeStateStatus) head \(.headRefOid[0:9])"'
done
