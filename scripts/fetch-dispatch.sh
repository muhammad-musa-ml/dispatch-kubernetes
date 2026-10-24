#!/usr/bin/env bash
# Puts the Dispatch source at the pinned commit in vendor/dispatch-api (not committed to this repository).
# The commit is the single line of DISPATCH_API_COMMIT. Safe to run again: it only fetches when the
# checkout is missing or at another commit, and it fails if the result is not exactly the pinned commit.
set -euo pipefail
cd "$(dirname "$0")/.."

repo="${DISPATCH_API_REPO:-https://github.com/muhammad-musa-ml/dispatch-api.git}"
want=$(tr -d '[:space:]' < DISPATCH_API_COMMIT)
dir=vendor/dispatch-api

if [ ! -d "$dir/.git" ] && [ ! -f "$dir/.git" ]; then
  mkdir -p vendor
  git init -q "$dir"
  git -C "$dir" remote add origin "$repo"
fi
have=$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)
if [ "$have" != "$want" ]; then
  git -C "$dir" fetch -q --depth 1 origin "$want"
  git -C "$dir" checkout -q --detach "$want"
fi
have=$(git -C "$dir" rev-parse HEAD)
if [ "$have" != "$want" ]; then
  echo "vendor/dispatch-api is at $have, expected $want" >&2
  exit 1
fi
echo "dispatch-api at $have"
