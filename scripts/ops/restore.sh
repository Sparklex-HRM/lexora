#!/usr/bin/env bash
# Lexora HRM — restore a backup made by backup.sh into a database (and files target).
#
#   scripts/ops/restore.sh --db <target_db> <lexora_db_*.dump> [lexora_files_*.tar.gz]
#
# Safety:
#   * the target database must NOT exist (it is created) — unless --replace is given, which
#     drops it first. Restoring over the live database name additionally needs
#     CONFIRM_RESTORE_INTO_LIVE=yes (stop the app first: scale every deployment to 0).
#   * the sha256 manifest next to the dump is checked when present.
#   * files are extracted only into an EMPTY FILES_DIR / FILES_VOLUME (or with --replace).
#
# Database access: same env as backup.sh (PG_DOCKER_CONTAINER, or PGHOST/PGPORT/PGUSER/PGPASSWORD).
# Live database name: LIVE_DB (default lexora).
# After restoring the live DB, run: scripts/ops/migrate.sh upgrade  (brings an older backup's schema up).
set -euo pipefail
export MSYS_NO_PATHCONV=1

TARGET=""; REPLACE=0; ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --db) TARGET="$2"; shift 2 ;;
    --replace) REPLACE=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
[ -n "$TARGET" ] && [ "${#ARGS[@]}" -ge 1 ] || { sed -n '2,16p' "$0"; exit 2; }
DUMP="${ARGS[0]}"; FILES_TAR="${ARGS[1]:-}"
PGUSER="${PGUSER:-postgres}"; LIVE="${LIVE_DB:-${PGDATABASE:-lexora}}"
log() { printf '[restore %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
[ -f "$DUMP" ] || { log "no such dump: $DUMP"; exit 2; }
[[ "$TARGET" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || { log "bad database name"; exit 2; }
if [ "$TARGET" = "$LIVE" ] && [ "${CONFIRM_RESTORE_INTO_LIVE:-}" != "yes" ]; then
  log "refusing to restore into the LIVE database '$LIVE' without CONFIRM_RESTORE_INTO_LIVE=yes"; exit 3
fi

if [ -n "${PG_DOCKER_CONTAINER:-}" ]; then
  PSQL() { docker exec -i ${PGPASSWORD:+-e PGPASSWORD} "$PG_DOCKER_CONTAINER" psql -U "$PGUSER" -v ON_ERROR_STOP=1 "$@"; }
  PGR() { docker exec -i ${PGPASSWORD:+-e PGPASSWORD} "$PG_DOCKER_CONTAINER" pg_restore -U "$PGUSER" "$@"; }
else
  PSQL() { psql -U "$PGUSER" -v ON_ERROR_STOP=1 "$@"; }
  PGR() { pg_restore -U "$PGUSER" "$@"; }
fi

# ── checksum ──────────────────────────────────────────────────────────────────
TS="$(basename "$DUMP" | sed -E 's/^lexora_db_(.*)\.dump$/\1/')"
MAN="$(dirname "$DUMP")/lexora_backup_${TS}.sha256"
if [ -f "$MAN" ]; then
  # go-live 2026-10-24: a mismatch must STOP the restore (an `a && b` list does not trip set -e)
  if ( cd "$(dirname "$DUMP")" && sha256sum -c --quiet --ignore-missing "$(basename "$MAN")" ); then
    log "checksums ok ($MAN)"
  else
    log "ERROR: checksum mismatch — refusing to restore"; exit 4
  fi
else
  log "no manifest next to the dump — checksum not verified"
fi

# ── database ──────────────────────────────────────────────────────────────────
EXISTS="$(PSQL -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$TARGET'")"
if [ "$EXISTS" = "1" ]; then
  [ "$REPLACE" = 1 ] || { log "database '$TARGET' exists — pass --replace to drop and recreate it"; exit 3; }
  log "dropping existing database $TARGET"
  PSQL -d postgres -c "DROP DATABASE \"$TARGET\" WITH (FORCE)"
fi
PSQL -d postgres -c "CREATE DATABASE \"$TARGET\"" >/dev/null
log "pg_restore → $TARGET"
START=$(date +%s)
PGR -d "$TARGET" --no-owner --no-privileges --exit-on-error < "$DUMP"
log "pg_restore done in $(( $(date +%s) - START ))s"
TABLES="$(PSQL -d "$TARGET" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema NOT IN ('pg_catalog','information_schema')")"
log "restored: $TABLES tables"
for t in organizations users employees pay_runs; do
  n="$(PSQL -d "$TARGET" -tAc "SELECT CASE WHEN to_regclass('public.$t') IS NULL THEN -1 ELSE (SELECT count(*) FROM public.$t) END" 2>/dev/null || echo '?')"
  log "  rows $t = $n"
done

# ── files ─────────────────────────────────────────────────────────────────────
if [ -n "$FILES_TAR" ]; then
  [ -f "$FILES_TAR" ] || { log "no such files archive: $FILES_TAR"; exit 2; }
  if [ -n "${FILES_DIR:-}" ]; then
    mkdir -p "$FILES_DIR"
    if [ -n "$(ls -A "$FILES_DIR")" ] && [ "$REPLACE" != 1 ]; then log "FILES_DIR not empty — pass --replace"; exit 3; fi
    tar -xzf "$FILES_TAR" -C "$FILES_DIR"
    log "files restored into $FILES_DIR: $(find "$FILES_DIR" -type f | wc -l) file(s)"
  elif [ -n "${FILES_VOLUME:-}" ]; then
    CLEAN=""; [ "$REPLACE" = 1 ] && CLEAN="find /data -mindepth 1 -delete && "
    docker run --rm -i -v "$FILES_VOLUME:/data" alpine:3.20 sh -c \
      "${CLEAN}[ -z \"\$(ls -A /data)\" ] || { echo 'volume not empty — pass --replace'; exit 3; }; tar -xzf - -C /data && find /data -type f | wc -l" \
      < "$FILES_TAR" | sed 's/^/[restore] files restored into volume, count: /'
  else
    log "FILES_DIR / FILES_VOLUME not set — files archive NOT restored"
  fi
fi
log "DONE. If this is the live database: scripts/ops/migrate.sh upgrade, then scale the services back up."
