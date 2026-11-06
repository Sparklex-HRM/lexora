"""``/ai/agents/exit/*`` — the Exit Agent workspace (route list: agents/lifecycle_router.py)."""
from __future__ import annotations

from agents.lifecycle_router import make_router

from . import AGENT
from .checks import CHECKS, DEFAULTS
from .run import run_exit

router = make_router(agent=AGENT, slug="exit", run_fn=run_exit, defaults=DEFAULTS, checks=CHECKS, label="Exit Agent")
