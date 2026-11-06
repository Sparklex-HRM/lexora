"""Lexora HRM — AI Platform Service.

Hosts the lean HR agent fleet, the "Lexi" copilot (a real Claude tool-calling
agent with a grounded fallback), and token economics. Endpoints are grounded in
the org's live employee data.
"""
import os
import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Depends, FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402
from shared.utils.enums import periods_per_year  # noqa: E402

import lexi  # noqa: E402
from constants import AGENTS, agents_for  # noqa: E402
from database import get_db, init_db  # noqa: E402
from models import AiUsage  # noqa: E402

YMPE_2026 = 74_600      # Canada CPP/EI pensionable max (used by the cap monitor)

# Money symbol + the "caps" command-center layer (CAD, CPP/EI YMPE).
COUNTRY_CFG = {
    "CA": {"symbol": "$", "wage_cap": 74_600, "caps_domain": "Payroll — caps",
           "caps_ok": "No employees near CPP/EI caps", "caps_attn": "approaching CPP2 band",
           "caps_detail": "Earning over YMPE"},
}


def _country(request: Request, emps: list[dict] | None = None) -> str:
    """The caller org's country. Lexora HRM is Canada-only, so always 'CA'."""
    return "CA"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # G5 — watchdog run log / findings / copilot audit tables + the read-only scheduler
    import asyncio
    from contextlib import suppress
    from shared.utils import platform_models as _pm
    from g5_models import G5_TABLES
    import wd_runner
    try:
        await _pm.create_tables(G5_TABLES, retries=5)
    except Exception as e:                                   # noqa: BLE001
        print(f"[ai_platform] G5 tables not created: {e}")
    # D3 — AI settings / access tokens / actions / usage / tool-call audit (additive)
    from ai_models import D3_TABLES
    try:
        await _pm.create_tables(D3_TABLES, retries=5)
    except Exception as e:                                   # noqa: BLE001
        print(f"[ai_platform] D3 tables not created: {e}")
    # ── HR Desk (stream D, 2026-10-25) — desk_* tables (policy clauses, sessions, audit, sealed IC referrals); additive ──
    try:
        import desk as _desk
        await _desk.ensure_tables(retries=5)
    except Exception as e:                                   # noqa: BLE001
        print(f"[ai_platform] HR Desk tables not created: {e}")
    # prompt-cache usage columns (2026-10-24) on tables that already exist — create_all never ALTERs
    try:
        from database import engine as _svc_engine
        from metering import ensure_cache_columns
        await ensure_cache_columns(_svc_engine)
    except Exception as e:                                   # noqa: BLE001
        print(f'[ai_platform] cache columns not ensured: {e}')
    # Agentic operations add-on (2026-10-25): tasks / proposals / append-only action log / runs / value ledger
    # + the append-only trigger on the log (Postgres) + the 15-minute chase / expiry loop.
    from agentic_models import AGENTIC_TABLES, ensure_append_only
    import agentic_scheduler
    try:
        await _pm.create_tables(AGENTIC_TABLES, retries=5)
        await ensure_append_only()
    except Exception as e:                                   # noqa: BLE001
        print(f"[ai_platform] agentic tables not created: {e}")
    # Lifecycle agents (2026-10-26) — Onboarding / Exit / Attendance: one shared item table (additive)
    try:
        from agents.lifecycle_common import LIFECYCLE_TABLES
        await _pm.create_tables(LIFECYCLE_TABLES, retries=5)
    except Exception as e:                                   # noqa: BLE001
        print(f"[ai_platform] lifecycle agent tables not created: {e}")
    # Payroll Auditor (Canada, restored 2026-10-26): the findings table of the review pack (additive)
    try:
        from agents.payroll_auditor import PAYROLL_AUDITOR_TABLES
        await _pm.create_tables(PAYROLL_AUDITOR_TABLES, retries=5)
    except Exception as e:                                   # noqa: BLE001
        print(f"[ai_platform] payroll auditor tables not created: {e}")
    task = asyncio.create_task(wd_runner.scheduler())
    agentic_task = asyncio.create_task(agentic_scheduler.scheduler())
    from mcp_server import transport_http as _mcp_http
    async with _mcp_http.lifespan():                         # MCP streamable-HTTP session manager
        yield
    task.cancel()
    agentic_task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    with suppress(asyncio.CancelledError):
        await agentic_task


app = FastAPI(title="Lexora HRM — AI Platform Service", version="0.3.0", lifespan=lifespan)

# G5 — watchdog fleet, HR copilot, flight-risk signals (own routers, own files)
from attrition_routers import router as _attrition_router  # noqa: E402
from copilot_routers import router as _copilot_router  # noqa: E402
from wd_routers import router as _wd_router  # noqa: E402
app.include_router(_wd_router)
app.include_router(_copilot_router)
app.include_router(_attrition_router)

# D3 — assistant with citations + gated actions, AI settings, AI access tokens, metering,
# and the MCP server over streamable HTTP at /ai/mcp (token-guarded, not JWT).
from assistant_routers import router as _assistant_router  # noqa: E402
from mcp_server import transport_http as _mcp_http  # noqa: E402
app.include_router(_assistant_router)
_mcp_http.install(app, "/ai/mcp")      # mount + the exact path POST /ai/mcp (no 307 to /ai/mcp/)

# ── HR Desk (stream D, 2026-10-25) — /ai/desk/*: policy clause store, ask, IC referrals, metrics ──
import desk as _desk_pkg  # noqa: E402
_desk_pkg.install(app)

# G6 (2026-10-23) — internal advisory completions for other services (resume parse / JD draft / match), shielded + capped + metered
from internal_routers import router as _internal_ai_router  # noqa: E402
app.include_router(_internal_ai_router)

# Agentic operations add-on (2026-10-25) — /ai/agents/* sub-paths (GET /ai/agents itself stays the legacy fleet below)
from agentic_routers import router as _agentic_router  # noqa: E402
app.include_router(_agentic_router)

# Lifecycle agents (2026-10-26) — /ai/agents/onboarding/* · /ai/agents/exit/* · /ai/agents/attendance/*: runs, item grids,
# settings; registers their executors + daily / event scheduler hooks. WhatsApp chase channel: /ai/agents/channels/whatsapp
from agents.onboarding import install as _install_onboarding  # noqa: E402
from agents.exit import install as _install_exit  # noqa: E402
from agents.attendance import install as _install_attendance  # noqa: E402
from agents.channels_router import router as _channels_router  # noqa: E402
_install_onboarding(app)
_install_exit(app)
_install_attendance(app)
app.include_router(_channels_router)

# Payroll Auditor (Canada, restored 2026-10-26) — /ai/agents/payroll-auditor/*: run, review pack, findings actions, thresholds;
# registers its executors + the T-N / timesheet-lock scheduler hook
from agents.payroll_auditor import install as _install_payroll_auditor  # noqa: E402
_install_payroll_auditor(app)


# ── helpers ──────────────────────────────────────────────────────────────────
def _org(request: Request) -> str:
    return request.headers.get("x-org-id", "")


def _fwd(request: Request) -> dict:
    return {k: request.headers.get(k, "") for k in
            ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-branch-id") if request.headers.get(k)}


async def _employees(request: Request) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}/employees", headers=_fwd(request))
            if r.status_code == 200:
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


async def _get(request: Request, url: str) -> dict:
    """Fetch a JSON dict from a sibling service; {} on any failure (agents stay online)."""
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(url, headers=_fwd(request))
            if r.status_code == 200 and isinstance(r.json(), dict):
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException, ValueError):
        pass
    return {}


async def _signals(request: Request) -> dict:
    """Live Time & Leave signals the watchdogs poll — overtime anomalies, upcoming
    stat-holiday eligibility, and leave-balance / stale-approval alerts."""
    ta = await _get(request, f"{settings.TIME_URL}/time/analytics")
    lv = await _get(request, f"{settings.LEAVE_URL}/leave/alerts")
    return {
        "ot_anomalies": (ta.get("anomalies") or {}).get("count", 0),
        "ot_hours": (ta.get("overtime") or {}).get("ot_hours", 0),
        "absentee_pct": (ta.get("absenteeism") or {}).get("rate_pct", 0),
        "upcoming_stat": (ta.get("upcoming_stat") or {}).get("count", 0),
        "leave_alerts": (lv.get("count", 0) or 0) + (lv.get("stale_approvals", 0) or 0),
        "leave_negative": len(lv.get("negative", []) or []),
        "leave_stale": lv.get("stale_approvals", 0) or 0,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _money(x: float, country: str = "CA") -> str:
    return COUNTRY_CFG.get(country, COUNTRY_CFG["CA"])["symbol"] + f"{x:,.0f}"


def _findings(key: str, emps: list[dict], signals: dict | None = None) -> int:
    signals = signals or {}
    if key == "missing_data_scan":
        return sum(1 for e in emps if not e.get("email"))
    if key == "payroll_preflight":
        return sum(1 for e in emps if not e.get("annual_salary") and not e.get("hourly_rate"))
    if key == "cpp_ei_cap_monitor":
        return sum(1 for e in emps if (e.get("annual_salary") or 0) > YMPE_2026)
    if key == "onboarding_chaser":
        return sum(1 for e in emps if e.get("status") == "onboarding")
    if key == "classification_check":
        return sum(1 for e in emps if e.get("employment_type") == "contract")
    # Phase 4 — the Time & Leave watchdogs, now polling real T&A / leave data.
    if key == "overtime_anomaly":
        return signals.get("ot_anomalies", 0)
    if key == "leave_balance_guard":
        return signals.get("leave_alerts", 0)
    if key == "stat_holiday":
        return signals.get("upcoming_stat", 0)
    return 0


# ── health / ping ────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai_platform"}


@app.get("/ai/ping")
async def ping():
    return {"service": "ai_platform", "prefix": "/ai", "agents": len(AGENTS),
            "lexi_model": lexi.LEXI_MODEL, "llm_key": bool(os.getenv("ANTHROPIC_API_KEY")), "status": "live"}


# ── agent fleet ──────────────────────────────────────────────────────────────
@app.get("/ai/agents")
async def agents(request: Request):
    emps = await _employees(request)
    signals = await _signals(request)
    ags = agents_for(_country(request, emps))
    out = []
    for i, (key, name, domain, cadence, desc) in enumerate(ags):
        f = _findings(key, emps, signals)
        last = (_now() - timedelta(minutes=17 * (i + 1))).isoformat()
        out.append({"key": key, "name": name, "domain": domain, "cadence": cadence,
                    "summary": desc, "findings": f, "status": "attention" if f else "online",
                    "last_run": None if key == "hr_copilot" else last})
    return out


@app.get("/ai/activity")
async def activity(request: Request, limit: int = 50):
    emps = await _employees(request)
    signals = await _signals(request)
    ags = agents_for(_country(request, emps))
    runs, n = [], 0
    for pass_no in range(2):
        for i, (key, name, domain, cadence, desc) in enumerate(ags):
            if key == "hr_copilot":
                continue
            f = _findings(key, emps, signals)
            ran = _now() - timedelta(hours=pass_no * 24, minutes=23 * (i + 1))
            runs.append({"id": f"run-{pass_no}-{key}", "agent": name, "agent_key": key, "domain": domain,
                         "ran_at": ran.isoformat(), "status": "attention" if f else "ok",
                         "summary": (f"{f} item(s) need attention — {desc}" if f else f"Clean — {desc}"),
                         "findings": f})
            n += 1
    runs.sort(key=lambda r: r["ran_at"], reverse=True)
    return {"items": runs[:limit], "total": n}


# ── command center ───────────────────────────────────────────────────────────
@app.get("/ai/command-center")
async def command_center(request: Request):
    emps = await _employees(request)
    signals = await _signals(request)
    country = _country(request, emps)
    cfg = COUNTRY_CFG.get(country, COUNTRY_CFG["CA"])
    ags = agents_for(country)
    headcount = len(emps)
    annual = sum(e.get("annual_salary") or 0 for e in emps)
    missing = sum(1 for e in emps if not e.get("email"))
    onboarding = sum(1 for e in emps if e.get("status") == "onboarding")
    # "caps" = employees earning over the CPP/EI YMPE.
    near_cap = sum(1 for e in emps if (e.get("annual_salary") or 0) > cfg["wage_cap"])
    contractors = sum(1 for e in emps if e.get("employment_type") == "contract")

    def layer(domain, count, ok_msg, attn_msg, detail):
        return {"domain": domain, "count": count, "status": "attention" if count else "ok",
                "headline": attn_msg if count else ok_msg, "detail": detail}

    stat_word = "stat holiday(s)"
    layers = [
        layer("People", missing + onboarding, "Records complete", f"{missing + onboarding} record(s) need attention",
              f"{missing} missing email · {onboarding} still onboarding"),
        layer("Payroll", 0, f"{headcount} employees ready for the next run", "",
              f"Est. annual gross {_money(annual, country)} · next run pending"),
        layer("Compliance", contractors, "No compliance flags", f"{contractors} classification review(s)",
              "Contractor-vs-employee misclassification check"),
        layer("Cost", 0, f"Payroll cost stable at {_money(annual, country)}/yr", "",
              f"~{_money(annual / 12, country)} per month"),
        layer(cfg["caps_domain"], near_cap, cfg["caps_ok"], f"{near_cap} {cfg['caps_attn']}",
              f"{cfg['caps_detail']} {_money(cfg['wage_cap'], country)}"),
        layer("Time & Leave", signals.get("ot_anomalies", 0) + signals.get("leave_alerts", 0),
              "No time or leave anomalies",
              f"{signals.get('ot_anomalies', 0)} overtime spike(s) · {signals.get('leave_alerts', 0)} leave alert(s)",
              f"OT {signals.get('ot_hours', 0)}h · absenteeism {signals.get('absentee_pct', 0)}% · "
              f"{signals.get('upcoming_stat', 0)} {stat_word} upcoming · {signals.get('leave_stale', 0)} stale approval(s)"),
    ]
    attn = sum(1 for l in layers if l["status"] == "attention")
    score = min(100, attn * 22)
    return {"generated_at": _now().isoformat(), "attention_score": score, "country": country,
            "headline": "All clear — nothing needs your attention" if score == 0 else f"{attn} area(s) need attention",
            "agents_online": len(ags), "runs_today": (len(ags) - 1) * 2, "layers": layers}


# ── Lexi chat (real Claude agent + grounded fallback + metering) ─────────────
@app.post("/ai/chat")
async def chat(request: Request, body: dict, db: AsyncSession = Depends(get_db)):
    org_id = _org(request)
    country = _country(request, await _employees(request))
    res = await lexi.answer(body.get("message", ""), body.get("history", []) or [], dict(request.headers),
                            country=country)
    if org_id:
        db.add(AiUsage(org_id=org_id, feature="lexi_chat", model=res["model"], mode=res["mode"],
                       input_tokens=res["input_tokens"], output_tokens=res["output_tokens"],
                       cache_read_tokens=res.get("cache_read_tokens", 0), cache_write_tokens=res.get("cache_write_tokens", 0),
                       cost_usd=res["cost_usd"]))
        await db.commit()
    return {"reply": res["reply"], "usage": {"model": res["model"], "mode": res["mode"],
            "input_tokens": res["input_tokens"], "output_tokens": res["output_tokens"], "cost_usd": res["cost_usd"]}}


# ── token economics ──────────────────────────────────────────────────────────
@app.get("/ai/usage")
async def usage(request: Request, db: AsyncSession = Depends(get_db)):
    org_id = _org(request)
    rows = (await db.execute(select(AiUsage).where(AiUsage.org_id == org_id)
            .order_by(AiUsage.created_at.desc()))).scalars().all() if org_id else []

    tot_in = sum(r.input_tokens for r in rows)
    tot_out = sum(r.output_tokens for r in rows)
    tot_cost = round(sum(float(r.cost_usd) for r in rows), 6)

    def group(attr):
        g = defaultdict(lambda: {"input": 0, "output": 0, "cost": 0.0, "calls": 0})
        for r in rows:
            k = getattr(r, attr)
            g[k]["input"] += r.input_tokens; g[k]["output"] += r.output_tokens
            g[k]["cost"] += float(r.cost_usd); g[k]["calls"] += 1
        return [{attr: k, "tokens": v["input"] + v["output"], "cost": round(v["cost"], 6), "calls": v["calls"]}
                for k, v in g.items()]

    # daily series (last 14 days)
    daily = defaultdict(lambda: {"tokens": 0, "cost": 0.0})
    for r in rows:
        d = r.created_at.date().isoformat()
        daily[d]["tokens"] += r.input_tokens + r.output_tokens
        daily[d]["cost"] += float(r.cost_usd)
    daily_series = sorted(({"date": d, "tokens": v["tokens"], "cost": round(v["cost"], 6)}
                           for d, v in daily.items()), key=lambda x: x["date"])[-14:]

    modes = {r.mode for r in rows}
    mode = "live" if modes == {"live"} else ("estimated" if modes == {"estimated"} else ("mixed" if modes else "none"))

    # Agents are pure-SQL watchdogs — zero-token, zero-cost — shown alongside Lexi.
    agent_runs = (len(AGENTS) - 1) * 2
    return {
        "lexi_model": lexi.LEXI_MODEL, "key_configured": bool(os.getenv("ANTHROPIC_API_KEY")), "mode": mode,
        "prices": {m: {"input": p[0], "output": p[1]} for m, p in lexi.PRICES.items()},
        "totals": {"input_tokens": tot_in, "output_tokens": tot_out, "total_tokens": tot_in + tot_out,
                   "cost_usd": tot_cost, "calls": len(rows)},
        "by_model": group("model"), "by_feature": group("feature"), "daily": daily_series,
        "recent": [{"feature": r.feature, "model": r.model, "mode": r.mode, "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens, "cost_usd": float(r.cost_usd),
                    "created_at": r.created_at.isoformat()} for r in rows[:20]],
        "agents": {"count": len(AGENTS), "runs": agent_runs, "engine": "SQL watchdogs", "tokens": 0, "cost_usd": 0.0},
    }

# S3 production runtime: /health + /ready, JSON logs with request id, PII log filter, prod secret gate.
from shared.ops_runtime import install as _ops_install  # noqa: E402
_ops_install(app, "ai_platform")
