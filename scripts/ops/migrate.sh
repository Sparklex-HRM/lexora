#!/usr/bin/env bash
# Lexora HRM — schema migrations (Alembic chain in backend/migrations, version table
# lexora_schema_version). Wraps `python -m migrations.cli`.
#
#   scripts/ops/migrate.sh upgrade          # baseline (idempotent) + every newer revision
#   scripts/ops/migrate.sh check            # models vs DB per service; exit 1 on drift
#   scripts/ops/migrate.sh current|history
#   scripts/ops/migrate.sh downgrade <rev>
#   scripts/ops/migrate.sh revision -m "add foo to bar"   (local mode only — writes a file)
#
# Where it runs (MIGRATE_MODE):
#   docker (default) — the lexora-migrate image, on the compose network. Needs DATABASE_URL
#                      reachable from that network (e.g. …@postgres:5432/lexora).
#   local            — this machine's python (pip install -r backend/requirements.txt
#                      -r backend/requirements-migrate.txt).
#   (Kubernetes: the Helm chart's pre-upgrade Job runs the same image with `upgrade`.)
#
# Env: DATABASE_URL (required) · MIGRATE_IMAGE (default lexora-migrate:dev)
#      MIGRATE_NETWORK (default lexora_default) · LEXORA_ENV (passed through: production
#      makes the services' secret gate apply to the migration run too).
set -euo pipefail
export MSYS_NO_PATHCONV=1                      # Git-Bash on Windows: don't mangle /app paths

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${MIGRATE_MODE:-docker}"
: "${DATABASE_URL:?set DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:5432/DB}"
[ $# -ge 1 ] || set -- upgrade

echo "migrate: $* on $(printf '%s' "$DATABASE_URL" | sed -E 's#//[^@]*@#//***@#')" >&2

if [ "$MODE" = "local" ]; then
  cd "$ROOT/backend"
  NB="$(cygpath -m "$ROOT/backend" 2>/dev/null || echo "$ROOT/backend")"
  PYTHONPATH="$NB" exec python -m migrations.cli "$@"
fi

IMAGE="${MIGRATE_IMAGE:-lexora-migrate:dev}"
NET="${MIGRATE_NETWORK:-lexora_default}"
ENV_ARGS=(-e DATABASE_URL)
for v in LEXORA_ENV JWT_SECRET LEXORA_INTERNAL_TOKEN LEXORA_DATA_KEY REDIS_URL CORS_ORIGINS APP_BASE_URL; do
  [ -n "${!v:-}" ] && ENV_ARGS+=(-e "$v")
done
exec docker run --rm --network "$NET" "${ENV_ARGS[@]}" "$IMAGE" "$@"
