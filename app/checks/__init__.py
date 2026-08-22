"""Shared types for individual health checks.

Every check module exposes a `run(config) -> list[CheckResult]` function.
`main.py` collects all of them, computes a deterministic fallback status from
the worst one, and hands the whole list to codex_client to build the AI
prompt.
"""

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    OK = "ok"
    WARN = "warning"
    CRIT = "critical"


_SEVERITY = {Status.OK: 0, Status.WARN: 1, Status.CRIT: 2}


def worst(statuses):
    statuses = list(statuses)
    if not statuses:
        return Status.OK
    return max(statuses, key=lambda s: _SEVERITY[s])


@dataclass
class CheckResult:
    category: str
    name: str
    status: Status
    note: str
    data: dict = field(default_factory=dict)

    def to_prompt_line(self) -> str:
        return f"- [{self.status.value.upper()}] {self.category}/{self.name}: {self.note}"
