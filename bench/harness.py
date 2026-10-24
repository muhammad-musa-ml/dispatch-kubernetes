#!/usr/bin/env python3
"""Fault-injection harness: k6 open-loop load plus one fault per run, 5 runs per scenario.

  python bench/harness.py capture-env
  python bench/harness.py run SCENARIO [--runs 5] [--rate 60] ...

SCENARIO is one of: baseline, rolling, kill, drain, badrelease, hpa.

Each run starts the official grafana/k6 image on the `kind` Docker network, waits until load is flowing,
injects the fault at a fixed offset, waits for the cluster to return to its desired state, and writes
  results/<scenario>/run-<n>.json         every number reported in the README
  results/<scenario>/run-<n>.requests.csv.gz   one row per request: seconds since load start, ms, status

Clocks: k6 timestamps come from the Docker VM's clock, so every fault time is read from that same clock
(`docker exec <node> date`). The harness never compares the host clock with k6 timestamps.

Standard library only.
"""
import argparse
import calendar
import csv
import datetime
import gzip
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench"
RESULTS = ROOT / "results"
KUBECONFIG = ROOT / "terraform" / "envs" / "local" / "kubeconfig"
NS = "dispatch"
RELEASE = "dispatch"
CLUSTER = "dispatch"
CLOCK_NODE = f"{CLUSTER}-control-plane"
BASE_URL = f"http://{CLOCK_NODE}:30080"
K6_IMAGE = "grafana/k6:2.2.0"
API_SELECTOR = f"app.kubernetes.io/instance={RELEASE},app.kubernetes.io/component=api"
LOCK_PATH = Path(os.environ.get("MEASURE_LOCK", "measure.lock"))
LOCK_OWNER = os.environ.get("MEASURE_LOCK_OWNER", "P1")
EXPECTED_REPLICAS = 3  # autoscaling.minReplicas in the chart


# ------------------------------------------------------------------------------------------ helpers
def log(msg):
    print(f"{datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%SZ')} {msg}", flush=True)


def sh(args, check=True, timeout=None, kube=True, input_text=None):
    env = dict(os.environ)
    if kube:
        env["KUBECONFIG"] = str(KUBECONFIG)
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env, input=input_text)
    if check and p.returncode != 0:
        raise RuntimeError(f"{args[:4]} failed ({p.returncode}): {p.stderr.strip()[:500]}")
    return p


def kubectl_json(*args):
    return json.loads(sh(["kubectl", "-n", NS, *args, "-o", "json"]).stdout)


def vm_now():
    """Seconds since the epoch on the Docker VM's clock, the clock k6 stamps its samples with."""
    return float(sh(["docker", "exec", CLOCK_NODE, "date", "+%s.%N"], kube=False).stdout.strip())


_TIME_RE = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$")


def parse_k6_time(text):
    m = _TIME_RE.match(text)
    if not m:
        raise ValueError(f"unparsed k6 time {text!r}")
    y, mo, d, h, mi, s, frac, tz = m.groups()
    epoch = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s), 0, 0, 0))
    epoch += float("0." + frac) if frac else 0.0
    if tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        epoch -= sign * (int(tz[1:3]) * 3600 + int(tz[4:6]) * 60)
    return epoch


def api_pods():
    items = kubectl_json("get", "pods", "-l", API_SELECTOR)["items"]
    pods = []
    for it in items:
        conds = {c["type"]: c["status"] for c in it["status"].get("conditions", [])}
        pods.append({
            "name": it["metadata"]["name"],
            "node": it["spec"].get("nodeName"),
            "ready": conds.get("Ready") == "True",
            "deleting": bool(it["metadata"].get("deletionTimestamp")),
            "image": it["spec"]["containers"][0]["image"],
            "phase": it["status"].get("phase"),
        })
    return pods


def ready_pods(pods=None):
    return [p for p in (pods if pods is not None else api_pods()) if p["ready"] and not p["deleting"]]


def hpa_status():
    st = kubectl_json("get", "hpa", RELEASE)["status"]
    cpu = None
    for m in st.get("currentMetrics", []) or []:
        if m.get("type") == "Resource":
            cpu = m["resource"]["current"].get("averageUtilization")
    return {"current": st.get("currentReplicas"), "desired": st.get("desiredReplicas"), "cpu_pct": cpu}


def backlog():
    q = "select count(*) from deliveries where not completed"
    out = sh(["kubectl", "-n", NS, "exec", "dispatch-postgres-0", "--", "psql", "-U", "dispatch", "-d",
              "dispatch", "-tAc", q]).stdout.strip()
    return int(out)


def api_key():
    secret = kubectl_json("get", "secret", "dispatch-app")
    import base64
    keys = json.loads(base64.b64decode(secret["data"]["API_KEYS"]))
    return sorted(keys)[0]


def percentile(values, q):
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    s = sorted(values)
    k = max(1, math.ceil(q / 100 * len(s)))
    return s[k - 1]


# ------------------------------------------------------------------------------------------- lock
def acquire_lock():
    waited = 0
    while True:
        try:
            with open(LOCK_PATH, "x") as f:
                f.write(f"{LOCK_OWNER} {datetime.datetime.now(datetime.timezone.utc).isoformat()}\n")
            log(f"measurement lock acquired after waiting {waited}s")
            return waited
        except FileExistsError:
            if waited % 60 == 0:
                try:
                    holder = LOCK_PATH.read_text().strip()
                except OSError:
                    holder = "?"
                log(f"measurement lock held by [{holder}]; waiting")
            time.sleep(10)
            waited += 10


def release_lock():
    try:
        LOCK_PATH.unlink()
        log("measurement lock released")
    except FileNotFoundError:
        log("measurement lock was already gone")


# ----------------------------------------------------------------------------- steady-state checks
def settle(timeout=420):
    """Wait until the cluster is in the starting state of a run: the minimum replicas Ready, nothing
    terminating, the autoscaler at its minimum, no delivery backlog. Returns what it saw."""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        pods = api_pods()
        rp = ready_pods(pods)
        hp = hpa_status()
        try:
            bl = backlog()
        except RuntimeError:
            bl = None
        last = {"ready": len(rp), "pods": len(pods), "hpa_current": hp["current"], "hpa_desired": hp["desired"],
                "backlog": bl}
        if (len(rp) == EXPECTED_REPLICAS and len(pods) == EXPECTED_REPLICAS and hp["current"] == EXPECTED_REPLICAS
                and hp["desired"] == EXPECTED_REPLICAS and bl == 0):
            last["settle_seconds"] = round(time.time() - t0, 1)
            return last
        time.sleep(3)
    raise RuntimeError(f"cluster did not settle in {timeout}s: {last}")


def rebalance_pods():
    """Return the API pods to a known placement before a run: a rollout restart, then wait."""
    sh(["kubectl", "-n", NS, "rollout", "restart", f"deployment/{RELEASE}"])
    sh(["kubectl", "-n", NS, "rollout", "status", f"deployment/{RELEASE}", "--timeout=180s"])


# --------------------------------------------------------------------------------------------- k6
def start_k6(workdir, run_id, stages, start_rate, max_vus):
    workdir.mkdir(parents=True, exist_ok=True)
    raw = workdir / "k6.json"
    if raw.exists():
        raw.unlink()
    cmd = ["docker", "run", "--rm", "--name", "p1-k6", "--network", "kind",
           "-v", f"{BENCH}:/bench:ro", "-v", f"{workdir}:/out",
           "-e", f"BASE_URL={BASE_URL}", "-e", f"API_KEY={api_key()}", "-e", f"RUN_ID={run_id}",
           "-e", f"STAGES={json.dumps(stages)}", "-e", f"START_RATE={start_rate}", "-e", f"MAX_VUS={max_vus}",
           K6_IMAGE, "run", "--quiet", "--out", "json=/out/k6.json", "/bench/load.js"]
    sh(["docker", "rm", "-f", "p1-k6"], check=False, kube=False)
    out = open(workdir / "k6.stdout.txt", "w")
    proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)
    return proc, raw


def first_sample_time(raw, timeout=90):
    """k6 clock time of the first request sample (the load start)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if raw.exists():
            with open(raw, "r", encoding="utf-8") as f:
                for line in f:
                    if '"metric":"http_req_duration"' in line and '"type":"Point"' in line:
                        return parse_k6_time(json.loads(line)["data"]["time"])
        time.sleep(0.5)
    raise RuntimeError("k6 produced no samples")


def wait_vm_time(target):
    while True:
        remaining = target - vm_now()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.5))


def load_requests(raw, load_start):
    """(offset seconds, latency ms, status) per request, plus dropped iterations, from the k6 JSON stream."""
    rows = []
    dropped = 0.0
    with open(raw, "r", encoding="utf-8") as f:
        for line in f:
            if '"type":"Point"' not in line:
                continue
            if '"metric":"http_req_duration"' in line:
                d = json.loads(line)["data"]
                rows.append((parse_k6_time(d["time"]) - load_start, d["value"], d["tags"].get("status", "0")))
            elif '"metric":"dropped_iterations"' in line:
                dropped += json.loads(line)["data"]["value"]
    rows.sort()
    return rows, int(dropped)


def summarize_window(rows, lo, hi):
    sel = [r for r in rows if lo <= r[0] < hi]
    lat = [r[1] for r in sel]
    failed = [r for r in sel if r[2] != "201"]
    return {"from_s": lo, "to_s": hi, "requests": len(sel), "failed": len(failed),
            "p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95), "p99_ms": percentile(lat, 99),
            "max_ms": max(lat) if lat else None}


def error_window(rows):
    bad = [r[0] for r in rows if r[2] != "201"]
    if not bad:
        return {"failed": 0, "first_s": None, "last_s": None, "span_s": 0.0}
    return {"failed": len(bad), "first_s": round(min(bad), 3), "last_s": round(max(bad), 3),
            "span_s": round(max(bad) - min(bad), 3)}


# ---------------------------------------------------------------------------------------- the faults
def wait_all_ready(expected=EXPECTED_REPLICAS, exclude=(), timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        pods = api_pods()
        rp = [p for p in ready_pods(pods) if p["name"] not in exclude]
        if len(rp) >= expected and not any(p["deleting"] for p in pods):
            return time.time() - t0
        time.sleep(0.5)
    raise RuntimeError("pods did not return to Ready")


def helm(*args, timeout=600, check=True):
    return sh(["helm", "--namespace", NS, *args], timeout=timeout, check=check)


def fault_rolling(ctx):
    t = time.time()
    helm("upgrade", RELEASE, str(ROOT / "charts" / "dispatch"), "--reuse-values",
         "--set-string", f"podAnnotations.rolloutId={ctx['run_id']}", "--wait", "--timeout", "5m")
    ctx["detail"] = {"helm_upgrade_seconds": round(time.time() - t, 2)}
    return time.time() - t


def fault_kill(ctx):
    pods = ready_pods()
    victim = sorted(pods, key=lambda p: p["name"])[0]
    t = time.time()
    sh(["kubectl", "-n", NS, "delete", "pod", victim["name"], "--grace-period=0", "--force", "--wait=false"])
    wait_all_ready(exclude=(victim["name"],))
    took = time.time() - t
    ctx["detail"] = {"killed_pod": victim["name"], "killed_node": victim["node"],
                     "replacement_ready_seconds": round(took, 2)}
    return took


def fault_drain(ctx):
    pods = ready_pods()
    per_node = {}
    for p in pods:
        per_node[p["node"]] = per_node.get(p["node"], 0) + 1
    node = max(per_node, key=lambda n: (per_node[n], n))
    ctx["drained_node"] = node
    t = time.time()
    p = sh(["kubectl", "drain", node, "--ignore-daemonsets", "--delete-emptydir-data", "--timeout=240s"],
           check=False, timeout=300)
    drain_seconds = time.time() - t
    wait_all_ready()
    took = time.time() - t
    ctx["detail"] = {"drained_node": node, "pods_on_node_before": per_node[node], "pods_per_node_before": per_node,
                     "kubectl_drain_seconds": round(drain_seconds, 2), "drain_exit": p.returncode,
                     "pods_ready_again_seconds": round(took, 2)}
    return took


def fault_badrelease(ctx):
    deadline = ctx["rollout_deadline_s"]
    t0 = time.time()
    up = helm("upgrade", RELEASE, str(ROOT / "charts" / "dispatch"), "--reuse-values", "--set", "image.tag=bad",
              "--wait", "--timeout", f"{deadline}s", check=False)
    t_detect = time.time()
    rollout_failed = up.returncode != 0
    pods_mid = [{"image": p["image"], "ready": p["ready"]} for p in api_pods()]
    t_rb = time.time()
    rb = helm("rollback", RELEASE, "--wait", "--timeout", "5m", "--cleanup-on-fail")
    t_done = time.time()
    wait_all_ready()
    t_ready = time.time()
    ctx["detail"] = {
        "rollout_deadline_s": deadline, "upgrade_failed": rollout_failed,
        "upgrade_to_detection_seconds": round(t_detect - t0, 2),
        "rollback_seconds": round(t_done - t_rb, 2),
        "bad_release_to_restored_seconds": round(t_ready - t0, 2),
        "rollback_to_ready_seconds": round(t_ready - t_rb, 2),
        "pods_when_deadline_hit": pods_mid, "rollback_stdout": rb.stdout.strip()[:200],
    }
    return t_ready - t_rb


FAULTS = {"rolling": fault_rolling, "kill": fault_kill, "drain": fault_drain, "badrelease": fault_badrelease}


# ----------------------------------------------------------------------------------------- one run
def run_once(sc, n, args, outdir):
    run_id = f"{sc}-{n}-{int(time.time())}"
    workdir = ROOT / "bench" / "work" / run_id
    ctx = {"run_id": run_id, "rollout_deadline_s": args.rollout_deadline}
    log(f"--- {sc} run {n}: settling")
    if sc == "drain":
        rebalance_pods()
    before = settle()
    pods_before = api_pods()
    duration = args.duration
    stages = [{"duration": f"{duration}s", "target": args.rate}]
    proc, raw = start_k6(workdir, run_id, stages, args.rate, args.max_vus)
    try:
        load_start = first_sample_time(raw)
        log(f"load started (k6 clock {load_start:.3f}); fault at +{args.fault_at}s")
        wait_vm_time(load_start + args.fault_at)
        fault_vm = vm_now()
        t_fault_off = fault_vm - load_start
        recovery = None
        if sc in FAULTS:
            recovery = FAULTS[sc](ctx)
            log(f"fault done; cluster recovery {recovery:.1f}s")
        rc = proc.wait(timeout=duration + 120)
    except Exception:
        sh(["docker", "rm", "-f", "p1-k6"], check=False, kube=False)
        raise
    log(f"k6 exited {rc}")
    rows, dropped = load_requests(raw, load_start)
    W = args.window
    result = {
        "scenario": sc, "run": n, "run_id": run_id,
        "params": {"rate_per_s": args.rate, "duration_s": duration, "fault_at_s": args.fault_at, "window_s": W,
                   "request_timeout_s": 5, "k6_image": K6_IMAGE},
        "load_start_vm_epoch": load_start, "fault_offset_s": round(t_fault_off, 3),
        "settled_before": before, "pods_before": pods_before, "k6_exit": rc,
        "requests": len(rows), "failed": sum(1 for r in rows if r[2] != "201"), "dropped_iterations": dropped,
        "status_counts": {},
        "overall": summarize_window(rows, 0, 1e9),
        "pre_fault": summarize_window(rows, 5, args.fault_at),
        "fault_window": summarize_window(rows, args.fault_at, args.fault_at + W),
        "post_window": summarize_window(rows, args.fault_at + W, 1e9),
        "error_window": error_window(rows),
        "cluster_recovery_s": None if recovery is None else round(recovery, 2),
        "detail": ctx.get("detail", {}),
    }
    for r in rows:
        result["status_counts"][r[2]] = result["status_counts"].get(r[2], 0) + 1
    result["hpa_after"] = hpa_status()
    result["pods_after"] = api_pods()
    outdir.mkdir(parents=True, exist_ok=True)
    with gzip.open(outdir / f"run-{n}.requests.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["offset_s", "latency_ms", "status"])
        for r in rows:
            w.writerow([round(r[0], 4), round(r[1], 3), r[2]])
    (outdir / f"run-{n}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    raw.unlink()
    log(f"run {n}: requests={result['requests']} failed={result['failed']} dropped={dropped} "
        f"p95(fault window)={result['fault_window']['p95_ms']} error_span={result['error_window']['span_s']}s "
        f"recovery={result['cluster_recovery_s']}")
    if sc == "drain" and ctx.get("drained_node"):
        sh(["kubectl", "uncordon", ctx["drained_node"]], check=False)
    return result


# ------------------------------------------------------------------------------------------- hpa
def run_hpa(n, args, outdir):
    run_id = f"hpa-{n}-{int(time.time())}"
    workdir = ROOT / "bench" / "work" / run_id
    log(f"--- hpa run {n}: settling")
    before = settle()
    lead, hold = args.hpa_lead, args.hpa_hold
    stages = [{"duration": f"{lead}s", "target": args.rate}, {"duration": "1s", "target": args.hpa_rate},
              {"duration": f"{hold}s", "target": args.hpa_rate}]
    proc, raw = start_k6(workdir, run_id, stages, args.rate, args.max_vus)
    samples = []
    try:
        load_start = first_sample_time(raw)
        step_at = load_start + lead
        end_at = load_start + lead + 1 + hold
        log(f"load started; step to {args.hpa_rate}/s at +{lead}s")
        base_ready = EXPECTED_REPLICAS
        while True:
            now = vm_now()
            if now >= end_at:
                break
            try:
                hp = hpa_status()
                pods = api_pods()
                samples.append({"t": round(now - load_start, 2), "hpa_current": hp["current"],
                                "hpa_desired": hp["desired"], "cpu_pct": hp["cpu_pct"],
                                "pods": len(pods), "ready": len(ready_pods(pods))})
            except RuntimeError as e:
                samples.append({"t": round(now - load_start, 2), "error": str(e)[:80]})
            time.sleep(1.0)
        rc = proc.wait(timeout=240)
    except Exception:
        sh(["docker", "rm", "-f", "p1-k6"], check=False, kube=False)
        raise
    rows, dropped = load_requests(raw, load_start)
    step_off = lead

    def first_after(pred):
        for s in samples:
            if s["t"] >= step_off and "error" not in s and pred(s):
                return round(s["t"] - step_off, 1)
        return None

    desired_max = max((s["hpa_desired"] or 0) for s in samples if "error" not in s)
    ready_max = max((s["ready"] or 0) for s in samples if "error" not in s)
    out = {
        "scenario": "hpa", "run": n, "run_id": run_id,
        "params": {"rate_before_per_s": args.rate, "rate_after_per_s": args.hpa_rate, "lead_s": lead,
                   "hold_s": hold, "k6_image": K6_IMAGE, "cpu_target_pct": 60, "metrics_resolution_s": 15},
        "settled_before": before, "k6_exit": rc, "requests": len(rows),
        "failed": sum(1 for r in rows if r[2] != "201"), "dropped_iterations": dropped,
        "before_step": summarize_window(rows, 5, lead), "after_step": summarize_window(rows, lead, 1e9),
        "cpu_pct_before_step_max": max([s["cpu_pct"] for s in samples if s.get("t", 0) < step_off
                                        and s.get("cpu_pct") is not None] or [None]),
        "seconds_to_first_scale_decision": first_after(lambda s: (s["hpa_desired"] or 0) > EXPECTED_REPLICAS),
        "seconds_to_first_new_pod_ready": first_after(lambda s: s["ready"] > EXPECTED_REPLICAS),
        "desired_replicas_max": desired_max, "ready_replicas_max": ready_max,
        "seconds_to_all_desired_ready": first_after(lambda s: s["ready"] >= desired_max and desired_max > EXPECTED_REPLICAS),
        "samples": samples,
    }
    outdir.mkdir(parents=True, exist_ok=True)
    with gzip.open(outdir / f"run-{n}.requests.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["offset_s", "latency_ms", "status"])
        for r in rows:
            w.writerow([round(r[0], 4), round(r[1], 3), r[2]])
    (outdir / f"run-{n}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    raw.unlink()
    log(f"hpa run {n}: decision +{out['seconds_to_first_scale_decision']}s, first new pod Ready "
        f"+{out['seconds_to_first_new_pod_ready']}s, all desired Ready +{out['seconds_to_all_desired_ready']}s, "
        f"desired max {desired_max}, failed {out['failed']}")
    return out


# --------------------------------------------------------------------------------------------- cli
def capture_env():
    RESULTS.mkdir(exist_ok=True)

    def first_line(args, **kw):
        try:
            return sh(args, check=False, **kw).stdout.strip().splitlines()[0]
        except Exception as e:  # noqa: BLE001 - recorded, not hidden
            return f"unavailable: {e}"

    cpu = first_line(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"], kube=False)
    info = json.loads(sh(["docker", "info", "--format", "{{json .}}"], kube=False).stdout)
    env = {
        "captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "host_cpu": cpu, "host_os": first_line(["cmd", "/c", "ver"], kube=False),
        "docker_server_version": info.get("ServerVersion"), "docker_ncpu": info.get("NCPU"),
        "docker_mem_bytes": info.get("MemTotal"),
        "kind": first_line(["kind", "version"], kube=False),
        "helm": first_line(["helm", "version", "--short"], kube=False),
        "terraform": first_line(["terraform", "version"], kube=False),
        "kubectl_client": first_line(["kubectl", "version", "--client"], kube=False),
        "k6_image": K6_IMAGE,
        "k6_version": first_line(["docker", "run", "--rm", K6_IMAGE, "version"], kube=False),
        "node_image": sh(["docker", "inspect", "-f", "{{.Config.Image}}", f"{CLUSTER}-worker"], kube=False).stdout.strip(),
        "kubernetes_server": json.loads(sh(["kubectl", "version", "-o", "json"]).stdout)["serverVersion"]["gitVersion"],
        "nodes": [n["metadata"]["name"] + " " + json.dumps(n["metadata"]["labels"].get("dispatch-kubernetes/role"))
                  for n in json.loads(sh(["kubectl", "get", "nodes", "-o", "json"]).stdout)["items"]],
        "dispatch_api_commit": sh(["git", "-C", str(ROOT / "vendor" / "dispatch-api"), "rev-parse", "HEAD"], kube=False).stdout.strip(),
        "chart_values": json.loads(helm("get", "values", RELEASE, "--all", "-o", "json").stdout),
    }
    (RESULTS / "environment.json").write_text(json.dumps(env, indent=2), encoding="utf-8")
    log("wrote results/environment.json")


def batch(args):
    sc = args.scenario
    outdir = RESULTS / sc
    outdir.mkdir(parents=True, exist_ok=True)
    waited = acquire_lock() if not args.no_lock else 0
    try:
        ps = sh(["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"], kube=False).stdout
        (outdir / "docker-ps-start.txt").write_text(
            f"# docker ps at the start of the {sc} batch ({datetime.datetime.now(datetime.timezone.utc).isoformat()}, "
            f"waited {waited}s for the lock)\n{ps}", encoding="utf-8")
        for n in range(args.first_run, args.first_run + args.runs):
            if sc == "hpa":
                run_hpa(n, args, outdir)
            else:
                run_once(sc, n, args, outdir)
        ps = sh(["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"], kube=False).stdout
        (outdir / "docker-ps-end.txt").write_text(
            f"# docker ps at the end of the {sc} batch ({datetime.datetime.now(datetime.timezone.utc).isoformat()})\n{ps}",
            encoding="utf-8")
    finally:
        if not args.no_lock:
            release_lock()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("capture-env")
    r = sub.add_parser("run")
    r.add_argument("scenario", choices=["baseline", "rolling", "kill", "drain", "badrelease", "hpa"])
    r.add_argument("--runs", type=int, default=5)
    r.add_argument("--first-run", type=int, default=1)
    r.add_argument("--rate", type=int, default=60, help="arrivals per second")
    r.add_argument("--duration", type=int, default=120)
    r.add_argument("--fault-at", type=int, default=30)
    r.add_argument("--window", type=int, default=60, help="fault window length for the per-window figures")
    r.add_argument("--max-vus", type=int, default=600)
    r.add_argument("--rollout-deadline", type=int, default=45)
    r.add_argument("--hpa-rate", type=int, default=400)
    r.add_argument("--hpa-lead", type=int, default=30)
    r.add_argument("--hpa-hold", type=int, default=240)
    r.add_argument("--no-lock", action="store_true", help="calibration and debugging only; never for reported runs")
    args = ap.parse_args()
    if args.cmd == "capture-env":
        capture_env()
    else:
        if args.scenario == "badrelease" and args.duration < 170:
            args.duration = 170
            args.window = max(args.window, 100)
        batch(args)


if __name__ == "__main__":
    main()
