#!/usr/bin/env python3
"""Turn results/<scenario>/run-*.json into results/summary.json and results/summary.md.

Every figure is the median of the runs, with the range (min-max) beside it. Nothing is computed that is
not in the per-run files, and nothing is typed by hand.
"""
import collections
import csv
import gzip
import json
import math
import os
import re
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


def cpu_samples(runs):
    """CPU in millicores from the `kubectl top` samples stored in each run file, taken once the load was flowing
    (offset >= 0): the API pods and PostgreSQL. The one sample before that (offset < 0) is not used: metrics-server
    averages over its own window, so it still shows the previous run. Per run the median of that run's samples;
    then median and range over the runs."""
    api, pg = [], []
    for r in runs:
        ts = r.get("top_samples") or []
        a1 = [v for s in ts if s["offset_s"] >= 0 for v in s["api_cpu_m"]]
        p1 = [s["postgres_cpu_m"] for s in ts if s["offset_s"] >= 0 and s.get("postgres_cpu_m") is not None]
        api.append(statistics.median(a1) if a1 else None)
        pg.append(statistics.median(p1) if p1 else None)
    return mr(api, 0), mr(pg, 0)


def scenario_summary(runs):
    prm = runs[0]["params"]
    cpu_api, cpu_pg = cpu_samples(runs)
    return {
        "api_cpu_load_m": cpu_api, "postgres_cpu_load_m": cpu_pg,
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
        "non_201_statuses": dict(sorted(sum((collections.Counter({k: v for k, v in r["status_counts"].items() if k != "201"})
                                             for r in runs), collections.Counter()).items())),
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
        "cpu_pct_after_step_max": mr([max([s["cpu_pct"] for s in r["samples"]
                                           if s["t"] >= r["params"]["lead_s"] and s.get("cpu_pct") is not None] or [None])
                                     for r in runs], 0),
        "p95_before_step_ms": mr([r["before_step"]["p95_ms"] for r in runs]),
        "p95_after_step_ms": mr([r["after_step"]["p95_ms"] for r in runs]),
        "failed": mr([r["failed"] for r in runs], 0),
        "requests": mr([r["requests"] for r in runs], 0),
    }


def provenance_rows():
    """One row per batch from results/<scenario>/harness.log: the command and the UTC times at which the batch
    got the measurement lock and let it go."""
    rows = []
    for sc in ORDER + ["hpa"]:
        log = RESULTS / sc / "harness.log"
        if not log.is_file():
            continue
        cmd = start = end = None
        for ln in log.read_text(encoding="utf-8").splitlines():
            m = re.match(r"(\S+Z) batch \S+: (.*)$", ln)
            if m:
                cmd = m.group(2)
            m = re.match(r"(\S+Z) measurement lock acquired", ln)
            if m:
                start = m.group(1)
            m = re.match(r"(\S+Z) measurement lock released", ln)
            if m:
                end = m.group(1)
        if cmd and start and end:
            rows.append(f"| {LABEL.get(sc, 'autoscaler step')} | `python bench/harness.py {cmd}` | {start} | {end} |")
    return rows


def calibration_rows():
    """Table rows for results/calibration/rate-<n>/baseline/run-1.json, lowest rate first; none if absent."""
    rows = []
    base = RESULTS / "calibration"
    if not base.is_dir():
        return rows
    for d in sorted(base.glob("rate-*"), key=lambda p: int(p.name.split("-")[1])):
        f = d / "baseline" / "run-1.json"
        if not f.is_file():
            continue
        r = json.loads(f.read_text(encoding="utf-8"))
        api, pg = cpu_samples([r])
        o = r["overall"]
        rows.append(f"| {r['params']['rate_per_s']} | {r['requests']} | {r['failed']} | {r['dropped_iterations']} | "
                    f"{o['p95_ms']:.1f} | {o['p99_ms']:.1f} | {o['max_ms']:.1f} | "
                    f"{api['median'] if api else 'n/a'} | {pg['median'] if pg else 'n/a'} |")
    return rows


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
    shown = [summary[sc] for sc in ORDER if sc in summary]
    lines.append(f"Over all {sum(s['runs'] for s in shown)} runs in the table: {sum(s['failed_total'] for s in shown)} "
                 f"failed requests out of {sum(s['requests_total'] for s in shown)} sent; "
                 f"{sum(s['runs_with_failures'] for s in shown)} runs had a failed request.")
    lines.append("")
    others = [int(m.group(1)) for p in sorted(RESULTS.glob("**/docker-ps-*.txt"))
              for m in [re.search(r"# plus (\d+) other containers", p.read_text(encoding="utf-8"))] if m]
    if others:
        lines.append(f"The machine was shared. {len(others)} `docker ps` captures were saved, at the start and the end of "
                     f"every timed batch (`docker-ps-start.txt`, `docker-ps-end.txt`; container names withheld). "
                     f"In them {min(others)} to {max(others)} containers unrelated to this project were running.")
        lines.append("")
    for sc in ORDER:
        s = summary.get(sc)
        if s and s["non_201_statuses"]:
            codes = ", ".join(f"status {k}: {v}" for k, v in s["non_201_statuses"].items())
            lines.append(f"**{LABEL[sc]}**, responses other than 201 over the {s['runs']} runs ({codes}; status 0 means the "
                         f"connection failed before a response, 200 is Dispatch's answer to a repeated Idempotency-Key).")
            lines.append("")
    for sc in ORDER:
        s = summary.get(sc)
        if s and s["detail"]:
            extra = f"; container exit codes seen: {', '.join(s['container_exit_codes'])}" if s["container_exit_codes"] else ""
            lines.append(f"**{LABEL[sc]}**: " + "; ".join(f"{k.replace('_', ' ')}: {fmt(v)}" for k, v in s["detail"].items() if v) + extra)
            lines.append("")
    lines += ["## CPU in the samples (millicores)", "",
              "`kubectl top` samples stored in the run files, one per API pod about every 15 seconds (metrics-server's "
              "resolution), taken while the load was flowing. Per run the median of that run's samples; then median "
              "and range over the runs.", "",
              "| scenario | API pod | PostgreSQL |", "|---|---|---|"]
    for sc in ORDER:
        s = summary.get(sc)
        if s:
            lines.append(f"| {LABEL[sc]} | {fmt(s['api_cpu_load_m'])} | {fmt(s['postgres_cpu_load_m'])} |")
    lines.append("")
    prov = provenance_rows()
    if prov:
        env = json.loads((RESULTS / "environment.json").read_text(encoding="utf-8")) \
            if (RESULTS / "environment.json").is_file() else {}
        lines += ["## When and how each batch was run", "",
                  "Times are UTC, from the harness log of each batch (`results/<scenario>/harness.log`): the moment the "
                  "batch took the shared measurement lock and the moment it released it.", "",
                  "| scenario | command | started | finished |", "|---|---|---|---|"]
        lines += prov
        lines.append("")
        if env:
            lines += ["Machine and tools (`results/environment.json`, captured " + env.get("captured_utc", "?")[:19] + " UTC): "
                      f"{env.get('host_cpu')}; {env.get('host_os')}; Docker {env.get('docker_server_version')} with "
                      f"{env.get('docker_ncpu')} CPUs and {round(env.get('docker_mem_bytes', 0) / 2**30, 1)} GiB of memory "
                      f"visible to it; {env.get('kind')}; Helm {env.get('helm')}; {env.get('terraform')}; "
                      f"kubectl ({env.get('kubectl_client')}); Kubernetes server {env.get('kubernetes_server')}; "
                      f"{env.get('k6_version')}; Dispatch commit {env.get('dispatch_api_commit')}.", ""]
    calib = calibration_rows()
    if calib:
        lines += ["## Rate sweep used to choose the arrival rate", "",
                  "One 60-second run of the no-fault baseline per rate (autoscaler off, 3 API replicas), made with "
                  "`bench/run-calibration.sh`. One run each, so these are calibration figures and not results. "
                  "CPU as above, in millicores.", "",
                  "| arrival rate (requests/s) | requests sent | failed | k6 iterations dropped | p95 (ms) | p99 (ms) | "
                  "max (ms) | API pod CPU | PostgreSQL CPU |", "|---|---|---|---|---|---|---|---|---|"]
        lines += calib
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
                  f"| highest CPU utilisation before the step (percent of request) | {fmt(h['cpu_pct_before_step_max'])} |",
                  f"| highest CPU utilisation after the step (percent of request) | {fmt(h['cpu_pct_after_step_max'])} |",
                  f"| p95 before / after the step (ms) | {fmt(h['p95_before_step_ms'])} / {fmt(h['p95_after_step_ms'])} |",
                  f"| failed requests per run | {fmt(h['failed'])} |", ""]
    (RESULTS / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
