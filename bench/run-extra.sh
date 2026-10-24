#!/usr/bin/env bash
# Two batches added after the first round showed that `kubectl delete --force --grace-period=0` still lets
# the container exit cleanly (results/kill/force-delete-probe.txt):
#   sigkill          the API process is killed with SIGKILL, no SIGTERM and no preStop hook
#   rolling-nosleep  the rolling deploy again, with the preStop sleep removed from the chart values
# Same rate, duration and fault offset as the other fault scenarios. MEASURE_LOCK must be set.
set -u
cd "$(dirname "$0")/.." || exit 1
for sc in sigkill rolling-nosleep; do
  python bench/harness.py run "$sc" --runs 5 --rate 40 --duration 120 --fault-at 30 --window 60 || echo "$sc FAILED"
done
python bench/summarize.py
echo "ALL DONE"
