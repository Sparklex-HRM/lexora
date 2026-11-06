"""Letter templates (D1): merge fields incl. custom fields, live text preview, draft → publish,
version history and rollback.

Run:  cd backend/services/employee && PYTHONPATH=../../ python -m pytest -q test_letters_versions.py
"""
import asyncio
import uuid
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import letters_engine as eng
import letters_routers as LR
from custom_fields_models import CustomFieldDef
from letters_models import IssuedLetter, LetterCounter, LetterSettings, LetterTemplate, LetterTemplateVersion
from comp_models import CompHistory
from models import Employee
from test_lifecycle_b import ORG, Req, _employee, _no_audit


@pytest.fixture
def world(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [m.__table__ for m in (Employee, CompHistory, LetterTemplate, LetterTemplateVersion, LetterSettings, LetterCounter,
                                    IssuedLetter, CustomFieldDef)]

    async def make():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Employee.metadata.create_all(c, tables=tables))
            from career_common import EXTRA_COLUMNS
            for col, ddl in EXTRA_COLUMNS:
                if col not in Employee.__table__.columns:
                    await conn.execute(text(f"ALTER TABLE employees ADD COLUMN {col} {ddl}"))
            await conn.execute(text("ALTER TABLE employees ADD COLUMN custom_data JSON"))
            await conn.execute(text("CREATE TABLE organizations (id CHAR(32) PRIMARY KEY, name VARCHAR, legal_name VARCHAR)"))
            await conn.execute(text("INSERT INTO organizations VALUES (:i, 'Acme Canada', 'Acme Canada Inc.')"), {"i": ORG.hex})
    asyncio.run(make())
    monkeypatch.setattr(LR, "record_audit", _no_audit)
    LR._seeded.clear()
    return async_sessionmaker(engine, expire_on_commit=False)


# ── engine: the custom namespace ──────────────────────────────────────────────

def test_custom_namespace_is_a_merge_field():
    assert eng.is_allowed("custom.blood_group") and eng.is_allowed("extra.anything") and not eng.is_allowed("secret.sin")
    assert eng.validate_template("Dear {{employee.full_name}}, blood group {{custom.blood_group}}.") == []
    cat = eng.catalogue([{"key": "blood_group", "label": "Blood group"}])
    assert {"field": "custom.blood_group", "label": "Blood group", "group": "custom", "block": False} in cat
    vals, refused = eng.flatten_data({"custom": {"blood_group": "O+"}, "custom.visa": "H1"})
    assert vals == {"custom.blood_group": "O+", "custom.visa": "H1"} and refused == []
    blocks, missing = eng.build_blocks("Group: **{{custom.blood_group}}**\n\n{{custom.nothing}}", {"custom.blood_group": "O+"})
    assert eng.plain_text(blocks) == "Group: O+\n[custom.nothing]" and missing == ["custom.nothing"]
    js = eng.blocks_json(blocks)
    assert js[0] == {"t": "p", "mark": None, "runs": [{"text": "Group: ", "bold": False}, {"text": "O+", "bold": True}]}


# ── preview-text fills employee + custom values; fields lists the org's custom fields ──

def test_live_preview_fills_custom_fields_and_reports_missing(world):
    async def go():
        async with world() as db:
            e = _employee()
            db.add(e)
            db.add(CustomFieldDef(org_id=ORG, entity="employee", key="blood_group", label="Blood group", type="select",
                                  options=["O+", "A+"], sort_order=1))
            db.add(CustomFieldDef(org_id=ORG, entity="employee", key="locker", label="Locker no.", type="text", sort_order=2))
            await db.commit()
            await db.execute(text("UPDATE employees SET custom_data = :d WHERE id = :id"),
                             {"d": '{"blood_group": "O+", "skills": ["py", "sql"]}', "id": e.id.hex})
            await db.commit()
            f = await LR.fields(Req(), db)
            body = ("# Medical card\n\nDear {{employee.full_name}}, your blood group on record is **{{custom.blood_group}}**, "
                    "skills {{custom.skills}}, locker {{custom.locker}}. Salary {{salary.annual_ctc}}.")
            p = await LR.preview_text(LR.PreviewTextIn(body=body), Req(), db)          # no employee_id → the sample employee
            bad = await LR.preview_text(LR.PreviewTextIn(body="<b>html</b> {{nope.x}}"), Req(), db)
            return f, p, bad
    f, p, bad = asyncio.run(go())
    customs = [x for x in f["fields"] if x["group"] == "custom"]
    assert [c["field"] for c in customs] == ["custom.blood_group", "custom.locker"]
    assert p["ok"] and p["employee"] == "Asha Rao" and p["name"] == "Preview"
    assert "your blood group on record is O+, skills py, sql, locker [custom.locker]. Salary $80,000.00." in p["text"]
    assert [m["field"] for m in p["missing"]] == ["custom.locker"] and p["letterhead"]["company_name"] == "Acme Canada"
    assert p["blocks"][0]["t"] == "h1" and p["blocks"][1]["runs"][1] == {"text": "O+", "bold": True}
    assert not bad["ok"] and len(bad["errors"]) == 2


# ── draft → publish → history → rollback ─────────────────────────────────────

def test_draft_publish_history_rollback(world):
    async def go():
        async with world() as db:
            tpls = await LR.list_templates(Req(), db)
            conf = next(t for t in tpls if t["key"] == "confirmation")
            tid = conf["id"]
            v1_body = conf["body"]
            # a draft does not change the live template
            d = await LR.save_draft(tid, LR.DraftIn(body=v1_body + "\n\nDraft line {{employee.employee_number}}."), Req(), db)
            live = next(t for t in await LR.list_templates(Req(), db) if t["id"] == tid)
            assert live["version"] == 1 and live["body"] == v1_body and live["has_draft"] is True and d["published_version"] == 1
            # a change note given with the draft is kept on it (a later save without a note keeps it)
            d = await LR.save_draft(tid, LR.DraftIn(body=v1_body + "\n\nDraft line {{employee.employee_number}}.", note="  Added the employee number  "), Req(), db)
            assert d["draft"]["note"] == "Added the employee number"
            d = await LR.save_draft(tid, LR.DraftIn(body=v1_body + "\n\nDraft line {{employee.employee_number}}."), Req(), db)
            assert d["draft"]["note"] == "Added the employee number"
            # publish → version 2, history has v1 and v2, draft gone; v2 carries the draft's note
            pub = await LR.publish_draft(tid, Req(), db)
            assert pub["version"] == 2 and "Draft line" in pub["body"] and pub["is_default"] is False
            hist = await LR.list_versions(tid, Req(), db)
            assert [(v["version"], v["state"], v["current"]) for v in hist["versions"]] == [(2, "published", True), (1, "published", False)]
            assert hist["versions"][0]["note"] == "Added the employee number"
            with pytest.raises(HTTPException) as nodraft:
                await LR.publish_draft(tid, Req(), db)
            # a direct save (the old PUT) is still a publish → version 3
            saved = await LR.update_template(tid, LR.TemplateIn(name="Confirmation letter", body=v1_body + "\n\nThird."), Req(), db)
            assert saved["version"] == 3
            # rollback to version 1 → NEW version 4 with v1's body; history keeps every row
            rb = await LR.rollback(tid, 1, Req(), db)
            assert rb["version"] == 4 and rb["body"] == v1_body
            hist = await LR.list_versions(tid, Req(), db)
            assert [v["version"] for v in hist["versions"]] == [4, 3, 2, 1] and hist["versions"][0]["note"] == "Rolled back to version 1"
            with pytest.raises(HTTPException) as same:
                await LR.rollback(tid, 4, Req(), db)
            with pytest.raises(HTTPException) as gone:
                await LR.rollback(tid, 99, Req(), db)
            with pytest.raises(HTTPException) as not_setup:
                await LR.rollback(tid, 2, Req(role="hr_manager"), db)
            # a letter issued now uses version 4's text and keeps its own snapshot
            e = _employee()
            db.add(e)
            await db.commit()
            out = await LR.create_letter(str(e.id), LR.LetterIn(template_key="confirmation", data={}, effective_date="2026-11-01"), Req(), db)
            rows = (await db.execute(text("SELECT template_version FROM issued_letters"))).all()
            return nodraft.value, same.value, gone.value, not_setup.value, out, rows
    nodraft, same, gone, not_setup, out, rows = asyncio.run(go())
    assert nodraft.status_code == 409 and same.status_code == 409 and gone.status_code == 404 and not_setup.status_code == 403
    assert out["status"] == "issued" and rows == [(4,)]


def test_entity_letterhead_and_entity_transfer_letter(world, monkeypatch):
    """D4: the employee's legal entity becomes the letterhead; the entity_transfer template renders."""
    from shared import legal_entities as LE
    ENT = {"id": uuid.uuid4(), "name": "Acme Textiles Pvt Ltd", "short_code": "ATX", "address_line1": "4 Mill Road", "city": "Coimbatore",
           "state_code": "TN", "pincode": "641001", "signatory": {"name": "R. Iyer", "designation": "Director"},
           "letterhead": {"footer_text": "CIN U12345TN2020PTC000001"}}

    async def by_id(org, eid):
        return ENT if str(eid) == str(ENT["id"]) else None
    monkeypatch.setattr(LE, "entity_by_id", by_id)

    async def go():
        async with world() as db:
            e = _employee(entity_id=ENT["id"])
            db.add(e)
            await db.commit()
            head = await LR.letterhead(db, str(ORG), str(ENT["id"]))
            plain = await LR.letterhead(db, str(ORG))
            p = await LR.preview_text(LR.PreviewTextIn(employee_id=str(e.id), template_key="entity_transfer",
                                                       data={"career": {"old_entity": "Acme Canada Inc.", "new_entity": "Acme Textiles Pvt Ltd",
                                                                        "effective_date": "2026-11-01"}}), Req(), db)
            return head, plain, p
    head, plain, p = asyncio.run(go())
    assert head["company_name"] == "Acme Textiles Pvt Ltd" and head["entity_code"] == "ATX" and head["address"] == "4 Mill Road\nCoimbatore TN 641001"
    assert head["signatory_name"] == "R. Iyer" and head["footer_text"].startswith("CIN ")
    assert plain["company_name"] == "Acme Canada" and plain["entity_name"] == ""
    assert p["ok"] and p["letterhead"]["company_name"] == "Acme Textiles Pvt Ltd" and p["name"] == "Legal entity transfer letter"
    assert "moves from Acme Canada Inc. to Acme Textiles Pvt Ltd" in p["text"] and "1 November 2026" in p["text"] and p["missing"] == []


def test_default_templates_still_render_with_no_custom_fields(world):
    """The generation API is unchanged: every seeded template still renders for a plain employee."""
    async def go():
        async with world() as db:
            e = _employee(hire_date=date(2024, 4, 1))
            db.add(e)
            await db.commit()
            p = await LR.preview_text(LR.PreviewTextIn(employee_id=str(e.id), template_key="appointment_letter"), Req(), db)
            return p
    p = asyncio.run(go())
    assert p["ok"] and "Asha Rao" in p["text"] and "$80,000.00" in p["text"] and p["name"] == "Appointment letter"
