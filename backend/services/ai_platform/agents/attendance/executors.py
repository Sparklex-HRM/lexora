"""Attendance Agent executors — one tool: ``attendance.chase`` (tier 0, the deduplicated task to the employee / manager /
HR; shadow: never runs). The agent has NO tool that edits a punch, approves a regularisation or leave, or acknowledges an
OT breach — the person does that on the existing screens (tested by absence)."""
from __future__ import annotations

import sys

sys.path.append("/app")
from agents.lifecycle_common import register_chase_executor  # noqa: E402

AGENT = "attendance"
TOOLS = {"attendance.chase": 0}

register_chase_executor(AGENT)
