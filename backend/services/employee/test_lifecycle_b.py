"""Stream B (employee service): the letter engine, salary history, career changes, probation.

Pure rules are tested directly; numbering, immutability, back-fill and apply-on-date
run against an in-memory SQLite database.

Run:  cd backend/services/employee && PYTHONPATH="../..;." python -m pytest test_lifecycle_b.py -q
"""
import asyncio
import os
import sys
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):
    return "JSON"


import career_logic as CL  # noqa: E402
import career_routers as CR  # noqa: E402
import comp_routers as COMP  # noqa: E402
import letters_engine as E  # noqa: E402
import letters_routers as LR  # noqa: E402
from career_common import APPROVER_ROLES, get_extras  # noqa: E402
from career_models import CareerChange, CareerSnapshot, LifecycleSettings, ProbationAction  # noqa: E402
from comp_models import CompHistory  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from letters_defaults import DEFAULT_KEYS, DEFAULT_TEMPLATES  # noqa: E402
from letters_models import IssuedLetter, LetterCounter, LetterSettings, LetterTemplate  # noqa: E402
from models import Employee  # noqa: E402

from shared.utils.org_time import local_today  # noqa: E402

TODAY = local_today()   # the business date the code uses (America/Toronto) — date.today() differs near midnight


# ══ letter engine: the allow-list ═════════════════════════════════════════════
def test_known_fields_pass_and_unknown_fields_are_refused():
    assert E.validate_template("Dear {{employee.full_name}}, your salary is {{ salary.new_annual }}.") == []
    assert E.validate_template("Hello {{extra.cab_allowance}}") == []           # caller-supplied value
    errs = E.validate_template("Dear {{employee.password}} {{org.bank_account}} {{secrets.key}}")
    assert errs == ["Unknown field {{employee.password}}.", "Unknown field {{org.bank_account}}.",
                    "Unknown field {{secrets.key}}."]
    assert not E.is_allowed("employee.bank_account") and not E.is_allowed("employee.sin") and not E.is_allowed("salary")


@pytest.mark.parametrize("body", [
    "{{ 7 * 7 }}",                                   # an expression
    "{{ employee.full_name | upper }}",              # a filter
    "{{ employee.__class__.__mro__ }}",              # attribute walking
    "{{ config.items() }}",                          # a call
    "{{employee.full_name.upper()}}",
    "{% for x in range(9) %}x{% endfor %}",          # template code
    "{% import os %}{{ os.popen('id').read() }}",
    "{# hidden #} hello",
    "<script>alert(1)</script>",                     # HTML
    "<b>bold</b>",
    "{{ employee.full_name ",                        # not closed
    "",
])
def test_code_html_and_broken_templates_are_refused(body):
    assert E.validate_template(body) != []


def test_nothing_in_a_template_is_ever_executed():
    # even if a bad template reached the renderer, a field is only ever looked up by name
    blocks, missing = E.build_blocks("Total {{ 7 * 7 }} {{employee.full_name.upper()}}", {"employee.full_name": "Asha"})
    out = E.plain_text(blocks)
    assert "49" not in out and "ASHA" not in out
    assert missing == ["7 * 7", "employee.full_name.upper()"]


def test_a_value_can_never_add_markup_or_another_field():
    values = {"employee.full_name": "**Asha** {{org.name}} | x | # big", "org.name": "SECRET CORP"}
    blocks, missing = E.build_blocks("Dear {{employee.full_name}},\n\n| A | B |\n| {{employee.full_name}} | 1 |", values)
    para, table = blocks
    assert para["t"] == "p" and para["runs"] == [("Dear **Asha** {{org.name}} | x | # big,", False)]   # one plain run
    assert len(table["rows"]) == 2 and len(table["rows"][1]) == 2        # the value did not add a column
    assert "SECRET CORP" not in E.plain_text(blocks) and missing == []


def test_blocks_headings_bold_lists_tables():
    body = "# Title\n\nHello **{{employee.first_name}}** there.\nSame paragraph.\n\n- one\n- two\n1. first\n\n| H1 | H2 |\n|---|---|\n| a | b |\n---\n## Sub"
    blocks, missing = E.build_blocks(body, {"employee.first_name": "Asha"})
    assert [b["t"] for b in blocks] == ["h1", "p", "li", "li", "li", "table", "hr", "h2"]
    assert blocks[1]["runs"] == [("Hello ", False), ("Asha", True), (" there. Same paragraph.", False)]
    assert blocks[4]["mark"] == "1." and len(blocks[5]["rows"]) == 2 and missing == []


def test_missing_values_are_reported_not_invented():
    blocks, missing = E.build_blocks("Join on {{employee.joining_date}} as {{employee.designation}}.\n\n{{salary.breakup_table}}",
                                     {"employee.designation": "Engineer"})
    assert missing == ["employee.joining_date", "salary.breakup_table"]
    assert "[employee.joining_date]" in E.plain_text(blocks)


def test_caller_data_is_checked_against_the_allow_list():
    vals, refused = E.flatten_data({"salary": {"new_annual": 900000, "ssn": "x"}, "exit.last_working_date": "2026-10-31",
                                    "Cab Allowance": 1500, "employee.password": "p", "weird.thing": 1})
    assert vals == {"salary.new_annual": 900000, "exit.last_working_date": "2026-10-31", "extra.cab_allowance": 1500}
    assert sorted(refused) == ["employee.password", "salary.ssn", "weird.thing"]


def test_every_default_template_uses_only_allowed_fields():
    assert len(DEFAULT_TEMPLATES) == 16 and len(set(DEFAULT_KEYS)) == 16      # the 12 asked for + resignation acceptance + entity_transfer (D4/D1) + contract renewal / conversion (2026-10-22)
    for want in ("offer_letter", "appointment_letter", "ctc_annexure", "confirmation", "probation_extension",
                 "salary_revision", "promotion", "transfer", "relieving", "experience_certificate", "fnf_cover",
                 "employment_verification"):
        assert want in DEFAULT_KEYS
    for t in DEFAULT_TEMPLATES:
        assert E.validate_template(t["body"]) == [], t["key"]


def test_money_and_dates():
    assert E.fmt_money(1234567.5, "CAD") == "$1,234,567.50"
    assert E.fmt_money(999, "CAD") == "$999.00" and E.fmt_money(-100000, "CAD") == "-$100,000.00"
    assert E.fmt_money(83200, "") == "83,200.00"
    assert E.fmt_date("2026-10-01") == "1 October 2026"
    assert E.display_value("salary.new_annual", 90000.0, "CAD") == "$90,000.00"
    assert E.display_value("exit.last_working_date", "2026-10-31") == "31 October 2026"
    assert E.display_value("extra.note", "line one\nline two") == "line one line two"


def test_pdf_is_rendered_on_the_letterhead():
    blocks, _ = E.build_blocks(DEFAULT_TEMPLATES[5]["body"], {
        "employee.full_name": "Asha Rao", "salary.effective_from": "1 April 2026", "salary.old_annual": "$80,000.00",
        "salary.new_annual": "$88,000.00", "salary.increase_amount": "$8,000.00"},
        {"salary.breakup_table": [["Component", "Per period", "Per year"], ["Base salary", "$3,384.62", "$88,000.00"]]})
    pdf = E.render_pdf(blocks, letterhead={"company_name": "Acme Canada Inc.", "address": "12 MG Road\nBengaluru",
                                           "signatory_name": "Priya N", "signatory_designation": "Head – HR",
                                           "footer_text": "Confidential $"},
                       number="LTR/2026/0001", date_text="13 October 2026")
    assert pdf[:5] == b"%PDF-" and len(pdf) > 1500
    assert E.render_pdf(blocks, watermark="DRAFT")[:5] == b"%PDF-"


def test_numbering_format_and_status_rules():
    assert E.format_number("LTR", 2026, 1) == "LTR/2026/0001"
    assert E.format_number("acme hr!", 2027, 12345) == "ACMEHR/2027/12345"
    assert E.format_number("", 2026, 7) == "LTR/2026/0007"
    assert E.allowed_actions("draft") == {"issue", "discard", "download"}
    assert E.allowed_actions("issued") == {"download", "void", "reissue"}
    assert E.allowed_actions("void") == {"download"}
    for status, action in (("issued", "discard"), ("issued", "edit"), ("issued", "issue"), ("void", "void"),
                           ("void", "reissue"), ("void", "edit"), ("draft", "void")):
        with pytest.raises(PermissionError):
            E.check_action(status, action)


# ══ career + probation rules ══════════════════════════════════════════════════
CUR = {"job_title": "Engineer", "department": "Tech", "grade": "L2", "manager_id": "m1", "manager_name": "Mo",
       "work_location": "Pune", "division": None, "cost_center": None}


def test_career_change_keeps_only_real_changes_and_captures_old_values():
    nv, errs = CL.clean_new_values("promotion", {"job_title": "Senior Engineer", "grade": "L3", "department": "Tech"}, CUR)
    assert errs == [] and nv == {"job_title": "Senior Engineer", "grade": "L3"}     # department did not change
    assert CL.capture_old(CUR, nv) == {"job_title": "Engineer", "grade": "L2"}
    nv, _ = CL.clean_new_values("manager_change", {"manager_id": "m2"}, CUR)
    assert CL.capture_old(CUR, nv) == {"manager_id": "m1", "manager_name": "Mo"}


def test_career_change_validation():
    assert CL.clean_new_values("promotion", {"work_location": "Delhi"}, CUR)[1] == ["A promotion must change the designation or grade."]
    assert CL.clean_new_values("transfer", {"department": "Tech"}, CUR)[1]             # nothing really changes
    assert "cannot be changed" in CL.clean_new_values("transfer", {"annual_salary": 9, "department": "Ops"}, CUR)[1][0]
    assert CL.clean_new_values("demotion", {"grade": "L1"}, CUR)[1][0].startswith("Type must be one of")
    assert set(CL.TYPES) == {"promotion", "transfer", "re_designation", "manager_change", "location_change",
                             "department_change", "grade_change", "entity_transfer"}


def test_career_change_maker_cannot_approve():
    kw = {"role": "hr_admin", "approver_roles": APPROVER_ROLES}
    assert CL.decision_error("submitted", "approve", is_maker=False, **kw) is None
    assert "Someone else" in CL.decision_error("submitted", "approve", is_maker=True, **kw)
    assert CL.decision_error("submitted", "reject", is_maker=True, **kw) is None       # a maker may withdraw / reject
    assert "HR head" in CL.decision_error("submitted", "approve", is_maker=False, role="hr_manager", approver_roles=APPROVER_ROLES)
    assert CL.decision_error("draft", "approve", is_maker=False, **kw) is not None
    assert "new change" in CL.decision_error("applied", "cancel", is_maker=False, **kw)
    assert CL.decision_error("approved", "cancel", is_maker=False, **kw) is None       # withdraw before it is applied


def test_change_is_due_only_when_approved_and_dated_today_or_earlier():
    assert CL.is_due("approved", TODAY, TODAY) and CL.is_due("approved", TODAY - timedelta(days=3), TODAY)
    assert not CL.is_due("approved", TODAY + timedelta(days=1), TODAY)
    assert not CL.is_due("submitted", TODAY, TODAY) and not CL.is_due("applied", TODAY, TODAY)


def test_probation_dates():
    assert CL.probation_end(date(2026, 1, 1), 6) == date(2026, 6, 30)
    assert CL.probation_end(date(2026, 8, 31), 6) == date(2027, 2, 27)       # 28 Feb 2027 − 1 day
    assert CL.probation_end(date(2026, 10, 15), 3) == date(2027, 1, 14)
    assert CL.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)          # leap year
    assert CL.probation_end(date(2026, 3, 1), 12) == date(2027, 2, 28)


def test_probation_months_most_specific_rule_wins():
    s = {"probation_months": 6, "probation_rules": [{"grade": "L1", "months": 3},
                                                    {"employment_type": "contract", "months": 1},
                                                    {"grade": "L1", "employment_type": "contract", "months": 0}]}
    assert CL.probation_months_for(s) == 6 and CL.probation_months_for(None) == CL.DEFAULT_PROBATION_MONTHS
    assert CL.probation_months_for(s, "l1", "full_time") == 3
    assert CL.probation_months_for(s, "L5", "contract") == 1
    assert CL.probation_months_for(s, "L1", "contract") == 0


def test_probation_buckets():
    d = date(2026, 10, 13)
    assert CL.probation_bucket(d - timedelta(days=2), d) == ("overdue", -2)
    assert CL.probation_bucket(d, d) == ("7", 0) and CL.probation_bucket(d + timedelta(days=7), d) == ("7", 7)
    assert CL.probation_bucket(d + timedelta(days=8), d)[0] == "15" and CL.probation_bucket(d + timedelta(days=15), d)[0] == "15"
    assert CL.probation_bucket(d + timedelta(days=30), d)[0] == "30" and CL.probation_bucket(d + timedelta(days=31), d)[0] == "later"


# ══ database-backed ═══════════════════════════════════════════════════════════
ORG = uuid.uuid4()


class Req:
    """Just enough of a request: the gateway-stamped headers."""

    def __init__(self, role="hr_admin", email="hr@acme.example", uid="u-hr", employee_id=""):
        self.headers = {"x-org-id": str(ORG), "x-role": role, "x-user-email": email, "x-user-id": uid,
                        **({"x-employee-id": employee_id} if employee_id else {})}


async def _no_audit(*a, **kw):
    return True


@pytest.fixture
def world(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [m.__table__ for m in (Employee, CompHistory, CareerChange, CareerSnapshot, ProbationAction, LifecycleSettings,
                                    LetterTemplate, LetterSettings, LetterCounter, IssuedLetter)]

    async def make():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Employee.metadata.create_all(c, tables=tables))
            from career_common import EXTRA_COLUMNS
            for col, ddl in EXTRA_COLUMNS:               # SQLite has no ADD COLUMN IF NOT EXISTS
                if col not in Employee.__table__.columns:
                    await conn.execute(text(f"ALTER TABLE employees ADD COLUMN {col} {ddl}"))
            await conn.execute(text("CREATE TABLE organizations (id CHAR(32) PRIMARY KEY, name VARCHAR, legal_name VARCHAR)"))
            await conn.execute(text("INSERT INTO organizations VALUES (:i, 'Acme Canada', 'Acme Canada Inc.')"), {"i": ORG.hex})
    asyncio.run(make())
    for mod in (LR, CR, COMP):
        monkeypatch.setattr(mod, "record_audit", _no_audit)
    LR._seeded.clear()
    return async_sessionmaker(engine, expire_on_commit=False)


def _employee(**kw):
    base = dict(id=uuid.uuid4(), org_id=ORG, employee_number="E001", first_name="Asha", last_name="Rao",
                email="asha@acme.example", status="active", employment_type="full_time", address_country="CA",
                job_title="Engineer", department="Tech", work_location="Vancouver", hire_date=date(2024, 4, 1),
                pay_type="salary", annual_salary=80000, pay_frequency="biweekly", is_active=True,
                province_of_employment="ON", standard_hours_per_week=40)
    return Employee(**{**base, **kw})


def test_backfill_adds_one_opening_row_and_is_idempotent(world):
    async def go():
        async with world() as db:
            db.add_all([_employee(), _employee(id=uuid.uuid4(), employee_number="E002", annual_salary=None, hourly_rate=None),
                        _employee(id=uuid.uuid4(), employee_number="C001", address_country="CA", pay_type="hourly",
                                  annual_salary=None, hourly_rate=30)])
            await db.commit()
            first, second = await COMP.backfill(db), await COMP.backfill(db)
            rows = (await db.execute(select(CompHistory).order_by(CompHistory.currency))).scalars().all()
            return first, second, rows
    first, second, rows = asyncio.run(go())
    assert (first, second) == (2, 0)                       # the employee with no salary gets no row
    assert {r.currency for r in rows} == {"CAD"}
    salaried = next(r for r in rows if r.pay_type == "salary")
    hourly = next(r for r in rows if r.pay_type == "hourly")
    assert (salaried.reason, salaried.source, float(salaried.annual_amount), salaried.effective_from) == \
        ("opening", "opening", 80000.0, date(2024, 4, 1))
    assert float(hourly.hourly_rate) == 30.0


def test_history_row_and_live_salary_are_written_together_once_per_revision(world):
    async def go():
        async with world() as db:
            e = _employee()
            db.add(e)
            await db.commit()
            rid = str(uuid.uuid4())
            body = COMP.HistoryIn(effective_from="2026-10-16", annual_amount=1440000, components={"BASIC": 60000},
                                  reason="annual_increment", source_revision_id=rid, approved_by="admin@acme.example")
            a = await COMP.add_history(str(e.id), body, Req(), db)
            b = await COMP.add_history(str(e.id), body, Req(), db)                 # the same revision again
            await db.refresh(e)
            rows = (await db.execute(select(CompHistory).order_by(CompHistory.effective_from))).scalars().all()
            with pytest.raises(HTTPException) as denied:
                await COMP.add_history(str(e.id), body, Req(role="employee"), db)
            return a, b, e, rows, denied.value.status_code
    a, b, e, rows, denied = asyncio.run(go())
    assert a["applied_to_employee"] is True and b.get("already_recorded") is True and b["id"] == a["id"]
    assert float(e.annual_salary) == 1440000.0 and denied == 403
    assert [(r.reason, r.effective_from, r.effective_to) for r in rows] == [
        ("opening", date(2024, 4, 1), date(2026, 10, 15)), ("annual_increment", date(2026, 10, 16), None)]


def test_direct_edit_of_salary_shows_up_in_history(world):
    async def go():
        async with world() as db:
            e = _employee()
            db.add(e)
            await db.commit()
            await COMP.backfill(db)
            e.annual_salary = 86000                          # typed on the employee form, no revision
            await db.commit()
            n1, n2 = await COMP.reconcile(db, str(ORG)), await COMP.reconcile(db, str(ORG))
            view = await COMP.employee_compensation(str(e.id), Req(), db)
            return n1, n2, view
    n1, n2, view = asyncio.run(go())
    assert (n1, n2) == (1, 0)
    newest = view["history"][0]
    assert newest["source"] == "direct_edit" and newest["annual_amount"] == 86000.0
    assert newest["change_amount"] == 6000.0 and abs(newest["change_percent"] - 7.5) < 0.01


def test_career_change_is_applied_only_when_approved_and_on_its_date(world):
    async def go():
        async with world() as db:
            e = _employee()
            db.add(e)
            await db.commit()
            eid = str(e.id)
            maker, head = Req(role="hr_manager", email="maker@acme.example", uid="u-m"), Req()
            now_c = await CR.create_change(eid, CR.ChangeIn(change_type="promotion", effective_date=TODAY.isoformat(),
                                                            new_values={"job_title": "Senior Engineer", "grade": "L3"},
                                                            reason="Annual cycle", submit=True), maker, db)
            later = await CR.create_change(eid, CR.ChangeIn(change_type="transfer",
                                                            effective_date=(TODAY + timedelta(days=20)).isoformat(),
                                                            new_values={"department": "Ops", "work_location": "Delhi"},
                                                            submit=True), maker, db)
            with pytest.raises(HTTPException) as own:
                await CR.approve_change(now_c["id"], maker, None, db)              # hr_manager is not an approver
            with pytest.raises(HTTPException) as same:
                await CR.approve_change(now_c["id"], Req(email="maker@acme.example", uid="u-m"), None, db)   # the maker
            await db.refresh(e)
            before = (e.job_title, e.department)
            a1 = await CR.approve_change(now_c["id"], head, None, db)
            a2 = await CR.approve_change(later["id"], head, None, db)
            await db.refresh(e)
            extras = (await get_extras(db, str(ORG), eid))[eid]
            again = await CR.apply_due(db, str(ORG))
            future = await CR.apply_due(db, str(ORG), today=TODAY + timedelta(days=20))
            twice = await CR.apply_due(db, str(ORG), today=TODAY + timedelta(days=21))
            await db.refresh(e)
            with pytest.raises(HTTPException) as locked:
                await CR.cancel_change(now_c["id"], head, None, db)
            return now_c, before, a1, a2, extras, (e.job_title, e.department, e.work_location), again, future, twice, \
                own.value.status_code, same.value.detail, locked.value.detail
    now_c, before, a1, a2, extras, after, again, future, twice, own, same, locked = asyncio.run(go())
    assert now_c["old_values"] == {"job_title": "Engineer", "grade": None} and now_c["status"] == "submitted"
    assert before == ("Engineer", "Tech")                               # nothing changes before approval
    assert own == 403 and "Someone else" in same
    assert a1["status"] == "applied" and extras["grade"] == "L3"        # dated today → applied on approval
    assert a2["status"] == "approved"                                   # future-dated → waits
    assert (again, future, twice) == (0, 1, 0)                          # applied once, on its date
    assert after == ("Senior Engineer", "Ops", "Delhi")
    assert "new change" in locked                                       # history is not edited


def test_a_field_edited_on_the_employee_form_is_recorded_not_silent(world):
    assert CL.infer_type({"grade": "L3"}) == "grade_change" and CL.infer_type({"job_title": "x", "grade": "y"}) == "re_designation"
    assert CL.infer_type({"department": "Ops", "work_location": "Delhi"}) == "transfer"
    assert CL.infer_type({"manager_id": "m2", "manager_name": "Mo"}) == "manager_change"

    async def go():
        async with world() as db:
            e = _employee()
            db.add(e)
            await db.commit()
            first = await CR.record_direct_edits(db, str(ORG))          # first sight: snapshot only
            e.department, e.job_title = "Ops", "Lead Engineer"          # the plain edit form
            await db.commit()
            second = await CR.record_direct_edits(db, str(ORG))
            third = await CR.record_direct_edits(db, str(ORG))
            # a change that goes through the proper record is NOT reported as a direct edit
            c = await CR.create_change(str(e.id), CR.ChangeIn(change_type="location_change", effective_date=TODAY.isoformat(),
                                                              new_values={"work_location": "Delhi"}, submit=True),
                                       Req(role="hr_manager", email="maker@acme.example", uid="u-m"), db)
            await CR.approve_change(c["id"], Req(), None, db)
            fourth = await CR.record_direct_edits(db, str(ORG))
            rows = (await db.execute(select(CareerChange).order_by(CareerChange.created_at))).scalars().all()
            return first, second, third, fourth, rows
    first, second, third, fourth, rows = asyncio.run(go())
    assert (first, second, third, fourth) == (0, 1, 0, 0)
    auto = rows[0]
    assert auto.status == "applied" and auto.created_by == "system" and auto.change_type == "transfer"
    assert auto.old_values == {"job_title": "Engineer", "department": "Tech"}
    assert auto.new_values == {"job_title": "Lead Engineer", "department": "Ops"}
    assert "without a career-change record" in auto.note and len(rows) == 2


def test_probation_dates_are_set_only_for_people_still_inside_the_window(world):
    async def go():
        async with world() as db:
            new = _employee(hire_date=TODAY - timedelta(days=30))
            old = _employee(id=uuid.uuid4(), employee_number="E002", hire_date=date(2020, 1, 1))
            db.add_all([new, old])
            await db.commit()
            n1, n2 = await CR.assign_missing(db, str(ORG)), await CR.assign_missing(db, str(ORG))
            await db.refresh(new)
            await db.refresh(old)
            ex = await get_extras(db, str(ORG))
            due = await CR.probation_due(Req(), within=30, all=1, db=db)
            res = await CR.extend(str(new.id), CR.ExtendIn(new_end_date=(new.probation_end_date + timedelta(days=60)).isoformat(),
                                                           reason="More time needed", issue_letter=False), Req(), db)
            done = await CR.confirm(str(new.id), CR.ConfirmIn(issue_letter=False), Req(), db)
            with pytest.raises(HTTPException) as twice:
                await CR.confirm(str(new.id), CR.ConfirmIn(issue_letter=False), Req(), db)
            ex2 = await get_extras(db, str(ORG), str(new.id))
            return n1, n2, new, old, ex, due, res, done, twice.value.status_code, ex2[str(new.id)]
    n1, n2, new, old, ex, due, res, done, twice, ex2 = asyncio.run(go())
    assert (n1, n2) == (1, 0)
    assert old.probation_end_date is None and ex[str(old.id)]["employment_status"] is None     # not assumed
    assert ex[str(new.id)]["employment_status"] == "probation"
    assert [r["employee_id"] for r in due["rows"]] == [str(new.id)] and due["settings"]["probation_months"] == 6
    assert res["probation_end_date"] == new.probation_end_date.isoformat()
    assert done["employment_status"] == "confirmed" and twice == 409
    assert ex2["employment_status"] == "confirmed" and ex2["confirmation_date"] == done["confirmation_date"]


def test_letters_are_numbered_per_org_and_year_and_never_change_once_issued(world):
    async def go():
        async with world() as db:
            e = _employee()
            db.add(e)
            await db.commit()
            eid, hr = str(e.id), Req()
            data = {"salary": {"old_annual": 80000, "new_annual": 90000, "effective_from": "2026-04-01",
                               "components": {"Base salary": 7500}}}
            one = await LR.create_letter(eid, LR.LetterIn(template_key="salary_revision", data=data), hr, db)
            two = await LR.create_letter(eid, LR.LetterIn(template_key="employment_verification", data={}, allow_blank=True), hr, db)
            draft = await LR.create_letter(eid, LR.LetterIn(template_key="confirmation", data={}, issue=False), hr, db)
            with pytest.raises(HTTPException) as missing:
                await LR.create_letter(eid, LR.LetterIn(template_key="relieving", data={}), hr, db)
            with pytest.raises(HTTPException) as unknown:
                await LR.create_letter(eid, LR.LetterIn(template_key="confirmation", data={"employee.password": "x"}), hr, db)
            row = (await db.execute(select(IssuedLetter).where(IssuedLetter.number == one["number"]))).scalars().one()
            pdf_before, sha_before = bytes(row.pdf), row.sha256
            # the template changes afterwards — the issued copy must not
            tpl = (await db.execute(select(LetterTemplate).where(LetterTemplate.key == "salary_revision"))).scalars().one()
            await LR.update_template(str(tpl.id), LR.TemplateIn(name=tpl.name, body="# New wording\n\n{{employee.full_name}}"), hr, db)
            with pytest.raises(HTTPException) as bad_tpl:
                await LR.update_template(str(tpl.id), LR.TemplateIn(name="x", body="{{employee.salary_secret}}"), hr, db)
            with pytest.raises(HTTPException) as no_delete:
                await LR.discard_draft(one["letter_id"], hr, db)
            with pytest.raises(HTTPException) as no_reissue_draft:
                await LR.reissue(draft["letter_id"], hr, db)
            again = await LR.reissue(one["letter_id"], hr, db)
            voided = await LR.void_letter(one["letter_id"], LR.VoidIn(reason="Wrong amount"), hr, db)
            with pytest.raises(HTTPException) as void_twice:
                await LR.void_letter(one["letter_id"], LR.VoidIn(reason="again"), hr, db)
            await db.refresh(row)
            issued_draft = await LR.issue_draft(draft["letter_id"], hr, False, db)
            mine = await LR.employee_letters(eid, Req(role="employee", email="asha@acme.example", uid="u-e"), db)
            with pytest.raises(HTTPException) as other:
                await LR.employee_letters(eid, Req(role="employee", email="someone@acme.example", uid="u-x"), db)
            counter = (await db.execute(select(LetterCounter))).scalars().one()
            return (one, two, draft, missing.value, unknown.value, row, pdf_before, sha_before, tpl, bad_tpl.value,
                    no_delete.value, no_reissue_draft.value, again, voided, void_twice.value, issued_draft, mine,
                    other.value, counter)
    (one, two, draft, missing, unknown, row, pdf_before, sha_before, tpl, bad_tpl, no_delete, no_reissue_draft,
     again, voided, void_twice, issued_draft, mine, other, counter) = asyncio.run(go())
    y = TODAY.year
    assert one["number"] == f"LTR/{y}/0001" and two["number"] == f"LTR/{y}/0002"
    assert one["pdf_url"] == f"/letters/{one['letter_id']}/pdf" and set(one) >= {"letter_id", "number", "pdf_url"}
    assert draft["number"] is None and draft["status"] == "draft"        # a draft takes no number
    assert missing.status_code == 422 and "exit.last_working_date" in missing.detail["missing_fields"]
    assert unknown.status_code == 422 and unknown.detail["errors"] == ["employee.password"]
    assert bad_tpl.status_code == 422 and no_delete.status_code == 409 and no_reissue_draft.status_code == 409
    # immutable: same bytes, same hash, same number, same template version — after a template edit AND a void
    assert bytes(row.pdf) == pdf_before and row.sha256 == sha_before == E.sha256_hex(pdf_before)
    assert row.status == "void" and row.void_reason == "Wrong amount" and row.number == one["number"]
    assert row.template_version == 1 and tpl.version == 2 and "Salary revision" in row.body_snapshot
    assert "$90,000.00" in row.text_copy and "Base Salary" in row.text_copy
    assert again["number"] == f"LTR/{y}/0003" and again["reissued_from"] == one["letter_id"]   # a NEW number
    assert voided["status"] == "void" and void_twice.status_code == 409
    assert issued_draft["number"] == f"LTR/{y}/0004" and counter.last_seq == 4 and counter.year == y
    # the employee sees only their own ISSUED letters (not the void one, not another person's)
    assert sorted(m["number"] for m in mine) == [f"LTR/{y}/0002", f"LTR/{y}/0003", f"LTR/{y}/0004"]
    assert other.status_code == 403
