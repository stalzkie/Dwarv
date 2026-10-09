import pytest

from dwarv.agent.prompts import (
    DWARV_RESPONSE_SCHEMA,
    MalformedStructuredResponse,
    extract_patch,
    parse_structured_response,
    repair_prompt,
    salvage_message,
    structured_system_prompt,
    system_prompt,
)


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


def test_structured_system_prompt_mentions_test_command_when_present():
    prompt = structured_system_prompt("/repo", ["pytest"])
    assert "pytest" in prompt


def test_structured_system_prompt_says_no_test_command_when_absent():
    prompt = structured_system_prompt("/repo", None)
    assert "No test command was discoverable" in prompt


def test_response_schema_requires_kind_message_files():
    assert set(DWARV_RESPONSE_SCHEMA["required"]) == {"kind", "message", "files"}
    assert DWARV_RESPONSE_SCHEMA["properties"]["kind"]["enum"] == ["direct_answer", "patch"]


def test_parse_structured_response_direct_answer():
    result = parse_structured_response(
        '{"kind": "direct_answer", "message": "it is 4", "files": []}'
    )
    assert result.kind == "direct_answer"
    assert result.message == "it is 4"
    assert result.files == []


def test_parse_structured_response_patch_with_files():
    # Real text captured from a live llama-server call against the real
    # small model with --json-schema, not hand-constructed.
    text = (
        '{\n  "kind": "patch",\n  "message": "The function `add` should add, not subtract.",\n'
        '  "files": [\n    {\n      "path": "app.py",\n'
        '      "content": "def add(a, b): return a + b"\n    }\n  ]\n}'
    )
    result = parse_structured_response(text)
    assert result.kind == "patch"
    assert result.files == [("app.py", "def add(a, b): return a + b")]


def test_parse_structured_response_rejects_invalid_json():
    with pytest.raises(MalformedStructuredResponse, match="not valid JSON"):
        parse_structured_response("not json at all")


def test_parse_structured_response_rejects_non_object():
    with pytest.raises(MalformedStructuredResponse, match="expected a JSON object"):
        parse_structured_response("[1, 2, 3]")


def test_parse_structured_response_rejects_bad_kind():
    with pytest.raises(MalformedStructuredResponse, match="'kind'"):
        parse_structured_response('{"kind": "something_else", "message": "x", "files": []}')


def test_parse_structured_response_rejects_missing_message():
    with pytest.raises(MalformedStructuredResponse, match="'message'"):
        parse_structured_response('{"kind": "direct_answer", "files": []}')


def test_parse_structured_response_rejects_malformed_file_entry():
    with pytest.raises(MalformedStructuredResponse, match="malformed file entry"):
        parse_structured_response('{"kind": "patch", "message": "x", "files": [{"path": "a.py"}]}')


# --- salvage_message: the fallback when MalformedStructuredResponse is raised ---


def test_salvage_message_from_well_formed_json():
    text = '{"kind": "direct_answer", "message": "hello there", "files": []}'
    assert salvage_message(text) == "hello there"


def test_salvage_message_from_json_truncated_mid_string():
    """Real case live-observed: max_tokens cut off generation mid-sentence,
    before the closing quote/brace -- the regex has no closing-quote
    requirement, so it still recovers the readable prefix."""
    text = '{"kind": "direct_answer", "message": "The function now handles the'
    assert salvage_message(text) == "The function now handles the"


def test_salvage_message_unescapes_json_string_escapes():
    text = r'{"kind": "direct_answer", "message": "line one\nline two", "files": []}'
    assert salvage_message(text) == "line one\nline two"


def test_salvage_message_returns_none_when_no_message_field_present():
    assert salvage_message("not json at all, no message field here") is None
