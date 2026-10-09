import re

# Matches ```<language>:<relative/path>\n<content>```. The model is asked to
# always emit the full new content of a file, never a unified diff -- see
# repo/patch.py's docstring for why.
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*:(?P<path>[^\n`]+)\n(?P<content>.*?)```", re.DOTALL)


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


def repair_prompt(previous_response: str, feedback: str) -> str:
    return (
        "Your previous attempt did not pass verification. Failure details:\n"
        f"{feedback}\n\n"
        "Your previous attempt was:\n"
        f"{previous_response}\n\n"
        "Produce a corrected version using the same fenced-block format ('```<language>:<path>')."
    )


def extract_patch(text: str) -> list[tuple[str, str]]:
    """Pull every ```<language>:<relative/path> fenced block out of model
    output. Returns [(relative_path, new_file_content), ...] in the order
    they appeared; [] if the model produced no such block (EMPTY_OR_NO_CODE)."""
    return [(m.group("path").strip(), m.group("content")) for m in _FENCE_RE.finditer(text)]
