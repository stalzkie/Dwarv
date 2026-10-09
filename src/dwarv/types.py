from dataclasses import dataclass, field


@dataclass
class State:
    task_id: str
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
