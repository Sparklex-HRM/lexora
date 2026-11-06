#!/usr/bin/env bash
# Lexora HRM — the one CI gate. Runs locally and in .github/workflows/ci.yml.
#
#   scripts/ci/check.sh                 # unit tests + frontend tsc/lint/build + config checks
#   SKIP_FRONTEND=1 scripts/ci/check.sh # backend only (frontend build is the slow/heavy part)
#   CHECK_DB_URL=postgresql+asyncpg://…  scripts/ci/check.sh   # + migrations drift check vs that DB
#   LEXORA_GATEWAY=http://localhost:8200 RUN_E2E=1 scripts/ci/check.sh
#                                       # + scripts/e2e_canada.py, e2e_rbac.py, e2e_search.py against a RUNNING stack
#                                       #   seeded with scripts/seed_demo_canada.py (MAPLE; e2e_canada writes test data —
#                                       #   never point it at production)
#
# Each service's tests run in their OWN python process from that service's folder: services
# import their modules by bare name (models, routers, database), so two cannot share one run.
# Exit code = number of failed steps (0 = green). A summary table is printed at the end.
set -uo pipefail
export MSYS_NO_PATHCONV=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# native path for tools that are not MSYS-aware (python / docker on Windows Git-Bash)
NROOT="$(cygpath -m "$ROOT" 2>/dev/null || echo "$ROOT")"
PY="${PYTHON:-python}"
declare -a NAMES=() RESULTS=()
FAILED=0

step() {                     # step <name> <command…>
  local name="$1"; shift
  local t0=$SECONDS
  printf '\n\033[1m── %s\033[0m\n' "$name"
  if "$@"; then RESULTS+=("PASS  $(( SECONDS - t0 ))s"); else RESULTS+=("FAIL  $(( SECONDS - t0 ))s"); FAILED=$((FAILED + 1)); fi
  NAMES+=("$name")
}

# ── backend unit tests ────────────────────────────────────────────────────────
pytest_in() {                # pytest_in <dir relative to backend> [pytest args]
  local d="$1"; shift
  ( cd "$ROOT/backend/$d" && PYTHONPATH="$NROOT/backend" SMTP_HOST= SMTP_USER= SMTP_PASSWORD= ANTHROPIC_API_KEY= \
      LEXORA_ENV=test "$PY" -m pytest -q -p no:cacheprovider "$@" )
}
step "pytest backend/shared" pytest_in . shared
step "pytest api-gateway" pytest_in api-gateway
# Permission matrix (WASA hardening 2026-10-21): routes.json vs code, policy.yaml coverage, guard findings vs baseline.
step "pytest security/authz (permission matrix)" bash -c "cd '$ROOT' && '$PY' -m pytest -q -p no:cacheprovider security/authz"
for svc in "$ROOT"/backend/services/*/; do
  s="$(basename "$svc")"
  if [ -n "$(find "$svc" -name 'test_*.py' -not -path '*/__pycache__/*' -print -quit)" ]; then
    step "pytest services/$s" pytest_in "services/$s"
  fi
done

# Agentic operations (2026-10-25): property checks — no execution without an approval event, shadow never
# executes, hash chain intact, hard-deny never passes. SQLite scenario by default; CHECK_DB_URL = the real DB.
step "agentic properties" bash -c "cd '$ROOT' && SMTP_HOST= SMTP_USER= SMTP_PASSWORD= ANTHROPIC_API_KEY= LEXORA_ENV=test '$PY' scripts/ci/agentic_properties.py"

# ── migrations: tooling imports; optional drift check against a database ─────
step "migrations cli loads" bash -c "cd '$ROOT/backend' && PYTHONPATH='$NROOT/backend' '$PY' -c 'import migrations.cli, migrations.worker'"
if [ -n "${CHECK_DB_URL:-}" ]; then
  step "migrations check (models vs DB)" env DATABASE_URL="$CHECK_DB_URL" MIGRATE_MODE=local bash "$ROOT/scripts/ops/migrate.sh" check
fi

# ── config: compose files render ──────────────────────────────────────────────
if command -v docker >/dev/null 2>&1; then
  step "compose (dev) config" docker compose --project-directory "$NROOT" -f "$NROOT/docker-compose.yml" config -q
  step "compose (prod) config" env LEXORA_ENV=production POSTGRES_PASSWORD=ci-placeholder-0000 REDIS_PASSWORD=ci-placeholder-0000 \
    JWT_SECRET=x LEXORA_INTERNAL_TOKEN=y LEXORA_DATA_KEY=z LEXORA_UI_GRANT_PRIVATE_KEY=ci-placeholder CORS_ORIGINS=https://ci.invalid APP_BASE_URL=https://ci.invalid LEXORA_PUBLIC_HOST=ci.invalid \
    docker compose --project-directory "$NROOT" -f "$NROOT/docker-compose.prod.yml" config -q
fi
if command -v helm >/dev/null 2>&1; then
  step "helm lint" helm lint "$NROOT/deploy/helm/lexora" --set secrets.existingSecret=ci
fi
step "shell scripts parse" bash -c "for f in '$ROOT'/scripts/ops/*.sh '$ROOT'/scripts/ci/*.sh; do bash -n \"\$f\" || exit 1; done"

# ── frontend ──────────────────────────────────────────────────────────────────
if [ -z "${SKIP_FRONTEND:-}" ] || [ "${SKIP_FRONTEND}" = "0" ]; then   # SKIP_FRONTEND=0 means "run it"
  FE="$ROOT/frontend"
  if [ ! -d "$FE/node_modules" ] || [ -n "${CI:-}" ]; then
    step "npm ci" bash -c "cd '$FE' && npm ci --no-audit --no-fund"
  fi
  # 2026-10-25 time-zone rule: lib/datetime.ts checks (TZ=America/Toronto edge cases) + 0 toISOString() date slices in src/
  step "frontend datetime helpers" bash -c "cd '$FE' && node scripts/check_datetime.mjs"
  # 2026-10-26 go-live: build-time pinned UI-grant key set (current + previous kid, never dev) + kid rotation in WebCrypto
  step "frontend ui-grant key set" bash -c "cd '$FE' && node scripts/check_ui_grant_keys.mjs"
  step "frontend tsc --noEmit" bash -c "cd '$FE' && npx tsc --noEmit -p tsconfig.json"
  if grep -q '"lint"' "$FE/package.json"; then
    step "frontend lint" bash -c "cd '$FE' && npm run -s lint"
  else
    echo "(frontend has no lint script — skipped; add eslint + \"lint\" to package.json to enable)"
  fi
  step "frontend vite build" bash -c "cd '$FE' && npx vite build --mode production --outDir '${FE_OUT:-dist}' --emptyOutDir"
fi

# ── e2e against a running stack (optional) ────────────────────────────────────
if [ -n "${RUN_E2E:-}" ]; then
  export LEXORA_GATEWAY="${LEXORA_GATEWAY:-http://localhost:8200}"
  step "e2e_canada.py" "$PY" -X utf8 "$NROOT/scripts/e2e_canada.py"
  step "e2e_rbac.py" "$PY" -X utf8 "$NROOT/scripts/e2e_rbac.py"
  step "e2e_search.py" "$PY" -X utf8 "$NROOT/scripts/e2e_search.py"
fi

# ── summary ───────────────────────────────────────────────────────────────────
printf '\n\033[1m══ summary\033[0m\n'
for i in "${!NAMES[@]}"; do printf '  %-40s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}"; done
printf '\n%s step(s) failed\n' "$FAILED"
exit "$FAILED"
