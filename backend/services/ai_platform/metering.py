"""Usage metering — tokens, estimated cost, monthly caps. Pure maths + one small DB layer.

Prices are CONFIG, never invented at run time:
  * ``DEFAULT_PRICES_USD`` is the Anthropic first-party price list as cached by the
    ``claude-api`` reference on 2026-10-06 (USD per million tokens). An org may override
    any model on the AI settings page; a model with no price anywhere costs ``None``
    ("set on the AI settings page").
  * C$ needs a USD→CAD rate. There is no built-in rate — the org sets it; until then
    ``cost_cad`` is ``None`` and the screen says so.
"""
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

from ai_models import AiUsageEvent  # noqa: E402

PRICE_SOURCE = "Anthropic API price list (USD per 1M tokens) as cached on 2026-10-06; override per model on the AI settings page."

DEFAULT_PRICES_USD: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}

MODELS = list(DEFAULT_PRICES_USD)


def price_for(model: str | None, overrides: dict | None = None) -> tuple[float, float] | None:
    """(input, output) USD per 1M tokens, org override first; None when unknown."""
    ov = (overrides or {}).get(model or "") or {}
    try:
        if ov.get("input_usd_per_mtok") is not None and ov.get("output_usd_per_mtok") is not None:
            return float(ov["input_usd_per_mtok"]), float(ov["output_usd_per_mtok"])
    except (TypeError, ValueError):
        pass
    return DEFAULT_PRICES_USD.get(model or "")


# Prompt caching (2026-10-24): the API's input_tokens EXCLUDES cached tokens. Cache reads
# bill at 0.1x the input price; cache writes at 1.25x (5-minute TTL) or 2x (1-hour TTL).
CACHE_READ_MULT = 0.1
CACHE_WRITE_MULT = {"5m": 1.25, "1h": 2.0}


def cost_of(model: str | None, input_tokens: int, output_tokens: int, overrides: dict | None = None,
            usd_cad_rate: float | None = None, cache_read_tokens: int = 0, cache_write_tokens: int = 0,
            cache_ttl: str = "5m") -> dict:
    """{cost_usd, cost_cad, note}. Either may be None — never a guessed number."""
    p = price_for(model, overrides)
    if p is None:
        return {"cost_usd": None, "cost_cad": None, "note": f"No price for {model or 'this model'} — set on the AI settings page."}
    usd = round((input_tokens or 0) / 1e6 * p[0] + (output_tokens or 0) / 1e6 * p[1]
                + (cache_read_tokens or 0) / 1e6 * p[0] * CACHE_READ_MULT
                + (cache_write_tokens or 0) / 1e6 * p[0] * CACHE_WRITE_MULT.get(cache_ttl, 1.25), 6)
    if not usd_cad_rate or usd_cad_rate <= 0:
        return {"cost_usd": usd, "cost_cad": None, "note": "C$ needs a USD→CAD rate — set on the AI settings page."}
    return {"cost_usd": usd, "cost_cad": round(usd * float(usd_cad_rate), 4), "note": ""}


def cap_status(used_tokens: int, used_cad: float | None, cap_tokens: int, cap_cad: float, mode: str) -> dict:
    """Monthly cap verdict. soft = warn at 80 % and over; hard = refuse model calls when over.
    An C$ cap with no C$ figures (no rate) cannot be enforced — reported honestly."""
    pct_t = (used_tokens / cap_tokens * 100) if cap_tokens else None
    pct_r = (float(used_cad or 0) / cap_cad * 100) if (cap_cad and used_cad is not None) else None
    over = (pct_t is not None and pct_t >= 100) or (pct_r is not None and pct_r >= 100)
    warn = (pct_t is not None and pct_t >= 80) or (pct_r is not None and pct_r >= 80)
    pct = max([p for p in (pct_t, pct_r) if p is not None], default=None)
    return {"cap_tokens": cap_tokens, "cap_cad": cap_cad, "mode": mode if mode in ("soft", "hard") else "soft",
            "used_tokens": used_tokens, "used_cad": used_cad, "pct": round(pct, 1) if pct is not None else None,
            "warn": bool(warn), "over": bool(over), "blocked": bool(over and mode == "hard"),
            "cad_cap_enforceable": not (cap_cad and used_cad is None)}


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def cap_tokens(input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> int:
    """Tokens counted against the monthly token cap: uncached input + output + cache writes,
    plus cache reads at their 0.1x weight (so caching lowers cap use the way it lowers cost)."""
    reads = int(cache_read_tokens or 0)
    return int(input_tokens or 0) + int(output_tokens or 0) + int(cache_write_tokens or 0) + (reads + 9) // 10


async def record(org_id: str, *, user_id: str | None, user_email: str | None, channel: str, feature: str, model: str | None,
                 key_source: str | None, input_tokens: int, output_tokens: int, cost: dict,
                 cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> None:
    async with pm.session() as s:
        s.add(AiUsageEvent(org_id=org_id, user_id=user_id, user_email=user_email, channel=channel, feature=feature, model=model,
                           key_source=key_source, input_tokens=int(input_tokens or 0), output_tokens=int(output_tokens or 0),
                           cache_read_tokens=int(cache_read_tokens or 0), cache_write_tokens=int(cache_write_tokens or 0),
                           cost_usd=cost.get("cost_usd"), cost_cad=cost.get("cost_cad")))
        await s.commit()


# create_all never ALTERs an existing table, so boot adds the 2026-10-24 cache columns (idempotent).
CACHE_COLUMN_DDL = [
    "ALTER TABLE ai_usage_events ADD COLUMN IF NOT EXISTS cache_read_tokens INTEGER DEFAULT 0",
    "ALTER TABLE ai_usage_events ADD COLUMN IF NOT EXISTS cache_write_tokens INTEGER DEFAULT 0",
    "ALTER TABLE ai_copilot_audit ADD COLUMN IF NOT EXISTS cache_read_tokens INTEGER DEFAULT 0",
    "ALTER TABLE ai_copilot_audit ADD COLUMN IF NOT EXISTS cache_write_tokens INTEGER DEFAULT 0",
]
USAGE_CACHE_COLUMN_DDL = [
    "ALTER TABLE ai_usage ADD COLUMN IF NOT EXISTS cache_read_tokens INTEGER DEFAULT 0",
    "ALTER TABLE ai_usage ADD COLUMN IF NOT EXISTS cache_write_tokens INTEGER DEFAULT 0",
]


async def ensure_cache_columns(service_engine=None) -> None:
    """Postgres only (SQLite test DBs are built from the models, columns included). Never raises."""
    from sqlalchemy import text
    for eng, stmts in ((pm.get_engine(), CACHE_COLUMN_DDL), (service_engine, USAGE_CACHE_COLUMN_DDL)):
        if eng is None or eng.dialect.name != "postgresql":
            continue
        for stmt in stmts:
            try:
                async with eng.begin() as conn:
                    await conn.execute(text(stmt))
            except Exception as e:                           # noqa: BLE001
                print(f"[ai_platform] cache column DDL skipped ({stmt[:60]}): {e}")


async def month_totals(org_id: str, user_id: str | None = None, since: datetime | None = None) -> dict:
    since = since or month_start()
    q = select(func.coalesce(func.sum(AiUsageEvent.input_tokens), 0), func.coalesce(func.sum(AiUsageEvent.output_tokens), 0),
               func.sum(AiUsageEvent.cost_usd), func.sum(AiUsageEvent.cost_cad), func.count(AiUsageEvent.id),
               func.count(AiUsageEvent.cost_cad), func.coalesce(func.sum(AiUsageEvent.cache_read_tokens), 0),
               func.coalesce(func.sum(AiUsageEvent.cache_write_tokens), 0)).where(AiUsageEvent.org_id == org_id, AiUsageEvent.created_at >= since)
    if user_id:
        q = q.where(AiUsageEvent.user_id == user_id)
    async with pm.session() as s:
        i, o, usd, cad, n, n_cad, cr, cw = (await s.execute(q)).one()
    return {"input_tokens": int(i or 0), "output_tokens": int(o or 0), "cache_read_tokens": int(cr or 0), "cache_write_tokens": int(cw or 0),
            "tokens": cap_tokens(i, o, cr, cw), "calls": int(n or 0),
            "cost_usd": round(float(usd), 6) if usd is not None else None,
            # C$ is only a total when EVERY call had a rate; otherwise it would under-report
            "cost_cad": round(float(cad), 2) if (cad is not None and n_cad == n) else None, "since": since.isoformat()}


async def summary(org_id: str, since: datetime | None = None, limit_recent: int = 50) -> dict:
    """Per user / feature / model / day for the usage screen (this month by default)."""
    since = since or month_start()
    async with pm.session() as s:
        rows = (await s.execute(select(AiUsageEvent).where(AiUsageEvent.org_id == org_id, AiUsageEvent.created_at >= since)
                                .order_by(AiUsageEvent.created_at.desc()))).scalars().all()

    def group(keyfn, label):
        g: dict = {}
        for r in rows:
            k = keyfn(r) or "—"
            d = g.setdefault(k, {label: k, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
                                 "cost_usd": 0.0, "cost_cad": 0.0, "_usd_ok": True, "_cad_ok": True})
            d["calls"] += 1; d["input_tokens"] += r.input_tokens; d["output_tokens"] += r.output_tokens
            d["cache_read_tokens"] += int(r.cache_read_tokens or 0); d["cache_write_tokens"] += int(r.cache_write_tokens or 0)
            if r.cost_usd is None:
                d["_usd_ok"] = False
            else:
                d["cost_usd"] += float(r.cost_usd)
            if r.cost_cad is None:
                d["_cad_ok"] = False
            else:
                d["cost_cad"] += float(r.cost_cad)
        out = []
        for d in g.values():
            d["tokens"] = cap_tokens(d["input_tokens"], d["output_tokens"], d["cache_read_tokens"], d["cache_write_tokens"])
            d["cost_usd"] = round(d["cost_usd"], 6) if d.pop("_usd_ok") else None
            d["cost_cad"] = round(d["cost_cad"], 2) if d.pop("_cad_ok") else None
            out.append(d)
        return sorted(out, key=lambda x: -x["tokens"])

    return {"since": since.isoformat(),
            "by_user": group(lambda r: r.user_email or r.user_id, "user"),
            "by_feature": group(lambda r: r.feature, "feature"),
            "by_model": group(lambda r: r.model, "model"),
            "by_channel": group(lambda r: r.channel, "channel"),
            "daily": sorted(group(lambda r: pm.aware(r.created_at).date().isoformat(), "date"), key=lambda x: x["date"]),
            "recent": [{"at": pm.iso(r.created_at), "user": r.user_email or r.user_id, "channel": r.channel, "feature": r.feature, "model": r.model,
                        "key_source": r.key_source, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                        "cache_read_tokens": int(r.cache_read_tokens or 0), "cache_write_tokens": int(r.cache_write_tokens or 0),
                        "cost_usd": r.cost_usd, "cost_cad": r.cost_cad} for r in rows[:limit_recent]]}
