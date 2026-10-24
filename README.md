# dispatch-kubernetes

Runs [Dispatch](https://github.com/muhammad-musa-ml/dispatch-api), a multi-tenant webhook-delivery API in Go
on PostgreSQL, on a local multi-node Kubernetes cluster, and measures what a deploy, a pod kill, a node
drain and a bad release do to a client sending a steady request rate.

Work in progress. The design is in [docs/rfc/0001-dispatch-on-kubernetes.md](docs/rfc/0001-dispatch-on-kubernetes.md).
