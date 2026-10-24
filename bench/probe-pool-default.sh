#!/usr/bin/env bash
# Probe, not a timed run: how large is Dispatch's connection pool when DATABASE_URL has no pool_max_conns?
# Runs bench/pool-default (pgx v5.11.0, the version Dispatch pins) in the Go image that builds Dispatch,
# with all CPUs visible and then with the process restricted to 2 and to 8 CPUs.
# Output is saved as results/pool-default.txt.
set -eu
cd "$(dirname "$0")/.."
src=$(cd bench/pool-default && { pwd -W 2>/dev/null || pwd; })
img=golang:1.26-bookworm
run() {
  MSYS_NO_PATHCONV=1 docker run --rm "$@" -v "$src:/src" -w /src -e GOFLAGS=-mod=mod "$img" go run .
}
echo "image=$img pgx=v5.11.0"
echo "# all CPUs visible to the container"
run
echo "# --cpuset-cpus 0-1"
run --cpuset-cpus 0-1
echo "# --cpuset-cpus 0-7"
run --cpuset-cpus 0-7
