"""Policy clause store — importer, AI-readiness score, server-side ACL, as-of selection, retrieval.

Pure functions first (unit-tested without a database), then a thin DB layer.

Retrieval contract (``policy_answer_context``):
  1. ACL BEFORE retrieval: only clauses whose scope admits the caller's location / grade /
     employment type / role, and whose [effective_from, effective_to] contains ``as_of``;
     only ``status = approved`` versions; one version per clause_id (the one in force);
  2. BM25 (retrieval.rank) over the admitted clauses, normalised to a 0..1 relevance;
  3. ABSTAIN when the best relevance is under the org threshold, when nothing is admitted,
     or when the question is about another person (desk.router).
"""
from __future__ import annotations

import re
import sys
from datetime import date

sys.path.append("/app")

import retrieval  # noqa: E402  (BM25 + tokens — the one ranker)

# ── importer ──────────────────────────────────────────────────────────────────

# a heading / numbering line that starts a clause
_HEAD = re.compile(r"^\s*(?:(#{1,6})\s+(?P<md>.+?)\s*#*\s*$"
                   r"|(?P<num>\d+(?:\.\d+)*)[.)]?\s+(?P<ntitle>[A-Z][^\n]{2,120})$"
                   r"|(?P<cap>[A-Z][A-Z0-9 ,&/\-()]{3,80})$"
                   r"|(?:clause|section|article|part)\s+(?P<cnum>\d+(?:\.\d+)*)[.:)]?\s*(?P<ctitle>[^\n]{0,120})$)",
                   re.IGNORECASE | re.MULTILINE)
_DATE_WORDS = re.compile(r"\b(effective|w\.?e\.?f\.?|with effect from|from)\b[^\n.]{0,40}?"
                         r"(\d{1,2}[ /-](?:\d{1,2}|[A-Za-z]{3,9})[ /-]\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\s+\d{4})", re.IGNORECASE)
_DEAD_ENDS = re.compile(r"\b(see hr|contact hr|refer to hr|ask hr|as per management discretion|case[- ]to[- ]case|"
                        r"at the discretion of|refer (?:to )?the (?:hr )?(?:team|department)|to be decided)\b", re.IGNORECASE)
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def extract_text(data: bytes, filename: str = "") -> str:
    """Plain text from txt / md / docx / pdf bytes. docx and pdf need python-docx / pypdf (optional)."""
    name = (filename or "").lower()
    if name.endswith(".docx") or data[:2] == b"PK":
        try:
            import io
            import docx                                       # python-docx
            d = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in d.paragraphs)
        except Exception as e:                               # noqa: BLE001
            raise ValueError(f"Could not read the .docx ({type(e).__name__}) — paste the text instead.")
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        try:
            import io
            from pypdf import PdfReader
            r = PdfReader(io.BytesIO(data))
            return "\n".join((p.extract_text() or "") for p in r.pages)
        except Exception as e:                               # noqa: BLE001
            raise ValueError(f"Could not read the PDF ({type(e).__name__}) — paste the text instead.")
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def split_clauses(text: str) -> list[dict]:
    """Split a policy document into clauses by headings / numbering. Returns
    [{clause_id, title, text, effective_from}] in document order; the preamble before the first
    heading becomes clause "0" when it has substance."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[dict] = []
    cur: dict | None = None
    auto = 0

    def flush():
        nonlocal cur
        if cur is not None:
            body = "\n".join(cur["_body"]).strip()
            cur["text"] = (cur["title"] + "\n" + body).strip() if body else cur["title"]
            cur.pop("_body", None)
            if body or cur["title"]:
                out.append(cur)
        cur = None

    for raw in lines:
        line = raw.rstrip()
        m = _HEAD.match(line) if line.strip() else None
        if m and len(line.strip()) <= 140:
            flush()
            if m.group("md") is not None:
                title = m.group("md").strip()
                num = _leading_number(title)
                cid = num or ""
                if num:
                    title = title[len(num):].lstrip(" .)-")
            elif m.group("num") is not None:
                cid, title = m.group("num"), m.group("ntitle").strip()
            elif m.group("cnum") is not None:
                cid, title = m.group("cnum"), (m.group("ctitle") or "").strip() or f"Clause {m.group('cnum')}"
            else:
                cid, title = "", m.group("cap").strip().title()
            if not cid:
                auto += 1
                cid = f"A{auto}"
            cur = {"clause_id": cid, "title": title[:200], "_body": []}
        else:
            if cur is None:
                if not line.strip():
                    continue
                cur = {"clause_id": "0", "title": "Preamble", "_body": []}
            cur["_body"].append(line)
    flush()
    for c in out:
        c["effective_from"] = _find_effective(c["text"])
    return out


def _leading_number(s: str) -> str:
    m = re.match(r"^(\d+(?:\.\d+)*)", s.strip())
    return m.group(1) if m else ""


def _find_effective(text: str) -> str | None:
    m = _DATE_WORDS.search(text or "")
    if not m:
        return None
    raw = m.group(2)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%d-%b-%Y", "%d-%B-%Y", "%B %Y", "%b %Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            from datetime import datetime
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def ai_ready_score(clause: dict) -> tuple[int, list[str]]:
    """0-100 heuristic + the notes that hold it down. Length, heading, effective date, no 'see HR'
    dead ends, no unresolved placeholders, one topic (not a wall of text)."""
    text = (clause.get("text") or "").strip()
    title = (clause.get("title") or "").strip()
    words = len(re.findall(r"\w+", text))
    score, notes = 100, []
    if words < 25:
        score -= 35; notes.append("too short to answer a question on its own (under 25 words)")
    elif words > 450:
        score -= 20; notes.append("very long — split it so one clause answers one question")
    if not title or title.lower() in ("preamble",) or len(title) < 4:
        score -= 15; notes.append("no clear heading")
    if not (clause.get("effective_from") or _find_effective(text)):
        score -= 15; notes.append("no effective date stated")
    if _DEAD_ENDS.search(text):
        score -= 25; notes.append("dead end: 'see HR' / 'at the discretion of' — write the rule or the exact contact")
    if re.search(r"\[[^\]]{1,40}\]|<[^>]{1,40}>|TBD|TBC|XX", text):
        score -= 15; notes.append("unfilled placeholder (TBD / [ ] / < >)")
    if re.search(r"\b(may|might|usually|generally|normally)\b", text, re.IGNORECASE) and words < 80:
        score -= 5; notes.append("hedged wording (may / usually) — state the rule")
    return max(0, min(100, score)), notes


# ── ACL + as-of ───────────────────────────────────────────────────────────────

def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def scope_admits(scope: dict | None, caller: dict | None) -> bool:
    """Empty list / missing key = everyone. Each present list is an allow-list matched
    case-insensitively against the caller's attribute; a caller with no value for a restricted
    dimension is NOT admitted (fail closed)."""
    scope = scope or {}
    caller = caller or {}
    for dim, attr in (("locations", "location"), ("grades", "grade"), ("employment_types", "employment_type"), ("roles", "role")):
        allowed = [_norm(x) for x in (scope.get(dim) or []) if _norm(x)]
        if not allowed:
            continue
        mine = _norm(caller.get(attr))
        if not mine or mine not in allowed:
            return False
    return True


def in_force(clause: dict, as_of: date | str) -> bool:
    d = as_of.isoformat() if isinstance(as_of, date) else str(as_of)[:10]
    f, t = clause.get("effective_from"), clause.get("effective_to")
    if f and d < str(f)[:10]:
        return False
    if t and d > str(t)[:10]:
        return False
    return True


def admitted_clauses(clauses: list[dict], caller: dict | None, as_of: date | str) -> list[dict]:
    """Approved, in force on as_of, scope admits caller; ONE version per (policy_id, clause_id):
    the highest version in force."""
    best: dict[tuple, dict] = {}
    for c in clauses:
        if (c.get("status") or "draft") != "approved":
            continue
        if not in_force(c, as_of) or not scope_admits(c.get("scope"), caller):
            continue
        k = (c.get("policy_id"), c.get("clause_id"))
        if k not in best or int(c.get("version") or 0) > int(best[k].get("version") or 0):
            best[k] = c
    return list(best.values())


# ── retrieval ─────────────────────────────────────────────────────────────────

def _stem(w: str) -> str:
    """Tiny English stemmer so 'leaves' meets 'leave' and 'credited' meets 'credit' (no library)."""
    if len(w) > 4 and w.endswith(("ies", "ied")):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]                                        # leaves → leave, days → day
    return w


def relevance(bm25: float, query: str, text: str) -> float:
    """0..1: 75 % query-term coverage (how much of the question the clause actually mentions, stemmed) +
    25 % saturating BM25. Coverage is corpus-size independent, so one threshold works for a 3-clause
    policy and a 300-clause handbook alike."""
    q = {_stem(w) for w in retrieval.tokens(query)}
    if not q:
        return 0.0
    t = {_stem(w) for w in retrieval.tokens(text)}
    cover = len(q & t) / len(q)
    sat = min(1.0, bm25 / 2.0) if bm25 > 0 else 0.0
    return round(0.75 * cover + 0.25 * sat, 3)


def deep_link(policy_id: str, clause_pk: str) -> str:
    return f"/ai/desk/policies?policy={policy_id}&clause={clause_pk}"


def policy_answer_context(org: str, caller: dict | None, question: str, as_of: date | str, clauses: list[dict],
                          *, threshold: float = 0.35, k: int = 5, about_other_person: bool = False) -> dict:
    """The deterministic context the answer builder receives. ``clauses`` are the org's rows as dicts
    (see ``clause_dict``); ACL and as-of run here, BEFORE ranking.

    Returns {clauses: [{id, policy, policy_id, clause_id, version, title, text, effective_from, deep_link, score,
    relevance}], best: float, abstain: bool, abstain_reason: str|None, admitted: int, threshold}."""
    if about_other_person:
        return {"clauses": [], "best": 0.0, "abstain": True, "abstain_reason": "about_other_person", "admitted": 0, "threshold": threshold}
    mine = [c for c in clauses if str(c.get("org_id")) == str(org)]
    adm = admitted_clauses(mine, caller, as_of)
    if not adm:
        return {"clauses": [], "best": 0.0, "abstain": True, "abstain_reason": "no_approved_policy", "admitted": 0, "threshold": threshold}
    passages = [{**c, "text": f"{c.get('title') or ''}\n{c.get('text') or ''}", "_pk": c.get("id")} for c in adm]
    ranked = retrieval.rank(passages, question, k=max(1, k))
    out = []
    for r in ranked:
        rel = relevance(float(r.get("score") or 0.0), question, r["text"])
        out.append({"id": r["_pk"], "policy": r.get("policy_title") or r.get("policy_key") or r.get("policy_id"),
                    "policy_id": r.get("policy_id"), "clause_id": r.get("clause_id"), "version": int(r.get("version") or 1),
                    "title": r.get("title") or "", "text": (r.get("text") or "").split("\n", 1)[-1].strip(),
                    "effective_from": r.get("effective_from"), "effective_to": r.get("effective_to"),
                    "deep_link": deep_link(r.get("policy_id"), r["_pk"]), "score": r.get("score"), "relevance": rel})
    out.sort(key=lambda x: -x["relevance"])
    best = out[0]["relevance"] if out else 0.0
    abstain = best < threshold
    return {"clauses": out if not abstain else out[:1], "best": best, "abstain": abstain,
            "abstain_reason": "below_threshold" if abstain else None, "admitted": len(adm), "threshold": threshold}


# ── DB layer ──────────────────────────────────────────────────────────────────

def clause_dict(c, policy=None) -> dict:
    return {"id": c.id, "org_id": c.org_id, "policy_id": c.policy_id, "clause_id": c.clause_id, "version": c.version,
            "title": c.title, "text": c.text, "effective_from": c.effective_from, "effective_to": c.effective_to,
            "scope": c.scope or {}, "status": c.status, "approved_by": c.approved_by,
            "approved_at": c.approved_at.isoformat() if c.approved_at else None,
            "ai_ready_score": c.ai_ready_score, "ai_ready_notes": list(c.ai_ready_notes or []), "source_doc": c.source_doc,
            "order_no": c.order_no, "created_at": c.created_at.isoformat() if c.created_at else None,
            "policy_title": getattr(policy, "title", None), "policy_key": getattr(policy, "key", None)}


async def load_approved(org: str) -> list[dict]:
    """Every approved clause of the org with its policy title (the retrieval corpus)."""
    from sqlalchemy import select
    from shared.utils import platform_models as pm
    from desk.policy_models import DeskPolicy, DeskPolicyClause
    async with pm.session() as s:
        pols = {p.id: p for p in (await s.execute(select(DeskPolicy).where(DeskPolicy.org_id == org))).scalars().all()}
        rows = (await s.execute(select(DeskPolicyClause).where(DeskPolicyClause.org_id == org, DeskPolicyClause.status == "approved"))).scalars().all()
    return [clause_dict(c, pols.get(c.policy_id)) for c in rows if pols.get(c.policy_id) and pols[c.policy_id].status != "retired"]


async def answer_context_db(org: str, caller: dict | None, question: str, as_of: date | str, *, threshold: float, k: int,
                            about_other_person: bool = False) -> dict:
    return policy_answer_context(org, caller, question, as_of, await load_approved(org), threshold=threshold, k=k,
                                 about_other_person=about_other_person)
