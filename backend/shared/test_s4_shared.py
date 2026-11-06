"""S4 shared helpers: decrypt_field / employee_identifier (for payroll exports) and
the PII-masked before/after audit diff.
Run:  cd backend && python -m pytest -q shared/test_s4_shared.py
"""
import os

os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///unused-s4-shared.db")

import pytest  # noqa: E402

from shared import audit_trail, crypto_fields  # noqa: E402
from shared.utils import crypto  # noqa: E402


def test_decrypt_field_roundtrip_and_legacy():
    tok = crypto_fields.encrypt_field("046454286")
    assert tok.startswith("enc:v1:") and crypto_fields.decrypt_field(tok) == "046454286"
    assert crypto_fields.decrypt_field("LEGACYPLAIN") == "LEGACYPLAIN"
    assert crypto_fields.decrypt_field(None) is None and crypto_fields.decrypt_field("") is None
    with pytest.raises(crypto_fields.FieldDecryptError):
        crypto_fields.decrypt_field("enc:v1:nokey:AAAA")


def test_employee_identifier_prefers_token_then_legacy():
    row = {"sin_enc": crypto.encrypt("046454286"), "sin_encrypted": None,
           "bank_account_enc": None, "bank_account_encrypted": "1234567", "passport_enc": crypto.encrypt("gk123456")}
    assert crypto_fields.employee_identifier(row, "sin") == "046454286"
    assert crypto_fields.employee_identifier(row, "bank_account") == "1234567"       # legacy plaintext, not yet backfilled
    assert crypto_fields.employee_identifier(row, "passport") == "GK123456"
    assert "passport_enc" in crypto_fields.IDENTIFIER_SELECT and "sin_enc" in crypto_fields.IDENTIFIER_SELECT.split(", ")
    assert crypto_fields.mask_field("046454286", "sin") == "•••-•••-286"


def test_masked_diff_never_leaks_pii():
    before = {"first_name": "Chloe", "email": "chloe.gagnon@maple.example", "phone": "+1 416 555 0123",
              "sin_enc": "enc:v1:k1:aaa", "sin_hash": "h1", "annual_salary": 72000.0, "date_of_birth": "1992-05-17",
              "address_line1": "12 King St W", "updated_at": "x"}
    after = {**before, "email": "chloe@maple.example", "phone": "+1 416 555 9999", "sin_enc": "enc:v1:k1:bbb",
             "sin_hash": "h2", "annual_salary": 78000.0, "date_of_birth": "1992-05-18",
             "address_line1": "14 King St W", "updated_at": "y"}
    d = audit_trail._collapse_secret_columns(audit_trail.masked_diff(before, after))
    flat = repr(d)
    for secret in ("chloe.gagnon", "555 0123", "555 9999", "12 King", "14 King", "05-17", "05-18", "aaa", "bbb"):
        assert secret not in flat
    assert d["sin"] == {"from": "[set]", "to": "[changed]"}
    assert d["annual_salary"] == {"from": 72000.0, "to": 78000.0}             # pay changes stay visible
    assert d["email"]["to"] == "c***@maple.example" and d["phone"]["to"] == "••••9999"
    assert "updated_at" not in d and "first_name" not in d


def test_audit_change_skips_noop_and_tags_op(monkeypatch):
    seen = []

    async def fake(request, action, **kw):
        seen.append((action, kw))
        return True
    monkeypatch.setattr(audit_trail, "record_audit", fake)
    import asyncio
    assert asyncio.run(audit_trail.audit_change(None, "x.update", entity_type="e", entity_id=1,
                                                before={"a": 1}, after={"a": 1})) is False
    asyncio.run(audit_trail.audit_change(None, "x.create", entity_type="e", entity_id=1, before=None,
                                         after={"a": 1, "sin": "046454286"}))
    meta = seen[-1][1]["meta"]
    assert meta["op"] == "create" and meta["changes"]["sin"]["to"] == "[set]"
