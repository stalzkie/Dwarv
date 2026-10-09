from dwarv.agent.prompts import extract_patch, repair_prompt, system_prompt


def test_extract_patch_single_file():
    text = "Here's the fix:\n```python:src/app.py\ndef add(a, b):\n    return a + b\n```\nDone."

    blocks = extract_patch(text)

    assert blocks == [("src/app.py", "def add(a, b):\n    return a + b\n")]


def test_extract_patch_multiple_files():
    text = (
        "```python:a.py\nprint('a')\n```\n"
        "some commentary in between\n"
        "```python:b.py\nprint('b')\n```"
    )

    blocks = extract_patch(text)

    assert [path for path, _ in blocks] == ["a.py", "b.py"]


def test_extract_patch_no_blocks_returns_empty():
    assert extract_patch("just some prose, no code") == []


def test_system_prompt_mentions_test_command_when_present():
    prompt = system_prompt("/repo", ["pytest"])
    assert "pytest" in prompt


def test_system_prompt_says_no_test_command_when_absent():
    prompt = system_prompt("/repo", None)
    assert "No test command was discoverable" in prompt


def test_repair_prompt_includes_feedback():
    prompt = repair_prompt("previous code", "AssertionError: expected 5, got 4")
    assert "AssertionError" in prompt
    assert "previous code" in prompt
