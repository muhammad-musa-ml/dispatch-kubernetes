#!/usr/bin/env bash
# Builds the three images from source inside Docker (no Go toolchain is needed on the host).
#   dispatch-api:<sha>      Dispatch, from the pinned submodule's own Dockerfile (API and demo receiver)
#   dispatch-migrate:<sha>  psql + the submodule's migrations + docker/migrate/migrate.sh
#   dispatch-api:bad        a release that cannot start, for the rollback experiment
set -euo pipefail
cd "$(dirname "$0")/.."

sha=$(git -C vendor/dispatch-api rev-parse --short=7 HEAD)
echo "building images for dispatch-api@${sha}"
docker build -t "dispatch-api:${sha}" vendor/dispatch-api
docker build -f docker/migrate/Dockerfile -t "dispatch-migrate:${sha}" .
docker build -f docker/bad/Dockerfile --build-arg "BASE=dispatch-api:${sha}" -t dispatch-api:bad .
docker image ls --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}' | grep -E "^dispatch-(api|migrate):(${sha}|bad) "
