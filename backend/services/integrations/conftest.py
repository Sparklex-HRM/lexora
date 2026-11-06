"""Test set-up for the integrations service: throw-away SQLite, no network.

Run:  cd backend/services/integrations && PYTHONPATH=../../ python -m pytest -q
"""
import os
import sys
import tempfile

_dir = tempfile.mkdtemp(prefix="lexora-integrations-test-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{os.path.join(_dir, 'int.db').replace(os.sep, '/')}"
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")
os.environ["APP_BASE_URL"] = "https://hr.example.ca"
os.environ["LEXORA_PUBLIC_API_URL"] = "https://hr.example.ca/api"
for _k in [k for k in os.environ if k.startswith("LEXORA_WHATSAPP_")]:
    os.environ.pop(_k, None)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.abspath(os.path.join(HERE, "..", "..")))

from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):      # SQLite has no JSONB type
    return "JSON"
