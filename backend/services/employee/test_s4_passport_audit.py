"""S4: passport is stored encrypted + masked; an employee update writes ONE audit
row with a PII-masked before/after diff; the plan employee limit fails open
without a catalogue.
Run:  cd backend/services/employee && PYTHONPATH=../../ python -m pytest -q test_s4_passport_audit.py
"""
import types

import pytest

import sensitive
from shared import audit_trail
from shared.utils import crypto


def test_passport_encrypted_and_masked():
    e = types.SimpleNamespace(passport_enc=None, passport_masked=None, enc_key_id=None)
    sensitive.set_field(e, "passport", sensitive.clean_passport(" z-1234567 "))
    assert crypto.is_encrypted(e.passport_enc) and "Z1234567" not in e.passport_enc
    assert sensitive.get_field(e, "passport") == "Z1234567"
    assert sensitive.masked(e, "passport") == "XXXX4567"
    with pytest.raises(ValueError):
        sensitive.clean_passport("12")


def test_employee_snapshot_diff_masks_identifiers():
    before = {"job_title": "Engineer", "sin_enc": "enc:v1:k1:A", "sin_masked": "XXX-XXX-286",
              "bank_account_last4": "1111", "annual_salary": 90000}
    after = {"job_title": "Senior Engineer", "sin_enc": "enc:v1:k1:B", "sin_masked": "XXX-XXX-999",
             "bank_account_last4": "2222", "annual_salary": 100000}
    d = audit_trail._collapse_secret_columns(audit_trail.masked_diff(before, after))
    assert d["job_title"] == {"from": "Engineer", "to": "Senior Engineer"}
    assert d["sin"]["to"] == "[changed]" and "999" not in repr(d) and "2222" not in repr(d)


def test_seat_limit_fails_open_without_catalogue():
    import asyncio

    import entitlements

    class _DB:
        async def execute(self, *a, **k):
            raise RuntimeError("no such table")

        async def rollback(self):
            pass
    assert asyncio.run(entitlements.limit_for(_DB(), "x")) == (None, None)
    asyncio.run(entitlements.require_seat(_DB(), "x"))          # no exception = allowed
