#!/usr/bin/env bash
# Probe, not a timed run: what does `kubectl delete pod --grace-period=0 --force` do to the container?
# Prints the container's exit code from containerd (137 = SIGKILL, 0 = clean exit after SIGTERM).
# Needs KUBECONFIG to point at the cluster and the kind node containers to be reachable with `docker exec`.
set -u
pod=$(kubectl -n dispatch get pods -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}')
node=$(kubectl -n dispatch get pod "$pod" -o jsonpath='{.spec.nodeName}')
cid=$(docker exec "$node" crictl ps --label "io.kubernetes.pod.name=$pod" --name dispatch -q | head -1)
echo "pod=$pod node=$node container=$cid"
kubectl -n dispatch delete pod "$pod" --grace-period=0 --force --wait=false
sleep 1
docker exec "$node" crictl ps -a --id "$cid" | tail -n +2
docker exec "$node" crictl inspect --output go-template --template '{{.status.state}} exit={{.status.exitCode}} reason={{.status.reason}}' "$cid"
