from dataclasses import dataclass

MAX_FEEDBACK_CHARS = 2000


class FailureClass:
    PASS = "PASS"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    IMPORT_ERROR = "IMPORT_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    WRONG_OUTPUT = "WRONG_OUTPUT"
    TIMEOUT = "TIMEOUT"
    EMPTY_OR_NO_CODE = "EMPTY_OR_NO_CODE"


@dataclass
class ClassifiedFailure:
    failure_class: str
    feedback: str


def classify(
    *,
    code: str | None,
    passed: bool,
    timed_out: bool,
    returncode: int,
    stdout: str,
    stderr: str,
) -> ClassifiedFailure:
    """Classify one verification attempt. `code` is the generated patch/snippet
    (None or blank -> EMPTY_OR_NO_CODE before anything else is even inspected)."""
    if passed:
        return ClassifiedFailure(FailureClass.PASS, "")
    if timed_out:
        return ClassifiedFailure(FailureClass.TIMEOUT, _truncate("Execution timed out."))
    if code is None or not code.strip():
        return ClassifiedFailure(FailureClass.EMPTY_OR_NO_CODE, _truncate("No code was produced."))

    combined = f"{stdout}\n{stderr}"
    if "SyntaxError" in combined or "IndentationError" in combined:
        return ClassifiedFailure(FailureClass.SYNTAX_ERROR, _truncate(_last_line(combined)))
    if "ImportError" in combined or "ModuleNotFoundError" in combined:
        return ClassifiedFailure(FailureClass.IMPORT_ERROR, _truncate(_last_line(combined)))
    if "AssertionError" in combined:
        return ClassifiedFailure(FailureClass.WRONG_OUTPUT, _truncate(_last_line(combined)))
    if returncode != 0:
        return ClassifiedFailure(FailureClass.RUNTIME_ERROR, _truncate(_last_line(combined)))
    return ClassifiedFailure(
        FailureClass.WRONG_OUTPUT, _truncate("Test run failed with no recognized error signature.")
    )


def _last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else text.strip()


def _truncate(text: str, limit: int = MAX_FEEDBACK_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."
