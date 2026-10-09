import time
from dataclasses import dataclass


@dataclass
class SqueezeEvent:
    new_ram_limit_mb: float
    reason: str = "memory squeeze"
    after_turns: int | None = None
    after_s: float | None = None
    fired: bool = False


class SqueezeScheduler:
    """Deterministic memory-squeeze scheduler (Step 8): changes a session's
    effective RAM budget at a pre-registered point (after N turns, or after
    a fixed elapsed time), for the live demo and the Step 9 internal eval's
    ablations. Budget-cut mode only -- the plan's optional competing-process
    mode is demo-only and intentionally not implemented here."""

    def __init__(self):
        self.events: list[SqueezeEvent] = []
        self._start_t = time.monotonic()

    def schedule(
        self,
        new_ram_limit_mb: float,
        reason: str = "memory squeeze",
        after_turns: int | None = None,
        after_s: float | None = None,
    ) -> SqueezeEvent:
        event = SqueezeEvent(
            new_ram_limit_mb=new_ram_limit_mb,
            reason=reason,
            after_turns=after_turns,
            after_s=after_s,
        )
        self.events.append(event)
        return event

    def maybe_fire(self, turn_index: int) -> SqueezeEvent | None:
        """Returns the first due, not-yet-fired event (marking it fired), or
        None. Call this repeatedly (e.g. once per retry attempt) so a
        time-based event can fire mid-turn, not just between turns."""
        elapsed = time.monotonic() - self._start_t
        for event in self.events:
            if event.fired:
                continue
            turns_due = event.after_turns is not None and turn_index >= event.after_turns
            time_due = event.after_s is not None and elapsed >= event.after_s
            if turns_due or time_due:
                event.fired = True
                return event
        return None
