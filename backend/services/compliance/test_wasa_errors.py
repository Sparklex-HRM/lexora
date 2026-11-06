"""WASA2 #6 — the retention preview never puts database / driver error text into the answer.
Run:  cd backend/services/compliance && PYTHONPATH=../../ python -m pytest test_wasa_errors.py -q
"""
import asyncio
import uuid
from datetime import date

import privacy

SECRET = 'relation "employees" does not exist; password authentication failed for user lexora@10.0.0.5'


class _Scalars:
    def all(self):
        return []


class _Res:
    def scalars(self):
        return _Scalars()


class FailingDB:
    def __init__(self):
        self.calls = 0

    async def execute(self, *_a, **_k):
        self.calls += 1
        if self.calls == 1:                          # the retention-policy read
            return _Res()
        raise RuntimeError(SECRET)


def test_purge_preview_masks_exception_text(monkeypatch, caplog):
    async def today(_org):
        return date(2026, 10, 21)
    monkeypatch.setattr(privacy, "org_today", today)
    out = asyncio.new_event_loop().run_until_complete(privacy.compute_purge_candidates(FailingDB(), uuid.uuid4()))
    assert out["record_types"], out
    for name, v in out["record_types"].items():
        assert "error" in v and v.get("ref"), name
        assert "relation" not in v["error"] and "password" not in v["error"] and "10.0.0.5" not in str(v)
    assert SECRET in caplog.text                    # the real error is in the log, next to the ref
