import pytest


@pytest.mark.skip(reason="Step 4: failure classification not implemented yet")
@pytest.mark.parametrize(
    "failure_class",
    [
        "PASS",
        "SYNTAX_ERROR",
        "IMPORT_ERROR",
        "RUNTIME_ERROR",
        "WRONG_OUTPUT",
        "TIMEOUT",
        "EMPTY_OR_NO_CODE",
    ],
)
def test_each_failure_class_is_classified(failure_class): ...
