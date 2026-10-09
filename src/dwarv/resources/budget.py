import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

DEFAULT_BUDGETS_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "budgets.yaml"


def load_budgets_config(path: str | Path = DEFAULT_BUDGETS_CONFIG_PATH) -> dict:
    import yaml

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class BudgetStatus(Enum):
    OK = "OK"
    WARN = "WARN"
    VIOLATION = "VIOLATION"


class BudgetViolation(Exception):
    """Raised by callers that choose to treat a VIOLATION as fatal; BudgetManager itself
    only records it -- killing the runtime is the agent loop's responsibility (Step 6)."""

    def __init__(self, rss_mb: float, ram_limit_mb: float):
        super().__init__(f"server RSS {rss_mb:.1f}MB exceeds ram_limit {ram_limit_mb:.1f}MB")
        self.rss_mb = rss_mb
        self.ram_limit_mb = ram_limit_mb


@dataclass
class BudgetChangeEvent:
    old_limit_mb: float
    new_limit_mb: float
    reason: str
    ts: float = field(default_factory=time.monotonic)


class BudgetManager:
    """Tracks RAM/time/attempt budget for one session turn. `rss_fn` and
    `sys_available_fn` stand in for a ResourceMonitor -- tests pass fakes."""

    def __init__(
        self,
        ram_limit_mb: float,
        time_limit_s: float,
        max_attempts: int,
        warn_fraction: float = 0.10,
        rss_fn: Callable[[], float] | None = None,
        sys_available_fn: Callable[[], float] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ram_limit_mb = ram_limit_mb
        self.time_limit_s = time_limit_s
        self.max_attempts = max_attempts
        self.warn_fraction = warn_fraction
        self._rss_fn = rss_fn or (lambda: 0.0)
        self._sys_available_fn = sys_available_fn or (lambda: float("inf"))
        self._clock = clock
        self._start_t = self._clock()
        self._attempts_used = 0
        self._changes: list[BudgetChangeEvent] = []
        self._violations: list[dict] = []

    def set_ram_limit(self, new_limit_mb: float, reason: str) -> None:
        self._changes.append(BudgetChangeEvent(self.ram_limit_mb, new_limit_mb, reason))
        self.ram_limit_mb = new_limit_mb

    def server_rss_mb(self) -> float:
        return self._rss_fn()

    def sys_available_mb(self) -> float:
        return self._sys_available_fn()

    def headroom_mb(self) -> float:
        return self.ram_limit_mb - self.server_rss_mb()

    def remaining_time_s(self) -> float:
        return max(0.0, self.time_limit_s - (self._clock() - self._start_t))

    def attempts_left(self) -> int:
        return max(0, self.max_attempts - self._attempts_used)

    def record_attempt(self) -> None:
        self._attempts_used += 1

    def check(self) -> BudgetStatus:
        rss = self.server_rss_mb()
        if rss > self.ram_limit_mb:
            self._violations.append(
                {"rss_mb": rss, "ram_limit_mb": self.ram_limit_mb, "ts": self._clock()}
            )
            return BudgetStatus.VIOLATION
        if self.headroom_mb() < self.warn_fraction * self.ram_limit_mb:
            return BudgetStatus.WARN
        return BudgetStatus.OK

    @property
    def violations(self) -> list[dict]:
        return list(self._violations)

    @property
    def changes(self) -> list[BudgetChangeEvent]:
        return list(self._changes)
