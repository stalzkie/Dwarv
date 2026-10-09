import json

import pytest

from dwarv.telemetry.logger import (
    FLOW_NODES,
    EventLogger,
    NullEventLogger,
    current_session_log_path,
    read_events,
)


def test_log_writes_one_json_object_per_line(tmp_path):
    logger = EventLogger(tmp_path, session_id="abc123")
    logger.log("run_started", "run_started", repo_root="/tmp/x")
    logger.log("model_loaded", "model_loaded", model_id="small")

    lines = logger.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "run_started"
    assert first["flow_node"] == "run_started"
    assert first["session_id"] == "abc123"
    assert first["seq"] == 1
    assert first["repo_root"] == "/tmp/x"
    assert "ts" in first and "rel_t_s" in first

    second = json.loads(lines[1])
    assert second["seq"] == 2


def test_log_rejects_unknown_flow_node(tmp_path):
    logger = EventLogger(tmp_path)
    with pytest.raises(ValueError):
        logger.log("something", "not_a_real_flow_node")


def test_start_stop_manage_current_pointer(tmp_path):
    logger = EventLogger(tmp_path, session_id="s1")
    assert current_session_log_path(tmp_path) is None

    logger.start()
    assert current_session_log_path(tmp_path) == logger.path

    logger.stop()
    assert current_session_log_path(tmp_path) is None


def test_stop_does_not_clobber_a_newer_sessions_pointer(tmp_path):
    """A session that's slow to call stop() must not erase a second
    session's pointer if one has since started in the same log_dir."""
    first = EventLogger(tmp_path, session_id="first")
    first.start()

    second = EventLogger(tmp_path, session_id="second")
    second.start()
    assert current_session_log_path(tmp_path) == second.path

    first.stop()
    assert current_session_log_path(tmp_path) == second.path


def test_read_events_skips_blank_and_partial_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(
        '{"event": "a"}\n\n{"event": "b"}\n{"event": "trunc',  # no closing brace -- a crash mid-write
        encoding="utf-8",
    )
    events = read_events(path)
    assert [e["event"] for e in events] == ["a", "b"]


def test_null_event_logger_is_a_safe_noop(tmp_path):
    logger = NullEventLogger()
    logger.start()
    result = logger.log("run_started", "run_started", anything=1)
    logger.stop()
    assert result == {}
    assert list(tmp_path.iterdir()) == []


def test_all_known_flow_nodes_are_accepted(tmp_path):
    logger = EventLogger(tmp_path)
    for node in FLOW_NODES:
        logger.log(node, node)
    events = read_events(logger.path)
    assert {e["flow_node"] for e in events} == set(FLOW_NODES)
