#!/usr/bin/env bash
# Regenerate backend/constraints.txt (the full pinned dependency set) from a clean build.
# Run after changing backend/requirements*.txt, review the diff, rebuild ONE image, run CI.
#   scripts/ops/refresh-constraints.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/backend"
printf '# placeholder during refresh\n' > constraints.txt.new
cp constraints.txt constraints.txt.bak
mv constraints.txt.new constraints.txt                       # unconstrained resolve
trap 'mv constraints.txt.bak constraints.txt 2>/dev/null || true' ERR
docker build -f Dockerfile --target migrate -t lexora-migrate:constraints . >/dev/null
{
  printf '# Full pinned dependency set (pip freeze of the lexora-migrate image, %s).\n' "$(date -u +%F)"
  printf '# Used as `pip install -r requirements.txt -c constraints.txt`. Refresh: scripts/ops/refresh-constraints.sh\n'
  docker run --rm --entrypoint pip lexora-migrate:constraints freeze 2>/dev/null
} > constraints.txt
rm -f constraints.txt.bak
git -C "$ROOT" diff --stat -- backend/constraints.txt || true
