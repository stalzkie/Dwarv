"""Step 10A: builds the transparency panel's /api/session payload purely
from the active session's real event log (telemetry/logger.py). No fake
data -- if there's no active session, callers get {"active": False} and the
panel renders "No active session." (DWARV_PLAN.md Step 10A's hard
constraint), never a placeholder model name or invented decision.
"""

from pathlib import Path

from dwarv.telemetry.logger import current_session_log_path, read_events

MAX_DECISIONS = 50


def build_session_summary(log_dir: str | Path) -> dict:
    log_path = current_session_log_path(log_dir)
    if log_path is None:
        return {"active": False}

    events = read_events(log_path)
    if not events:
        return {"active": False}

    model_id = None
    explanation = None
    ctx_size = None
    sandbox_tier = None
    sandbox_tier_detail = None
    repo_root = None
    test_command = None
    decisions: list[dict] = []
    latest_sample = None
    turns_completed = 0

    for ev in events:
        kind = ev.get("event")
        if kind == "run_started":
            sandbox_tier = ev.get("sandbox_tier")
            sandbox_tier_detail = ev.get("sandbox_tier_detail")
            repo_root = ev.get("repo_root")
            test_command = ev.get("test_command")
        elif kind == "model_loaded":
            model_id = ev.get("model_id")
            explanation = ev.get("explanation")
            ctx_size = ev.get("ctx_size")
        elif kind == "decision":
            decisions.append(ev)
        elif kind == "monitor_sample":
            latest_sample = ev
        elif kind == "turn_started":
            turns_completed = max(turns_completed, (ev.get("turn_index") or 0) + 1)

    return {
        "active": True,
        "session_id": events[0].get("session_id"),
        "model_id": model_id,
        "explanation": explanation,
        "ctx_size": ctx_size,
        "sandbox_tier": sandbox_tier,
        "sandbox_tier_detail": sandbox_tier_detail,
        "repo_root": repo_root,
        "test_command": test_command,
        "turns_completed": turns_completed,
        "decisions": decisions[-MAX_DECISIONS:],
        "latest_sample": latest_sample,
        "event_count": len(events),
    }
