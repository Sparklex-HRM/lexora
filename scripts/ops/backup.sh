#!/usr/bin/env bash
# Lexora HRM — backup: Postgres (pg_dump -Fc) + uploaded files (tar.gz) + sha256 manifest.
#
#   scripts/ops/backup.sh                     # uses the env below
#
# Database (pick one):
#   PG_DOCKER_CONTAINER=lexora-postgres-1      run pg_dump inside that container (local / compose)
#   (unset)                                   run pg_dump on this machine with PGHOST/PGPORT/PGUSER/PGPASSWORD
#   PGDATABASE (default lexora) · PGUSER (default postgres) · PGPASSWORD (never printed)
#
# Files (pick one, optional):
#   FILES_DIR=/srv/lexora/files               a directory on this machine
#   FILES_VOLUME=lexora_lexora_files           a docker volume (read through a throw-away alpine container)
#
# Output / retention:
#   BACKUP_DIR (default ./backups) · RETENTION_DAYS (default 14; 0 = keep everything)
#   BACKUP_UPLOAD_CMD  optional hook, run as: $BACKUP_UPLOAD_CMD <file>...   e.g.
#                      'rclone copy --immutable -q' remote:lexora-backups  (put the remote last via a wrapper)
#                      or 'aws s3 cp --sse AES256' … — a non-zero exit fails the backup.
#
# Exit code is non-zero if ANY step fails (dump, verification, files, upload) — wire it to alerting.
# Schedule: cron / systemd timer / the Helm chart's backup CronJob (deploy/helm/lexora, backup.enabled).
set -euo pipefail
export MSYS_NO_PATHCONV=1

TS="$(date -u +%Y%m%dT%H%M%SZ)"
DB="${PGDATABASE:-lexora}"
PGUSER="${PGUSER:-postgres}"
OUT="${BACKUP_DIR:-./backups}"
KEEP="${RETENTION_DAYS:-14}"
mkdir -p "$OUT"
DUMP="$OUT/lexora_db_${TS}.dump"
trap 'rm -f "$DUMP.part"' EXIT          # no half-written dump left behind on failure
FILES_TAR="$OUT/lexora_files_${TS}.tar.gz"
log() { printf '[backup %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# ── 1. database ───────────────────────────────────────────────────────────────
log "pg_dump -Fc database=$DB → $DUMP"
if [ -n "${PG_DOCKER_CONTAINER:-}" ]; then
  docker exec ${PGPASSWORD:+-e PGPASSWORD} "$PG_DOCKER_CONTAINER" \
    pg_dump -U "$PGUSER" -d "$DB" -Fc --no-owner --no-privileges > "$DUMP.part"
  LIST() { docker exec -i "$PG_DOCKER_CONTAINER" pg_restore --list; }
else
  pg_dump -U "$PGUSER" -d "$DB" -Fc --no-owner --no-privileges -f "$DUMP.part"
  LIST() { pg_restore --list; }
fi
mv "$DUMP.part" "$DUMP"

# verify: the archive's table of contents must be readable and contain table data
TOC_TABLES="$(LIST < "$DUMP" | grep -c ' TABLE DATA ' || true)"
[ "${TOC_TABLES:-0}" -gt 0 ] || { log "ERROR: dump has no TABLE DATA entries"; exit 2; }
log "dump ok: $(du -h "$DUMP" | cut -f1), $TOC_TABLES tables with data"

# ── 2. uploaded files ─────────────────────────────────────────────────────────
ARTS=("$DUMP")
if [ -n "${FILES_DIR:-}" ]; then
  log "files: tar $FILES_DIR → $FILES_TAR"
  tar -czf "$FILES_TAR" -C "$FILES_DIR" .
  ARTS+=("$FILES_TAR")
elif [ -n "${FILES_VOLUME:-}" ]; then
  log "files: tar volume $FILES_VOLUME → $FILES_TAR"
  docker run --rm -v "$FILES_VOLUME:/data:ro" alpine:3.20 tar -czf - -C /data . > "$FILES_TAR"
  ARTS+=("$FILES_TAR")
else
  log "files: FILES_DIR / FILES_VOLUME not set — database only"
fi
if [ -f "$FILES_TAR" ]; then
  log "files ok: $(du -h "$FILES_TAR" | cut -f1), $(tar -tzf "$FILES_TAR" | grep -vc '/$' || true) file(s)"
fi

# ── 3. manifest ───────────────────────────────────────────────────────────────
MAN="$OUT/lexora_backup_${TS}.sha256"
( cd "$OUT" && for a in "${ARTS[@]}"; do sha256sum "$(basename "$a")"; done ) > "$MAN"
ARTS+=("$MAN")
log "manifest $MAN"

# ── 4. off-site upload hook ───────────────────────────────────────────────────
if [ -n "${BACKUP_UPLOAD_CMD:-}" ]; then
  log "upload: running BACKUP_UPLOAD_CMD on ${#ARTS[@]} file(s)"
  # shellcheck disable=SC2086
  $BACKUP_UPLOAD_CMD "${ARTS[@]}"
fi

# ── 5. retention (local copies only; the remote keeps its own lifecycle rules) ─
if [ "$KEEP" -gt 0 ]; then
  N="$(find "$OUT" -maxdepth 1 -type f -name 'lexora_*' -mtime +"$KEEP" | wc -l)"
  find "$OUT" -maxdepth 1 -type f -name 'lexora_*' -mtime +"$KEEP" -delete
  log "retention: removed $N file(s) older than $KEEP day(s)"
fi
log "DONE $TS"
