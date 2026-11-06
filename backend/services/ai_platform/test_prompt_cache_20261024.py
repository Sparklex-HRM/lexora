"""Prompt caching (2026-10-24) — every Lexora HRM Claude call site. No paid call, no network.

Asserts, on the exact request payload:
  * cache_control sits on the STATIC system block only (tools + rules are the cached prefix);
  * nothing variable (company name, today's date, role, the question) is inside a cached block;
  * multi-turn tool loops add top-level automatic caching (extra_body.cache_control), next to the
    server-side fallbacks the assistant already sends;
  * usage carries cache_read_tokens / cache_write_tokens and the cost prices them
    (read 0.1x input, write 1.25x; 2x for the 1-hour TTL); the monthly cap counts reads at 0.1x;
  * with ANTHROPIC_API_KEY blank (the local default) the copilot / Lexi fall back unchanged.
Run:  cd backend/services/ai_platform && python -m pytest -q test_prompt_cache_20261024.py
"""
import json
import os
import sys
import types
from datetime import date
from types import SimpleNamespace as NS

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from test_d3_assistant import FakeClaude, ctx, db, run  # noqa: E402,F401  (db = autouse SQLite fixture)

import assistant as AS  # noqa: E402
import claude_client as LLM  # noqa: E402
import copilot as CP  # noqa: E402
import lexi  # noqa: E402
import llm  # noqa: E402
import metering as MT  # noqa: E402

ORG_NAME = "MAPLE TEXTILES"


def cached_text(req) -> str:
    """Everything at or before the last system breakpoint (tools + static system)."""
    sys_ = req["system"]
    assert isinstance(sys_, list)
    idx = [i for i, b in enumerate(sys_) if "cache_control" in b]
    assert idx, "no system breakpoint"
    return json.dumps(req.get("tools") or []) + "\n".join(b["text"] for b in sys_[: idx[-1] + 1])


# ── claude_client helpers ─────────────────────────────────────────────────────
def test_system_blocks_and_auto_cache_helpers():
    assert LLM.system_blocks("S", "D") == [{"type": "text", "text": "S", "cache_control": {"type": "ephemeral"}},
                                           {"type": "text", "text": "D"}]
    assert LLM.system_blocks("S", ttl="1h")[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    with pytest.raises(ValueError):
        LLM.cache_control("1d")
    assert LLM.with_auto_cache({"fallbacks": "default"}) == {"fallbacks": "default", "cache_control": {"type": "ephemeral"}}
    assert LLM.stable_json({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
    u = LLM.usage_fields(NS(input_tokens=5, output_tokens=2, cache_read_input_tokens=900, cache_creation_input_tokens=40))
    assert u == {"input_tokens": 5, "output_tokens": 2, "cache_read_tokens": 900, "cache_write_tokens": 40}
    assert LLM.usage_fields(NS(input_tokens=5, output_tokens=2))["cache_read_tokens"] == 0     # older SDK / fakes
    assert LLM.min_cacheable_tokens("claude-opus-5-5") == 512 and LLM.min_cacheable_tokens("claude-haiku-4-5") == 4096


def test_complete_payload_and_usage():
    fake = FakeClaude([("text", "ok")])
    r = run(LLM.complete(fake, model="claude-opus-5-5", system="STATIC RULES", system_dynamic="Today is 2026-10-24.",
                         messages=[{"role": "user", "content": "q"}], auto_cache=True))
    req = fake.requests[0]
    assert req["system"][0] == {"type": "text", "text": "STATIC RULES", "cache_control": {"type": "ephemeral"}}
    assert "cache_control" not in req["system"][1] and "2026-10-24" not in cached_text(req)
    assert req["extra_body"] == {"fallbacks": "default", "cache_control": {"type": "ephemeral"}}
    assert req["betas"] == [LLM.FALLBACK_BETA]
    assert r["cache_read_tokens"] == 0 and r["cache_write_tokens"] == 0
    # single-shot: no automatic breakpoint
    fake = FakeClaude([("text", "ok")])
    run(LLM.complete(fake, model="claude-opus-5-5", system="S", messages=[{"role": "user", "content": "q"}]))
    assert fake.requests[0]["extra_body"] == {"fallbacks": "default"}
    # key test: nothing cached, plain string system as before
    fake = FakeClaude([("text", "OK")])
    run(LLM.ping(fake, "claude-opus-5-5"))
    assert fake.requests[0]["system"] == "Reply with the single word OK." and "cache_control" not in fake.requests[0]["extra_body"]


# ── assistant (tool loop) ─────────────────────────────────────────────────────
def test_assistant_cached_prefix_has_no_company_date_or_role():
    reqs = []
    for org, day, role in ((ORG_NAME, date(2026, 10, 24), "employee"), ("ANOTHER CO", date(2026, 11, 2), "employee")):
        fake = FakeClaude([("text", "ok")])
        res = run(AS.answer(ctx(), "how many casual leaves do I have?", [], client=fake, model="claude-opus-5-5", effort="medium",
                            role=role, country="CA", today=day, org_name=org))
        assert "cache_read_tokens" in res and "cache_write_tokens" in res
        reqs.append(fake.requests[0])
    for req, org, day in ((reqs[0], ORG_NAME, "2026-10-24"), (reqs[1], "ANOTHER CO", "2026-11-02")):
        cached = cached_text(req)
        for variable in (org, day, "casual leaves do I have"):
            assert variable not in cached
        assert org in req["system"][-1]["text"] and day in req["system"][-1]["text"]
        assert req["extra_body"]["cache_control"] == {"type": "ephemeral"}
    # the cached prefix is byte-identical across companies and days -> one cache entry
    assert reqs[0]["system"][0] == reqs[1]["system"][0] and reqs[0]["tools"] == reqs[1]["tools"]
    # same instructions as before, just re-ordered
    full = AS.system_prompt("employee", "CA", date(2026, 10, 24), ORG_NAME, True)
    assert "Rules:" in full and ORG_NAME in full and "2026-10-24" in full


def test_assistant_accumulates_cache_usage():
    class Cached(FakeClaude):
        async def create(self, **kw):
            r = await super().create(**kw)
            r.usage.cache_read_input_tokens, r.usage.cache_creation_input_tokens = 3000, 200
            return r
    fake = Cached([("tool", "leave_balance", {}), ("text", "You have 8 left [S1].")])
    res = run(AS.answer(ctx(), "leave balance", [], client=fake, model="claude-opus-5-5", effort="medium", role="employee",
                        country="CA", today=date(2026, 10, 24), org_name="K"))
    assert res["cache_read_tokens"] == 6000 and res["cache_write_tokens"] == 400


# ── metering ──────────────────────────────────────────────────────────────────
def test_cost_and_cap_include_cache():
    base = MT.cost_of("claude-opus-5-5", 1000, 200)["cost_usd"]
    assert base == pytest.approx((1000 * 4 + 200 * 20) / 1e6)
    c = MT.cost_of("claude-opus-5-5", 1000, 200, cache_read_tokens=10000, cache_write_tokens=2000)["cost_usd"]
    assert c == pytest.approx((1000 * 4 + 200 * 20 + 10000 * 0.4 + 2000 * 5) / 1e6)
    c1h = MT.cost_of("claude-opus-5-5", 0, 0, cache_write_tokens=1000, cache_ttl="1h")["cost_usd"]
    assert c1h == pytest.approx(1000 * 8 / 1e6)
    assert MT.cap_tokens(1000, 200, 10000, 2000) == 1000 + 200 + 2000 + 1000
    assert MT.cap_tokens(1, 1, 5, 0) == 3                       # reads round up, never free


def test_record_and_totals_carry_cache_columns():
    run(MT.record("org-c", user_id="u", user_email="u@x", channel="chat", feature="assistant_chat", model="claude-opus-5-5",
                  key_source="org", input_tokens=100, output_tokens=50, cost={"cost_usd": 0.001, "cost_cad": None},
                  cache_read_tokens=4000, cache_write_tokens=300))
    t = run(MT.month_totals("org-c"))
    assert t["cache_read_tokens"] == 4000 and t["cache_write_tokens"] == 300 and t["tokens"] == 100 + 50 + 300 + 400
    s = run(MT.summary("org-c"))
    assert s["by_feature"][0]["cache_read_tokens"] == 4000 and s["recent"][0]["cache_write_tokens"] == 300
    assert any("ai_usage_events ADD COLUMN IF NOT EXISTS cache_read_tokens" in x for x in MT.CACHE_COLUMN_DDL)


# ── copilot (llm.py) + Lexi (lexi.py) with a fake SDK ───────────────────
def _fake_sdk(monkeypatch, turns):
    sent = []

    class _Msgs:
        async def create(self, **kw):
            sent.append(kw)
            kind, *rest = turns.pop(0)
            usage = NS(input_tokens=80, output_tokens=20, cache_read_input_tokens=1500, cache_creation_input_tokens=0)
            if kind == "tool":
                return NS(content=[NS(type="tool_use", id="t1", name=rest[0], input={})], stop_reason="tool_use", usage=usage)
            return NS(content=[NS(type="text", text=rest[0])], stop_reason="end_turn", usage=usage)

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Msgs()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(AsyncAnthropic=_Client))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return sent


def test_copilot_llm_payload(monkeypatch):
    sent = _fake_sdk(monkeypatch, [("text", "ok")])
    res = run(llm.complete(system=CP.SYSTEM_STATIC, system_dynamic=CP.system_context("employee", "CA", date(2026, 10, 24), ORG_NAME),
                           messages=[{"role": "user", "content": "hi"}], tools=CP.TOOLS))
    req = sent[0]
    cached = cached_text(req)
    assert ORG_NAME not in cached and "2026-10-24" not in cached and "employee'" not in cached
    assert req["extra_body"] == {"cache_control": {"type": "ephemeral"}}
    assert res["cache_read_tokens"] == 1500 and res["cost_usd"] == pytest.approx(lexi.cost_usd(res["model"], 80, 20, 1500, 0))
    assert "{" not in CP.SYSTEM_STATIC                                   # no un-filled placeholders


def test_lexi_payload_and_cost(monkeypatch):
    sent = _fake_sdk(monkeypatch, [("text", "You have 12 employees.")])
    res = run(lexi._claude("how many employees?", [], {}, ORG_NAME, "CA"))
    req = sent[0]
    assert ORG_NAME not in cached_text(req) and ORG_NAME in req["system"][-1]["text"]
    assert req["extra_body"] == {"cache_control": {"type": "ephemeral"}} and req["tools"] == lexi.TOOLS
    assert res["cache_read_tokens"] == 1500 and res["cost_usd"] == lexi.cost_usd(lexi.LEXI_MODEL, 80, 20, 1500, 0)
    assert lexi.cost_usd("claude-opus-5", 0, 0, 1_000_000, 0) == pytest.approx(0.5)


def test_blank_key_falls_back(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not llm.configured()
    assert run(lexi._claude("x", [], {}, "org", "CA")) is None
