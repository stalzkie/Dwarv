from dwarv.controller.actions import Action
from dwarv.models.suite import MODEL_ORDER
from dwarv.types import Decision, State

DEFAULT_WARN_FRACTION = 0.10
DEFAULT_SAFETY_MARGIN = 0.10
DEFAULT_MAX_RELOADS_PER_TURN = 2

SYNTAX_FAILURES = {"SYNTAX_ERROR", "EMPTY_OR_NO_CODE", "IMPORT_ERROR"}
LOGIC_FAILURES = {"WRONG_OUTPUT", "RUNTIME_ERROR"}

RELOAD_ACTIONS = {Action.SHRINK_CONTEXT, Action.SWITCH_SMALLER_MODEL, Action.SWITCH_LARGER_MODEL}


def decide(
    state: State,
    *,
    warn_fraction: float = DEFAULT_WARN_FRACTION,
    safety_margin: float = DEFAULT_SAFETY_MARGIN,
    max_reloads_per_turn: int = DEFAULT_MAX_RELOADS_PER_TURN,
    reloads_used: int = 0,
    cheapest_action_cost_s: float = 0.0,
) -> Decision:
    """Pure function: same state in, same Decision out. DWARV_PLAN.md Step 7's
    ordered rules; the first rule that matches fires."""
    safety_margin_mb = safety_margin * state.ram_limit_mb

    # 1. Hard stop: no attempts left, or not enough time for the cheapest next action.
    if state.attempts_left <= 0 or state.time_left_s < cheapest_action_cost_s:
        reason = "no attempts left" if state.attempts_left <= 0 else "insufficient time left"
        return Decision(
            action=Action.STOP_SAFELY,
            reason=reason,
            inputs_snapshot=_snapshot(state),
            narration="I've run out of attempts or time, so I'm stopping here.",
        )

    # 2. Budget shrank / violation risk: step down before anything else.
    if (
        state.server_rss_mb > state.ram_limit_mb
        or state.headroom_mb < warn_fraction * state.ram_limit_mb
    ):
        if state.ctx_size > _min_ctx(state):
            decision = Decision(
                action=Action.SHRINK_CONTEXT,
                reason="headroom below warn_fraction, a smaller context fits",
                inputs_snapshot=_snapshot(state),
                narration="Memory is getting tight, so I'm shrinking the context window to free up room.",
            )
        else:
            smaller = _smaller_model(state, safety_margin_mb)
            if smaller:
                decision = Decision(
                    action=Action.SWITCH_SMALLER_MODEL,
                    reason=f"headroom below warn_fraction, stepping down to {smaller}",
                    inputs_snapshot=_snapshot(state),
                    narration=f"Memory is tight, so I'm stepping down to {smaller}.",
                )
            else:
                decision = Decision(
                    action=Action.STOP_SAFELY,
                    reason="headroom below warn_fraction, no smaller model fits",
                    inputs_snapshot=_snapshot(state),
                    narration="Memory is too tight to keep going safely, so I'm stopping here.",
                )
        return _cap_reloads(decision, reloads_used, max_reloads_per_turn)

    # 3. Syntax/format failures: cheap retry first, then lower the temperature.
    if state.last_failure in SYNTAX_FAILURES:
        if state.repeated_same_failure >= 2:
            return Decision(
                action=Action.RETRY_LOWER_TEMP,
                reason=f"{state.last_failure} repeated, lowering temperature",
                inputs_snapshot=_snapshot(state),
                narration="That keeps coming out malformed, so I'm trying again with a lower temperature.",
            )
        return Decision(
            action=Action.RETRY_WITH_FEEDBACK,
            reason=f"{state.last_failure}, retrying with feedback",
            inputs_snapshot=_snapshot(state),
            narration="That didn't parse, so I'm retrying with the error as feedback.",
        )

    # 4. Logic failures: retry, then escalate to a larger model if one fits.
    if state.last_failure in LOGIC_FAILURES:
        if state.repeated_same_failure >= 2:
            larger = _larger_model(state, safety_margin_mb)
            if larger:
                decision = Decision(
                    action=Action.SWITCH_LARGER_MODEL,
                    reason=f"{state.last_failure} repeated, escalating to {larger}",
                    inputs_snapshot=_snapshot(state),
                    narration=f"That's still wrong after a few tries, so I'm switching up to {larger}.",
                )
                return _cap_reloads(decision, reloads_used, max_reloads_per_turn)
            return Decision(
                action=Action.RETRY_HIGHER_TEMP,
                reason=f"{state.last_failure} repeated, no larger model fits, resampling",
                inputs_snapshot=_snapshot(state),
                narration="Still not right, so I'm trying a different approach with a higher temperature.",
            )
        return Decision(
            action=Action.RETRY_WITH_FEEDBACK,
            reason=f"{state.last_failure}, retrying with feedback",
            inputs_snapshot=_snapshot(state),
            narration="That didn't pass, so I'm retrying with the failure details.",
        )

    # 5. Timeout: the code is too slow / looping.
    if state.last_failure == "TIMEOUT":
        return Decision(
            action=Action.RETRY_WITH_FEEDBACK,
            reason="TIMEOUT, retrying with an efficiency note",
            inputs_snapshot=_snapshot(state),
            narration="That timed out, so I'm retrying and asking for a more efficient approach.",
        )

    # 6. Default.
    return Decision(
        action=Action.RETRY_WITH_FEEDBACK,
        reason="default retry",
        inputs_snapshot=_snapshot(state),
        narration="Retrying with feedback.",
    )


def _cap_reloads(decision: Decision, reloads_used: int, max_reloads_per_turn: int) -> Decision:
    if decision.action not in RELOAD_ACTIONS or reloads_used < max_reloads_per_turn:
        return decision
    return Decision(
        action=Action.STOP_SAFELY,
        reason=f"reload cap ({max_reloads_per_turn}) reached, would have: {decision.reason}",
        inputs_snapshot=decision.inputs_snapshot,
        narration="I've already switched models as many times as I should this turn, so I'm stopping rather than thrashing.",
    )


def _min_ctx(state: State) -> int:
    ctxs = [ctx for (model_id, ctx) in state.est_rss_mb if model_id == state.model_id]
    return min(ctxs) if ctxs else state.ctx_size


def _smaller_model(state: State, safety_margin_mb: float) -> str | None:
    if state.model_id not in MODEL_ORDER:
        return None
    idx = MODEL_ORDER.index(state.model_id)
    for candidate in reversed(MODEL_ORDER[:idx]):
        rss = state.est_rss_mb.get((candidate, state.ctx_size))
        if rss is not None and rss <= state.ram_limit_mb - safety_margin_mb:
            return candidate
    return None


def _larger_model(state: State, safety_margin_mb: float) -> str | None:
    if state.model_id not in MODEL_ORDER:
        return None
    idx = MODEL_ORDER.index(state.model_id)
    for candidate in MODEL_ORDER[idx + 1 :]:
        rss = state.est_rss_mb.get((candidate, state.ctx_size))
        load_cost = state.model_load_cost_s.get(candidate, 0.0)
        fits_ram = rss is not None and rss <= state.ram_limit_mb - safety_margin_mb
        fits_time = load_cost <= state.time_left_s
        if fits_ram and fits_time:
            return candidate
    return None


def _snapshot(state: State) -> dict:
    return {
        "server_rss_mb": state.server_rss_mb,
        "ram_limit_mb": state.ram_limit_mb,
        "headroom_mb": state.headroom_mb,
        "time_left_s": state.time_left_s,
        "attempts_left": state.attempts_left,
        "last_failure": state.last_failure,
        "model_id": state.model_id,
        "ctx_size": state.ctx_size,
    }
