import random
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from evalplus.data import get_human_eval_plus, get_human_eval_plus_hash
from evalplus.evaluate import get_groundtruth

from dwarv.verify.sandbox import SandboxResult
from dwarv.verify.sandbox import run as sandbox_run

DEFAULT_SEED = 42
DEFAULT_N_TASKS = 5
DEFAULT_TIMEOUT_S = 15.0


@dataclass
class EvalTask:
    task_id: str
    prompt: str
    entry_point: str
    inputs: list
    expected: list
    atol: float


def load_subset(n: int = DEFAULT_N_TASKS, seed: int = DEFAULT_SEED) -> list[str]:
    """Seeded random sample of HumanEval+ task IDs. Never hand-picked -- if
    results look bad later, freeze a new seed into a new subset file instead
    of swapping tasks; see eval_tasks/subset_v1.json."""
    problems = get_human_eval_plus()
    task_ids = sorted(problems.keys())
    return sorted(random.Random(seed).sample(task_ids, n))


def build_eval_tasks(task_ids: list[str]) -> list[EvalTask]:
    """Loads the given HumanEval+ task ids plus their ground-truth expected
    outputs (base + plus inputs combined), ready for verify_candidate().
    Ground truth is computed by running each problem's own canonical
    solution (evalplus.evaluate.get_groundtruth) -- real, not guessed."""
    problems = get_human_eval_plus()
    hashcode = get_human_eval_plus_hash()
    groundtruth = get_groundtruth(problems, hashcode, set())

    tasks = []
    for task_id in task_ids:
        problem = problems[task_id]
        oracle = groundtruth[task_id]
        inputs = list(problem["base_input"]) + list(problem["plus_input"])
        expected = list(oracle["base"]) + list(oracle["plus"])
        tasks.append(
            EvalTask(
                task_id=task_id,
                prompt=problem["prompt"],
                entry_point=problem["entry_point"],
                inputs=inputs,
                expected=expected,
                atol=problem["atol"],
            )
        )
    return tasks


def _build_check_script(task: EvalTask, candidate_completion: str) -> str:
    """Candidate code is concatenated as plain text, never passed through
    .format()/an f-string -- it can legitimately contain `{`/`}` (dict/set
    literals, f-strings) that would otherwise be misread as format fields."""
    candidate_code = task.prompt + candidate_completion
    footer = (
        "\n\n"
        f"INPUTS = {task.inputs!r}\n"
        f"EXPECTED = {task.expected!r}\n"
        f"ATOL = {task.atol!r}\n"
        f"ENTRY_POINT = {task.entry_point!r}\n"
        "\n"
        "def _is_floats(x):\n"
        "    if isinstance(x, float):\n"
        "        return True\n"
        "    if isinstance(x, (list, tuple)) and x:\n"
        "        return all(isinstance(i, float) for i in x)\n"
        "    return False\n"
        "\n"
        "fn = globals()[ENTRY_POINT]\n"
        "\n"
        "for i, inp in enumerate(INPUTS):\n"
        "    try:\n"
        "        out = fn(*inp)\n"
        "        exp = EXPECTED[i]\n"
        "        exact = out == exp\n"
        "        atol = ATOL\n"
        "        if atol == 0 and _is_floats(exp):\n"
        "            atol = 1e-6\n"
        "        if not exact and atol != 0:\n"
        "            import numpy as np\n"
        "            assert type(out) == type(exp)\n"
        "            if isinstance(exp, (list, tuple)):\n"
        "                assert len(out) == len(exp)\n"
        "            assert np.allclose(out, exp, rtol=1e-7, atol=atol)\n"
        "        else:\n"
        "            assert exact\n"
        "    except Exception as exc:\n"
        "        print(f'EVALPLUS_FAIL at input {i}: {type(exc).__name__}: {exc}')\n"
        "        raise SystemExit(1)\n"
        "\n"
        "print('EVALPLUS_PASS')\n"
    )
    return "import sys\n\n" + candidate_code + footer


def verify_candidate(
    task: EvalTask, candidate_completion: str, timeout_s: float = DEFAULT_TIMEOUT_S
) -> tuple[bool, SandboxResult]:
    """Runs `task.prompt + candidate_completion` against the task's combined
    base+plus inputs via our own cross-platform sandbox (verify/sandbox.py),
    NOT evalplus's own checker -- evalplus.eval.unsafe_execute unconditionally
    imports the POSIX-only `resource` module and crashes on Windows. evalplus
    is used only as the data source (problems + ground truth); see
    docs/DECISIONS.md."""
    script = _build_check_script(task, candidate_completion)
    with tempfile.TemporaryDirectory(prefix="dwarv-evalplus-") as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "check.py").write_text(script, encoding="utf-8")
        result = sandbox_run(
            [sys.executable, "check.py"], cwd=tmp_path, timeout_s=timeout_s, skip_docker=True
        )
    passed = result.returncode == 0 and not result.timed_out and "EVALPLUS_PASS" in result.stdout
    return passed, result
