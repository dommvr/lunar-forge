"""Plan-only workflow."""

from __future__ import annotations

from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig
from lunar_forge.planning import Plan


def run(request: str, config: AppConfig | None = None) -> Plan:
    agent = CodeAgent(config or AppConfig())
    return agent.plan(request)
