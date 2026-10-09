import json
from pathlib import Path

import pytest

from dwarv.gui.data import build_session_summary
from dwarv.gui.live import sse_tail
from dwarv.telemetry.logger import FLOW_NODES, EventLogger

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "dwarv" / "gui" / "static"


def test_flow_json_matches_emitted_flow_nodes():
    """DWARV_PLAN.md Step 10A: node ids in flow.json must match the
    flow_node values the logger can actually emit, in both directions, so
    the two can never silently drift apart."""
    flow = json.loads((STATIC_DIR / "flow.json").read_text(encoding="utf-8"))
    flow_node_ids = {n["id"] for n in flow["nodes"]}
    assert flow_node_ids == set(FLOW_NODES)


def test_app_js_never_uses_innerhtml():
    """Hard constraint: model output and repo content are untrusted --
    render with textContent, never innerHTML. A permanent regression guard,
    not just a one-time manual check."""
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    # Checks for the actual property-access/call syntax, not the bare word,
    # so a comment that merely *mentions* innerHTML (e.g. explaining why it's
    # avoided) can't make this test flaky.
    assert ".innerHTML" not in source
    assert ".insertAdjacentHTML" not in source
    assert "document.write" not in source


def test_build_session_summary_no_active_session(tmp_path):
    assert build_session_summary(tmp_path) == {"active": False}


def test_build_session_summary_real_session(tmp_path):
    logger = EventLogger(tmp_path, session_id="sess1")
    logger.start()
    logger.log(
        "run_started",
        "run_started",
        repo_root="/repo",
        is_git_repo=True,
        test_command=["pytest"],
        sandbox_tier=3,
        sandbox_tier_detail="Windows watchdog",
    )
    logger.log(
        "model_loaded",
        "model_loaded",
        model_id="small",
        ctx_size=4096,
        explanation="Picked small because RAM is tight.",
    )
    logger.log("turn_started", "turn_started", turn_index=0)
    logger.log(
        "decision",
        "decision",
        action="RETRY_WITH_FEEDBACK",
        reason="WRONG_OUTPUT",
        narration="Retrying with feedback.",
        inputs_snapshot={"attempts_left": 2},
    )
    logger.log("monitor_sample", "monitor_sample", server_rss_mb=1700.0, sys_available_mb=4000.0)

    summary = build_session_summary(tmp_path)
    assert summary["active"] is True
    assert summary["session_id"] == "sess1"
    assert summary["model_id"] == "small"
    assert summary["explanation"] == "Picked small because RAM is tight."
    assert summary["sandbox_tier"] == 3
    assert summary["repo_root"] == "/repo"
    assert summary["test_command"] == ["pytest"]
    assert summary["turns_completed"] == 1
    assert len(summary["decisions"]) == 1
    assert summary["decisions"][0]["reason"] == "WRONG_OUTPUT"
    assert summary["latest_sample"]["server_rss_mb"] == 1700.0


def test_build_session_summary_includes_hostile_text_unescaped(tmp_path):
    """The API layer must not mangle or reject hostile-looking text -- JSON
    encoding itself is safe; escaping is the client's job (see
    test_app_js_never_uses_innerhtml). This confirms the data survives the
    round trip intact for the client to safely render as plain text."""
    logger = EventLogger(tmp_path, session_id="sess1")
    logger.start()
    logger.log("run_started", "run_started", repo_root="/repo")
    logger.log("model_loaded", "model_loaded", model_id="small", explanation="x")
    logger.log(
        "decision",
        "decision",
        action="RETRY_WITH_FEEDBACK",
        reason="WRONG_OUTPUT",
        narration="<script>alert(1)</script>",
        inputs_snapshot={"last_failure": "<img src=x onerror=alert(1)>"},
    )

    summary = build_session_summary(tmp_path)
    assert summary["decisions"][0]["narration"] == "<script>alert(1)</script>"
    assert (
        summary["decisions"][0]["inputs_snapshot"]["last_failure"] == "<img src=x onerror=alert(1)>"
    )


def test_sse_tail_no_active_session(tmp_path):
    events = list(sse_tail(tmp_path, max_idle_polls=1))
    assert len(events) == 1
    assert json.loads(events[0].removeprefix("data: ").strip())["event"] == "no_active_session"


def test_sse_tail_replays_history_then_reports_session_ended(tmp_path):
    """Pulls the generator manually (rather than draining it with a `for`
    loop) so stop() can land exactly between "caught up to existing
    history" and "checked whether the session is still active" -- the same
    place a real client's stream sits most of the time."""
    logger = EventLogger(tmp_path, session_id="sess1")
    logger.start()
    logger.log("run_started", "run_started", repo_root="/repo")
    logger.log("model_loaded", "model_loaded", model_id="small")

    gen = sse_tail(tmp_path, poll_interval_s=0.01, max_idle_polls=5)
    first = json.loads(next(gen).removeprefix("data: ").strip())
    second = json.loads(next(gen).removeprefix("data: ").strip())
    assert first["event"] == "run_started"
    assert second["event"] == "model_loaded"

    logger.stop()
    third = json.loads(next(gen).removeprefix("data: ").strip())
    assert third["event"] == "session_ended"

    with pytest.raises(StopIteration):
        next(gen)


def test_server_api_session_and_static_files(tmp_path):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from dwarv.gui.server import create_app

    app = create_app(tmp_path)
    client = fastapi_testclient.TestClient(app)

    no_session = client.get("/api/session")
    assert no_session.status_code == 200
    assert no_session.json() == {"active": False}

    logger = EventLogger(tmp_path, session_id="sess1")
    logger.start()
    logger.log("run_started", "run_started", repo_root="/repo")
    logger.log("model_loaded", "model_loaded", model_id="small", explanation="hi")

    with_session = client.get("/api/session")
    assert with_session.json()["model_id"] == "small"

    index = client.get("/")
    assert index.status_code == 200

    logo = client.get("/logo.png")
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/png"
    assert "dwarv" in index.text.lower()

    flow = client.get("/flow.json")
    assert flow.status_code == 200
    assert {n["id"] for n in flow.json()["nodes"]} == set(FLOW_NODES)

    docs = client.get("/docs")
    assert docs.status_code == 404  # Swagger UI disabled -- it would pull from a CDN


def test_server_api_session_returns_hostile_text_as_plain_json(tmp_path):
    """The server layer must never try to "help" by stripping/escaping --
    JSON itself is the safe transport; see test_app_js_never_uses_innerhtml
    for where the actual XSS defense lives (the client)."""
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from dwarv.gui.server import create_app

    app = create_app(tmp_path)
    client = fastapi_testclient.TestClient(app)

    logger = EventLogger(tmp_path, session_id="sess1")
    logger.start()
    logger.log("run_started", "run_started", repo_root="/repo")
    logger.log("model_loaded", "model_loaded", model_id="small")
    logger.log(
        "decision",
        "decision",
        action="RETRY_WITH_FEEDBACK",
        reason="x",
        narration="<script>alert(1)</script>",
        inputs_snapshot={},
    )

    data = client.get("/api/session").json()
    assert data["decisions"][0]["narration"] == "<script>alert(1)</script>"
