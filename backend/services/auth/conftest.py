"""Test set-up for the auth service: a throw-away SQLite file instead of Postgres.

Must run before anything imports shared.utils.config (it reads DATABASE_URL at
import time), which is why it lives in conftest.py.
Run:  cd backend/services/auth && PYTHONPATH=../../ python -m pytest -q
"""
import os
import sys
import tempfile

_dir = tempfile.mkdtemp(prefix="lexora-auth-test-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{os.path.join(_dir, 'auth.db').replace(os.sep, '/')}"
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")
os.environ.pop("SMTP_HOST", None)            # no mail in tests → links are returned
os.environ["LEXORA_DEV_SHOW_LINKS"] = "1"
# S4: mandatory admin MFA is ON by default in the service. The older suites sign
# admins in with a password only, so they run with it off; test_saas_s4.py turns
# it on per test (monkeypatch) to prove the enforcement.
os.environ["LEXORA_MFA_ENFORCE"] = "0"
# WASA 2026-10-21: our proof-of-work CAPTCHA is ON by default in the service. The
# older suites post logins / sign-ups without a captcha_token, so they run with it
# off; test_pow_captcha_*.py switch it on per test (monkeypatch) to prove it.
os.environ["CAPTCHA_MODE"] = "off"
# Decision 2026-10-21: public sign-up is OFF by default (LEXORA_PUBLIC_SIGNUP). The sign-up
# suites exercise the flow, so the tests run with it on; test_wasa_followup proves the default.
os.environ["LEXORA_PUBLIC_SIGNUP"] = "1"
os.environ.pop("CAPTCHA_HMAC_SECRET", None)
for _k in ("LEXORA_SELLER_GST_HST_NUMBER", "LEXORA_SELLER_PROVINCE", "LEXORA_SALES_TAX_RATES",
           "LEXORA_SELLER_LEGAL_NAME", "LEXORA_SELLER_ADDRESS", "LEXORA_CAPTCHA_PROVIDER"):
    os.environ.pop(_k, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):      # SQLite has no JSONB type
    return "JSON"


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_anti_automation():
    """WASA 2026-10-21: per-IP forgot-password limit + proof-of-work counters are
    process-wide; every test starts from zero (the TestClient IP is shared)."""
    import anti_automation
    anti_automation.reset_for_tests()
    yield
