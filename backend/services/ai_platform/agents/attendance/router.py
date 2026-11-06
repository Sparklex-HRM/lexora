"""``/ai/agents/attendance/*`` — the Attendance Agent workspace (route list: agents/lifecycle_router.py)."""
from __future__ import annotations

from agents.lifecycle_router import make_router

from . import AGENT
from .checks import CHECKS, DEFAULTS
from .run import run_attendance

router = make_router(agent=AGENT, slug="attendance", run_fn=run_attendance, defaults=DEFAULTS, checks=CHECKS, label="Attendance Agent")
