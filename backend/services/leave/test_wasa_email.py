"""WASA2 #8 — the leave-decision e-mail escapes the reviewer note and names (no HTML injection).
Run:  cd backend/services/leave && python -m pytest -q test_wasa_email.py
"""
import asyncio
from types import SimpleNamespace

import routers as R

XSS = "<img src=x onerror=alert(1)>"


def test_leave_decision_email_escapes_note_and_names(monkeypatch):
    sent = {}

    async def fake_employees(_request):
        return {"e1": {"work_email": "e@x.example"}}

    async def fake_send(to, subject, body, html=None):
        sent.update(to=to, body=body, html=html)
        return True
    monkeypatch.setattr(R, "_employees", fake_employees)
    monkeypatch.setattr(R, "send_email", fake_send)
    r = SimpleNamespace(employee_id="e1", employee_name=f"Priya {XSS}", leave_type_name="Vacation", leave_type_code="VAC",
                        start_date="2026-10-21", end_date="2026-10-22", days=2, status="approved",
                        reviewed_by_name=f"Boss {XSS}", review_note=f'<a href="javascript:alert(1)">see</a>')

    async def go():
        await R._notify_leave_decision(None, r)
        await asyncio.sleep(0)
    asyncio.new_event_loop().run_until_complete(go())
    h = sent["html"]
    assert "<img" not in h and '<a href="javascript' not in h
    assert "&lt;img src=x onerror=alert(1)&gt;" in h and "&lt;a href=&quot;javascript:alert(1)&quot;&gt;" in h
    assert "<b>approved</b>" in h                                           # the intended bold is kept
    assert "has been approved by Boss" in sent["body"] and "<b>" not in sent["body"]   # plain text has no tags
