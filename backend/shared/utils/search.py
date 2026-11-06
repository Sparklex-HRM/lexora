"""Global search (2026-10-20) — the small toolkit every service's `/…/search` handler shares.

Each owning service answers `GET /<prefix>/search?q=&types=&limit=` for ITS records, applies its
OWN org scope + role rules (the same rules as its list endpoints — never trust the client), and
returns a list of hits built with `hit()`. The gateway (`GET /search`) fans out to every service
in parallel and merges by `score`.

Hit shape (stable, the frontend reads exactly these keys):
    {"type": "payslip", "id": "...", "title": "...", "subtitle": "...", "url": "/my-pay?stub=...",
     "score": 0.0-100.0, "badge": "posted" | None}

Matching is ILIKE per token in SQL (cheap, uses the pg_trgm GIN indexes of migration 0010 on
Postgres, works on SQLite in the unit tests) and ranking is done here in Python, with a
typo-tolerant similarity so "priay" still ranks Priya first when the SQL side found it.
"""
from __future__ import annotations

import difflib
import re
from typing import Iterable

MAX_Q = 80          # characters — a longer query is cut, never an error
MAX_LIMIT = 25      # hits per type per service
DEFAULT_LIMIT = 8

_WS = re.compile(r"\s+")


def normalise(q: str | None) -> str:
    """Trim, collapse spaces, cap the length."""
    return _WS.sub(" ", (q or "").strip())[:MAX_Q]


def tokens(q: str | None) -> list[str]:
    """Lower-case words of the query (max 5, each at least 1 char)."""
    return [t for t in normalise(q).lower().split(" ") if t][:5]


def like(token: str) -> str:
    """An ILIKE pattern for `token`, with LIKE wildcards in the token escaped (escape char `\\`)."""
    t = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{t}%"


def clamp_limit(limit: int | None) -> int:
    try:
        n = int(limit or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        n = DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def parse_types(types: str | None, offered: Iterable[str]) -> set[str]:
    """The record types to answer: the caller's `types=a,b` ∩ what this service offers.
    Empty / missing → everything offered."""
    offer = set(offered)
    if not types:
        return offer
    asked = {t.strip().lower() for t in types.split(",") if t.strip()}
    return offer & asked


def modules_of(headers) -> set[str] | None:
    """Subscribed modules stamped by the gateway (X-Modules). None = header absent (fail open,
    exactly like the gateway's own gate for a pre-claim token)."""
    raw = headers.get("x-modules")
    if raw is None:
        return None
    return {m.strip() for m in raw.split(",") if m.strip()}


def has_module(headers, module: str) -> bool:
    mods = modules_of(headers)
    return mods is None or module in mods


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def score(q: str, primary: str | None, *others: str | None) -> float:
    """0..100 — exact > prefix > word-prefix > contains > every-token > fuzzy.
    `primary` is the record's main label (name, number); `others` are secondary fields."""
    needle = normalise(q).lower()
    if not needle:
        return 0.0
    p = (primary or "").lower()
    rest = " ".join((o or "").lower() for o in others)
    hay = f"{p} {rest}"
    if p == needle:
        return 100.0
    if p.startswith(needle):
        return 90.0
    words = re.split(r"[\s·,/()\-]+", hay)
    if any(w == needle for w in words):
        return 85.0
    if any(w.startswith(needle) for w in words if w):
        return 75.0
    if needle in p:
        return 70.0
    if needle in hay:
        return 60.0
    toks = tokens(needle)
    if toks and all(t in hay for t in toks):
        return 50.0
    # typo tolerance: best similarity between the query and any word / the primary label
    best = max([_fuzzy(needle, p)] + [_fuzzy(needle, w) for w in words if w])
    return round(40.0 * best, 2) if best >= 0.6 else 0.0


def hit(type_: str, id_, title: str, subtitle: str | None, url: str, score_: float,
        badge: str | None = None) -> dict:
    return {"type": type_, "id": str(id_), "title": title or "—", "subtitle": subtitle or "",
            "url": url, "score": float(score_), "badge": badge}


def subtitle(*parts) -> str:
    """Non-empty parts joined with ' · '."""
    return " · ".join(str(p) for p in parts if p not in (None, "", "None"))


def finish(hits: list[dict], limit: int) -> list[dict]:
    """Drop zero scores, best first, at most `limit` per type."""
    hits = [h for h in hits if h["score"] > 0]
    hits.sort(key=lambda h: (-h["score"], h["title"].lower()))
    per: dict[str, int] = {}
    out = []
    for h in hits:
        per[h["type"]] = per.get(h["type"], 0) + 1
        if per[h["type"]] <= limit:
            out.append(h)
    return out


def ilike_any(columns, toks: list[str]):
    """SQLAlchemy condition: EVERY token appears in at least one of `columns` (AND of ORs)."""
    from sqlalchemy import and_, or_
    conds = []
    for t in toks:
        pat = like(t)
        conds.append(or_(*[c.ilike(pat, escape="\\") for c in columns]))
    return and_(*conds) if conds else None


def response(q: str, hits: list[dict], limit: int, source: str) -> dict:
    return {"q": normalise(q), "source": source, "hits": finish(hits, limit)}
