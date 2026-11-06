"""G6 (2026-10-23) — /ai/internal/complete: internal-token only, honest when not configured,
cap-blocked, shielded prompt, capped output, metered. No network, no paid call (fake client).

Run:  cd backend/services/ai_platform && PYTHONPATH="../..;." python -m pytest -q test_internal_complete.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)
os.environ.pop("ANTHROPIC_API_KEY", None)

from shared.utils.internal import internal_headers  # noqa: E402

import ai_config as CFG  # noqa: E402
import claude_client as LLM  # noqa: E402
import internal_routers as IR  # noqa: E402
import metering as MT  # noqa: E402


class Req:
    def __init__(self, headers):
        self.headers = headers


class Fake:
    def __init__(self, text):
        self.text, self.requests = text, []
        self.beta = NS(messages=NS(create=self.create))

    async def create(self, **kw):
        self.requests.append(kw)
        return NS(content=[NS(type="text", text=self.text)], stop_reason="end_turn", usage=NS(input_tokens=50, output_tokens=20), model=kw["model"])


@pytest.fixture
def env(monkeypatch):
    state = {"key": None, "used": 0, "metered": []}
    cfg = {**CFG.defaults("org-1"), "_key_enc": None}

    async def _load(org):
        return dict(cfg)

    async def _totals(org, *a, **kw):
        return {"tokens": state["used"], "cost_cad": 0.0}

    async def _record(org, **kw):
        state["metered"].append(kw)
    monkeypatch.setattr(CFG, "load", _load)
    monkeypatch.setattr(CFG, "resolve_key", lambda c: (state["key"], "platform" if state["key"] else None))
    monkeypatch.setattr(CFG, "sdk_available", lambda: True)
    monkeypatch.setattr(MT, "month_totals", _totals)
    monkeypatch.setattr(MT, "record", _record)
    state["cfg"] = cfg
    yield state
    LLM.set_fake(None)


def run(c):
    return asyncio.run(c)


BODY = {"org_id": "org-1", "feature": "hiring.resume_parse", "system": "Extract skills.",
        "prompt": "Reach me at asha@example.com or 9876543210. Skills: Python.", "max_tokens": 99999}


def test_needs_the_internal_token(env):
    with pytest.raises(HTTPException) as ex:
        run(IR.internal_complete(BODY, Req({"x-org-id": "org-1", "x-role": "org_admin"})))
    assert ex.value.status_code == 404
    with pytest.raises(HTTPException):
        run(IR.internal_complete({**BODY, "feature": "anything"}, Req(internal_headers("org-1", "employee"))))


def test_not_configured_is_honest(env):
    r = run(IR.internal_complete(BODY, Req(internal_headers("org-1", "employee"))))
    assert r["ok"] is False and r["reason"] == "not_configured"


def test_shielded_capped_metered_and_cap_blocks(env):
    env["key"] = "sk-test"
    fake = Fake('{"skills": ["python"], "contact": "[[EMAIL_1]]"}')
    LLM.set_fake(fake)
    r = run(IR.internal_complete(BODY, Req(internal_headers("org-1", "employee"))))
    sent = fake.requests[0]
    assert "asha@example.com" not in str(sent["messages"]) and "9876543210" not in str(sent["messages"])
    assert sent["max_tokens"] == IR.MAX_OUT and sent["output_config"]["effort"] == "low"
    assert r["ok"] and "asha@example.com" in r["text"] and r["masked"] >= 2      # restored for the caller only
    assert env["metered"][0]["feature"] == "hiring.resume_parse" and env["metered"][0]["channel"] == "internal"
    env["cfg"].update(monthly_cap_tokens=100, cap_mode="hard")
    env["used"] = 500
    blocked = run(IR.internal_complete(BODY, Req(internal_headers("org-1", "employee"))))
    assert blocked["ok"] is False and blocked["reason"] == "cap_reached"
