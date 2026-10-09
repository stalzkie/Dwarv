"""Terminal rendering for the chat REPL -- styled via `rich` (already a
dependency), aiming for a Claude-Code-like terminal feel: a spinner while
the model is generating, colored diffs, a clear pass/fail verdict, and
Dwarv's own status narration visually distinct from the user's prompt and
the final reply.

Deliberately separate from session.py: ChatSession's own print_fn contract
stays plain text (so it's still trivially fake-able/assertable in tests --
see tests/test_session.py, none of which touch this module), this module
is purely presentational and operates on the strings that contract already
produces.
"""

from rich.text import Text

_DIFF_FILE_HEADER_PREFIXES = ("+++", "---")
_DIFF_HUNK_PREFIX = "@@"
_SUCCESS_MARKERS = ("applied -- verified", "applied -- unverified")
_FAILURE_MARKERS = ("not applied", "not continuing")


def style_narration(text: str) -> Text:
    """Dwarv's own status narration (model choice explanation, decision
    narration, squeeze events) -- dim and prefixed with a small marker so
    it reads as a live status feed, distinct from the user's prompt and
    the final structured reply. ASCII only (not e.g. "·") -- a Unicode
    marker here crashed outright on a legacy Windows console (cp1252,
    common outside Windows Terminal/UTF-8 setups), confirmed live."""
    result = Text("- ", style="dim cyan")
    result.append(text, style="dim")
    return result


def style_reply(text: str) -> Text:
    """Line-by-line styling for a turn's final reply: diff lines colored
    like a real diff (unified-diff format from repo/patch.py -- see its
    own docstring), the pass/fail verdict colored, everything else left
    as the model's own plain message text."""
    result = Text()
    lines = text.split("\n")
    for i, line in enumerate(lines):
        lowered = line.lower()
        if line.startswith(_DIFF_FILE_HEADER_PREFIXES):
            result.append(line, style="bold")
        elif line.startswith(_DIFF_HUNK_PREFIX):
            result.append(line, style="cyan")
        elif line.startswith("+"):
            result.append(line, style="green")
        elif line.startswith("-"):
            result.append(line, style="red")
        elif any(marker in lowered for marker in _SUCCESS_MARKERS):
            result.append(line, style="bold green")
        elif any(marker in lowered for marker in _FAILURE_MARKERS):
            result.append(line, style="bold yellow")
        else:
            result.append(line)
        if i < len(lines) - 1:
            result.append("\n")
    return result
