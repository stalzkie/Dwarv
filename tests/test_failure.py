from dwarv.verify.failure import MAX_FEEDBACK_CHARS, FailureClass, classify


def test_pass():
    result = classify(
        code="def f(): return 1", passed=True, timed_out=False, returncode=0, stdout="", stderr=""
    )
    assert result.failure_class == FailureClass.PASS


def test_syntax_error():
    result = classify(
        code="def f(:\n    pass",
        passed=False,
        timed_out=False,
        returncode=1,
        stdout="",
        stderr='  File "sol.py", line 1\n    def f(:\nSyntaxError: invalid syntax',
    )
    assert result.failure_class == FailureClass.SYNTAX_ERROR


def test_import_error():
    result = classify(
        code="import nonexistent_module",
        passed=False,
        timed_out=False,
        returncode=1,
        stdout="",
        stderr="ModuleNotFoundError: No module named 'nonexistent_module'",
    )
    assert result.failure_class == FailureClass.IMPORT_ERROR


def test_runtime_error():
    result = classify(
        code="def f():\n    return 1 / 0",
        passed=False,
        timed_out=False,
        returncode=1,
        stdout="",
        stderr="ZeroDivisionError: division by zero",
    )
    assert result.failure_class == FailureClass.RUNTIME_ERROR


def test_wrong_output():
    result = classify(
        code="def f(): return 'Fizz'",
        passed=False,
        timed_out=False,
        returncode=1,
        stdout="",
        stderr="AssertionError: assert 'Fizz' == 'FizzBuzz'",
    )
    assert result.failure_class == FailureClass.WRONG_OUTPUT


def test_timeout():
    result = classify(
        code="while True: pass", passed=False, timed_out=True, returncode=-1, stdout="", stderr=""
    )
    assert result.failure_class == FailureClass.TIMEOUT


def test_empty_or_no_code():
    result = classify(code="", passed=False, timed_out=False, returncode=1, stdout="", stderr="")
    assert result.failure_class == FailureClass.EMPTY_OR_NO_CODE


def test_feedback_is_truncated():
    long_stderr = "AssertionError: " + "x" * 5000
    result = classify(
        code="def f(): pass",
        passed=False,
        timed_out=False,
        returncode=1,
        stdout="",
        stderr=long_stderr,
    )
    assert len(result.feedback) <= MAX_FEEDBACK_CHARS
