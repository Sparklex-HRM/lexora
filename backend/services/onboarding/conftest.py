"""Test set-up for the onboarding service: a throw-away SQLite file instead of
Postgres. Lives in conftest.py so it runs before shared.utils.config is imported.
Run:  cd backend/services/onboarding && PYTHONPATH=../../ python -m pytest -q
"""
import os
import sys
import tempfile

_dir = tempfile.mkdtemp(prefix="lexora-onboarding-test-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{os.path.join(_dir, 'onboarding.db').replace(os.sep, '/')}"
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")
os.environ.pop("SMTP_HOST", None)
os.environ.setdefault("LEXORA_DEV_SHOW_LINKS", "1")       # the tests read the joiner link back (local test machine)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):      # SQLite has no JSONB type
    return "JSON"
