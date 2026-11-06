"""Module paywall: a plan without the "compliance" module cannot reach /compliance; with it, it can.
Also: /ready is public (k8s / load-balancer probe)."""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///unused-gateway-test.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402


def _req(path: str, modules=("core", "payroll"), role="org_admin"):
    return SimpleNamespace(url=SimpleNamespace(path=path), state=SimpleNamespace(modules=list(modules), role=role))


def test_compliance_is_gated_by_its_module():
    r = main._module_denied("compliance", _req("/compliance/audit"))
    assert r is not None and r.status_code == 403
    assert main._module_denied("compliance", _req("/compliance/audit", modules=("core", "compliance"))) is None


def test_ready_is_public():
    from shared.middleware import tenant
    assert tenant._is_public("/ready")
    assert not tenant._is_public("/employees")


def test_mcp_is_public_but_rest_of_ai_is_not():
    """D3: /ai/mcp authenticates by the lxai_ token inside the service; the rest of /ai needs a JWT."""
    from shared.middleware import tenant
    assert tenant._is_public("/ai/mcp")
    assert tenant._is_public("/ai/mcp/tools")
    assert not tenant._is_public("/ai/tokens")
    assert not tenant._is_public("/ai/assistant/chat")
