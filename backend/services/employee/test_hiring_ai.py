"""G6 hiring (2026-10-23): résumé parsing, JD drafts, advisory fit, partner job feeds, AI provider privacy.

Run:  cd backend/services/employee && PYTHONPATH="../..;." python -m pytest test_hiring_ai.py -q
"""
import asyncio
import io
import os
import sys
import uuid
import zipfile
from datetime import date, datetime, timezone
from xml.etree import ElementTree as ET

import pytest
from fastapi import HTTPException
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


import hiring_ai_logic as A  # noqa: E402
import hiring_ai_provider as PV  # noqa: E402
import hiring_ai_routers as HR  # noqa: E402
import hiring_d2_logic as D  # noqa: E402
import hiring_logic as L  # noqa: E402
from hiring_ai_models import HireCandidateProfile, HireMatchScore  # noqa: E402
from hiring_logic import Actor  # noqa: E402
from hiring_models import HireApplication, HireCandidate, HireDocument, HirePosting, HireRequisition, HireSettings  # noqa: E402

RESUME = """PRIYA RAMESH
Toronto, ON | priya.r@example.com | +1 (416) 555-0142 | linkedin.com/in/priyar
Profile
Backend engineer with 6+ years of experience building payments systems.
Skills: Python, FastAPI, PostgreSQL, Docker, Kubernetes, AWS, Kafka, REST APIs
Experience
Senior Engineer, FinCo Inc.   Jan 2021 - Present
Engineer, ShopKart   2018 - 2020
Education
B.Tech in Computer Science, 2018
Current salary: $95,000   Expected salary: 110k
Notice period: 60 days
Date of Birth: 01/01/1995
"""


# ═════════════ parsing ═════════════
def test_parse_resume_fields_with_evidence():
    p = A.parse_resume(RESUME, today=date(2026, 10, 23))
    assert (p["first_name"], p["last_name"]) == ("Priya", "Ramesh")
    assert p["email"] == "priya.r@example.com" and p["phone"] == "4165550142"
    assert {"python", "fastapi", "postgresql", "docker", "kubernetes", "aws", "kafka", "rest api"} <= set(p["skills"])
    assert p["experience_years"] == 6.0 and "6+ years" in p["evidence"]["experience_years"]
    assert p["education"][0]["degree"] == "B.Tech / B.E." and p["education"][0]["level"] == 3
    assert p["notice_period_days"] == 60
    assert p["current_ctc"] == 95000 and p["expected_ctc"] == 110000
    assert A.find_ctc("Current salary: 85", "current")[0] == 85000          # no unit → read as thousands, and said so
    assert "thousands" in A.find_ctc("Current salary: 85", "current")[1]
    assert A.find_ctc("Current salary: CAD 92,500", "current")[0] == 92500
    assert A.find_ctc("Current salary: $32/hr", "current")[0] == 66560      # hourly × 40 × 52
    assert A.find_notice_days("Immediate joiner")[0] == 0 and A.find_notice_days("notice period of 2 months")[0] == 60


def test_experience_from_job_dates_when_not_stated():
    yrs, src = A.find_experience_years("Analyst, X  2015 - 2018\nLead, Y  Mar 2018 to Present", today=date(2026, 10, 23))
    assert yrs == 11.0 and "dates" in src                                     # 2015-2018 + 2018-2026, overlap merged


def test_strip_for_ai_removes_contacts_name_money_and_identity():
    p = A.parse_resume(RESUME)
    s = A.strip_for_ai(RESUME, p)
    for leak in ("priya.r@example.com", "555-0142", "linkedin.com", "Priya", "Ramesh", "$95,000", "110k", "Date of Birth"):
        assert leak not in s
    assert "FastAPI" in s
    safe = A.profile_for_ai({**p, "current_employer": "FinCo"})
    assert set(safe) == {"skills", "experience_years", "qualification_levels", "notice_period_days", "current_title"}
    assert "FinCo" not in str(safe) and "example.com" not in str(safe)


def _docx(text: str) -> bytes:
    buf = io.BytesIO()
    paras = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.split("\n"))
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<w:document xmlns:w="w"><w:body>{paras}</w:body></w:document>')
    return buf.getvalue()


def test_extract_text_from_docx_and_pdf():
    t = A.extract_text(_docx("Ravi Kumar\nSkills: QuickBooks, Excel, CPA\nNotice period: 30 days"), "docx")
    assert "QuickBooks" in t and A.parse_resume(t)["notice_period_days"] == 30
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in ("Anita Desai", "Skills: Excel, Power BI, SQL", "5 years of experience in data analysis"):
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    raw = bytes(pdf.output())
    assert L.check_upload(raw, 5)[0] == "pdf"
    p = A.parse_resume(A.extract_text(raw, "pdf"))
    assert {"excel", "power bi", "sql"} <= set(p["skills"]) and p["experience_years"] == 5.0
    assert A.extract_text(b"not a pdf", "pdf") == ""


# ═════════════ JD + match ═════════════
def test_jd_draft_is_deterministic_and_neutral():
    d = A.draft_jd({"designation": "Senior Backend Engineer", "department": "Engineering", "location": "Bengaluru, KA",
                    "employment_type": "full_time", "skills": ["Python", "PostgreSQL"], "experience_min": 4, "experience_max": 8})
    assert d["method"] == "rules" and "Senior Backend Engineer" in d["description"] and "4 to 8 years" in d["requirements"]
    assert d["skills"] == ["postgresql", "python"]
    for word in (" he ", " she ", "young", "age "):
        assert word not in d["description"].lower() + d["requirements"].lower()
    assert "sales" in A.draft_jd({"designation": "Area Sales Manager"})["skills"]


def test_match_score_explains_and_is_advisory():
    prof = A.parse_resume(RESUME)
    job = A.job_requirements({"title": "Backend Engineer", "requirements": "5-8 years. Python, FastAPI, Go, Kubernetes. B.E./B.Tech."})
    assert job["experience_min"] == 5 and job["experience_max"] == 8 and job["degree_level"] == 3 and "go" in job["skills"]
    m = A.match_score(prof, job)
    assert m["matched_skills"] == ["fastapi", "kubernetes", "python"] and m["missing_skills"] == ["go"]
    # skills 3/4 x 60 = 45 ; experience 25 ; degree 10 ; notice 60 d = 3 → 83
    assert m["score"] == 83 and m["band"] == "strong" and [x["part"] for x in m["lines"]] == ["Skills", "Experience", "Qualification", "Notice period"]
    assert "no one is moved or rejected" in m["advisory"]
    empty = A.match_score({"skills": []}, {"skills": [], "experience_min": None, "degree_level": 0})
    assert empty["score"] == 98                                                # nothing asked = neutral, unknown notice = half
    junior = A.match_score({**prof, "experience_years": 2}, job)
    assert junior["score"] < m["score"]


# ═════════════ partner feeds ═════════════
JOB = {"id": str(uuid.uuid4()), "title": "Backend Engineer", "description": "Build and run our payments platform. " * 4,
       "requirements": "4-6 years of Python and PostgreSQL.", "reference": "REQ-0007", "department": "Engineering", "location": "Toronto, ON",
       "city": "Toronto", "state": "ON", "country": "CA", "employment_type": "full_time",
       "published_at": datetime(2026, 10, 20, tzinfo=timezone.utc), "url": "https://www.maple.example/careers/MAPLE/x?src=linkedin"}


def test_linkedin_feed_has_the_documented_mandatory_fields():
    xml = D.linkedin_xml("Maple Logistics", "https://www.maple.example/careers/MAPLE", [JOB], "1234567", "talent@maple.example")
    root = ET.fromstring(xml)
    assert root.tag == "source" and root.findtext("expectedJobCount") == "1"
    j = root.find("job")
    for tag in ("partnerJobId", "title", "description", "applyUrl", "companyId", "location", "posterEmail", "jobtype"):
        assert (j.findtext(tag) or "").strip(), tag
    assert len(j.findtext("partnerJobId")) <= 40 and j.findtext("jobtype") == "FULL_TIME" and j.findtext("listDate") == "10/20/2026"
    assert len(j.findtext("description")) >= 100 and j.findtext("location") == "Toronto, ON, Canada"
    assert [s.text for s in j.find("skills")] == ["postgresql", "python"]
    assert "salar" not in xml.lower()
    assert D.linkedin_issues(JOB) == []
    assert "apply URL" in " ".join(D.linkedin_issues({**JOB, "url": "http://localhost:3100/careers/X"}))


def test_partner_states_and_settings():
    cfg = {"linkedin_company_id": "1234567", "linkedin_poster_email": "talent@maple.example"}
    assert D.board_state("linkedin", False, True, cfg)["state"] == "off"
    assert D.board_state("linkedin", True, True, cfg)["state"] == "in_feed"
    s = L.clean_settings({"linkedin_company_id": "1234567", "linkedin_poster_email": "Talent@Maple.example", "ai_assist": 0})
    assert s == {"linkedin_company_id": "1234567", "linkedin_poster_email": "talent@maple.example", "ai_assist": False}
    with pytest.raises(L.RuleError):
        L.clean_settings({"linkedin_company_id": "maple"})


# ═════════════ routes + provider privacy (SQLite) ═════════════
ORG = uuid.uuid4()
HM = uuid.uuid4()


class Req:
    def __init__(self, role="hr_admin", emp=None):
        self.headers = {"x-org-id": str(ORG), "x-role": role, "x-user-id": f"u-{role}", "x-user-email": f"{role}@maple.example",
                        **({"x-employee-id": str(emp)} if emp else {})}


class FakeProvider:
    name = "fake"

    def __init__(self, reply=None):
        self.prompts, self.reply = [], reply

    async def complete(self, org, feature, system, prompt, max_tokens):
        self.prompts.append((feature, prompt, max_tokens))
        return (self.reply, None) if self.reply is not None else (None, "AI is not configured (no ANTHROPIC_API_KEY).")


@pytest.fixture
def world(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [t.__table__ for t in (HireSettings, HireRequisition, HirePosting, HireCandidate, HireApplication, HireDocument,
                                    HireCandidateProfile, HireMatchScore)]
    from hiring_models import HireInterview
    tables.append(HireInterview.__table__)

    async def make():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: HireSettings.metadata.create_all(c, tables=tables))
    asyncio.run(make())

    async def _actor(request, db):
        h = request.headers
        return Actor(org=uuid.UUID(h["x-org-id"]), user_id=h["x-user-id"], role=h["x-role"], employee_id=h.get("x-employee-id") or "",
                     email=h["x-user-email"], name="Tester")

    async def _no(*a, **kw):
        return True
    monkeypatch.setattr(HR, "get_actor", _actor)
    monkeypatch.setattr(HR, "record_audit", _no)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    ids = {}

    async def seed():
        async with sm() as db:
            r = HireRequisition(org_id=ORG, req_number="REQ-0001", designation="Backend Engineer", department="Engineering", location="Bengaluru, KA",
                                employment_type="full_time", openings=1, status="open", hiring_manager_id=HM, approvals=[])
            db.add(r)
            await db.flush()
            p = HirePosting(org_id=ORG, requisition_id=r.id, title="Backend Engineer", description="Payments platform.",
                            requirements="5-8 years. Python, FastAPI, Go, Kubernetes. B.E./B.Tech.", is_published=True)
            c = HireCandidate(org_id=ORG, first_name="Priya", last_name="Ramesh", email="priya.r@example.com", tags=[])
            db.add_all([p, c])
            await db.flush()
            a = HireApplication(org_id=ORG, candidate_id=c.id, requisition_id=r.id, posting_id=p.id, stage="applied", source_type="careers_page")
            d = HireDocument(org_id=ORG, candidate_id=c.id, kind="resume", file_name="cv.docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                             size_bytes=10, content=_docx(RESUME))
            db.add_all([a, d])
            await db.commit()
            ids.update(req=str(r.id), cand=str(c.id), app=str(a.id))
    asyncio.run(seed())
    sm.ids = ids
    return sm


def test_parse_profile_match_without_ai_key(world):
    fake = FakeProvider()                       # behaves like the local stack: no key → rules only
    PV.set_provider(fake)
    try:
        async def go():
            async with world() as db:
                res = await HR.parse_candidate(world.ids["cand"], Req(), None, db)
                assert res["profile"]["status"] == "parsed" and res["profile"]["method"] == "rules"
                assert res["profile"]["experience_years"] == 6.0 and res["profile"]["current_ctc"] == 95000
                assert "not configured" in (res["note"] or "")
                feature, prompt, cap = fake.prompts[0]
                assert feature == "hiring.resume_parse" and cap <= 1500
                for leak in ("priya.r@example.com", "555-0142", "Priya", "$95,000", "Date of Birth"):
                    assert leak not in prompt                                   # never sent to the AI layer
                # the hiring manager may read the profile but never the CTC
                mgr = await HR.get_profile(world.ids["cand"], Req("manager", HM), db)
                assert "current_ctc" not in mgr["profile"] and mgr["profile"]["skills"]
                with pytest.raises(HTTPException) as ex:
                    await HR.get_profile(world.ids["cand"], Req("manager", uuid.uuid4()), db)
                assert ex.value.status_code == 404
                with pytest.raises(HTTPException) as ex:
                    await HR.parse_candidate(world.ids["cand"], Req("manager", HM), None, db)
                assert ex.value.status_code == 403
                m = await HR.match(world.ids["app"], Req("manager", HM), db)
                assert m["score"] == 83 and m["method"] == "rules" and m["detail"]["missing_skills"] == ["go"]
                match_prompt = fake.prompts[-1][1]
                assert "Priya" not in match_prompt and "example.com" not in match_prompt and "1800000" not in match_prompt
                rk = await HR.ranking(world.ids["req"], Req(), db=db)
                assert rk["items"][0]["score"] == 83 and "not a decision" in rk["advisory"]
                conf = await HR.put_profile(world.ids["cand"], {"skills": "python, go", "confirm": True}, Req(), db)
                assert conf["profile"]["status"] == "confirmed" and conf["profile"]["skills"] == ["go", "python"]
                # nothing moved the application
                app = await db.get(HireApplication, uuid.UUID(world.ids["app"]))
                assert app.stage == "applied"
        asyncio.run(go())
    finally:
        PV.set_provider(None)


def test_ai_adds_but_never_overrides_and_jd_draft(world):
    fake = FakeProvider('{"skills": ["payments", "Python"], "experience_years": 30, "summary": "Builds payment systems.", "current_title": "Senior Engineer"}')
    PV.set_provider(fake)
    try:
        async def go():
            async with world() as db:
                res = await HR.parse_candidate(world.ids["cand"], Req(), None, db)
                pr = res["profile"]
                assert pr["method"] == "rules+ai" and "payments" in pr["skills"] and pr["experience_years"] == 6.0   # AI cannot override stated years
                assert pr["summary"] == "Builds payment systems." and res["contacts_found"]["email"] == "priya.r@example.com"
                fake.reply = None
                jd = await HR.draft_jd({"requisition_id": world.ids["req"], "skills": "python, postgresql"}, Req("manager", HM), db)
                assert jd["method"] == "rules" and "Backend Engineer" in jd["description"] and "Nothing is published" in jd["advice"]
                with pytest.raises(HTTPException) as ex:
                    await HR.draft_jd({"requisition_id": world.ids["req"]}, Req("manager", uuid.uuid4()), db)
                assert ex.value.status_code == 404
        asyncio.run(go())
    finally:
        PV.set_provider(None)
