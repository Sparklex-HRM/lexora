"""``/ai/agents/onboarding/*`` — the Onboarding Agent workspace (see agents/lifecycle_router.py for the route list)."""
from __future__ import annotations

from agents.lifecycle_router import make_router

from . import AGENT
from .checks import CHECKS, DEFAULTS
from .run import run_onboarding

router = make_router(agent=AGENT, slug="onboarding", run_fn=run_onboarding, defaults=DEFAULTS, checks=CHECKS, label="Onboarding Agent")
