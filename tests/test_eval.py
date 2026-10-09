from pathlib import Path

from dwarv.eval.analyze import (
    bootstrap_ci,
    mcnemar_test,
    per_task_diff,
    summarize,
    wilcoxon_signed_rank,
    write_significance_report,
    write_summary_csv,
    write_summary_markdown,
    write_with_vs_without_chart,
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


def test_verify_candidate_handles_ground_truth_integer_beyond_str_digit_limit():
    """Real bug found running the real eval past the dry run's first 5
    tasks: at least one real HumanEval+ task's ground truth is an integer
    whose decimal repr() exceeds Python 3.11+'s int-to-str conversion
    limit (default 4300 digits) -- and that limit is enforced at COMPILE
    time in the generated check.py subprocess, so it can't be worked
    around by raising the limit at runtime. This builds a synthetic task
    with a 5000-digit expected value (well past the default limit) to
    prove the fix (hex-literal serialization in evalplus_adapter._safe_repr)
    actually lets the subprocess compile and run instead of crashing."""
    huge = int("1" + "0" * 5000)
    task = EvalTask(
        task_id="synthetic/huge",
        prompt="def huge(a, b):\n",
        entry_point="huge",
        inputs=[[0, 0]],
        expected=[huge],
        atol=0.0,
    )
    # Computed at runtime via int('1' + '0'*5000), not a literal, so the
    # candidate code itself doesn't hit the same compile-time digit limit
    # this test is trying to isolate to the EXPECTED/INPUTS formatting.
    passed, result = verify_candidate(task, "    return int('1' + '0' * 5000)\n")
    assert passed, result.stdout + result.stderr
    assert "EVALPLUS_PASS" in result.stdout


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


def _paired_rows(profile: str, both_pass: int, both_fail: int, a_only: int, b_only: int) -> list:
    """Builds rows for "a" (dwarv) and "b" (fixed) across synthetic tasks
    with the exact requested discordant-pair structure, so the McNemar
    result is a hand-checkable fact about the input, not a black box."""
    rows = []
    idx = 0
    for _ in range(both_pass):
        rows.append({"system": "a", "profile": profile, "task_id": f"t{idx}", "passed": True})
        rows.append({"system": "b", "profile": profile, "task_id": f"t{idx}", "passed": True})
        idx += 1
    for _ in range(both_fail):
        rows.append({"system": "a", "profile": profile, "task_id": f"t{idx}", "passed": False})
        rows.append({"system": "b", "profile": profile, "task_id": f"t{idx}", "passed": False})
        idx += 1
    for _ in range(a_only):
        rows.append({"system": "a", "profile": profile, "task_id": f"t{idx}", "passed": True})
        rows.append({"system": "b", "profile": profile, "task_id": f"t{idx}", "passed": False})
        idx += 1
    for _ in range(b_only):
        rows.append({"system": "a", "profile": profile, "task_id": f"t{idx}", "passed": False})
        rows.append({"system": "b", "profile": profile, "task_id": f"t{idx}", "passed": True})
        idx += 1
    return rows


def test_mcnemar_test_counts_are_exact():
    rows = _paired_rows("p", both_pass=3, both_fail=2, a_only=4, b_only=1)
    result = mcnemar_test(rows, "a", "b", "p")
    assert result["n_tasks"] == 10
    assert result["both_pass"] == 3
    assert result["both_fail"] == 2
    assert result["a_only"] == 4
    assert result["b_only"] == 1
    assert 0.0 <= result["p_value"] <= 1.0


def test_mcnemar_test_symmetric_discordant_pairs_gives_p_one():
    # Equal a_only/b_only is the textbook "no evidence of difference" case
    # for McNemar's exact test -- always gives p=1.0.
    rows = _paired_rows("p", both_pass=0, both_fail=0, a_only=5, b_only=5)
    result = mcnemar_test(rows, "a", "b", "p")
    assert result["p_value"] == 1.0
    assert result["significant_at_0.05"] is False


def test_mcnemar_test_one_sided_discordant_pairs_is_significant():
    # 20 discordant pairs, all favoring "a" -- an obviously non-chance
    # pattern (binomial(20, 0.5) extreme tail), should be significant.
    rows = _paired_rows("p", both_pass=0, both_fail=0, a_only=20, b_only=0)
    result = mcnemar_test(rows, "a", "b", "p")
    assert result["p_value"] < 0.05
    assert result["significant_at_0.05"] is True


def test_mcnemar_test_no_discordant_pairs_gives_p_one():
    rows = _paired_rows("p", both_pass=4, both_fail=4, a_only=0, b_only=0)
    result = mcnemar_test(rows, "a", "b", "p")
    assert result["p_value"] == 1.0


def test_wilcoxon_signed_rank_detects_a_clear_difference():
    rows = []
    for i in range(10):
        rows.append({"system": "a", "profile": "p", "task_id": f"t{i}", "wall_s": 5.0})
        rows.append({"system": "b", "profile": "p", "task_id": f"t{i}", "wall_s": 20.0})
    result = wilcoxon_signed_rank(rows, "a", "b", "p", metric="wall_s")
    assert result["n_tasks"] == 10
    assert result["mean_a"] == 5.0
    assert result["mean_b"] == 20.0
    assert result["p_value"] < 0.05
    assert result["significant_at_0.05"] is True


def test_wilcoxon_signed_rank_handles_identical_values_gracefully():
    rows = [
        {"system": "a", "profile": "p", "task_id": "t0", "wall_s": 5.0},
        {"system": "b", "profile": "p", "task_id": "t0", "wall_s": 5.0},
        {"system": "a", "profile": "p", "task_id": "t1", "wall_s": 5.0},
        {"system": "b", "profile": "p", "task_id": "t1", "wall_s": 5.0},
    ]
    result = wilcoxon_signed_rank(rows, "a", "b", "p", metric="wall_s")
    assert result["p_value"] is None
    assert "note" in result


def test_write_significance_report_produces_real_files(tmp_path: Path):
    rows = _paired_rows("p", both_pass=2, both_fail=1, a_only=3, b_only=1)
    for row in rows:
        row["wall_s"] = 5.0 if row["system"] == "a" else 8.0
        row["peak_rss_mb"] = 1000.0 if row["system"] == "a" else 1200.0
        row["system"] = "dwarv" if row["system"] == "a" else "fixed"

    write_significance_report(rows, tmp_path / "significance")

    csv_text = (tmp_path / "significance.csv").read_text(encoding="utf-8")
    md_text = (tmp_path / "significance.md").read_text(encoding="utf-8")
    assert "mcnemar" in csv_text
    assert "wilcoxon" in csv_text
    assert "dwarv" in md_text and "fixed" in md_text


def test_write_with_vs_without_chart_produces_a_real_png(tmp_path: Path):
    summary = [
        {
            "system": "dwarv",
            "profile": "p",
            "pass_rate": 0.8,
            "pass_rate_ci_lo": 0.4,
            "pass_rate_ci_hi": 1.0,
        },
        {
            "system": "fixed",
            "profile": "p",
            "pass_rate": 0.6,
            "pass_rate_ci_lo": 0.2,
            "pass_rate_ci_hi": 0.9,
        },
    ]
    out_path = tmp_path / "with_vs_without.png"
    write_with_vs_without_chart(summary, out_path)
    assert out_path.exists()
    assert out_path.stat().st_size > 0


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
