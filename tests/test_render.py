from dwarv.agent.render import style_narration, style_reply


def _styled_lines(text) -> dict[str, str]:
    """{line_text: style} for every span in a rendered Text -- each styled
    line becomes exactly one span (see render.py's append-per-line
    construction), so this gives a simple line -> style lookup for tests."""
    return {text.plain[span.start : span.end]: str(span.style) for span in text.spans}


def test_style_narration_prefixes_a_marker_and_dims_the_text():
    result = style_narration("Using small this session.")
    assert "Using small this session." in result.plain
    assert result.plain.startswith("- ")  # ASCII only -- see render.py's own docstring
    styles = _styled_lines(result)
    assert styles["Using small this session."] == "dim"


def test_style_reply_colors_diff_add_and_remove_lines():
    diff = "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n-return a - b\n+return a + b\n"
    result = style_reply(diff)
    styles = _styled_lines(result)
    assert styles["+return a + b"] == "green"
    assert styles["-return a - b"] == "red"
    assert styles["@@ -1,2 +1,2 @@"] == "cyan"
    assert styles["--- a/app.py"] == "bold"
    assert styles["+++ b/app.py"] == "bold"


def test_style_reply_colors_success_verdict_green():
    result = style_reply("some diff\n\nApplied -- verified (tests pass).")
    styles = _styled_lines(result)
    assert styles["Applied -- verified (tests pass)."] == "bold green"


def test_style_reply_colors_failure_verdict_yellow():
    result = style_reply("some diff\n\nNOT applied -- out of attempts.")
    styles = _styled_lines(result)
    assert styles["NOT applied -- out of attempts."] == "bold yellow"


def test_style_reply_leaves_plain_message_text_unstyled():
    result = style_reply("Here's a direct answer with no code changes.")
    assert result.plain == "Here's a direct answer with no code changes."
    assert len(result.spans) == 0


def test_style_reply_preserves_full_text_content():
    text = "message\n\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n\nApplied -- verified (tests pass)."
    result = style_reply(text)
    assert result.plain == text


def test_render_module_adds_no_non_ascii_decoration():
    """Real regression guard: a Unicode marker character (originally "·"
    in style_narration, originally "❯" for the REPL prompt) crashed
    outright with UnicodeEncodeError on a legacy Windows console (cp1252),
    confirmed live. Any fixed decoration this module adds must stay ASCII
    -- the model's own text can be anything, but what *we* add can't
    reintroduce this crash."""
    narration = style_narration("plain ascii narration")
    reply = style_reply("plain ascii reply\n+added line\n-removed line")
    assert narration.plain.isascii()
    assert reply.plain.isascii()
