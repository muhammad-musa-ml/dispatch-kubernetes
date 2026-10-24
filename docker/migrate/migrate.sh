#!/bin/sh
# Applies every *.sql file in $MIGRATIONS_DIR once, in name order. Safe to run any number of times,
# including at the same moment from two pods: each file runs in one transaction under an advisory lock,
# and a file already recorded in schema_migrations is skipped.
set -eu

url="${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL is required}"
dir="${MIGRATIONS_DIR:-/migrations}"
wait_seconds="${WAIT_SECONDS:-90}"

i=0
until pg_isready -q -d "$url"; do
  i=$((i + 1))
  if [ "$i" -ge "$wait_seconds" ]; then
    echo "migrate: database not ready after ${wait_seconds}s" >&2
    exit 1
  fi
  sleep 1
done

psql "$url" -v ON_ERROR_STOP=1 -q <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

applied=0
skipped=0
for f in $(ls "$dir"/*.sql | sort); do
  version=$(basename "$f")
  out=$(psql "$url" -v ON_ERROR_STOP=1 -q -t -A --single-transaction -v version="$version" -v file="$f" <<'SQL'
SELECT pg_advisory_xact_lock(727274);
SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :'version') AS done \gset
\if :done
  SELECT 'skipped';
\else
  \i :file
  INSERT INTO schema_migrations (version) VALUES (:'version');
  SELECT 'applied';
\endif
SQL
)
  case "$out" in
    *applied*) applied=$((applied + 1)); echo "migrate: applied $version" ;;
    *skipped*) skipped=$((skipped + 1)); echo "migrate: already applied $version" ;;
    *) echo "migrate: unexpected output for $version: $out" >&2; exit 1 ;;
  esac
done
echo "migrate: done (applied=$applied already_applied=$skipped)"
