#!/usr/bin/env python3
"""Print per-bucket request counts, failures and latency from a run's requests.csv.gz.

  python bench/buckets.py results/rolling/run-1.requests.csv.gz [bucket_seconds]
"""
import csv
import gzip
import math
import sys


def pct(values, q):
    s = sorted(values)
    return s[max(1, math.ceil(q / 100 * len(s))) - 1] if s else None


def main():
    path = sys.argv[1]
    width = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    buckets = {}
    with gzip.open(path, "rt", newline="") as f:
        for row in csv.DictReader(f):
            b = int(float(row["offset_s"]) // width)
            buckets.setdefault(b, []).append((float(row["latency_ms"]), row["status"]))
    print("bucket_start_s  requests  failed  p50_ms  p95_ms  max_ms")
    for b in sorted(buckets):
        lat = [x[0] for x in buckets[b]]
        failed = sum(1 for x in buckets[b] if x[1] != "201")
        print(f"{b * width:>13.0f}  {len(lat):>8}  {failed:>6}  {pct(lat, 50):>6.1f}  {pct(lat, 95):>6.1f}  {max(lat):>6.1f}")


if __name__ == "__main__":
    main()
