"""Policy retrieval ("RAG" without embeddings): lexical BM25-style ranking over the
passages this installation actually holds — company settings, the policy register
(title / category / version / effective date; the documents' full text is NOT stored),
leave types, the PIPEDA privacy notice and the payroll engine's statutory constants (CRA T4127 /
Revenu Québec, each with its source and verified status). Every passage
carries an id + link so the answer can cite it.
"""
from __future__ import annotations

import math
import re
import sys
from collections import Counter

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402

import copilot as CP  # noqa: E402

STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "is", "are", "what", "how", "many", "does", "do", "our", "my", "me", "i", "can",
        "and", "or", "with", "be", "it", "this", "that", "policy", "company", "we", "you", "your", "about", "when", "which", "there"}


def constants_as_rules(report: dict) -> list[dict]:
    """GET /payroll/compliance ``constants`` rows (every role may read them) in the rule shape the assistant cites."""
    out = []
    for x in (report or {}).get("constants") or []:
        key = str(x.get("key") or "")
        area = key.split(".", 1)[0].upper() if "." in key else "Payroll"
        out.append({"id": key, "key": key, "area": area, "description": x.get("label"), "value": x.get("value"),
                    "effective_from": f"{(report or {}).get('as_of_edition') or '2026'}-01-01",
                    "status": "verified" if x.get("status") == "verified" else "needs_confirmation",
                    "applies_to": area if len(area) == 2 else "federal", "source_title": x.get("source"), "source_url": None,
                    "org_override": None, "note": (f"verified {x.get('verified_on')} by {x.get('by')}" if x.get("verified_on") else None),
                    "code_does": None})
    return out


async def statutory_rows(headers: dict) -> tuple[list[dict], str | None]:
    """The Canadian statutory constants the payroll engine applies (CPP / CPP2, EI, federal and provincial tax, QPP /
    QPIP …) from the Verification Center report, as the caller."""
    data, err = await CP._get(f"{settings.PAYROLL_URL}/payroll/compliance", headers)
    if err:
        return [], err
    return constants_as_rules(data if isinstance(data, dict) else {}), None


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOP and len(w) > 1]


def rank(passages: list[dict], query: str, k: int = 6) -> list[dict]:
    """BM25 (k1 = 1.5, b = 0.75) over ``text``; returns the top-k with a ``score``."""
    q = tokens(query)
    if not q or not passages:
        return []
    docs = [tokens(p.get("text", "")) for p in passages]
    n = len(docs)
    avg = sum(len(d) for d in docs) / max(1, n)
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    out = []
    for p, d in zip(passages, docs):
        tf = Counter(d)
        score = 0.0
        for w in set(q):
            if w not in tf:
                continue
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            score += idf * (tf[w] * 2.5) / (tf[w] + 1.5 * (1 - 0.75 + 0.75 * len(d) / max(1.0, avg)))
        if score > 0:
            out.append({**p, "score": round(score, 3)})
    out.sort(key=lambda x: -x["score"])
    return out[:k]


async def corpus(ctx) -> list[dict]:
    """Everything quotable, as passages {id, title, text, link, kind}. Best-effort per source."""
    org, h = ctx.org_id, ctx.headers
    out: list[dict] = []
    try:
        for i, line in enumerate((await CP.policy_text(org, h)).splitlines()):
            if line.strip():
                kind = "policy_document" if line.startswith("Policy document:") else "leave_type" if line.startswith("Leave type:") else "company_setting"
                out.append({"id": f"setting:{i}", "title": line.split(":")[0], "text": line, "link": "/ops/policies" if kind == "policy_document" else "/company-settings", "kind": kind})
    except Exception:                                        # noqa: BLE001
        pass
    notice, _ = await CP._get(f"{settings.COMPLIANCE_URL}/compliance/privacy/notice", h)
    text = (notice or {}).get("text") or (notice or {}).get("body") or ""
    if text:
        for i, para in enumerate([p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()][:40]):
            out.append({"id": f"privacy:{i}", "title": "Privacy notice (PIPEDA)", "text": para[:800], "link": "/privacy", "kind": "privacy_notice"})
    rows, _ = await statutory_rows(h)
    for r in rows[:400]:
        txt = f"{r.get('area')}: {r.get('description')} — value {r.get('value')} (from {r.get('effective_from')}, {r.get('status')}). {r.get('note') or ''} {r.get('code_does') or ''}"
        out.append({"id": f"statutory:{r.get('id') or r.get('key')}", "title": f"Statutory — {r.get('area')}", "text": txt[:700],
                    "link": "/payroll/compliance", "kind": "statutory_rule", "source_title": r.get("source_title"), "source_url": r.get("source_url"),
                    "status": r.get("status")})
    return out


async def search(ctx, question: str, k: int = 6) -> list[dict]:
    hits = rank(await corpus(ctx), question, k)
    return [{k2: v for k2, v in h.items() if k2 in ("id", "title", "text", "link", "kind", "score", "source_title", "source_url", "status")} for h in hits]
