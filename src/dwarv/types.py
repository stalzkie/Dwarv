from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from dwarv.controller.actions import Action


@dataclass
class State:
    attempt_idx: int
    attempts_left: int
    time_left_s: float
    ram_limit_mb: float
    server_rss_mb: float
    headroom_mb: float
    sys_available_mb: float
    model_id: str
    ctx_size: int
    last_failure: str | None
    failure_history: list[str] = field(default_factory=list)
    repeated_same_failure: int = 0
    model_load_cost_s: dict[str, float] = field(default_factory=dict)
    est_rss_mb: dict[tuple[str, int], float] = field(default_factory=dict)


@dataclass
class Decision:
    action: "Action"
    reason: str
    inputs_snapshot: dict
    narration: str  # the sentence actually said to the user, derived from reason/inputs_snapshot


@dataclass
class Turn:
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime


@dataclass
class Session:
    session_id: str
    started_at: datetime
    model_id: str
    sandbox_tier: int
    turns: list[Turn] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)


@dataclass
class EvalResult:
    """Internal eval harness only (Step 9) -- never surfaced to the end user."""

    system: str
    task_id: str
    passed: bool  # final hidden-test verdict
    attempts: int
    wall_s: float
    peak_rss_mb: float
    budget_violation: bool
    final_code: str | None
    decisions: list[dict] = field(default_factory=list)
    stop_reason: str = "PASS"  # PASS | BUDGET_TIME | BUDGET_ATTEMPTS | BUDGET_RAM | CRASH | GAVE_UP
