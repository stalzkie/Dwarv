from dwarv.controller.actions import Action
from dwarv.controller.policy import decide
from dwarv.types import State

EST_RSS_MB = {
    ("small", 4096): 1500.0,
    ("medium", 4096): 4000.0,
    ("large", 4096): 9000.0,
}
LOAD_COST_S = {"small": 2.0, "medium": 5.0, "large": 12.0}


def _state(**overrides) -> State:
    defaults = dict(
        attempt_idx=0,
        attempts_left=3,
        time_left_s=60.0,
        ram_limit_mb=8000.0,
        server_rss_mb=2000.0,
        headroom_mb=6000.0,
        sys_available_mb=6000.0,
        model_id="medium",
        ctx_size=4096,
        last_failure=None,
        failure_history=[],
        repeated_same_failure=0,
        model_load_cost_s=dict(LOAD_COST_S),
        est_rss_mb=dict(EST_RSS_MB),
    )
    defaults.update(overrides)
    return State(**defaults)


def test_low_headroom_triggers_downgrade():
    state = _state(ram_limit_mb=4200.0, server_rss_mb=4000.0, headroom_mb=200.0)
    decision = decide(state)
    assert decision.action == Action.SWITCH_SMALLER_MODEL


def test_larger_model_chosen_only_when_rss_fits():
    roomy = _state(
        ram_limit_mb=10000.0,
        server_rss_mb=4000.0,
        headroom_mb=6000.0,
        last_failure="WRONG_OUTPUT",
        repeated_same_failure=2,
    )
    assert decide(roomy).action == Action.SWITCH_LARGER_MODEL

    tight = _state(
        ram_limit_mb=5000.0,
        server_rss_mb=4000.0,
        headroom_mb=1000.0,
        last_failure="WRONG_OUTPUT",
        repeated_same_failure=2,
    )
    assert decide(tight).action == Action.RETRY_HIGHER_TEMP


def test_repeated_logic_failure_escalates():
    first_occurrence = _state(last_failure="RUNTIME_ERROR", repeated_same_failure=0)
    assert decide(first_occurrence).action == Action.RETRY_WITH_FEEDBACK

    repeated = _state(
        ram_limit_mb=10000.0,
        server_rss_mb=4000.0,
        headroom_mb=6000.0,
        last_failure="RUNTIME_ERROR",
        repeated_same_failure=2,
    )
    assert decide(repeated).action == Action.SWITCH_LARGER_MODEL


def test_no_attempts_left_stops():
    state = _state(attempts_left=0)
    assert decide(state).action == Action.STOP_SAFELY


def test_reload_cap_enforced():
    state = _state(ram_limit_mb=4200.0, server_rss_mb=4000.0, headroom_mb=200.0)
    decision = decide(state, reloads_used=2, max_reloads_per_turn=2)
    assert decision.action == Action.STOP_SAFELY
