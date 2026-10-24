# RFC 0001: Running Dispatch on Kubernetes

- Status: Accepted
- Author: muhammad-musa-ml
- Date: 2026-10-24
- Review: single author. This document records what was decided, what was rejected and why. The measured
  results are in the README, not here; this RFC states the questions and how they will be answered.

## Context

[Dispatch](https://github.com/muhammad-musa-ml/dispatch-api) is a multi-tenant webhook-delivery API in Go
on PostgreSQL. A producer posts an event; Dispatch stores it and its delivery job in one transaction, and
background workers send a signed webhook to the tenant's receiver. The repository runs it with Docker
Compose on one machine. Its own evidence file lists what was never tested: multi-host scaling, rolling
migrations, and behaviour when pods or nodes go away.

This project runs the same code on Kubernetes and measures what a deploy, a crash, a node drain and a bad
release do to a client sending a steady request rate. Dispatch itself is not changed: it is pulled in as
a git submodule at a pinned commit and built into an image from its own Dockerfile.

Facts about the service that shape the design (read from `cmd/api/main.go` and `docs/design.md` at the
pinned commit):

- On SIGTERM the HTTP server drains for at most 10 seconds, then the four delivery workers are cancelled
  and awaited. A worker's HTTP client timeout is 5 seconds. SIGKILL leaves leases that expire after 15
  seconds, after which another worker re-claims the job.
- `/healthz` answers 200 if the process is up. `/readyz` answers 200 only if a database ping succeeds. It
  does not check the schema.
- The schema is one SQL file, applied by Docker Compose only when the database volume is first created.
  There is no migration runner.
- Delivery is at least once. Any replica can claim any job (`FOR UPDATE SKIP LOCKED`), so replicas are
  interchangeable and the API is safe to scale horizontally.
- Configuration is environment only: `API_KEYS`, `DELIVERY_TARGETS` (which holds the signing secret),
  `DATABASE_URL`, `ALLOW_HTTP_TARGETS`, `LISTEN_ADDR`.

## Goals

1. One command stands up a multi-node cluster, the database, the Dispatch release and its demo receiver,
   and one command removes them.
2. A deploy of a new version, a pod crash, and a node drain each cost a client sending at a fixed rate as
   few failed requests as the platform allows, and the number is measured, not assumed.
3. A release that cannot start never takes traffic, and `helm rollback` restores the previous release; the
   time this takes is measured.
4. The number of API replicas follows load (a HorizontalPodAutoscaler), and the time from a load step to new
   pods serving traffic is measured.
5. The schema is applied by a migration Job that can run again on every upgrade without harm.
6. Continuous integration lints the chart and Terraform, builds the image, creates an ephemeral cluster,
   deploys, checks that a posted event is delivered, and exercises `helm rollback`.
7. Nothing in the repository is specific to a laptop in a way that blocks a later cloud backend: the cluster
   is one Terraform module behind a small interface, and the platform layer only needs a Kubernetes
   endpoint.

## Non-goals

- A cloud deployment. AWS and every other paid backend are out of scope; nothing here creates an account or
  spends money.
- A highly available database. PostgreSQL runs as a single pod on a labelled node. Losing that pod or node
  is an outage and is not part of the experiments.
- Changing Dispatch: no metrics endpoint, no schema change, no tenant rate limits.
- Ingress, TLS and DNS. The load generator reaches the Service through a NodePort.
- A claim about production capacity. The cluster is four containers on one laptop that other work shares.

## Proposed design

### Cluster

kind, one control-plane node and three workers. One worker carries the label `dispatch-kubernetes/role=data`
and holds PostgreSQL and its volume; the other two carry the API and the receiver. The control plane
publishes host port 18100 for manual `curl`; the load generator joins the `kind` Docker network and
calls the NodePort on the control plane directly, so traffic crosses `kube-proxy` as it would behind any
Service. kind runs Kubernetes with `metrics-server` installed (flag `--kubelet-insecure-tls`, needed
because kind kubelets serve self-signed certificates) so the HorizontalPodAutoscaler has a CPU signal.

### Terraform

```
terraform/
  modules/cluster-kind/   kind cluster, optional image preload   -> connection object
  modules/platform/       namespace, Secrets, metrics-server, PostgreSQL release, Dispatch release
  envs/local/             wires cluster-kind into platform
```

`platform` is configured only through the `kubernetes` and `helm` providers and knows nothing about kind.
The interface between the two modules is the path of a kubeconfig that reaches the cluster. (The first
version passed the kind provider's endpoint and certificate attributes to the providers instead;
`terraform destroy` then failed during its refresh, because the provider returned them empty, so the
kubeconfig file is the interface.) A cloud environment would add `envs/<cloud>/` with its own cluster
module that returns a kubeconfig the same way, and set the chart values that differ (Service type,
storage class, an external database). Secrets are created by
Terraform from `random_password` resources and read by the charts by name; the charts never contain a
credential.

### Helm charts

`charts/dispatch-postgres` is the database: a StatefulSet with a persistent volume, pinned to the data
node. It is a separate release because a database and an application have different lifecycles, and so the
migration hook below can rely on a database that already exists.

`charts/dispatch` holds:

| Object | Decision |
|---|---|
| Deployment | `RollingUpdate` with `maxUnavailable: 0`, `maxSurge: 1`; non-root, read-only root filesystem, all capabilities dropped; topology spread across nodes |
| Probes | startup and liveness on `/healthz`; readiness on `/readyz` with a short period so a pod that loses the database stops taking traffic quickly |
| Resources | requests and limits set; the HorizontalPodAutoscaler's utilisation is relative to the CPU request |
| HorizontalPodAutoscaler | CPU utilisation target, minimum 3 replicas, a bounded maximum, a shortened scale-down window so repeated experiments finish |
| PodDisruptionBudget | `maxUnavailable: 1`, so a drain evicts one API pod at a time |
| Migration Job | Helm hook `pre-install,pre-upgrade`; an idempotent runner that applies each `*.sql` file once, recorded in a `schema_migrations` table, inside a transaction and under an advisory lock |
| Receiver | the demo receiver from the same image (command `/receiver`), as a second Deployment and Service |
| Secrets | referenced by name (`existingSecret`); no credential in values or templates |

**Graceful termination.** The pod must outlast the service's own shutdown. The budget is: a `preStop` sleep
of 5 seconds so the endpoint is removed from `kube-proxy` before the process stops accepting connections;
10 seconds for the HTTP drain (the service's deadline); up to 5 seconds for an in-flight webhook attempt (the
worker client timeout); 10 seconds of margin. That is 30 seconds, written as `terminationGracePeriodSeconds`
with the arithmetic in a comment next to it. The sleep uses the native `preStop.sleep` action because the
image is distroless and has no shell.

**Why the migration is a Job and not an init container.** Several replicas start at once and each would
race to migrate. A Job runs once per release. Because `/readyz` does not check the schema, a pod could
report Ready on a database with no tables; running the Job as a `pre-install,pre-upgrade` hook means
the schema exists before any new pod starts.

### Images

Two images built locally and in CI. `dispatch-api` is built from the submodule's own Dockerfile and is used
for both the API and the receiver. `dispatch-migrate` is `postgres:17-alpine` plus a small shell runner and
the submodule's `migrations/` directory. A third image exists only for one experiment: `dispatch-api:bad`
copies the receiver binary to the path `/dispatch`. It is a release that was built and pushed but
exits immediately because the receiver refuses to start without its own secret.

## Measurement design

The load generator is k6 using a constant-arrival-rate executor (open loop): a request is started on
schedule whether or not earlier ones have finished, so a stall shows up as failures and latency instead of
the client quietly slowing down. Each request is `POST /v1/events` with a fresh idempotency key. A request
counts as failed on a transport error, a timeout of 5 seconds, or any status other than 201.

For each scenario and for a no-fault baseline: 5 runs, the same arrival rate, the same duration. In each
run the fault is injected at a known offset after the start of the load, and the cluster is returned to its
starting state and the delivery backlog drained before the next run. Per run we record failed requests, the
window in which failures occurred, p95 latency before, during and after the fault, and the time the cluster
took to return to its desired state. Results are reported as the median and range of the 5 runs.

| Scenario | Fault |
|---|---|
| Rolling deploy | `helm upgrade` with a changed pod annotation, so every API pod is replaced |
| Pod kill | one API pod deleted with a zero grace period (the worst case, as if the process were killed) |
| Node drain | `kubectl drain` of the worker holding the most API pods, with the PodDisruptionBudget in force |
| Bad release | `helm upgrade` to `dispatch-api:bad`, detection by a rollout deadline, then `helm rollback` |

Time to recover has two definitions and they are reported separately: the error window seen by the client
(first to last failed request), and the time until the cluster has all desired pods Ready again as polled
through the Kubernetes API. For the bad release the rollout deadline is a parameter I chose, not a result,
so the table reports the rollback time and the deadline separately.

The HorizontalPodAutoscaler is measured with a separate load step: the arrival rate is raised several
times at a known instant, and the time to the first scale-out decision and to the new pods being Ready is
recorded.

All timed runs hold a shared lock file, because other work runs on the same machine. The README states the
machine and what else was running.

## Alternatives considered

- **Compose or a single node.** Cannot show a drain or scheduling at all. Rejected.
- **minikube or k3d.** Both work. kind was chosen because it is what GitHub-hosted runners can create in a
  few lines, so the local and CI clusters are the same shape.
- **Terraform for everything versus Helm alone.** Helm alone cannot create the cluster or generate
  secrets. Terraform alone would hide the chart from `helm lint` and from CI. Both are used, with a thin
  boundary: Terraform owns the cluster, Secrets and release order; Helm owns the application objects.
- **PostgreSQL as a subchart.** Would couple the database lifecycle to application upgrades and make the
  pre-upgrade migration hook run before the database on a fresh install. Rejected for two releases.
- **A migration init container.** Races between replicas and runs on every pod start. Rejected for the Job.
- **Linkerd or an ingress controller to smooth deploys.** Out of scope; the point is what Kubernetes
  primitives alone achieve.
- **Cluster autoscaler or KEDA on queue depth.** Dispatch exports no metrics. The HorizontalPodAutoscaler
  on CPU is the only signal available without changing the service.

## Risks and open questions

- **Shared hardware.** The laptop runs other containers. The lock serialises timed runs between the four
  parallel builds but not against unrelated processes; `docker ps` is recorded beside every result file.
- **kind is not a cloud.** Node drain and pod startup are faster than on a managed cluster with real
  image pulls, so absolute recovery times will not transfer. The ordering of scenarios and what causes the
  errors should.
- **The demo receiver fails the first two attempts of every event** by design, so the background delivery
  load is three attempts per event. It is left as is: it is part of the service being run.
- **Single database pod.** The experiments never disturb it, so nothing here says how Dispatch behaves
  during a database failover.
- **At-least-once delivery.** A pod kill can cause a duplicate webhook after the lease expires. The README
  reports duplicates only if the harness counts them; otherwise it says "not measured".
