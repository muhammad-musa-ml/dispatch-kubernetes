#!/usr/bin/env python3
"""Turn results/<scenario>/run-*.json into results/summary.json and results/summary.md.

Every figure is the median of the runs, with the range (min-max) beside it. Nothing is computed that is
not in the per-run files, and nothing is typed by hand.
"""
import csv
import gzip
import json
import math
import os
import statistics
from pathlib import Path

RESULTS = Path(os.environ.get("RESULTS_DIR", Path(__file__).resolve().parent.parent / "results"))
ORDER = ["baseline", "rolling", "rolling-nosleep", "kill", "sigkill", "drain", "badrelease"]
LABEL = {"baseline": "no fault (baseline)", "rolling": "rolling deploy",
         "rolling-nosleep": "rolling deploy, preStop sleep removed (ablation)",
         "kill": "pod deleted, zero grace period", "sigkill": "container killed with SIGKILL",
         "drain": "node drain", "badrelease": "bad-image release, then rollback"}


def load(sc):
    runs = []
    for p in sorted((RESULTS / sc).glob("run-*.json"), key=lambda p: int(p.stem.split("-")[1])):
        runs.append(json.loads(p.read_text(encoding="utf-8")))
    return runs


def mr(values, nd=1):
    """median and range of the non-None values."""
    v = [x for x in values if x is not None]
    if not v:
        return None
    return {"median": round(statistics.median(v), nd), "min": round(min(v), nd), "max": round(max(v), nd), "n": len(v)}


def fmt(x, unit="", nd=1):
    if x is None:
        return "n/a"
    if x["min"] == x["max"]:
        return f"{x['median']:g}{unit} (all {x['n']} runs)"
    return f"{x['median']:g}{unit} ({x['min']:g}-{x['max']:g})"


def pct(values, q):
    s = sorted(values)
    return s[max(1, math.ceil(q / 100 * len(s))) - 1] if s else None


def baseline_window_p95(fault_at, window):
    """p95 of each baseline run over [fault_at, fault_at + window): the no-fault figure for the same window."""
    out = []
    for p in sorted((RESULTS / "baseline").glob("run-*.requests.csv.gz")):
        lat = []
        with gzip.open(p, "rt", newline="") as f:
            for row in csv.DictReader(f):
                t = float(row["offset_s"])
                if fault_at <= t < fault_at + window:
                    lat.append(float(row["latency_ms"]))
        out.append(pct(lat, 95))
    return out


def scenario_summary(runs):
    prm = runs[0]["params"]
    return {
        "window_s": prm["window_s"], "fault_at_s": prm["fault_at_s"], "rate_per_s": prm["rate_per_s"],
        "p95_baseline_same_window_ms": mr(baseline_window_p95(prm["fault_at_s"], prm["window_s"])),
        "runs": len(runs),
        "requests": mr([r["requests"] for r in runs], 0),
        "failed": mr([r["failed"] for r in runs], 0),
        "failed_total": sum(r["failed"] for r in runs),
        "requests_total": sum(r["requests"] for r in runs),
        "dropped_iterations": mr([r["dropped_iterations"] for r in runs], 0),
        "p95_overall_ms": mr([r["overall"]["p95_ms"] for r in runs]),
        "p95_pre_ms": mr([r["pre_fault"]["p95_ms"] for r in runs]),
        "p95_fault_window_ms": mr([r["fault_window"]["p95_ms"] for r in runs]),
        "max_fault_window_ms": mr([r["fault_window"]["max_ms"] for r in runs]),
        "p95_post_ms": mr([r["post_window"]["p95_ms"] for r in runs]),
        "error_span_s": mr([r["error_window"]["span_s"] for r in runs], 2),
        "runs_with_failures": sum(1 for r in runs if r["failed"] > 0),
        "cluster_recovery_s": mr([r["cluster_recovery_s"] for r in runs], 1),
        "detail": {k: mr([r["detail"].get(k) for r in runs], 1) for k in
                   ["helm_upgrade_seconds", "replacement_ready_seconds", "kubectl_drain_seconds",
                    "pods_ready_again_seconds", "upgrade_to_detection_seconds", "rollback_seconds",
                    "bad_release_to_restored_seconds", "rollback_to_ready_seconds",
                    "container_restarted_ready_seconds"] if runs and k in runs[0]["detail"]},
        "container_exit_codes": sorted({r["detail"]["container_exit_code"] for r in runs
                                        if "container_exit_code" in r["detail"]}),
    }


def hpa_summary(runs):
    return {
        "runs": len(runs),
        "rate_before_per_s": runs[0]["params"]["rate_before_per_s"], "rate_after_per_s": runs[0]["params"]["rate_after_per_s"],
        "seconds_to_first_scale_decision": mr([r["seconds_to_first_scale_decision"] for r in runs]),
        "seconds_to_first_new_pod_ready": mr([r["seconds_to_first_new_pod_ready"] for r in runs]),
        "seconds_to_all_desired_ready": mr([r["seconds_to_all_desired_ready"] for r in runs]),
        "desired_replicas_max": mr([r["desired_replicas_max"] for r in runs], 0),
        "cpu_pct_before_step_max": mr([r["cpu_pct_before_step_max"] for r in runs], 0),
        "p95_before_step_ms": mr([r["before_step"]["p95_ms"] for r in runs]),
        "p95_after_step_ms": mr([r["after_step"]["p95_ms"] for r in runs]),
        "failed": mr([r["failed"] for r in runs], 0),
        "requests": mr([r["requests"] for r in runs], 0),
    }


def main():
    summary = {}
    for sc in ORDER:
        runs = load(sc)
        if runs:
            summary[sc] = scenario_summary(runs)
    hpa = load("hpa")
    if hpa:
        summary["hpa"] = hpa_summary(hpa)
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = ["# Results summary", "",
             "Median of the runs, with the range (min-max) in brackets. Generated by `bench/summarize.py` from "
             "`results/<scenario>/run-*.json`.", "",
             "| scenario | runs | requests per run | failed requests per run | runs with failures | p95 before fault (ms) | "
             "p95 in fault window (ms) | p95 with no fault, same window (ms) | worst request in window (ms) | "
             "error span (s) | cluster back to desired state (s) |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for sc in ORDER:
        s = summary.get(sc)
        if not s:
            continue
        lines.append(f"| {LABEL[sc]} | {s['runs']} | {fmt(s['requests'])} | {fmt(s['failed'])} | "
                     f"{s['runs_with_failures']} of {s['runs']} | {fmt(s['p95_pre_ms'])} | {fmt(s['p95_fault_window_ms'])} | "
                     f"{fmt(s['p95_baseline_same_window_ms'])} | {fmt(s['max_fault_window_ms'])} | "
                     f"{fmt(s['error_span_s'])} | {fmt(s['cluster_recovery_s'])} |")
    lines.append("")
    for sc in ORDER:
        s = summary.get(sc)
        if s and s["detail"]:
            extra = f"; container exit codes seen: {', '.join(s['container_exit_codes'])}" if s["container_exit_codes"] else ""
            lines.append(f"**{LABEL[sc]}**: " + "; ".join(f"{k.replace('_', ' ')}: {fmt(v)}" for k, v in s["detail"].items() if v) + extra)
            lines.append("")
    if "hpa" in summary:
        h = summary["hpa"]
        lines += ["## Autoscaler (load step)", "",
                  f"Arrival rate raised from {h['rate_before_per_s']} to {h['rate_after_per_s']} requests/s; {h['runs']} runs.", "",
                  "| measure | median (range) |", "|---|---|",
                  f"| seconds from the step to the first scale-out decision | {fmt(h['seconds_to_first_scale_decision'])} |",
                  f"| seconds from the step to the first new pod Ready | {fmt(h['seconds_to_first_new_pod_ready'])} |",
                  f"| seconds from the step to all desired pods Ready | {fmt(h['seconds_to_all_desired_ready'])} |",
                  f"| desired replicas reached | {fmt(h['desired_replicas_max'])} |",
                  f"| CPU utilisation before the step (percent of request) | {fmt(h['cpu_pct_before_step_max'])} |",
                  f"| p95 before / after the step (ms) | {fmt(h['p95_before_step_ms'])} / {fmt(h['p95_after_step_ms'])} |",
                  f"| failed requests per run | {fmt(h['failed'])} |", ""]
    (RESULTS / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
