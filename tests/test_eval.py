from pathlib import Path

from dwarv.eval.analyze import (
    bootstrap_ci,
    per_task_diff,
    summarize,
    write_summary_csv,
    write_summary_markdown,
)
from dwarv.eval.baselines import run_dwarv_policy, run_fixed, run_retry, run_retry_escalate
from dwarv.eval.harness import _load_completed_keys, _result_key
from dwarv.resources.squeeze import SqueezeScheduler
from dwarv.runtime.base import GenResult
from dwarv.verify.evalplus_adapter import EvalTask, verify_candidate

SYNTHETIC_TASK = EvalTask(
    task_id="synthetic/add",
    prompt="def add(a, b):\n",
    entry_point="add",
    inputs=[[1, 2], [3, 4], [-1, 1]],
    expected=[3, 7, 0],
    atol=0.0,
)

FAKE_MODELS_CONFIG = {
    "models": [
        {
            "id": "small",
            "hf_repo": "x/small",
            "measured_rss_mb": {4096: 100.0},
            "load_time_s": {4096: 0.01},
        },
        {
            "id": "medium",
            "hf_repo": "x/medium",
            "measured_rss_mb": {4096: 200.0},
            "load_time_s": {4096: 0.01},
        },
        {
            "id": "large",
            "hf_repo": "x/large",
            "measured_rss_mb": {4096: 300.0},
            "load_time_s": {4096: 0.01},
        },
    ]
}


class FakeRuntime:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._model_id: str | None = None
        self._ctx: int | None = None

    def load(self, model_id: str, ctx_size: int) -> float:
        self._model_id = model_id
        self._ctx = ctx_size
        return 0.01

    def unload(self) -> None:
        self._model_id = None

    def generate(self, messages, params) -> GenResult:
        text = self._responses.pop(0)
        return GenResult(
            text=text, prompt_tokens=5, completion_tokens=5, wall_s=0.01, finish_reason="stop"
        )

    def pid(self) -> int | None:
        return None

    def current(self) -> tuple[str | None, int | None]:
        return self._model_id, self._ctx


CORRECT = "```python\n    return a + b\n```"
WRONG = "```python\n    return a - b\n```"


# --- verify_candidate: the core sandboxed checker, no evalplus dataset needed ---


def test_verify_candidate_pass():
    passed, result = verify_candidate(SYNTHETIC_TASK, "    return a + b\n")
    assert passed
    assert "EVALPLUS_PASS" in result.stdout


def test_verify_candidate_fail():
    passed, result = verify_candidate(SYNTHETIC_TASK, "    return a - b\n")
    assert not passed
    assert "EVALPLUS_FAIL" in result.stdout


# --- baselines ---


def test_run_fixed_pass_and_fail():
    runtime = FakeRuntime([CORRECT])
    result = run_fixed(runtime, SYNTHETIC_TASK, "small", ctx_size=4096)
    assert result.passed
    assert result.attempts == 1
    assert result.system == "fixed"

    runtime2 = FakeRuntime([WRONG])
    result2 = run_fixed(runtime2, SYNTHETIC_TASK, "small", ctx_size=4096)
    assert not result2.passed


def test_run_retry_succeeds_on_second_attempt():
    runtime = FakeRuntime([WRONG, CORRECT])
    result = run_retry(runtime, SYNTHETIC_TASK, "small", ctx_size=4096, max_attempts=3)
    assert result.passed
    assert result.attempts == 2


def test_run_retry_escalate_switches_model_after_repeated_failure():
    # repeated_same_failure reaches 2 (the policy's escalate threshold) on the
    # *third* identical failure, matching controller/policy.py's own
    # "repeated twice" semantics -- same convention tested in test_policy.py.
    runtime = FakeRuntime([WRONG, WRONG, WRONG, CORRECT])
    result = run_retry_escalate(
        runtime, SYNTHETIC_TASK, ["small", "medium", "large"], ctx_size=4096, max_attempts=4
    )
    assert result.passed
    assert result.decisions[0]["final_model_id"] == "medium"  # escalated after 3 identical failures


def test_run_dwarv_policy_passes_on_first_try():
    runtime = FakeRuntime([CORRECT])
    result = run_dwarv_policy(
        runtime, SYNTHETIC_TASK, FAKE_MODELS_CONFIG, ctx_size=4096, ram_limit_mb=10000.0
    )
    assert result.passed
    assert result.system == "dwarv"


def test_run_dwarv_policy_respects_squeeze():
    # rss_fn isn't injectable into run_dwarv_policy directly (it uses a real
    # ResourceMonitor tied to runtime.pid(), which is None for FakeRuntime,
    # always reading 0 RSS) -- so a squeeze here can only be observed via
    # the *loose* ram_limit never tripping pressure. This confirms the
    # squeeze plumbing doesn't crash the run and the policy still finishes.
    runtime = FakeRuntime([CORRECT])
    squeeze = SqueezeScheduler()
    squeeze.schedule(5000.0, reason="test squeeze", after_turns=0)
    result = run_dwarv_policy(
        runtime,
        SYNTHETIC_TASK,
        FAKE_MODELS_CONFIG,
        ctx_size=4096,
        ram_limit_mb=10000.0,
        squeeze=squeeze,
    )
    assert result.passed


# --- harness resumability ---


def test_result_key_and_resume(tmp_path: Path):
    jsonl_path = tmp_path / "results.jsonl"
    jsonl_path.write_text(
        '{"system": "fixed", "profile": "static_loose", "task_id": "t1", "seed": 1}\n',
        encoding="utf-8",
    )
    completed = _load_completed_keys(jsonl_path)
    assert _result_key("fixed", "static_loose", "t1", 1) in completed
    assert _result_key("retry", "static_loose", "t1", 1) not in completed


def test_load_completed_keys_missing_file_returns_empty(tmp_path: Path):
    assert _load_completed_keys(tmp_path / "nonexistent.jsonl") == set()


# --- analyze ---


def test_bootstrap_ci_all_pass():
    mean, lo, hi = bootstrap_ci([True, True, True])
    assert mean == 1.0
    assert lo <= mean <= hi


def test_bootstrap_ci_mixed():
    mean, lo, hi = bootstrap_ci([True, False, True, False, True])
    assert mean == 0.6
    assert 0.0 <= lo <= mean <= hi <= 1.0


def test_summarize_groups_by_system_and_profile():
    rows = [
        {
            "system": "fixed",
            "profile": "static_loose",
            "passed": True,
            "peak_rss_mb": 100.0,
            "budget_violation": False,
            "wall_s": 1.0,
            "attempts": 1,
        },
        {
            "system": "fixed",
            "profile": "static_loose",
            "passed": False,
            "peak_rss_mb": 110.0,
            "budget_violation": False,
            "wall_s": 2.0,
            "attempts": 1,
        },
        {
            "system": "dwarv",
            "profile": "static_loose",
            "passed": True,
            "peak_rss_mb": 90.0,
            "budget_violation": True,
            "wall_s": 1.5,
            "attempts": 2,
        },
    ]
    summary = summarize(rows)
    assert len(summary) == 2
    fixed_row = next(r for r in summary if r["system"] == "fixed")
    assert fixed_row["n"] == 2
    assert fixed_row["pass_rate"] == 0.5
    dwarv_row = next(r for r in summary if r["system"] == "dwarv")
    assert dwarv_row["budget_violations"] == 1


def test_per_task_diff_finds_winner():
    rows = [
        {"system": "dwarv", "profile": "p", "task_id": "t1", "passed": True},
        {"system": "retry_escalate", "profile": "p", "task_id": "t1", "passed": False},
        {"system": "dwarv", "profile": "p", "task_id": "t2", "passed": True},
        {"system": "retry_escalate", "profile": "p", "task_id": "t2", "passed": True},
    ]
    diffs = per_task_diff(rows, "dwarv", "retry_escalate", "p")
    assert len(diffs) == 1
    assert diffs[0]["task_id"] == "t1"
    assert diffs[0]["winner"] == "dwarv"


def test_write_summary_csv_and_markdown(tmp_path: Path):
    summary = [{"system": "fixed", "profile": "p", "pass_rate": 1.0}]
    write_summary_csv(summary, tmp_path / "summary.csv")
    write_summary_markdown(summary, tmp_path / "summary.md")
    assert (tmp_path / "summary.csv").read_text(encoding="utf-8").strip() != ""
    assert "fixed" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_write_summary_handles_empty_results(tmp_path: Path):
    write_summary_csv([], tmp_path / "empty.csv")
    write_summary_markdown([], tmp_path / "empty.md")
    assert "no results" in (tmp_path / "empty.md").read_text(encoding="utf-8")
