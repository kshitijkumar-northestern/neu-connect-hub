#!/usr/bin/env bash
# Rehearse a backup and restore end to end: dump the source database (pg_dump -Fc), restore the
# dump into a fresh scratch database, check its migration revision with `alembic current`,
# compare every table's row count with the source, then drop the scratch database. Exits
# non-zero on any mismatch or failure.
#
# The source is only ever read. Its counts and revision come from the same snapshot pg_dump
# reads (exported by a read-only transaction held open for the length of the dump), so writes
# landing on a live database while the dump runs can't cause a false mismatch.
#
# Usage, from apps/api with the virtualenv active (alembic must be on PATH):
#   bash scripts/backup_restore_check.sh
#
# Settings (environment variables; the defaults suit the local Compose stack):
#   PG_CONTAINER  container to run pg_dump, pg_restore and psql in, so they match the server's
#                 major version (default neu-connect-hub-postgres-1); set it empty to use the
#                 tools on PATH against PGHOST:PGPORT
#   PGUSER, PGPASSWORD, PGHOST, PGPORT   default app, local, localhost, 5432 (alembic always
#                 connects through PGHOST:PGPORT)
#   SOURCE_DB     default nu_hub
#   SCRATCH_DB    default nu_hub_restore_check; must end in _restore_check
#   KEEP_DUMP     1 keeps the dump file; it holds personal data, so by default it's deleted
#
# Written for the bash 3.2 that ships with macOS.

set -euo pipefail

PG_CONTAINER="${PG_CONTAINER-neu-connect-hub-postgres-1}"
PGUSER="${PGUSER:-app}"
PGPASSWORD="${PGPASSWORD:-local}"
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
SOURCE_DB="${SOURCE_DB:-nu_hub}"
SCRATCH_DB="${SCRATCH_DB:-nu_hub_restore_check}"
KEEP_DUMP="${KEEP_DUMP:-0}"
# Keeps "does not exist, skipping" notices out of the report; warnings and errors still show.
PGOPTIONS="${PGOPTIONS:--c client_min_messages=warning}"
export PGUSER PGPASSWORD PGHOST PGPORT PGOPTIONS

API_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DONE_MARKER="counts-complete"
# One line per base table in public, alembic_version included: "table|rows".
COUNT_SQL="SELECT table_name || '|' || (xpath('/row/n/text()', query_to_xml(format(
  'SELECT count(*) AS n FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text
  FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE';"

say() { printf '%s\n' "$*"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}
now() { python3 -c 'import time; print(f"{time.time():.3f}")'; }
since() { python3 -c 'import sys, time; print(f"{time.time() - float(sys.argv[1]):.2f}")' "$1"; }

# A PostgreSQL client tool, run in the container or from PATH.
pg() {
  if [ -n "$PG_CONTAINER" ]; then
    docker exec -i -e PGPASSWORD="$PGPASSWORD" -e PGOPTIONS="$PGOPTIONS" "$PG_CONTAINER" "$@"
  else
    "$@"
  fi
}
psql_at() {
  local db="$1"
  shift
  pg psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$db" "$@"
}
sorted_counts() { LC_ALL=C sort -t '|' -k 1,1 "$1"; }

# --- guards: the scratch database is the only thing this script creates or drops -------------

for name in "$SOURCE_DB" "$SCRATCH_DB"; do
  case "$name" in
    "" | *[!a-z0-9_]*) fail "database names must be lowercase letters, digits and _: '$name'" ;;
  esac
done
[ "$SCRATCH_DB" != "$SOURCE_DB" ] || fail "SCRATCH_DB must differ from SOURCE_DB"
case "$SCRATCH_DB" in
  *_restore_check) ;;
  *) fail "SCRATCH_DB must end in _restore_check, so no real database can be dropped" ;;
esac
command -v alembic >/dev/null || fail "alembic isn't on PATH: activate apps/api/.venv first"
[ "$(psql_at postgres -c "SELECT count(*) FROM pg_database WHERE datname = '$SOURCE_DB'")" = 1 ] ||
  fail "source database $SOURCE_DB doesn't exist"

umask 077
WORK="$(mktemp -d "${TMPDIR:-/tmp}/backup-restore-check.XXXXXX")"
DUMP="$WORK/$SOURCE_DB.dump"
# pg_dump and pg_restore work on a file, never a pipe: a custom-format archive written to a pipe
# has no data offsets, and pg_restore can't reorder one read from a pipe either.
if [ -n "$PG_CONTAINER" ]; then
  DUMP_IN_CONTAINER="/tmp/$(basename "$WORK").dump"
else
  DUMP_IN_CONTAINER=""
fi
HOLDER_PID=""

cleanup() {
  local status=$?
  { exec 3>&-; } 2>/dev/null || true
  if [ -n "$HOLDER_PID" ]; then
    kill "$HOLDER_PID" 2>/dev/null || true
    wait "$HOLDER_PID" 2>/dev/null || true
  fi
  if [ -n "$DUMP_IN_CONTAINER" ]; then
    docker exec "$PG_CONTAINER" rm -f "$DUMP_IN_CONTAINER" >/dev/null 2>&1 || true
  fi
  pg dropdb -U "$PGUSER" --if-exists --force "$SCRATCH_DB" >/dev/null 2>&1 ||
    printf 'WARN: could not drop %s; drop it by hand\n' "$SCRATCH_DB" >&2
  if [ "$KEEP_DUMP" = 1 ] && [ -f "$DUMP" ]; then
    say "Dump kept at $DUMP"
  else
    rm -rf "$WORK"
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

started="$(now)"

# --- 1. hold a read-only snapshot of the source; read its revision and counts inside it -------

mkfifo "$WORK/holder.in"
psql_at "$SOURCE_DB" <"$WORK/holder.in" >"$WORK/holder.out" 2>"$WORK/holder.err" &
HOLDER_PID=$!
exec 3>"$WORK/holder.in"
printf '%s\n' \
  "BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY;" \
  "SELECT pg_export_snapshot();" \
  "SELECT string_agg(version_num, ',' ORDER BY version_num COLLATE \"C\") FROM alembic_version;" \
  "$COUNT_SQL" \
  "SELECT '$DONE_MARKER';" >&3

tries=0
until grep -qx "$DONE_MARKER" "$WORK/holder.out" 2>/dev/null; do
  kill -0 "$HOLDER_PID" 2>/dev/null || fail "snapshot session ended: $(cat "$WORK/holder.err")"
  tries=$((tries + 1))
  [ "$tries" -le 600 ] || fail "timed out waiting for the source counts"
  sleep 0.1
done
SNAPSHOT="$(sed -n 1p "$WORK/holder.out")"
SOURCE_REVISION="$(sed -n 2p "$WORK/holder.out")"
sed -e '1,2d' -e "/^$DONE_MARKER\$/d" "$WORK/holder.out" >"$WORK/source.unsorted"
sorted_counts "$WORK/source.unsorted" >"$WORK/source.counts"
[ -n "$SOURCE_REVISION" ] || fail "$SOURCE_DB has no alembic revision"

# --- 2. dump from that same snapshot, then release it ----------------------------------------

dump_started="$(now)"
if [ -n "$PG_CONTAINER" ]; then
  pg pg_dump -U "$PGUSER" -d "$SOURCE_DB" -Fc --snapshot="$SNAPSHOT" -f "$DUMP_IN_CONTAINER"
  docker cp -q "$PG_CONTAINER:$DUMP_IN_CONTAINER" "$DUMP"
else
  pg_dump -U "$PGUSER" -d "$SOURCE_DB" -Fc --snapshot="$SNAPSHOT" -f "$DUMP"
fi
dump_seconds="$(since "$dump_started")"
printf '%s\n' "COMMIT;" >&3
exec 3>&-
wait "$HOLDER_PID" || fail "snapshot session failed: $(cat "$WORK/holder.err")"
HOLDER_PID=""
DUMP_BYTES="$(wc -c <"$DUMP" | tr -d ' ')"
[ "$DUMP_BYTES" -gt 0 ] || fail "the dump is empty"

# --- 3. restore the dump file (the copy that would be stored) into a fresh database -----------

pg dropdb -U "$PGUSER" --if-exists --force "$SCRATCH_DB"
pg createdb -U "$PGUSER" -T template0 "$SCRATCH_DB"
restore_started="$(now)"
if [ -n "$PG_CONTAINER" ]; then
  docker exec "$PG_CONTAINER" rm -f "$DUMP_IN_CONTAINER"
  docker cp -q "$DUMP" "$PG_CONTAINER:$DUMP_IN_CONTAINER"
  pg pg_restore -U "$PGUSER" -d "$SCRATCH_DB" --single-transaction --exit-on-error \
    "$DUMP_IN_CONTAINER"
else
  pg_restore -U "$PGUSER" -d "$SCRATCH_DB" --single-transaction --exit-on-error "$DUMP"
fi
restore_seconds="$(since "$restore_started")"

# --- 4. the restore is at the source's migration revision -------------------------------------

# The scratch database's URL for alembic. The user and password are percent-encoded, since a
# password may hold @ : / # or %; they're read from the environment, never put on a command
# line. A PGHOST that's a Unix-socket directory goes in ?host=, which asyncpg reads.
scratch_url() {
  python3 - "$SCRATCH_DB" <<'PY'
import os
import sys
from urllib.parse import quote

def encoded(value: str) -> str:
    return quote(value, safe="")

auth = f"{encoded(os.environ['PGUSER'])}:{encoded(os.environ['PGPASSWORD'])}"
host, port, database = os.environ["PGHOST"], os.environ["PGPORT"], sys.argv[1]
if host.startswith("/"):
    print(f"postgresql+asyncpg://{auth}@:{port}/{database}?host={encoded(host)}")
else:
    netloc = f"[{host}]" if ":" in host else host
    print(f"postgresql+asyncpg://{auth}@{netloc}:{port}/{database}")
PY
}
# Revisions one per line, sorted in C order and comma-joined, the same form as the source's.
alembic_at() {
  (cd "$API_ROOT" &&
    DATABASE_URL="$(scratch_url)" alembic "$@" 2>"$WORK/alembic.err" |
    awk 'NF { print $1 }' | LC_ALL=C sort | paste -sd ',' -)
}
RESTORED_REVISION="$(alembic_at current)" || fail "alembic current failed: $(cat "$WORK/alembic.err")"
HEAD_REVISION="$(alembic_at heads)" || fail "alembic heads failed: $(cat "$WORK/alembic.err")"
[ -n "$RESTORED_REVISION" ] || fail "alembic current found no revision in $SCRATCH_DB"
[ "$RESTORED_REVISION" = "$SOURCE_REVISION" ] ||
  fail "restored revision $RESTORED_REVISION isn't the source's $SOURCE_REVISION"

# --- 5. every table has the same number of rows ------------------------------------------------

psql_at "$SCRATCH_DB" -c "$COUNT_SQL" >"$WORK/restored.unsorted"
sorted_counts "$WORK/restored.unsorted" >"$WORK/restored.counts"
TABLES="$(wc -l <"$WORK/source.counts" | tr -d ' ')"
ROWS="$(awk -F '|' '{ total += $2 } END { print total + 0 }' "$WORK/source.counts")"

say "Source:   $SOURCE_DB at revision $SOURCE_REVISION (snapshot $SNAPSHOT)"
if [ "$RESTORED_REVISION" = "$HEAD_REVISION" ]; then
  say "Restored: $SCRATCH_DB at revision $RESTORED_REVISION, the repository head"
else
  say "Restored: $SCRATCH_DB at revision $RESTORED_REVISION (WARN: repository head is $HEAD_REVISION)"
fi
say "Dump:     $DUMP_BYTES bytes in $dump_seconds s"
say "Restore:  $restore_seconds s"
say ""
printf '%-24s %10s %10s\n' "table" "source" "restored"
LC_ALL=C join -t '|' -a 1 -a 2 -e MISSING -o 0,1.2,2.2 "$WORK/source.counts" \
  "$WORK/restored.counts" |
  awk -F '|' '{ printf "%-24s %10s %10s%s\n", $1, $2, $3, ($2 == $3 ? "" : "  MISMATCH") }'
say ""

cmp -s "$WORK/source.counts" "$WORK/restored.counts" ||
  fail "row counts differ between $SOURCE_DB and the restore (see MISMATCH above)"
say "OK: $TABLES tables and $ROWS rows match; $(since "$started") s in all"
