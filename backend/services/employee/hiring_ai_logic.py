"""Hiring G6 (2026-10-23) — résumé parsing, job-description drafts, candidate-to-job fit.
Pure Python (no DB, no HTTP), so every rule is unit-tested.

Deterministic first: everything here works with NO AI key. The AI provider (hiring_ai_provider)
may ADD skills / a summary / a second opinion, never replace the rules, and only ever sees
de-identified text (contacts, name and money removed here; the AI platform's Shield masks the rest).

Fairness: the fit score uses skills, years of experience, degree level and notice period only —
never name, gender, age, photo, address, college or employer names. It is ADVISORY: nothing is
moved or rejected because of it; a person decides.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date
from html import unescape
from shared.utils.org_time import local_today  # org-TZ business date (UTC store, local today)

MAX_TEXT = 60000

# ── text extraction ─────────────────────────────────────────────────────────
def extract_text(content: bytes, ext: str) -> str:
    """Plain text from a PDF (pypdf) or a DOCX (the document XML). Images → '' (no OCR)."""
    ext = (ext or "").lower()
    try:
        if ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            text = "\n".join((p.extract_text() or "") for p in reader.pages[:15])
        elif ext == "docx":
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>|<w:br\s*/>|<w:tab\s*/>", "\n", xml)
            text = unescape(re.sub(r"<[^>]+>", "", xml))
        else:
            return ""
    except Exception:                                  # noqa: BLE001  (broken / encrypted file → nothing to parse)
        return ""
    text = text.replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:MAX_TEXT]


# ── vocabulary (our own list; matched as whole words, case-insensitive) ─────
SKILLS = sorted({
    # software
    "python", "java", "javascript", "typescript", "react", "angular", "vue", "node.js", "django", "fastapi", "flask", "spring boot",
    "c++", "c#", ".net", "go", "golang", "rust", "kotlin", "swift", "php", "ruby", "scala", "sql", "postgresql", "mysql", "mongodb",
    "redis", "kafka", "docker", "kubernetes", "aws", "azure", "gcp", "terraform", "linux", "git", "ci/cd", "jenkins", "rest api",
    "graphql", "microservices", "html", "css", "tailwind", "android", "ios", "flutter", "react native", "selenium", "playwright",
    "machine learning", "deep learning", "nlp", "pandas", "numpy", "tensorflow", "pytorch", "power bi", "tableau", "excel",
    "data analysis", "data engineering", "spark", "hadoop", "airflow", "etl", "snowflake", "devops", "sre", "security", "networking",
    "testing", "qa", "automation testing", "manual testing", "agile", "scrum", "jira", "figma", "ui design", "ux design",
    # finance / accounts / payroll
    "accounting", "quickbooks", "sage", "cpa", "income tax", "payroll", "statutory compliance", "bank reconciliation", "accounts payable",
    "accounts receivable", "financial reporting", "budgeting", "audit", "sap", "sap fico", "ifrs", "aspe", "costing",
    "mis reporting", "treasury",
    # hr
    "recruitment", "talent acquisition", "onboarding", "employee engagement", "hr operations", "hrms", "labour law",
    "performance management", "compensation", "learning and development", "employment standards", "health and safety",
    # sales / marketing / service
    "sales", "b2b sales", "b2c sales", "inside sales", "field sales", "business development", "key account management", "crm",
    "salesforce", "lead generation", "negotiation", "digital marketing", "seo", "sem", "content writing", "social media",
    "google ads", "email marketing", "market research", "customer support", "customer service", "telecalling", "voice process",
    # operations / other
    "supply chain", "logistics", "procurement", "inventory management", "warehouse", "vendor management", "project management",
    "pmp", "six sigma", "lean", "quality control", "operations", "production planning", "autocad", "solidworks", "plc",
    "communication", "leadership", "team management", "stakeholder management", "presentation", "english", "french", "bilingual",
})
_ALIASES = {"js": "javascript", "ts": "typescript", "reactjs": "react", "react.js": "react", "nodejs": "node.js", "node": "node.js",
            "postgres": "postgresql", "k8s": "kubernetes", "ml": "machine learning", "ms excel": "excel", "advanced excel": "excel",
            "golang": "go", "powerbi": "power bi", "tally erp": "tally", "tally prime": "tally", "rest apis": "rest api",
            "restful api": "rest api", "restful apis": "rest api", "microservice": "microservices"}


def _skill_re(s: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9+#.])" + re.escape(s) + r"(?![a-z0-9+#])", re.IGNORECASE)


_SKILL_PATTERNS = [(s, _skill_re(s)) for s in SKILLS] + [(v, _skill_re(k)) for k, v in _ALIASES.items()]


def find_skills(text: str) -> list[str]:
    t = text or ""
    found = []
    for name, pat in _SKILL_PATTERNS:
        if name not in found and pat.search(t):
            found.append(name)
    return sorted(found)


DEGREES = [  # (pattern, label, level)  level: 1 school · 2 diploma · 3 bachelor · 4 master · 5 doctorate · 4 professional
    (r"\bph\.?\s?d\b|\bdoctorate\b", "PhD", 5),
    (r"\bm\.?\s?tech\b|\bm\.?e\.?\b(?=\s|,|$)", "M.Tech / M.E.", 4), (r"\bmba\b|\bpgdm\b", "MBA / PGDM", 4),
    (r"\bmca\b", "MCA", 4), (r"\bm\.?\s?sc\b", "M.Sc", 4), (r"\bm\.?\s?com\b", "M.Com", 4), (r"\bm\.?a\.?\b(?=\s|,|$)", "M.A.", 4),
    (r"\bchartered accountant\b|\bca\b(?=\s*(inter|final|qualified|,|$))|\bicwa\b|\bcma\b|\bacs\b", "CA / CMA / CS", 4),
    (r"\bb\.?\s?tech\b|\bb\.?e\.?\b(?=\s|,|$)", "B.Tech / B.E.", 3), (r"\bbca\b", "BCA", 3), (r"\bb\.?\s?sc\b", "B.Sc", 3),
    (r"\bb\.?\s?com\b", "B.Com", 3), (r"\bbba\b|\bbbm\b", "BBA", 3), (r"\bb\.?a\.?\b(?=\s|,|$)", "B.A.", 3),
    (r"\bbachelor", "Bachelor's degree", 3), (r"\bmaster", "Master's degree", 4),
    (r"\bdiploma\b|\biti\b", "Diploma / ITI", 2), (r"\b12th\b|\bhsc\b|\bpuc\b|\bintermediate\b", "Class 12", 1),
    (r"\b10th\b|\bssc\b|\bmatriculation\b", "Class 10", 1),
]
_DEGREE_RE = [(re.compile(p, re.IGNORECASE), label, lvl) for p, label, lvl in DEGREES]
LEVEL_LABEL = {1: "school", 2: "diploma", 3: "bachelor's", 4: "master's / professional", 5: "doctorate"}


def find_education(text: str) -> list[dict]:
    out, seen = [], set()
    for line in (text or "").split("\n"):
        for pat, label, lvl in _DEGREE_RE:
            if label not in seen and pat.search(line):
                seen.add(label)
                out.append({"degree": label, "level": lvl, "line": line.strip()[:160]})
    return sorted(out, key=lambda d: -d["level"])


def highest_level(education: list[dict]) -> int:
    return max([int(e.get("level") or 0) for e in education or []] or [0])


# ── field parsers ───────────────────────────────────────────────────────────
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\d+])(?:\+?1[\s.-]?)?(\(?[2-9]\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{4})(?!\d)")   # NANP (Canada)
URL_RE = re.compile(r"(https?://\S+|www\.\S+|linkedin\.com/\S+)", re.IGNORECASE)
_NOT_NAME = re.compile(r"resume|curriculum|vitae|\bcv\b|profile|address|email|phone|mobile|objective|summary|@|\d", re.IGNORECASE)
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december"
_RANGE_RE = re.compile(rf"(?:(?:{_MONTHS})[a-z]*[\s.'-]*)?((?:19|20)\d{{2}})\s*(?:-|–|—|to|till)\s*(?:(?:{_MONTHS})[a-z]*[\s.'-]*)?"
                       r"((?:19|20)\d{2}|present|current|now|till date|date)", re.IGNORECASE)
_YEARS_RE = re.compile(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)", re.IGNORECASE)
_NOTICE_RE = re.compile(r"notice\s*(?:period)?\s*(?:of|is|:|-)?\s*(\d{1,3})\s*(days?|months?|weeks?)", re.IGNORECASE)
_CTC_RE = r"(?:ctc|salary|package|compensation)"


def find_name(text: str) -> tuple[str | None, str | None]:
    for line in (text or "").split("\n")[:8]:
        s = line.strip().strip("|•-:").strip()
        if not s or _NOT_NAME.search(s):
            continue
        words = [w for w in re.split(r"\s+", s) if w]
        if 2 <= len(words) <= 4 and all(re.fullmatch(r"[A-Za-z][A-Za-z.'-]*", w) for w in words):
            words = [w.capitalize() if w.isupper() or w.islower() else w for w in words]
            return words[0], " ".join(words[1:])
    return None, None


def find_experience_years(text: str, today: date | None = None) -> tuple[float | None, str | None]:
    """Stated years ('6+ years of experience') win; else the union of year ranges in the text."""
    today = today or local_today()
    best, src = None, None
    for line in (text or "").split("\n"):
        if re.search(r"experience|exp\b", line, re.IGNORECASE):
            for m in _YEARS_RE.finditer(line):
                v = float(m.group(1))
                if v <= 45 and (best is None or v > best):
                    best, src = v, line.strip()[:160]
    if best is not None:
        return best, src
    spans = []
    for m in _RANGE_RE.finditer(text or ""):
        a = int(m.group(1))
        b_raw = m.group(2).lower()
        b = today.year if not b_raw[:1].isdigit() else int(b_raw)
        if 1970 <= a <= b <= today.year and b - a <= 45:
            spans.append((a, b))
    if not spans:
        return None, None
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    total = sum(b - a for a, b in merged)
    return (float(total), "worked out from the dates of the jobs listed") if total > 0 else (None, None)


def find_notice_days(text: str) -> tuple[int | None, str | None]:
    t = text or ""
    if re.search(r"immediate(ly)?\s*(joiner|joining|available)|available immediately", t, re.IGNORECASE):
        return 0, "immediate joiner"
    m = _NOTICE_RE.search(t)
    if not m:
        return None, None
    n, unit = int(m.group(1)), m.group(2).lower()
    days = n * 30 if unit.startswith("month") else n * 7 if unit.startswith("week") else n
    return (days if days <= 365 else None), m.group(0).strip()


def _amount(num: str, unit: str | None) -> tuple[float | None, str | None]:
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return None, None
    u = (unit or "").lower().strip(". ")
    if u in ("k",):
        return round(v * 1000, 2), "read as thousands per year"
    if u in ("/hr", "/h", "per hour", "an hour", "hourly"):
        return round(v * 40 * 52, 2), "hourly rate × 40 h × 52 weeks"
    if v >= 10000:
        return round(v, 2), None
    if 0 < v <= 500:
        return round(v * 1000, 2), "no unit written: read as thousands of dollars per year"
    return None, None


def find_ctc(text: str, which: str) -> tuple[float | None, str | None]:
    """which = current | expected. Only where stated; annual dollars (CAD)."""
    word = r"(?:current|present|existing)" if which == "current" else r"(?:expected|expectation)"
    pat = re.compile(word + r"\s*" + _CTC_RE + r"\s*(?:is|:|-|of)?\s*(?:ca\$|c\$|cad|\$)?\s*([\d,]+(?:\.\d+)?)\s*"
                     r"(k\b|/hr\b|/h\b|per hour|an hour|hourly)?", re.IGNORECASE)
    m = pat.search(text or "")
    if not m:
        return None, None
    v, note = _amount(m.group(1), m.group(2))
    return v, (m.group(0).strip()[:120] + (f" ({note})" if note else "")) if v is not None else None


def _labelled(text: str, labels: tuple[str, ...]) -> str | None:
    for lab in labels:
        m = re.search(rf"{lab}\s*[:\-]\s*(.+)", text or "", re.IGNORECASE)
        if m:
            return m.group(1).strip()[:200] or None
    return None


def parse_resume(text: str, today: date | None = None) -> dict:
    """Structured candidate from résumé text. Each field carries where it was found (`evidence`),
    so HR checks it before saving. Nothing here is a decision."""
    t = text or ""
    first, last = find_name(t)
    emails = EMAIL_RE.findall(t)
    phones = [re.sub(r"[\s().-]", "", p) for p in PHONE_RE.findall(t)]
    years, ysrc = find_experience_years(t, today)
    notice, nsrc = find_notice_days(t)
    cur, csrc = find_ctc(t, "current")
    exp, esrc = find_ctc(t, "expected")
    edu = find_education(t)
    skills = find_skills(t)
    out = {
        "first_name": first, "last_name": last, "email": emails[0].lower() if emails else None,
        "phone": phones[0] if phones else None,
        "current_title": _labelled(t, ("current designation", "designation", "current role", "position")),
        "current_employer": _labelled(t, ("current company", "current employer", "current organisation", "current organization")),
        "city": _labelled(t, ("current location", "location", "city")),
        "skills": skills, "experience_years": years, "education": edu, "notice_period_days": notice,
        "current_ctc": cur, "expected_ctc": exp,
        "evidence": {k: v for k, v in {"experience_years": ysrc, "notice_period_days": nsrc, "current_ctc": csrc, "expected_ctc": esrc}.items() if v},
        "warnings": [],
    }
    if not t.strip():
        out["warnings"].append("No text could be read from the file (a scanned image?). Type the details in.")
    if not skills:
        out["warnings"].append("No known skills were found. Add them by hand.")
    if len(set(emails)) > 1:
        out["warnings"].append("More than one e-mail address in the résumé; the first was used.")
    return out


def strip_for_ai(text: str, parsed: dict) -> str:
    """De-identify BEFORE the text leaves this service: drop lines with e-mail / phone / links /
    address labels, blank out the parsed name, cut money lines. The AI platform Shield masks the rest."""
    keep = []
    names = [n for n in (parsed.get("first_name"), parsed.get("last_name")) if n]
    for line in (text or "").split("\n"):
        if EMAIL_RE.search(line) or PHONE_RE.search(line) or URL_RE.search(line):
            continue
        if re.search(r"address|date of birth|\bdob\b|gender|marital|father|\bsin\b|social insurance|passport|" + _CTC_RE, line, re.IGNORECASE):
            continue
        for n in names:
            line = re.sub(re.escape(n), "[CANDIDATE]", line, flags=re.IGNORECASE)
        keep.append(line)
    return "\n".join(keep)[:20000]


def merge_ai(parsed: dict, ai: dict | None) -> dict:
    """AI may ADD skills and a summary / current title; it never overrides contacts or money."""
    if not ai:
        return parsed
    out = dict(parsed)
    extra = [str(s).strip().lower()[:60] for s in (ai.get("skills") or []) if str(s).strip()]
    out["skills"] = sorted(set(parsed.get("skills") or []) | set(extra))[:60]
    if ai.get("summary"):
        out["summary"] = str(ai["summary"])[:800]
    if not out.get("current_title") and ai.get("current_title"):
        out["current_title"] = str(ai["current_title"])[:200]
    if out.get("experience_years") is None and isinstance(ai.get("experience_years"), (int, float)) and 0 <= ai["experience_years"] <= 45:
        out["experience_years"] = float(ai["experience_years"])
        out.setdefault("evidence", {})["experience_years"] = "suggested by AI (no years were written in the résumé)"
    return out


def parse_json_reply(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


# ── job description drafts ──────────────────────────────────────────────────
ROLE_LIBRARY = [  # (keywords, responsibilities, default skills) — our own wording
    (("engineer", "developer", "programmer", "sde", "software"),
     ["Design, build and test features end to end with the team", "Review code and keep quality high", "Fix production issues and improve reliability",
      "Write clear technical notes for others"], ["git", "sql", "rest api", "testing"]),
    (("data", "analyst", "analytics", "scientist"),
     ["Collect, clean and analyse data to answer business questions", "Build dashboards and reports people use", "Explain findings in plain language"],
     ["sql", "excel", "data analysis", "power bi"]),
    (("sales", "business development", "account manager", "bdm", "bde"),
     ["Find and qualify new customers", "Run meetings, demos and negotiations", "Keep the CRM up to date and hit the monthly target"],
     ["sales", "negotiation", "crm", "communication"]),
    (("account", "finance", "payroll", "tax", "audit"),
     ["Keep the books accurate and on time", "Prepare GST/HST, source-deduction and payroll reconciliations", "Support month-end close and audits"],
     ["accounting", "quickbooks", "payroll", "excel"]),
    (("hr", "human resource", "recruit", "talent"),
     ["Run hiring from requisition to joining", "Handle onboarding, records and employee queries", "Keep policies and statutory records current"],
     ["recruitment", "onboarding", "hr operations", "communication"]),
    (("support", "customer", "service", "helpdesk"),
     ["Answer customer questions by phone, chat and e-mail", "Log and follow every issue to closure", "Share recurring problems with the product team"],
     ["customer support", "communication", "crm"]),
    (("designer", "design", "ux", "ui"),
     ["Turn requirements into flows, wireframes and polished screens", "Test designs with users and iterate", "Keep the design system consistent"],
     ["figma", "ui design", "ux design"]),
    (("marketing", "seo", "content", "brand"),
     ["Plan and run campaigns across channels", "Track results and adjust spend", "Write and review content"],
     ["digital marketing", "seo", "content writing", "social media"]),
    (("operations", "supply", "logistics", "warehouse", "procurement"),
     ["Plan and run daily operations to agreed service levels", "Work with vendors on cost, quality and timelines", "Track stock and fix bottlenecks"],
     ["operations", "vendor management", "inventory management", "excel"]),
]
EMPLOYMENT_LABEL = {"full_time": "Full-time", "part_time": "Part-time", "contract": "Contract", "seasonal": "Seasonal", "intern": "Internship"}


def draft_jd(role: dict) -> dict:
    """Deterministic draft. role = {designation, department, location, employment_type, skills[],
    experience_min, experience_max, responsibilities[] (optional hints), about (optional)}.
    HR edits the draft; nothing is published by this function."""
    title = str(role.get("designation") or "").strip() or "the role"
    t = title.lower()
    lib = next((r for r in ROLE_LIBRARY if any(k in t for k in r[0])), None)
    resp = [str(x).strip() for x in role.get("responsibilities") or [] if str(x).strip()] or (lib[1] if lib else [
        "Own the work agreed for this role and deliver it on time", "Work closely with the team and other departments",
        "Keep records and reports up to date"])
    skills = sorted({str(s).strip().lower() for s in role.get("skills") or [] if str(s).strip()} or set(lib[2] if lib else []))
    emin, emax = role.get("experience_min"), role.get("experience_max")
    exp = (f"{emin} to {emax} years" if emin not in (None, "") and emax not in (None, "") else
           f"At least {emin} years" if emin not in (None, "") else None)
    where = " · ".join(x for x in (role.get("department"), role.get("location"), EMPLOYMENT_LABEL.get(role.get("employment_type") or "", None)) if x)
    about = str(role.get("about") or "").strip()
    desc = [f"We are hiring a {title}" + (f" ({where})" if where else "") + "."]
    if about:
        desc += ["", about]
    desc += ["", "What you will do:"] + [f"- {r}" for r in resp]
    req = []
    if exp:
        req.append(f"- {exp} of relevant experience")
    if skills:
        req.append("- Working knowledge of: " + ", ".join(skills))
    req += ["- Clear written and spoken communication", "- Ownership: you follow work through to the end"]
    return {"description": "\n".join(desc), "requirements": "\n".join(req), "skills": skills, "method": "rules"}


def job_requirements(posting: dict) -> dict:
    """What the posting asks for: skills (from requirements + description), experience range, degree level."""
    text = "\n".join(str(posting.get(k) or "") for k in ("title", "description", "requirements"))
    req_text = str(posting.get("requirements") or "") or text
    skills = sorted(set(find_skills(req_text)) | set(s.lower() for s in posting.get("skills") or []))
    emin = emax = None
    m = re.search(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*(?:\+\s*)?(?:years?|yrs?)", text, re.IGNORECASE)
    if m:
        emin, emax = float(m.group(1)), float(m.group(2))
    else:
        m = re.search(r"(?:at least|minimum|min\.?)?\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", text, re.IGNORECASE)
        if m:
            emin = float(m.group(1))
    level = highest_level(find_education(req_text))
    return {"skills": skills, "experience_min": emin, "experience_max": emax, "degree_level": level}


def match_score(profile: dict, job: dict) -> dict:
    """0..100 with the working shown. Weights: skills 60, experience 25, degree 10, notice 5
    (a part the job does not ask for is neutral = full marks, so it cannot hurt anyone)."""
    have = {s.lower() for s in profile.get("skills") or []}
    need = [s.lower() for s in job.get("skills") or []]
    matched = sorted(s for s in need if s in have)
    missing = sorted(s for s in need if s not in have)
    lines = []
    if need:
        sk = len(matched) / len(need)
        lines.append({"part": "Skills", "weight": 60, "got": round(60 * sk, 1),
                      "why": f"{len(matched)} of {len(need)} skills the job asks for" + (f"; missing: {', '.join(missing[:8])}" if missing else "")})
    else:
        lines.append({"part": "Skills", "weight": 60, "got": 60.0, "why": "The posting lists no recognisable skills, so skills are neutral"})
    yrs = profile.get("experience_years")
    emin, emax = job.get("experience_min"), job.get("experience_max")
    if emin is None:
        lines.append({"part": "Experience", "weight": 25, "got": 25.0, "why": "The posting asks for no particular experience"})
    elif yrs is None:
        lines.append({"part": "Experience", "weight": 25, "got": 12.5, "why": "Experience not found in the résumé: half marks until someone checks"})
    elif yrs < emin:
        lines.append({"part": "Experience", "weight": 25, "got": round(25 * max(0.0, yrs / emin), 1), "why": f"{yrs:g} years; the posting asks for {emin:g}+"})
    elif emax is not None and yrs > emax + 3:
        lines.append({"part": "Experience", "weight": 25, "got": 20.0, "why": f"{yrs:g} years; more than the {emin:g}-{emax:g} asked (may be senior for the role)"})
    else:
        rng = f"{emin:g}-{emax:g}" if emax is not None else f"{emin:g}+"
        lines.append({"part": "Experience", "weight": 25, "got": 25.0, "why": f"{yrs:g} years fits the {rng} asked"})
    lvl, want = highest_level(profile.get("education") or []), int(job.get("degree_level") or 0)
    if not want:
        lines.append({"part": "Qualification", "weight": 10, "got": 10.0, "why": "The posting asks for no particular qualification"})
    elif lvl >= want:
        lines.append({"part": "Qualification", "weight": 10, "got": 10.0, "why": f"Has a {LEVEL_LABEL.get(lvl, 'degree')} level qualification"})
    elif lvl == 0:
        lines.append({"part": "Qualification", "weight": 10, "got": 5.0, "why": "Qualification not found in the résumé: half marks until someone checks"})
    else:
        lines.append({"part": "Qualification", "weight": 10, "got": 3.0, "why": f"Highest found is {LEVEL_LABEL.get(lvl)}; the posting mentions {LEVEL_LABEL.get(want)}"})
    nd = profile.get("notice_period_days")
    if nd is None:
        lines.append({"part": "Notice period", "weight": 5, "got": 2.5, "why": "Notice period not stated"})
    else:
        f = 1.0 if nd <= 30 else 0.6 if nd <= 60 else 0.3 if nd <= 90 else 0.0
        lines.append({"part": "Notice period", "weight": 5, "got": round(5 * f, 1), "why": f"{nd} days" + (" (immediate)" if nd == 0 else "")})
    score = round(sum(x["got"] for x in lines))
    band = "strong" if score >= 75 else "possible" if score >= 50 else "weak"
    return {"score": score, "band": band, "lines": lines, "matched_skills": matched, "missing_skills": missing,
            "method": "rules",
            "advisory": "Advisory only. A person reviews every candidate; no one is moved or rejected because of this score. "
                        "It uses skills, experience, qualification level and notice period only."}


def profile_for_ai(profile: dict) -> dict:
    """The ONLY candidate facts a fit-check prompt may carry: no name, contact, money, employer or college."""
    return {"skills": list(profile.get("skills") or [])[:60], "experience_years": profile.get("experience_years"),
            "qualification_levels": sorted({LEVEL_LABEL.get(int(e.get("level") or 0), "") for e in profile.get("education") or []} - {""}),
            "notice_period_days": profile.get("notice_period_days"), "current_title": profile.get("current_title")}
