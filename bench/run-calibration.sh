#!/usr/bin/env bash
# Rate sweep that justifies the arrival rate used by the fault scenarios: one 60-second run of the baseline
# (no fault, autoscaler off, 3 API replicas) at each rate. One run per rate, so these are calibration
# figures and not reported results; they are summarised in results/summary.md by bench/summarize.py.
# MEASURE_LOCK must be set.
set -u
cd "$(dirname "$0")/.." || exit 1
for rate in 20 40 80 160; do
  python bench/harness.py run baseline --runs 1 --rate "$rate" --duration 60 --fault-at 30 --window 30 \
    --results-dir "results/calibration/rate-$rate" || echo "rate $rate FAILED"
done
echo "CALIBRATION DONE"
