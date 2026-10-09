"""Section 5.3: one JSON object per line, written to
`<log_dir>/<session_id>.jsonl`. This is the single source of truth behind
both /status's narration and the Step 10A transparency panel -- neither
reimplements session state from anything else.

`<log_dir>/current.json` is a small pointer file naming whichever session is
currently live (written by `start()`, removed by `stop()`), so a reader
(`gui/data.py`) can tell "no active session" apart from "a session ran here
once and ended" without guessing from file mtimes.
"""

import json
import threading
import time
import uuid
from pathlib import Path

CURRENT_POINTER_NAME = "current.json"

# The complete set of flow_node values any event this logger emits can carry.
# gui/static/flow.json's node ids must match this set exactly in both
# directions -- tests/test_gui.py::test_flow_json_matches_emitted_flow_nodes
# enforces that so the two can never silently drift apart.
FLOW_NODES = frozenset(
    {
        "run_started",
        "model_loaded",
        "turn_started",
        "patch_proposed",
        "verified",
        "decision",
        "budget_change",
        "budget_warn",
        "budget_violation",
        "model_unloaded",
        "run_finished",
        "monitor_sample",
    }
)


class EventLogger:
    def __init__(self, log_dir: str | Path, session_id: str | None = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.path = self.log_dir / f"{self.session_id}.jsonl"
        self._pointer_path = self.log_dir / CURRENT_POINTER_NAME
        self._seq = 0
        self._start_monotonic = time.monotonic()
        # ResourceMonitor's background sampling thread and the main session
        # loop can both call log() -- guard the append so concurrent writes
        # can't interleave into a corrupted line.
        self._write_lock = threading.Lock()

    def start(self) -> None:
        """Mark this session as the active one (for gui/data.py). Touches
        the jsonl file into existence even before the first log() call --
        current_session_log_path() checks the file actually exists, and a
        session that has genuinely started should be discoverable
        immediately, not only after its first event."""
        self.path.touch(exist_ok=True)
        self._pointer_path.write_text(
            json.dumps({"session_id": self.session_id, "path": str(self.path)}),
            encoding="utf-8",
        )

    def stop(self) -> None:
        """Clear the active-session pointer, but only if it's still pointing
        at *this* session -- a second session started after this one (e.g.
        in tests that create several loggers against the same log_dir)
        must not have its own pointer clobbered by an earlier session's
        delayed stop()."""
        if not self._pointer_path.exists():
            return
        try:
            current = json.loads(self._pointer_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if current.get("session_id") == self.session_id:
            self._pointer_path.unlink(missing_ok=True)

    def log(self, event: str, flow_node: str, **fields: object) -> dict:
        if flow_node not in FLOW_NODES:
            raise ValueError(f"unknown flow_node {flow_node!r} -- not in FLOW_NODES")
        with self._write_lock:
            self._seq += 1
            record = {
                "ts": time.time(),
                "session_id": self.session_id,
                "seq": self._seq,
                "rel_t_s": round(time.monotonic() - self._start_monotonic, 3),
                "event": event,
                "flow_node": flow_node,
                **fields,
            }
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        return record


class NullEventLogger:
    """Same interface as EventLogger, touches no disk. Used when a
    ChatSession has neither an explicit log_dir nor a cache_dir (e.g. the
    existing fake-runtime unit tests) -- telemetry is a real feature of the
    product, not something every test needs to exercise or clean up after."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def log(self, event: str, flow_node: str, **fields: object) -> dict:
        return {}


def current_session_log_path(log_dir: str | Path) -> Path | None:
    """The JSONL path of the currently-active session in `log_dir`, or None
    if there isn't one -- used by gui/data.py and gui/live.py to tell "no
    active session" apart from a past session's log still sitting on disk."""
    pointer = Path(log_dir) / CURRENT_POINTER_NAME
    if not pointer.exists():
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    path = Path(data.get("path", ""))
    return path if path.exists() else None


def read_events(log_path: str | Path) -> list[dict]:
    """Parse a session's JSONL back into a list of event dicts, skipping any
    trailing partial line (a crash or kill mid-write leaves one behind)."""
    events = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events
