#!/usr/bin/env bash
# Smoke deploy and rollback exercise against whatever cluster kubectl points at. CI runs it on a fresh
# kind cluster; images must already be loaded into the nodes (scripts/build-images.sh, then `kind load`).
#
#   1. create the namespace and the Secrets the charts reference (random values, nothing committed)
#   2. install PostgreSQL, then Dispatch (its pre-install hook migrates the schema)
#   3. post an event and wait until it is delivered to the demo receiver
#   4. helm upgrade (rolling update), then a bad release that cannot start, then helm rollback
#   5. check the service answers again and the history shows the rollback
set -euo pipefail
cd "$(dirname "$0")/.."

NS="${NS:-dispatch}"
TAG="${TAG:-$(git -C vendor/dispatch-api rev-parse --short=7 HEAD)}"
PORT="${PORT:-18101}"
MIN_REPLICAS="${MIN_REPLICAS:-2}"
helm_dispatch=(helm --namespace "$NS")

rand() { openssl rand -hex "$1"; }

echo "== secrets"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
db_pass=$(rand 16)
key_alpha=$(rand 20)
webhook=$(rand 24)
base="postgres://dispatch:${db_pass}@dispatch-postgres.${NS}.svc.cluster.local:5432/dispatch?sslmode=disable"
kubectl -n "$NS" create secret generic dispatch-postgres --from-literal=POSTGRES_PASSWORD="$db_pass" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create secret generic dispatch-app \
  --from-literal=DATABASE_URL="${base}&pool_max_conns=10" \
  --from-literal=MIGRATION_DATABASE_URL="${base}" \
  --from-literal=API_KEYS="{\"${key_alpha}\":\"alpha\"}" \
  --from-literal=DELIVERY_TARGETS="{\"alpha\":{\"url\":\"http://dispatch-receiver.${NS}.svc.cluster.local:8081/webhook\",\"secret\":\"${webhook}\"}}" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create secret generic dispatch-receiver --from-literal=WEBHOOK_SECRET="$webhook" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "== install"
"${helm_dispatch[@]}" upgrade --install dispatch-postgres charts/dispatch-postgres \
  --set-string nodeSelector."dispatch-kubernetes/role"=data --wait --timeout 5m
"${helm_dispatch[@]}" upgrade --install dispatch charts/dispatch \
  --set image.tag="$TAG" --set migrateImage.tag="$TAG" \
  --set-string nodeSelector."dispatch-kubernetes/role"=app \
  --set autoscaling.minReplicas="$MIN_REPLICAS" --set autoscaling.maxReplicas=4 \
  --wait --timeout 5m
kubectl -n "$NS" get pods -o wide

kubectl -n "$NS" port-forward svc/dispatch "${PORT}:8080" >/tmp/port-forward.log 2>&1 &
pf=$!
trap 'kill $pf 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:${PORT}/readyz" >/dev/null 2>&1 && break
  sleep 1
done

post_event() {
  curl -sS -o /tmp/event.json -w '%{http_code}' "http://127.0.0.1:${PORT}/v1/events" \
    -H "Authorization: Bearer ${key_alpha}" -H "Idempotency-Key: $1" -H 'Content-Type: application/json' \
    -d '{"type":"smoke.created","payload":{"n":1}}'
}

echo "== deliver"
code=$(post_event "smoke-$(rand 4)")
[ "$code" = "201" ] || { echo "POST returned $code"; cat /tmp/event.json; exit 1; }
event_id=$(sed -n 's/.*"id":"\([0-9a-f]*\)".*/\1/p' /tmp/event.json | head -1)
echo "event $event_id"
for _ in $(seq 1 60); do
  status=$(curl -fsS "http://127.0.0.1:${PORT}/v1/events/${event_id}" -H "Authorization: Bearer ${key_alpha}" \
    | sed -n 's/.*"status":"\([a-z_]*\)".*/\1/p' | head -1)
  [ "$status" = "delivered" ] && break
  sleep 1
done
[ "$status" = "delivered" ] || { echo "event not delivered, status=$status"; exit 1; }
echo "delivered"

echo "== rolling upgrade"
"${helm_dispatch[@]}" upgrade dispatch charts/dispatch --reset-then-reuse-values --set-string podAnnotations.rolloutId=smoke \
  --wait --timeout 5m

echo "== bad release, then rollback"
if "${helm_dispatch[@]}" upgrade dispatch charts/dispatch --reset-then-reuse-values --set image.tag=bad --wait --timeout 60s; then
  echo "the bad release was expected to fail"
  exit 1
fi
"${helm_dispatch[@]}" rollback dispatch --wait --timeout 5m
"${helm_dispatch[@]}" history dispatch
"${helm_dispatch[@]}" history dispatch -o json | grep -q '"description":"Rollback to' || { echo "no rollback in history"; exit 1; }
kubectl -n "$NS" get pods -l app.kubernetes.io/component=api \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.containers[0].image}{" "}{.status.phase}{"\n"}{end}'
if kubectl -n "$NS" get pods -l app.kubernetes.io/component=api -o jsonpath='{.items[*].spec.containers[0].image}' | grep -q ':bad'; then
  echo "a pod still runs the bad image"
  exit 1
fi
code=$(post_event "smoke-after-$(rand 4)")
[ "$code" = "201" ] || { echo "POST after rollback returned $code"; exit 1; }
echo "smoke ok"
