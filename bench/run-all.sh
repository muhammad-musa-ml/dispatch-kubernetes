#!/usr/bin/env bash
# Every reported batch, in order: five runs each. Each batch takes the shared measurement lock itself.
# Fault scenarios: 40 requests/s open-loop for 120 s (170 s for the bad release), fault at +30 s.
# Autoscaler: 10 requests/s, then a step to 60 requests/s.
set -u
cd "$(dirname "$0")/.."
python bench/harness.py capture-env || exit 1
python bench/harness.py run baseline --runs 5 --rate 40 --duration 170 --fault-at 30 --window 60 || echo "baseline FAILED"
for sc in rolling kill drain; do
  python bench/harness.py run "$sc" --runs 5 --rate 40 --duration 120 --fault-at 30 --window 60 || echo "$sc FAILED"
done
python bench/harness.py run badrelease --runs 5 --rate 40 --duration 170 --fault-at 30 --window 100 --rollout-deadline 45 || echo "badrelease FAILED"
python bench/harness.py run hpa --runs 5 --hpa-base-rate 10 --hpa-rate 60 --hpa-lead 30 --hpa-hold 240 || echo "hpa FAILED"
python bench/summarize.py
echo "ALL DONE"
