"""WASA2 #6 — the MCP stdio bridge never returns exception text to the client.
Run:  cd backend/services/ai_platform && PYTHONPATH=../.. python -m pytest -q test_wasa_bridge_errors.py
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "mcp_server"))

import stdio_bridge as SB  # noqa: E402

SECRET = "ConnectError: https://hrm.internal:8100/api?token=lxai_supersecret"


class Boom:
    async def call(self, name, arguments):
        raise RuntimeError(SECRET)


def test_unexpected_tool_error_is_masked_with_a_ref(caplog):
    out = asyncio.new_event_loop().run_until_complete(SB.call_tool_safely(Boom(), "leave_balance", None))
    assert set(out) == {"error", "ref"} and len(out["ref"]) == 8
    assert "lxai_" not in str(out) and "hrm.internal" not in str(out) and "RuntimeError" not in str(out)
    assert SECRET in caplog.text


def test_normal_tool_answer_is_unchanged():
    out = asyncio.new_event_loop().run_until_complete(SB.call_tool_safely(SB.FakeBackend(), "leave_balance", {}))
    assert out["balances"][0]["remaining"] == 7
