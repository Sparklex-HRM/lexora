"""Integration-run additions: a letter for a CANDIDATE (no employee row yet),
re-linked to the employee after joining; exit-case scope; internal identity."""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from test_lifecycle_b import LR, ORG, Req, _employee, world  # noqa: F401  (world is a fixture)
from letters_models import IssuedLetter


def _recipient(ref):
    return LR.RecipientIn(kind="candidate", ref=ref, first_name="Kavya", last_name="Menon", email="k@x.example",
                          country="CA", designation="Staff Nurse", location="Toronto", joining_date="2026-11-02",
                          annual_ctc=78000)


def test_offer_letter_for_a_candidate_is_numbered_immutable_and_relinked(world):  # noqa: F811
    ref = str(uuid.uuid4())

    async def go():
        async with world() as db:
            hr = Req()
            data = {"offer": {"position": "Staff Nurse", "joining_date": "2026-11-02", "valid_until": "2026-10-20",
                              "annual_ctc": 78000.0, "location": "Toronto"}}
            first = await LR.create_recipient_letter(
                LR.RecipientLetterIn(template_key="offer_letter", data=data, recipient=_recipient(ref)), hr, db)
            # only pre-joining templates are allowed for a non-employee
            with pytest.raises(HTTPException) as not_allowed:
                await LR.create_recipient_letter(
                    LR.RecipientLetterIn(template_key="confirmation", data={}, recipient=_recipient(ref)), hr, db)
            # unknown fields are refused exactly as for an employee
            with pytest.raises(HTTPException) as unknown:
                await LR.create_recipient_letter(
                    LR.RecipientLetterIn(template_key="offer_letter", data={"employee.password": "x"},
                                         recipient=_recipient(ref)), hr, db)
            # an employee cannot read HR's candidate letter
            with pytest.raises(HTTPException) as foreign:
                await LR.create_recipient_letter(
                    LR.RecipientLetterIn(template_key="offer_letter", data=data, recipient=_recipient(ref)),
                    Req(role="employee"), db)
            row = (await db.execute(select(IssuedLetter).where(IssuedLetter.number == first["number"]))).scalars().one()
            sha, pdf = row.sha256, bytes(row.pdf)
            e = _employee(first_name="Kavya", last_name="Menon")
            db.add(e)
            await db.commit()
            n = await LR.relink_recipient_letters(db, str(ORG), ref, str(e.id))
            again = await LR.relink_recipient_letters(db, str(ORG), ref, str(e.id))
            await db.refresh(row)
            mine = await LR.employee_letters(str(e.id), Req(role="employee", email="asha@acme.ca", uid="u-e",
                                                            employee_id=str(e.id)), db)
            return first, not_allowed.value, unknown.value, foreign.value, row, sha, pdf, n, again, mine, e
    first, not_allowed, unknown, foreign, row, sha, pdf, n, again, mine, e = asyncio.run(go())
    assert first["status"] == "issued" and first["number"].endswith("/0001") and first["employee_id"] is None
    assert not_allowed.status_code == 422 and unknown.status_code == 422 and foreign.status_code == 403
    assert "Kavya Menon" in row.text_copy and "Staff Nurse" in row.text_copy
    assert (n, again) == (1, 0)                                  # relinked once, idempotent
    assert str(row.employee_id) == str(e.id) and row.sha256 == sha and bytes(row.pdf) == pdf
    assert [m["number"] for m in mine] == [first["number"]]       # now visible to the employee


def test_exit_case_list_refuses_an_unknown_scope():
    """An unknown ?scope= must not fall through to 'every case in the org'."""
    import inspect
    import exit_routers
    src = inspect.getsource(exit_routers.list_cases)
    assert 'scope not in ("org", "team", "mine")' in src


def test_internal_identity_needs_the_shared_token():
    from shared.utils.internal import internal_headers, is_internal
    h = internal_headers(str(ORG), "test")
    assert is_internal(h)
    assert not is_internal({"x-org-id": str(ORG)})
    assert not is_internal({**h, "x-internal-auth": "forged"})
