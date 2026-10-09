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

# Found live running the real eval beyond the dry run's first 5 tasks: at
# least one real HumanEval+ task's canonical-solution ground truth is an
# integer whose repr() exceeds Python 3.11+'s default 4300-digit
# int-to-str conversion limit (a real CPython DoS guard against untrusted
# input -- see PEP referenced in sys.set_int_max_str_digits's own docs).
# The actual crash this caused inside the generated check.py subprocess is
# fixed properly below (_safe_repr renders huge ints as hex literals,
# which are exempt from the *compile-time* version of this same limit --
# raising this process's own runtime limit can't reach a separate
# subprocess). This call is a secondary safety net so this parent process
# itself never hits the limit either (e.g. if a test or log line ever
# reprs a task's ground truth directly). Our own ground-truth values here
# are internally generated from evalplus's own canonical solutions, not
# untrusted input, so raising the limit is safe -- scoped to this
# eval-harness-only module (never the live chat path, which does handle
# untrusted model/repo content).
sys.set_int_max_str_digits(0)


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


def _safe_repr(value: object) -> str:
    """Like repr(), except every int is emitted as a hex literal. Found
    live running the real eval past the dry run's first 5 tasks: at least
    one real HumanEval+ task's ground truth is an integer whose *decimal*
    repr() exceeds Python 3.11+'s int-to-str conversion limit -- and
    critically, that limit is also enforced at COMPILE time when the
    generated script's decimal literal is parsed in the fresh subprocess
    that runs it, so raising the limit at runtime (e.g.
    sys.set_int_max_str_digits()) cannot fix it: the whole file must
    parse before any of its statements, including that one, can execute
    (confirmed empirically, not assumed). Hex literals are exempt from
    this limit entirely (CPython's own error message suggests this fix),
    so this sidesteps the problem rather than raising a limit that would
    still be hit."""
    if isinstance(value, bool):
        return repr(value)  # bool is an int subclass -- check before int
    if isinstance(value, int):
        return hex(value)
    if isinstance(value, float | str | type(None)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_safe_repr(v) for v in value) + "]"
    if isinstance(value, tuple):
        inner = ", ".join(_safe_repr(v) for v in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    if isinstance(value, dict):
        items = ", ".join(f"{_safe_repr(k)}: {_safe_repr(v)}" for k, v in value.items())
        return "{" + items + "}"
    return repr(value)  # anything else (e.g. a set) -- no known huge-int risk


def _build_check_script(task: EvalTask, candidate_completion: str) -> str:
    """Candidate code is concatenated as plain text, never passed through
    .format()/an f-string -- it can legitimately contain `{`/`}` (dict/set
    literals, f-strings) that would otherwise be misread as format fields."""
    candidate_code = task.prompt + candidate_completion
    footer = (
        "\n\n"
        f"INPUTS = {_safe_repr(task.inputs)}\n"
        f"EXPECTED = {_safe_repr(task.expected)}\n"
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
    # The hex-literal formatting above only sidesteps the *compile-time*
    # digit-limit check for the EXPECTED/INPUTS literals. Any runtime
    # int<->str conversion in this fresh subprocess -- in candidate code
    # itself, or in our own comparison/printing below -- would still hit
    # the (per-process, default 4300-digit) runtime limit, since this is
    # a separate interpreter from the parent that never saw its own
    # sys.set_int_max_str_digits(0) call. Raising it here is safe for the
    # same reason it's safe in the parent: this is our own eval-harness
    # subprocess, not a path that executes untrusted/adversarial input.
    return "import sys\nsys.set_int_max_str_digits(0)\n\n" + candidate_code + footer


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
