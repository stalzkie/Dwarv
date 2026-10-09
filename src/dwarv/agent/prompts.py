import json
import re
from dataclasses import dataclass, field

# Matches ```<language>:<relative/path>\n<content>```. The model is asked to
# always emit the full new content of a file, never a unified diff -- see
# repo/patch.py's docstring for why.
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*:(?P<path>[^\n`]+)\n(?P<content>.*?)```", re.DOTALL)

# DWARV_PLAN.md section 11.8: a JSON Schema for grammar-constrained
# generation (GenParams.json_schema), live-verified against the real
# small model and llama-server's --json-schema support -- 100% valid,
# parseable JSON across every trial, including correctly distinguishing
# "kind" for a direct answer vs a patch. This structurally eliminates the
# live-observed failure mode of the free-form fence format (a model
# emitting ```python instead of ```python:path.py, which extract_patch()'s
# regex then silently fails to match) -- it does NOT fix or worsen the
# model's actual task-correctness; that's a separate, orthogonal concern.
DWARV_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["direct_answer", "patch"]},
        "message": {
            "type": "string",
            "description": "What you'd say to the user: either the direct answer itself, "
            "or a short note about the patch you're proposing.",
        },
        "files": {
            "type": "array",
            "description": "Empty for a direct_answer. For a patch, the full new content "
            "of each changed file -- never a diff.",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the repo root."},
                    "content": {
                        "type": "string",
                        "description": "The file's complete new content.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    "required": ["kind", "message", "files"],
}


@dataclass
class StructuredResponse:
    kind: str  # "direct_answer" | "patch"
    message: str
    files: list[tuple[str, str]] = field(default_factory=list)  # [(path, content), ...]


class MalformedStructuredResponse(Exception):
    """Raised when model output doesn't parse as DWARV_RESPONSE_SCHEMA's
    shape -- schema-constrained generation should make this unreachable in
    practice, but a killed/crashed server or a future llama.cpp regression
    could still hand back something else, and this must never raise an
    unhandled exception into the middle of a turn."""


_MESSAGE_FIELD_RE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)')


def salvage_message(raw_text: str) -> str | None:
    """Best-effort extraction of the 'message' field's value from text that
    failed to parse as DWARV_RESPONSE_SCHEMA JSON. Found live running the
    demo script: a small model's degenerate repetition loop exhausted
    max_tokens before the JSON could close, so
    MalformedStructuredResponse's fallback was showing the user a raw,
    unclosed '{"kind": "direct_answer", "message": "...' blob -- this
    extracts just the readable message text instead, even from a string
    that was cut off mid-value (the regex has no closing-quote
    requirement, so it still matches up to wherever the text ends).
    Returns None only if no 'message' field is found at all."""
    match = _MESSAGE_FIELD_RE.search(raw_text)
    if match is None:
        return None
    captured = match.group(1)
    try:
        return json.loads(f'"{captured}"')  # unescapes \n, \", etc.
    except json.JSONDecodeError:
        return captured  # truncated mid-escape-sequence -- still readable


def parse_structured_response(text: str) -> StructuredResponse:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedStructuredResponse(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MalformedStructuredResponse(f"expected a JSON object, got {type(data).__name__}")
    kind = data.get("kind")
    if kind not in ("direct_answer", "patch"):
        raise MalformedStructuredResponse(f"'kind' must be direct_answer or patch, got {kind!r}")
    message = data.get("message")
    if not isinstance(message, str):
        raise MalformedStructuredResponse(f"'message' must be a string, got {type(message)}")
    files_raw = data.get("files", [])
    if not isinstance(files_raw, list):
        raise MalformedStructuredResponse(f"'files' must be a list, got {type(files_raw)}")
    files = []
    for f in files_raw:
        if (
            not isinstance(f, dict)
            or not isinstance(f.get("path"), str)
            or not isinstance(f.get("content"), str)
        ):
            raise MalformedStructuredResponse(f"malformed file entry: {f!r}")
        files.append((f["path"], f["content"]))
    return StructuredResponse(kind=kind, message=message, files=files)


def system_prompt(repo_root_display: str, test_command: list[str] | None) -> str:
    test_line = (
        f"This repo's test command is: {' '.join(test_command)}."
        if test_command
        else "No test command was discoverable for this repo; say so plainly rather than "
        "claiming an unearned verification."
    )
    return (
        "You are Dwarv, a local, offline coding assistant running entirely on the user's own "
        f"machine. You are working in the repository at {repo_root_display}. {test_line}\n"
        "When you propose a code change, output the FULL new content of each changed file in a "
        "fenced code block whose info string is '<language>:<path relative to the repo root>', "
        "for example:\n"
        "```python:src/app.py\n"
        "<entire new file content>\n"
        "```\n"
        "Only include files you are actually changing. Never claim a change is verified unless "
        "you were told its tests passed."
    )


def structured_system_prompt(repo_root_display: str, test_command: list[str] | None) -> str:
    """Section 11.8's grammar-constrained variant of system_prompt() -- the
    response structure itself is enforced mechanically by
    DWARV_RESPONSE_SCHEMA, so this doesn't need to describe a format
    convention the model might forget (that was the live-observed failure
    mode the fenced-block format had); it only needs to explain what the
    fields mean."""
    test_line = (
        f"This repo's test command is: {' '.join(test_command)}."
        if test_command
        else "No test command was discoverable for this repo; say so plainly in 'message' "
        "rather than claiming an unearned verification."
    )
    return (
        "You are Dwarv, a local, offline coding assistant running entirely on the user's own "
        f"machine. You are working in the repository at {repo_root_display}. {test_line}\n"
        "Respond with 'kind': 'direct_answer' and your answer in 'message' (leave 'files' empty) "
        "when you're just answering a question. Respond with 'kind': 'patch' when proposing a "
        "code change: put a short note in 'message' and the FULL new content of each changed "
        "file (never a diff) in 'files', with 'path' relative to the repo root. Only include "
        "files you are actually changing."
    )


def repair_prompt(previous_response: str, feedback: str) -> str:
    return (
        "Your previous attempt did not pass verification. Failure details:\n"
        f"{feedback}\n\n"
        "Your previous attempt was:\n"
        f"{previous_response}\n\n"
        "Produce a corrected version using the same fenced-block format ('```<language>:<path>')."
    )


def structured_repair_prompt(previous_response: str, feedback: str) -> str:
    """Section 11.8's variant of repair_prompt() -- doesn't need to remind
    the model of a format convention since the schema enforces the shape
    mechanically; only needs to hand back the failure and the prior attempt."""
    return (
        "Your previous attempt did not pass verification. Failure details:\n"
        f"{feedback}\n\n"
        "Your previous attempt was:\n"
        f"{previous_response}\n\n"
        "Produce a corrected response in the same structured format."
    )


def extract_patch(text: str) -> list[tuple[str, str]]:
    """Pull every ```<language>:<relative/path> fenced block out of model
    output. Returns [(relative_path, new_file_content), ...] in the order
    they appeared; [] if the model produced no such block (EMPTY_OR_NO_CODE)."""
    return [(m.group("path").strip(), m.group("content")) for m in _FENCE_RE.finditer(text)]
