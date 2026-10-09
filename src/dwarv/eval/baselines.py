import re
import time

from dwarv.controller.actions import Action
from dwarv.controller.policy import RELOAD_ACTIONS, decide
from dwarv.models.suite import MODEL_ORDER
from dwarv.resources.budget import BudgetManager, BudgetStatus
from dwarv.resources.monitor import ResourceMonitor
from dwarv.resources.squeeze import SqueezeScheduler
from dwarv.runtime.base import GenParams
from dwarv.types import Decision
from dwarv.types import EvalResult as EvalRunResult
from dwarv.types import State as PolicyState
from dwarv.verify.evalplus_adapter import EvalTask, verify_candidate
from dwarv.verify.failure import ClassifiedFailure, classify

DEFAULT_MAX_TOKENS = 512
DEFAULT_TEMPERATURE = 0.2

_CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)

_SYSTEM_PROMPT = (
    "You are a Python coding assistant completing a function body for a benchmark. "
    "You will be given a function signature and docstring. Respond with ONLY the "
    "missing body -- the code that continues directly after the prompt, correctly "
    "indented -- inside a single fenced python code block. Do not repeat the "
    "signature or docstring, and do not include any other text."
)


def _extract_code(text: str) -> str | None:
    match = _CODE_FENCE_RE.search(text)
    if not match:
        return None
    code = match.group(1)
    return code if code.strip() else None


def _initial_messages(task: EvalTask) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": task.prompt},
    ]


def _repair_message(completion: str | None, feedback_class: str, feedback: str) -> list[dict]:
    return [
        {"role": "assistant", "content": completion or "(no code produced)"},
        {
            "role": "user",
            "content": (
                f"That failed verification ({feedback_class}): {feedback}\n\n"
                "Provide a corrected, complete function body in a single fenced "
                "python code block."
            ),
        },
    ]


def _generate_and_verify(
    runtime, messages: list[dict], task: EvalTask, temperature: float, max_tokens: int
) -> tuple[str | None, bool, ClassifiedFailure]:
    result = runtime.generate(messages, GenParams(temperature=temperature, max_tokens=max_tokens))
    completion = _extract_code(result.text)
    if completion is None:
        return (
            None,
            False,
            classify(code=None, passed=False, timed_out=False, returncode=1, stdout="", stderr=""),
        )
    passed, sandbox_result = verify_candidate(task, completion)
    classified = classify(
        code=completion,
        passed=passed,
        timed_out=sandbox_result.timed_out,
        returncode=sandbox_result.returncode or 0,
        stdout=sandbox_result.stdout,
        stderr=sandbox_result.stderr,
    )
    return completion, passed, classified


def run_fixed(
    runtime, task: EvalTask, model_id: str, ctx_size: int, max_tokens: int = DEFAULT_MAX_TOKENS
) -> EvalRunResult:
    """Baseline A: one model, one generation, no retries."""
    runtime.load(model_id, ctx_size=ctx_size)
    monitor = ResourceMonitor(pid_fn=runtime.pid)
    monitor.start()
    start = time.monotonic()
    completion, passed, _classified = _generate_and_verify(
        runtime, _initial_messages(task), task, DEFAULT_TEMPERATURE, max_tokens
    )
    wall_s = time.monotonic() - start
    peak_rss = monitor.peak_rss_mb()
    monitor.stop()
    return EvalRunResult(
        system="fixed",
        task_id=task.task_id,
        passed=passed,
        attempts=1,
        wall_s=wall_s,
        peak_rss_mb=peak_rss,
        budget_violation=False,
        final_code=completion,
        stop_reason="PASS" if passed else "GAVE_UP",
    )


def run_retry(
    runtime,
    task: EvalTask,
    model_id: str,
    ctx_size: int,
    max_attempts: int = 3,
    time_limit_s: float = 120.0,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> EvalRunResult:
    """Baseline C: verification-guided retry, same model/ctx throughout, no resource awareness."""
    runtime.load(model_id, ctx_size=ctx_size)
    monitor = ResourceMonitor(pid_fn=runtime.pid)
    monitor.start()
    messages = _initial_messages(task)
    start = time.monotonic()
    attempts = 0
    completion: str | None = None
    passed = False
    while attempts < max_attempts and (time.monotonic() - start) < time_limit_s:
        attempts += 1
        completion, passed, classified = _generate_and_verify(
            runtime, messages, task, DEFAULT_TEMPERATURE, max_tokens
        )
        if passed:
            break
        messages = messages + _repair_message(
            completion, classified.failure_class, classified.feedback
        )
    wall_s = time.monotonic() - start
    peak_rss = monitor.peak_rss_mb()
    monitor.stop()
    stop_reason = (
        "PASS" if passed else ("BUDGET_TIME" if attempts >= max_attempts else "BUDGET_ATTEMPTS")
    )
    return EvalRunResult(
        system="retry",
        task_id=task.task_id,
        passed=passed,
        attempts=attempts,
        wall_s=wall_s,
        peak_rss_mb=peak_rss,
        budget_violation=False,
        final_code=completion,
        stop_reason=stop_reason,
    )


def run_retry_escalate(
    runtime,
    task: EvalTask,
    model_order: list[str],
    ctx_size: int,
    max_attempts: int = 3,
    time_limit_s: float = 120.0,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> EvalRunResult:
    """Baseline C+: retry + model escalation on repeated failure, deliberately
    with NO resource awareness -- it may pick a model that violates a RAM
    limit it never checks. That's the point of this baseline: isolate the
    effect of escalation alone from the effect of resource awareness."""
    model_idx = 0
    model_id = model_order[model_idx]
    runtime.load(model_id, ctx_size=ctx_size)
    monitor = ResourceMonitor(pid_fn=runtime.pid)
    monitor.start()

    messages = _initial_messages(task)
    start = time.monotonic()
    attempts = 0
    completion: str | None = None
    passed = False
    last_failure: str | None = None
    repeated_same_failure = 0

    while attempts < max_attempts and (time.monotonic() - start) < time_limit_s:
        attempts += 1
        completion, passed, classified = _generate_and_verify(
            runtime, messages, task, DEFAULT_TEMPERATURE, max_tokens
        )
        if passed:
            break
        repeated_same_failure = (
            repeated_same_failure + 1 if classified.failure_class == last_failure else 0
        )
        last_failure = classified.failure_class
        if repeated_same_failure >= 2 and model_idx + 1 < len(model_order):
            model_idx += 1
            model_id = model_order[model_idx]
            runtime.load(model_id, ctx_size=ctx_size)  # no RAM check -- deliberate
            repeated_same_failure = 0
        messages = messages + _repair_message(
            completion, classified.failure_class, classified.feedback
        )

    peak_rss = monitor.peak_rss_mb()
    monitor.stop()
    stop_reason = (
        "PASS" if passed else ("BUDGET_TIME" if attempts >= max_attempts else "BUDGET_ATTEMPTS")
    )
    return EvalRunResult(
        system="retry_escalate",
        task_id=task.task_id,
        passed=passed,
        attempts=attempts,
        wall_s=time.monotonic() - start,
        peak_rss_mb=peak_rss,
        budget_violation=False,
        final_code=completion,
        decisions=[{"final_model_id": model_id}],
        stop_reason=stop_reason,
    )


def _resolve_reload(
    action: Action, model_id: str, ctx_size: int, model_order: list[str]
) -> tuple[str, int]:
    idx = model_order.index(model_id)
    if action == Action.SWITCH_SMALLER_MODEL and idx > 0:
        return model_order[idx - 1], ctx_size
    if action == Action.SWITCH_LARGER_MODEL and idx + 1 < len(model_order):
        return model_order[idx + 1], ctx_size
    if action == Action.SHRINK_CONTEXT:
        return model_id, max(1024, ctx_size // 2)
    return model_id, ctx_size


def _policy_state(
    budget: BudgetManager,
    model_id: str,
    ctx_size: int,
    last_failure: str | None,
    repeated_same_failure: int,
    models_config: dict,
) -> PolicyState:
    load_cost = {
        m["id"]: (m.get("load_time_s") or {}).get(ctx_size, 0.0)
        for m in models_config.get("models", [])
    }
    rss_table: dict[tuple[str, int], float] = {}
    for m in models_config.get("models", []):
        for ctx, rss in (m.get("measured_rss_mb") or {}).items():
            rss_table[(m["id"], ctx)] = rss
    return PolicyState(
        attempt_idx=budget.max_attempts - budget.attempts_left(),
        attempts_left=budget.attempts_left(),
        time_left_s=budget.remaining_time_s(),
        ram_limit_mb=budget.ram_limit_mb,
        server_rss_mb=budget.server_rss_mb(),
        headroom_mb=budget.headroom_mb(),
        sys_available_mb=budget.sys_available_mb(),
        model_id=model_id,
        ctx_size=ctx_size,
        last_failure=last_failure,
        repeated_same_failure=repeated_same_failure,
        model_load_cost_s=load_cost,
        est_rss_mb=rss_table,
    )


def run_dwarv_policy(
    runtime,
    task: EvalTask,
    models_config: dict,
    ctx_size: int,
    ram_limit_mb: float,
    max_attempts: int = 3,
    time_limit_s: float = 120.0,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    squeeze: SqueezeScheduler | None = None,
    max_reloads_per_turn: int = 2,
) -> EvalRunResult:
    """Dwarv's actual policy (controller.policy.decide), reused unmodified
    from the live product -- same resource-aware retry/escalate/de-escalate
    rules, same BudgetManager, driven by this task's real measured RSS."""
    model_id = MODEL_ORDER[
        0
    ]  # same starting point for every task/seed -- isolates the POLICY's effect
    ctx = ctx_size
    runtime.load(model_id, ctx_size=ctx)
    monitor = ResourceMonitor(pid_fn=runtime.pid)
    monitor.start()

    budget = BudgetManager(
        ram_limit_mb=ram_limit_mb,
        time_limit_s=time_limit_s,
        max_attempts=max_attempts,
        rss_fn=lambda: monitor.sample_once().server_rss_mb,
        sys_available_fn=lambda: monitor.sample_once().sys_available_mb,
    )
    messages = _initial_messages(task)
    start = time.monotonic()
    completion: str | None = None
    passed = False
    last_failure: str | None = None
    repeated_same_failure = 0
    reloads_used = 0
    decisions_log: list[dict] = []
    budget_violation = False

    while True:
        if squeeze is not None:
            # "after_turns" here means "after this many attempts" -- there's
            # no chat-turn concept for a single eval task.
            attempts_so_far = budget.max_attempts - budget.attempts_left()
            event = squeeze.maybe_fire(attempts_so_far)
            if event is not None:
                budget.set_ram_limit(event.new_ram_limit_mb, event.reason)

        status = budget.check()
        if status == BudgetStatus.VIOLATION:
            budget_violation = True

        if status != BudgetStatus.OK:
            decision = _decide_and_log(
                budget,
                model_id,
                ctx,
                last_failure,
                repeated_same_failure,
                models_config,
                reloads_used,
                max_reloads_per_turn,
                decisions_log,
            )
            if decision.action == Action.STOP_SAFELY:
                break
            if decision.action in RELOAD_ACTIONS:
                reloads_used += 1
                model_id, ctx = _resolve_reload(decision.action, model_id, ctx, MODEL_ORDER)
                runtime.load(model_id, ctx_size=ctx)
            continue

        if budget.attempts_left() <= 0 or budget.remaining_time_s() <= 0:
            break
        budget.record_attempt()
        completion, passed, classified = _generate_and_verify(
            runtime, messages, task, DEFAULT_TEMPERATURE, max_tokens
        )
        if passed:
            break
        repeated_same_failure = (
            repeated_same_failure + 1 if classified.failure_class == last_failure else 0
        )
        last_failure = classified.failure_class

        decision = _decide_and_log(
            budget,
            model_id,
            ctx,
            last_failure,
            repeated_same_failure,
            models_config,
            reloads_used,
            max_reloads_per_turn,
            decisions_log,
        )
        if decision.action == Action.STOP_SAFELY:
            break
        if decision.action in RELOAD_ACTIONS:
            reloads_used += 1
            model_id, ctx = _resolve_reload(decision.action, model_id, ctx, MODEL_ORDER)
            runtime.load(model_id, ctx_size=ctx)
        messages = messages + _repair_message(
            completion, classified.failure_class, classified.feedback
        )

    wall_s = time.monotonic() - start
    peak_rss = monitor.peak_rss_mb()
    monitor.stop()
    stop_reason = "PASS" if passed else ("BUDGET_RAM" if budget_violation else "GAVE_UP")
    return EvalRunResult(
        system="dwarv",
        task_id=task.task_id,
        passed=passed,
        attempts=budget.max_attempts - budget.attempts_left(),
        wall_s=wall_s,
        peak_rss_mb=peak_rss,
        budget_violation=budget_violation,
        final_code=completion,
        decisions=decisions_log,
        stop_reason=stop_reason,
    )


def _decide_and_log(
    budget: BudgetManager,
    model_id: str,
    ctx_size: int,
    last_failure: str | None,
    repeated_same_failure: int,
    models_config: dict,
    reloads_used: int,
    max_reloads_per_turn: int,
    decisions_log: list[dict],
) -> Decision:
    state = _policy_state(
        budget, model_id, ctx_size, last_failure, repeated_same_failure, models_config
    )
    decision = decide(state, reloads_used=reloads_used, max_reloads_per_turn=max_reloads_per_turn)
    decisions_log.append({"action": decision.action.name, "reason": decision.reason})
    return decision
