"""Hiring D2 — pure rules (no DB, no HTTP) so every rule is unit-tested.

Job-board feeds (Indeed XML, Google for Jobs JSON-LD, LinkedIn export),
interview slots + the candidate pick link, offer approval chains, scorecard
maths + bias guard, referral bonus dates, candidate message templates, the
captcha hook and the signed OAuth state.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape

from hiring_logic import RuleError, hash_token, new_token  # noqa: F401  (re-exported for the routers)
from shared.utils.org_time import local_today  # org-TZ business date (UTC store, local today)

# ── Job boards ──────────────────────────────────────────────────────────────
BOARDS = {
    # key: (label, how it is published, docs)
    "indeed": ("Indeed", "feed",
               # Indeed reads a public XML feed of your jobs (no partner account needed to host one).
               "https://docs.indeed.com/indeed-apply/xml-feed"),
    "google_jobs": ("Google for Jobs", "jsonld",
                    # Google indexes JobPosting structured data on the job page itself.
                    "https://developers.google.com/search/docs/appearance/structured-data/job-posting"),
    "linkedin": ("LinkedIn", "partner_feed",
                 # G6 (2026-10-23): LinkedIn's Basic Jobs XML feed (published guide). LinkedIn reads it only after
                 # it onboards the feed for your LinkedIn Page (company id + poster e-mail are mandatory for an ATS).
                 "https://learn.microsoft.com/en-us/linkedin/talent/job-postings/xml-feeds-development-guide"),
}
PARTNER_ONLY = ("linkedin",)          # need the board's own onboarding (credentials) before anything is listed
PARTNER_SETUP = {"linkedin": ("linkedin_company_id", "linkedin_poster_email")}
PARTNER_SETUP_TEXT = {"linkedin": "your LinkedIn company id and the poster e-mail (Hiring, Settings, Job boards)"}


def partner_configured(board: str, settings: dict | None) -> bool:
    return all(str((settings or {}).get(k) or "").strip() for k in PARTNER_SETUP.get(board, ()))

# Our employment types → Google JobPosting employmentType (the documented enum).
GOOGLE_EMPLOYMENT = {"full_time": "FULL_TIME", "part_time": "PART_TIME", "contract": "CONTRACTOR",
                     "seasonal": "TEMPORARY", "intern": "INTERN"}
# → Indeed <jobtype> free text, as the feed docs describe it.
INDEED_JOBTYPE = {"full_time": "fulltime", "part_time": "parttime", "contract": "contract",
                  "seasonal": "temporary", "intern": "internship"}


def board_state(board: str, enabled: bool, posting_live: bool, settings: dict | None = None) -> dict:
    """What the screen says about one board for one posting. Honest: for partner boards
    (LinkedIn) we only ever say "in your feed" — the board decides whether it lists the job."""
    label, how, docs = BOARDS[board]
    if how == "partner_feed":
        base = {"board": board, "label": label, "how": how, "docs": docs, "enabled": bool(enabled), "partner_only": True}
        if not partner_configured(board, settings):
            return {**base, "state": "needs_setup",
                    "message": f"{label}: partner integration not configured. Add {PARTNER_SETUP_TEXT[board]}, then ask "
                               f"{label} to onboard your feed. Until then use Export to copy the text and post it yourself."}
        if not enabled:
            return {**base, "state": "off", "message": f"Not in your {label} feed. Export still works."}
        if not posting_live:
            return {**base, "state": "hidden", "message": "In the feed list, but the posting is not live (publish it and keep the requisition open)."}
        return {**base, "state": "in_feed",
                "message": f"Listed in your {label} feed. {label} shows it only once it has onboarded the feed for your account"
                           + " (format: LinkedIn Basic Jobs XML)."}
    if not enabled:
        return {"board": board, "label": label, "how": how, "docs": docs, "enabled": False, "state": "off",
                "message": f"Not published to {label}."}
    if not posting_live:
        return {"board": board, "label": label, "how": how, "docs": docs, "enabled": True, "state": "hidden",
                "message": "Switched on, but the posting is not live (publish it and keep the requisition open)."}
    return {"board": board, "label": label, "how": how, "docs": docs, "enabled": True, "state": "live",
            "message": ("Listed in your Indeed XML feed." if board == "indeed"
                        else "JobPosting structured data is on the job page for Google to index.")}


def _cdata(text: str | None) -> str:
    t = (text or "").replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{t}]]>"


def _rfc822(d: datetime | None) -> str:
    d = d or datetime.now(timezone.utc)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.strftime("%a, %d %b %Y %H:%M:%S %z")


def indeed_xml(org_name: str, careers_url: str, jobs: list[dict], now: datetime | None = None) -> str:
    """The Indeed XML job feed (https://docs.indeed.com/indeed-apply/xml-feed): one <job> per live
    posting with the documented elements — title, date, referencenumber, url, company, city, state,
    country, description, jobtype (+ optional salary / category / experience). Text in CDATA."""
    out = ['<?xml version="1.0" encoding="utf-8"?>', "<source>",
           f"  <publisher>{_cdata(org_name)}</publisher>",
           f"  <publisherurl>{_cdata(careers_url)}</publisherurl>",
           f"  <lastBuildDate>{_rfc822(now)}</lastBuildDate>"]
    for j in jobs:
        out += ["  <job>",
                f"    <title>{_cdata(j.get('title'))}</title>",
                f"    <date>{_rfc822(j.get('published_at'))}</date>",
                f"    <referencenumber>{_cdata(j.get('reference'))}</referencenumber>",
                f"    <url>{_cdata(j.get('url'))}</url>",
                f"    <company>{_cdata(org_name)}</company>",
                f"    <city>{_cdata(j.get('city'))}</city>",
                f"    <state>{_cdata(j.get('state'))}</state>",
                "    <country>" + _cdata(j.get("country") or "CA") + "</country>",
                f"    <description>{_cdata(j.get('description'))}</description>",
                f"    <jobtype>{_cdata(INDEED_JOBTYPE.get(j.get('employment_type') or '', 'fulltime'))}</jobtype>"]
        if j.get("category"):
            out.append(f"    <category>{_cdata(j['category'])}</category>")
        salary = j.get("salary")
        if not salary and j.get("salary_min") and j.get("salary_max"):
            salary = f"CAD {int(j['salary_min'])} - {int(j['salary_max'])} per year"
        if salary:
            out.append(f"    <salary>{_cdata(salary)}</salary>")
        out.append("  </job>")
    out.append("</source>")
    return "\n".join(out)


# ── G6 (2026-10-23): partner feed — LinkedIn Basic Jobs XML ──────────────
LINKEDIN_JOBTYPE = {"full_time": "FULL_TIME", "part_time": "PART_TIME", "contract": "CONTRACT", "seasonal": "CONTRACT",
                    "intern": "INTERNSHIP"}


def _job_skills(job: dict) -> list[str]:
    from hiring_ai_logic import find_skills   # pure; lazy so this module stays import-light
    return find_skills("\n".join(str(job.get(k) or "") for k in ("requirements", "description")))


def _html_desc(job: dict) -> str:
    body = _html_paragraphs(job.get("description"))
    if job.get("requirements"):
        body += "<p><strong>What we are looking for</strong></p>" + _html_paragraphs(job["requirements"])
    return body


def linkedin_issues(job: dict) -> list[str]:
    """Why LinkedIn would reject this job (its published feed rules): description under 100
    characters, an apply URL that does not start with https://www, no city."""
    out = []
    if len(re.sub(r"\s+", " ", f"{job.get('description') or ''} {job.get('requirements') or ''}").strip()) < 100:
        out.append("description shorter than 100 characters")
    if not str(job.get("url") or "").startswith("https://www"):
        out.append("apply URL must start with https://www (set APP_BASE_URL to your public site)")
    if not job.get("city"):
        out.append("no city in the requisition location")
    return out


def linkedin_xml(org_name: str, careers_url: str, jobs: list[dict], company_id: str, poster_email: str,
                 now: datetime | None = None) -> str:
    """LinkedIn Basic Jobs XML feed, element names as in LinkedIn's XML Feeds Development Guide:
    source/lastBuildDate/publisherUrl/publisher/expectedJobCount; per job partnerJobId (<= 40 chars),
    company, title, description (HTML in CDATA, >= 100 chars), applyUrl, companyId, location, city,
    state, country, jobtype, workplaceTypes, skills (<= 10), listDate (MM/DD/YYYY), posterEmail.
    No salary: the requisition budget is internal."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<source>",
           f"  <lastBuildDate>{_rfc822(now)}</lastBuildDate>",
           f"  <publisherUrl>{_cdata(careers_url)}</publisherUrl>",
           f"  <publisher>{_cdata(org_name)}</publisher>",
           f"  <expectedJobCount>{_cdata(str(len(jobs)))}</expectedJobCount>"]
    for j in jobs:
        pub = j.get("published_at")
        list_date = pub.strftime("%m/%d/%Y") if isinstance(pub, datetime) else ""
        loc = ", ".join(x for x in (j.get("city"), j.get("state"), "Canada") if x)
        out += ["  <job>",
                f"    <partnerJobId>{_cdata(str(j.get('id') or '')[:40])}</partnerJobId>",
                f"    <company>{_cdata(org_name)}</company>",
                f"    <title>{_cdata(j.get('title'))}</title>",
                f"    <description>{_cdata(_html_desc(j))}</description>",
                f"    <applyUrl>{_cdata(j.get('url'))}</applyUrl>",
                f"    <companyId>{_cdata(company_id)}</companyId>",
                f"    <location>{_cdata(loc)}</location>",
                f"    <city>{_cdata(j.get('city'))}</city>",
                f"    <state>{_cdata(j.get('state'))}</state>",
                f"    <country>{_cdata(j.get('country') or 'CA')}</country>",
                f"    <jobtype>{_cdata(LINKEDIN_JOBTYPE.get(j.get('employment_type') or '', 'FULL_TIME'))}</jobtype>",
                f"    <workplaceTypes>{_cdata('On-site')}</workplaceTypes>"]
        skills = _job_skills(j)[:10]
        if skills:
            out.append("    <skills>" + "".join(f"<skill>{_cdata(s)}</skill>" for s in skills) + "</skills>")
        if list_date:
            out.append(f"    <listDate>{_cdata(list_date)}</listDate>")
        out += [f"    <posterEmail>{_cdata(poster_email)}</posterEmail>", "  </job>"]
    out.append("</source>")
    return "\n".join(out)


def google_jobs_jsonld(job: dict, org: dict, url: str, valid_days: int = 30) -> dict:
    """schema.org JobPosting the way Google documents it
    (https://developers.google.com/search/docs/appearance/structured-data/job-posting):
    title, description, datePosted, validThrough, employmentType, hiringOrganization,
    jobLocation (PostalAddress) and identifier. baseSalary only when a figure is public."""
    posted = job.get("published_at")
    posted_d = posted.date() if isinstance(posted, datetime) else (date.fromisoformat(str(posted)[:10]) if posted else local_today())
    org_obj = {"@type": "Organization", "name": org.get("name") or ""}
    if org.get("website"):
        org_obj["sameAs"] = org["website"]
    if org.get("logo_url"):
        org_obj["logo"] = org["logo_url"]
    address = {"@type": "PostalAddress", "addressCountry": (job.get("country") or "CA")}
    if job.get("city"):
        address["addressLocality"] = job["city"]
    if job.get("state"):
        address["addressRegion"] = job["state"]
    out = {
        "@context": "https://schema.org/", "@type": "JobPosting",
        "title": job.get("title") or "", "description": _html_paragraphs(job.get("description")),
        "datePosted": posted_d.isoformat(), "validThrough": (posted_d + timedelta(days=valid_days)).isoformat(),
        "employmentType": GOOGLE_EMPLOYMENT.get(job.get("employment_type") or "", "FULL_TIME"),
        "hiringOrganization": org_obj,
        "jobLocation": {"@type": "Place", "address": address},
        "identifier": {"@type": "PropertyValue", "name": org.get("name") or "", "value": job.get("reference") or ""},
        "url": url, "directApply": True,
    }
    if job.get("salary_min") and job.get("salary_max"):
        out["baseSalary"] = {"@type": "MonetaryAmount", "currency": "CAD",
                             "value": {"@type": "QuantitativeValue", "minValue": job["salary_min"],
                                       "maxValue": job["salary_max"], "unitText": "YEAR"}}
    return out


def _html_paragraphs(text: str | None) -> str:
    paras = [p.strip() for p in re.split(r"\n{2,}", text or "") if p.strip()]
    return "".join(f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in paras) or "<p></p>"


def export_text(board: str, job: dict, org_name: str, apply_url: str) -> str:
    """Copy-ready posting text for a partner-only board (LinkedIn)."""
    label = BOARDS[board][0]
    lines = [f"{job.get('title') or ''}", f"{org_name}",
             " · ".join(x for x in (job.get("location"), job.get("department"),
                                     (job.get("employment_type") or "").replace("_", " ")) if x),
             "", "About the role", job.get("description") or ""]
    if job.get("requirements"):
        lines += ["", "What we are looking for", job["requirements"]]
    lines += ["", f"Apply here: {apply_url}", f"Reference: {job.get('reference') or ''}",
              "", f"(Prepared for {label}. Paste this into the {label} posting form; the apply link brings "
                  f"candidates back to your careers page, so the source is tracked as {label}.)"]
    return "\n".join(lines)


def export_csv(jobs: list[dict], org_name: str) -> str:
    """One CSV for all live postings (title, reference, location, type, description, requirements, apply URL)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["title", "company", "reference", "department", "location", "employment_type", "published_on",
                "description", "requirements", "apply_url"])
    for j in jobs:
        p = j.get("published_at")
        w.writerow([j.get("title"), org_name, j.get("reference"), j.get("department"), j.get("location"),
                    j.get("employment_type"), (p.date().isoformat() if isinstance(p, datetime) else p or ""),
                    j.get("description"), j.get("requirements"), j.get("url")])
    return buf.getvalue()


def split_location(location: str | None) -> tuple[str | None, str | None]:
    """'Calgary, AB' → ('Calgary', 'AB'); 'Calgary' → ('Calgary', None)."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    if not parts:
        return None, None
    return parts[0], (parts[1] if len(parts) > 1 else None)


# ── Interview slots + candidate pick link ───────────────────────────────────
def _dt(v) -> datetime:
    if isinstance(v, datetime):
        d = v
    else:
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            raise RuleError("Each slot must be a date and time like 2026-10-20T10:30+05:30.", 422)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def clean_slots(raw: list, duration_min: int, now: datetime | None = None, max_slots: int = 10) -> list[dict]:
    """Normalise proposed slots: future, ordered, no overlaps, at most `max_slots`.
    Each item may be an ISO start (the end = start + duration) or {start, end}."""
    now = now or datetime.now(timezone.utc)
    try:
        duration_min = int(duration_min)
    except (TypeError, ValueError):
        raise RuleError("Duration must be a number of minutes.", 422)
    if not 10 <= duration_min <= 480:
        raise RuleError("Duration must be between 10 and 480 minutes.", 422)
    if not raw or not isinstance(raw, list):
        raise RuleError("Propose at least one slot.", 422)
    if len(raw) > max_slots:
        raise RuleError(f"Propose at most {max_slots} slots.", 422)
    out = []
    for item in raw:
        start = _dt(item.get("start") if isinstance(item, dict) else item)
        end = _dt(item.get("end")) if isinstance(item, dict) and item.get("end") else start + timedelta(minutes=duration_min)
        if end <= start:
            raise RuleError("A slot must end after it starts.", 422)
        if start <= now:
            raise RuleError("Every slot must be in the future.", 422)
        out.append({"start": start.isoformat(), "end": end.isoformat()})
    out.sort(key=lambda s: s["start"])
    for a, b in zip(out, out[1:]):
        if b["start"] < a["end"]:
            raise RuleError("Two slots overlap.", 422)
    return out


def slot_link_state(status: str, expires_at: datetime | None, now: datetime | None = None) -> str:
    """ok | picked | expired | closed — what the self-schedule link may still do."""
    now = now or datetime.now(timezone.utc)
    if status == "picked":
        return "picked"
    if status in ("cancelled",):
        return "closed"
    if status == "expired" or (expires_at is not None and expires_at < now):
        return "expired"
    return "ok" if status == "proposed" else "closed"


def pick_slot(slots: list[dict], index, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    try:
        i = int(index)
    except (TypeError, ValueError):
        raise RuleError("Pick one of the offered slots.", 422)
    if not 0 <= i < len(slots):
        raise RuleError("Pick one of the offered slots.", 422)
    s = slots[i]
    if _dt(s["start"]) <= now:
        raise RuleError("That slot has passed. Pick another one.", 409)
    return s


# ── Offer approval chain ────────────────────────────────────────────────────
REQUEST_TYPE_OFFER = "hiring_offer"


def default_offer_chain(hiring_manager_id: str | None, hiring_manager_name: str | None = None) -> list[dict]:
    """Until the D1 chain registry answers: reporting (hiring) manager → hr_admin.
    No hiring manager on the requisition = that step is skipped, not blocked."""
    steps = []
    if hiring_manager_id:
        steps.append({"kind": "manager", "employee_id": str(hiring_manager_id),
                      "label": f"Hiring manager{': ' + hiring_manager_name if hiring_manager_name else ''}"})
    steps.append({"kind": "role", "role": "hr_admin", "label": "HR admin"})
    return steps


def clean_chain(raw) -> list[dict]:
    """Validate a chain the registry (or a test) hands back. Unknown shapes are refused,
    so a bad registry answer falls back to the default instead of blocking offers."""
    out = []
    for s in raw or []:
        if not isinstance(s, dict):
            raise RuleError("Chain step is not an object", 500)
        kind = s.get("kind") or s.get("approver_kind")
        if kind == "manager" and (s.get("employee_id") or s.get("approver_employee_id")):
            out.append({"kind": "manager", "employee_id": str(s.get("employee_id") or s.get("approver_employee_id")),
                        "label": s.get("label") or "Manager"})
        elif kind == "role" and (s.get("role") or s.get("approver_role")):
            out.append({"kind": "role", "role": str(s.get("role") or s.get("approver_role")),
                        "label": s.get("label") or str(s.get("role") or s.get("approver_role"))})
        elif kind == "user" and (s.get("user_id") or s.get("approver_user_id")):
            out.append({"kind": "user", "user_id": str(s.get("user_id") or s.get("approver_user_id")),
                        "label": s.get("label") or "Named approver"})
        else:
            raise RuleError(f"Unknown chain step {s!r}", 500)
    if not out:
        raise RuleError("Empty chain", 500)
    return out


_ROLE_FAMILY = {"hr": {"hr", "hr_admin", "hr_manager"}, "hr_admin": {"hr_admin"}, "org_admin": {"org_admin"},
                "payroll": {"payroll", "payroll_admin"}, "finance": {"finance", "finance_admin"},
                "manager": {"manager"}}


def step_may_decide(step: dict, actor_role: str, actor_user_id: str, actor_employee_id: str) -> bool:
    """Who may decide one chain step. An org admin / superadmin may always act (a one-admin
    company is never stuck) — but is shown as such in the audit."""
    if actor_role in ("org_admin", "superadmin"):
        return True
    kind = step.get("approver_kind") or step.get("kind")
    if kind == "manager":
        return bool(actor_employee_id) and str(step.get("approver_employee_id") or step.get("employee_id")) == str(actor_employee_id)
    if kind == "role":
        want = str(step.get("approver_role") or step.get("role") or "")
        return actor_role == want or actor_role in _ROLE_FAMILY.get(want, set())
    if kind == "user":
        return bool(actor_user_id) and str(step.get("approver_user_id") or step.get("user_id")) == str(actor_user_id)
    return False


def chain_after_decision(steps: list[dict], decision: str) -> tuple[list[dict], str]:
    """Apply approve / reject to the first pending step. Returns (steps, offer_status) where
    offer_status is 'approved' (all done), 'draft' (rejected: back to HR) or 'pending'."""
    steps = [dict(s) for s in steps]
    cur = next((s for s in steps if s["status"] == "pending"), None)
    if cur is None:
        raise RuleError("There is no step waiting for a decision.")
    if decision == "reject":
        cur["status"] = "rejected"
        for s in steps:
            if s["status"] in ("waiting", "pending"):
                s["status"] = "skipped"
        return steps, "draft"
    cur["status"] = "approved"
    nxt = next((s for s in steps if s["status"] == "waiting"), None)
    if nxt is None:
        return steps, "approved"
    nxt["status"] = "pending"
    return steps, "pending"


# ── Scorecards ──────────────────────────────────────────────────────────────
DEFAULT_COMPETENCIES = [
    {"key": "role_skills", "label": "Role skills", "weight": 2, "description": "Can do the job as described."},
    {"key": "problem_solving", "label": "Problem solving", "weight": 1, "description": "Works through an unfamiliar problem."},
    {"key": "communication", "label": "Communication", "weight": 1, "description": "Clear, listens, asks."},
    {"key": "ownership", "label": "Ownership", "weight": 1, "description": "Takes responsibility for outcomes."},
    {"key": "team_fit", "label": "Working with others", "weight": 1, "description": "Collaborates, gives and takes feedback."},
]


def clean_competencies(raw) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise RuleError("Add at least one competency.", 422)
    if len(raw) > 20:
        raise RuleError("At most 20 competencies.", 422)
    out, seen = [], set()
    for c in raw:
        label = str((c or {}).get("label") or "").strip()[:120]
        if not label:
            raise RuleError("Every competency needs a name.", 422)
        key = re.sub(r"[^a-z0-9]+", "_", (str(c.get("key") or label)).lower()).strip("_")[:60] or f"c{len(out) + 1}"
        if key in seen:
            raise RuleError(f"Competency '{label}' is listed twice.", 422)
        seen.add(key)
        w = c.get("weight", 1)
        try:
            w = 1.0 if w in (None, "") else float(w)
        except (TypeError, ValueError):
            raise RuleError("Weight must be a number.", 422)
        if not 0 < w <= 10:
            raise RuleError("Weight must be between 0 and 10.", 422)
        out.append({"key": key, "label": label, "weight": w, "description": str(c.get("description") or "").strip()[:400]})
    return out


def clean_scores(body_scores, competencies: list[dict]) -> list[dict]:
    """Ratings for every competency of the template (1..5, note optional)."""
    by_key = {s.get("key"): s for s in (body_scores or []) if isinstance(s, dict)}
    out = []
    for c in competencies:
        s = by_key.get(c["key"])
        try:
            r = int((s or {}).get("rating"))
        except (TypeError, ValueError):
            raise RuleError(f"Rate '{c['label']}' from 1 to 5.", 422)
        if not 1 <= r <= 5:
            raise RuleError(f"Rate '{c['label']}' from 1 to 5.", 422)
        out.append({"competency_key": c["key"], "label": c["label"], "weight": c["weight"], "rating": r,
                    "note": (str((s or {}).get("note") or "").strip()[:1000] or None)})
    return out


def weighted_score(scores: list[dict]) -> float | None:
    tot = sum(float(s.get("weight") or 1) for s in scores)
    if not tot:
        return None
    return round(sum(float(s.get("weight") or 1) * int(s["rating"]) for s in scores) / tot, 2)


def aggregate_scorecard(feedback: list[dict], answers: list[dict]) -> dict:
    """The candidate view: per competency average across interviewers, per interviewer
    weighted score, recommendation tally and an overall weighted average."""
    by_comp: dict[str, list] = defaultdict(list)
    label_of, weight_of = {}, {}
    by_fb: dict[str, list] = defaultdict(list)
    for a in answers:
        by_comp[a["competency_key"]].append(int(a["rating"]))
        label_of[a["competency_key"]] = a.get("label") or a["competency_key"]
        weight_of[a["competency_key"]] = float(a.get("weight") or 1)
        by_fb[str(a["feedback_id"])].append(a)
    comps = [{"key": k, "label": label_of[k], "weight": weight_of[k], "avg": round(sum(v) / len(v), 2), "n": len(v)}
             for k, v in by_comp.items()]
    comps.sort(key=lambda c: c["label"])
    rec = defaultdict(int)
    per_iv = []
    for f in feedback:
        rec[f.get("recommendation") or "hold"] += 1
        sc = by_fb.get(str(f["id"]), [])
        per_iv.append({"feedback_id": str(f["id"]), "interviewer_name": f.get("interviewer_name"),
                       "interview_id": str(f.get("interview_id")), "rating": f.get("rating"),
                       "recommendation": f.get("recommendation"), "weighted_score": weighted_score(sc) if sc else None,
                       "scores": [{"key": a["competency_key"], "label": a.get("label"), "rating": a["rating"], "note": a.get("note")} for a in sc]})
    overall = None
    if comps:
        tot = sum(c["weight"] for c in comps)
        overall = round(sum(c["weight"] * c["avg"] for c in comps) / tot, 2) if tot else None
    n = len(feedback)
    return {"competencies": comps, "interviewers": per_iv, "overall_weighted": overall,
            "recommendations": {"hire": rec.get("hire", 0), "no_hire": rec.get("no_hire", 0), "hold": rec.get("hold", 0)},
            "hire_share_pct": round(100 * rec.get("hire", 0) / n, 1) if n else None, "feedback_count": n}


def blind_label(application_id: str) -> str:
    """Bias guard: a stable, name-free label for a candidate ('Candidate K7Q2')."""
    h = hashlib.sha256(str(application_id).encode()).hexdigest()[:4].upper()
    return f"Candidate {h}"


# ── Referrals + bonus ───────────────────────────────────────────────────────
def referral_status_for_stage(stage: str | None) -> str:
    if stage == "joined":
        return "hired"
    if stage in ("rejected", "withdrawn"):
        return "closed"
    return "in_process" if stage else "submitted"


def bonus_due_on(joined_at, payable_after_days: int | None) -> date:
    if isinstance(joined_at, datetime):                  # a stored UTC instant -> the org's business date
        from shared.utils.org_time import to_local_date  # (late evening Eastern time is already "tomorrow" in UTC)
        d = to_local_date(joined_at)
    else:
        d = joined_at
    return d + timedelta(days=int(payable_after_days or 0))


def pay_month_for(due_on: date) -> str:
    return f"{due_on.year:04d}-{due_on.month:02d}"


def bonus_state(joined: bool, due_on: date | None, today: date, amount: float | None) -> str:
    """none (no bonus) | waiting_join | waiting_days | due."""
    if not amount or float(amount) <= 0:
        return "none"
    if not joined or due_on is None:
        return "waiting_join"
    return "due" if due_on <= today else "waiting_days"


def clean_referral(body: dict) -> dict:
    first = str(body.get("first_name") or "").strip()[:100]
    if not first:
        raise RuleError("The person's first name is required.", 422)
    email = str(body.get("email") or "").strip()[:200]
    phone = str(body.get("phone") or "").strip()[:30]
    if not email and not phone:
        raise RuleError("Give an e-mail or a phone number so HR can reach the person.", 422)
    if email and ("@" not in email or "." not in email.split("@")[-1] or " " in email):
        raise RuleError("Type a valid e-mail address.", 422)
    link = str(body.get("link_url") or "").strip()[:400]
    if link and not re.match(r"^https?://", link, re.I):
        raise RuleError("The link must start with http:// or https://", 422)
    return {"first_name": first, "last_name": str(body.get("last_name") or "").strip()[:100] or None,
            "email": email or None, "phone": phone or None, "link_url": link or None,
            "note": str(body.get("note") or "").strip()[:1000] or None}


def clean_referral_candidate(body: dict) -> dict:
    """Extra screening facts from a referral that live on the candidate record (hire_candidates)."""
    return {"current_employer": str(body.get("current_employer") or "").strip()[:200] or None,
            "city": str(body.get("city") or "").strip()[:100] or None}


# ── Candidate communications ────────────────────────────────────────────────
COMMS_EVENTS = {
    "applied": "Application received", "screening": "Moved to screening", "interview": "Moved to interview",
    "selected": "Selected", "offer": "Offer stage", "rejected": "Not taken forward", "on_hold": "Put on hold",
    "interview_scheduled": "Interview scheduled", "interview_reminder": "Interview reminder",
    "slot_pick": "Pick an interview slot",
}
COMMS_PLACEHOLDERS = ("first_name", "job_title", "org_name", "stage", "interview_time", "interview_mode",
                      "join_link", "link", "round_name")
DEFAULT_COMMS = {
    "applied": ("Thank you for applying to {{org_name}}",
                "Hi {{first_name}},\n\nThank you for applying for the {{job_title}} role at {{org_name}}. "
                "We have received your application and will be in touch if your profile fits.\n\n{{org_name}} hiring team"),
    "screening": ("Your application at {{org_name}}: screening",
                  "Hi {{first_name}},\n\nYour application for {{job_title}} is now being screened. "
                  "We will write again with the next step.\n\n{{org_name}} hiring team"),
    "interview": ("Your application at {{org_name}}: interview stage",
                  "Hi {{first_name}},\n\nGood news: your application for {{job_title}} has moved to the interview stage. "
                  "We will send the schedule shortly.\n\n{{org_name}} hiring team"),
    "selected": ("Your application at {{org_name}}: selected",
                 "Hi {{first_name}},\n\nWe are pleased to tell you that you have been selected for {{job_title}}. "
                 "The offer details will follow.\n\n{{org_name}} hiring team"),
    "rejected": ("Your application at {{org_name}}",
                 "Hi {{first_name}},\n\nThank you for your interest in the {{job_title}} role. We will not be taking your "
                 "application further this time. We wish you well in your search.\n\n{{org_name}} hiring team"),
    "on_hold": ("Your application at {{org_name}}: on hold",
                "Hi {{first_name}},\n\nYour application for {{job_title}} is on hold for now. We will write when it moves.\n\n"
                "{{org_name}} hiring team"),
    "interview_scheduled": ("Interview: {{job_title}} at {{org_name}}",
                            "Hi {{first_name}},\n\nYour {{round_name}} for {{job_title}} is scheduled on {{interview_time}} "
                            "({{interview_mode}}).\n{{join_link}}\n\n{{org_name}} hiring team"),
    "interview_reminder": ("Reminder: interview on {{interview_time}}",
                           "Hi {{first_name}},\n\nA reminder of your {{round_name}} for {{job_title}} on {{interview_time}} "
                           "({{interview_mode}}).\n{{join_link}}\n\n{{org_name}} hiring team"),
    "slot_pick": ("Pick a time for your interview: {{job_title}}",
                  "Hi {{first_name}},\n\nPlease pick a time for your {{round_name}} for {{job_title}} here:\n{{link}}\n\n"
                  "{{org_name}} hiring team"),
}
_PH = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render(text: str | None, values: dict) -> str:
    def sub(m):
        k = m.group(1)
        return str(values.get(k, "") or "") if k in COMMS_PLACEHOLDERS else m.group(0)
    return _PH.sub(sub, text or "")


def unknown_placeholders(text: str | None) -> list[str]:
    return sorted({m.group(1) for m in _PH.finditer(text or "") if m.group(1) not in COMMS_PLACEHOLDERS})


def mask_contact(v: str | None) -> str:
    if not v:
        return ""
    if "@" in v:
        u, _, d = v.partition("@")
        return (u[:2] + "•" * max(len(u) - 2, 1)) + "@" + d
    digits = re.sub(r"\D", "", v)
    return "•" * max(len(digits) - 4, 0) + digits[-4:]


def comms_decision(channel: str, opt_in: bool, address: str | None, configured: bool, enabled: bool) -> tuple[str, str | None]:
    """The honest outcome BEFORE anything is sent: sent is only written after the provider accepted."""
    if not enabled:
        return "disabled", "This message is switched off in Hiring, Messages."
    if not opt_in:
        return "opted_out", "The candidate did not agree to this channel."
    if not address:
        return "no_address", "No e-mail address on record." if channel == "email" else "No phone number on record."
    if not configured:
        return "not_configured", ("No SMTP server is set up on this installation." if channel == "email"
                                  else "WhatsApp is not configured (Integrations, WhatsApp).")
    return "ok", None


# ── Captcha hook (same env as sign-up: LEXORA_CAPTCHA_PROVIDER / SECRET / SITE_KEY) ──
CAPTCHA_VERIFY = {"turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                  "hcaptcha": "https://api.hcaptcha.com/siteverify"}


def captcha_info() -> dict:
    prov = (os.getenv("LEXORA_CAPTCHA_PROVIDER") or "").strip().lower()
    ok = prov in CAPTCHA_VERIFY and bool((os.getenv("LEXORA_CAPTCHA_SECRET") or "").strip())
    return {"configured": ok, "provider": prov if ok else None,
            "site_key": ((os.getenv("LEXORA_CAPTCHA_SITE_KEY") or "").strip() or None) if ok else None}


async def verify_captcha(token: str | None, ip: str | None = None, *, transport=None) -> bool:
    """True when no captcha is configured (nothing to check) or the provider said success.
    Fails CLOSED when a captcha IS configured and the token is missing or refused."""
    info = captcha_info()
    if not info["configured"]:
        return True
    if not token:
        return False
    import httpx
    data = {"secret": os.getenv("LEXORA_CAPTCHA_SECRET", "").strip(), "response": token}
    if ip:
        data["remoteip"] = ip
    try:
        async with httpx.AsyncClient(timeout=6.0, transport=transport) as c:
            r = await c.post(CAPTCHA_VERIFY[info["provider"]], data=data)
        return bool(r.status_code == 200 and r.json().get("success"))
    except Exception:                                    # noqa: BLE001
        return False


# ── Signed state for the calendar OAuth round-trip ──────────────────────────
def _secret() -> bytes:
    return (os.getenv("JWT_SECRET") or "dev-only-insecure-key-change-me").encode()


def sign_state(payload: dict, ttl_sec: int = 900, now: float | None = None) -> str:
    body = dict(payload)
    body["exp"] = int((now if now is not None else time.time()) + ttl_sec)
    raw = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def verify_state(token: str | None, now: float | None = None) -> dict | None:
    """The payload, or None when the signature is wrong or the state expired."""
    if not token or "." not in token:
        return None
    raw, _, sig = token.rpartition(".")
    want = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(want, sig):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, TypeError):
        return None
    if int(body.get("exp") or 0) < (now if now is not None else time.time()):
        return None
    return body


# ── Files on the volume ─────────────────────────────────────────────────────
def files_root() -> str:
    return os.getenv("LEXORA_FILES_DIR") or os.path.join(os.getcwd(), "files")


def resume_path(org_id: str, candidate_id: str, doc_id: str, ext: str) -> str:
    """Relative path under the files dir; no user input in it (ids + sniffed extension only)."""
    ext = re.sub(r"[^a-z0-9]", "", (ext or "bin").lower())[:8]
    return os.path.join(str(org_id), "hiring", str(candidate_id), f"{doc_id}.{ext}")


def write_file(rel: str, content: bytes) -> str | None:
    """Best-effort: the DB copy is the source of truth; the file copy is for the volume."""
    try:
        full = os.path.join(files_root(), rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)
        return rel.replace(os.sep, "/")
    except OSError:
        return None
