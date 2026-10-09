from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from dwarv.resources.budget import BudgetManager
    from dwarv.resources.monitor import ResourceMonitor
    from dwarv.runtime.base import RuntimeAdapter
    from dwarv.telemetry.logger import EventLogger


@dataclass
class Result:
    system: str
    task_id: str
    passed: bool  # final hidden-test verdict
    attempts: int
    wall_s: float
    peak_rss_mb: float
    budget_violation: bool
    final_code: str | None
    decisions: list[dict] = field(
        default_factory=list
    )  # empty for baselines unless they make choices
    stop_reason: str = "PASS"  # PASS | BUDGET_TIME | BUDGET_ATTEMPTS | BUDGET_RAM | CRASH | GAVE_UP


class System(Protocol):
    name: str

    def solve(
        self,
        task,
        budget: "BudgetManager",
        runtime: "RuntimeAdapter",
        monitor: "ResourceMonitor",
        logger: "EventLogger",
    ) -> Result: ...
