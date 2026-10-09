"""Step 10A: tails the active chat session's JSONL and yields it as
Server-Sent Events. Read-only -- this module never writes to the log.
"""

import json
import time
from collections.abc import Iterator
from pathlib import Path

from dwarv.telemetry.logger import current_session_log_path


def sse_tail(
    log_dir: str | Path,
    poll_interval_s: float = 0.5,
    max_idle_polls: int | None = None,
) -> Iterator[str]:
    """Yields SSE `data: <json>\n\n` strings: first the active session's
    full history so far (a panel opened mid-session isn't blind to what
    already happened), then every new line appended after that.

    Ends the stream (one terminal event, then returns) once the session's
    pointer file disappears, i.e. the real ChatSession called stop(). With
    no active session at all, yields a single `no_active_session` event and
    returns immediately -- the client (app.js) just shows "No active
    session." rather than hanging on an empty stream.

    `max_idle_polls` bounds how many empty polls to wait through before
    giving up -- real usage leaves it unset (runs until the client
    disconnects or the session ends); tests pass a small number so a test
    for "session never produces more events" terminates.
    """
    log_path = current_session_log_path(log_dir)
    if log_path is None:
        yield f"data: {json.dumps({'event': 'no_active_session'})}\n\n"
        return

    with open(log_path, encoding="utf-8") as f:
        idle_polls = 0
        while True:
            line = f.readline()
            if line:
                line = line.strip()
                if line:
                    yield f"data: {line}\n\n"
                idle_polls = 0
                continue
            if current_session_log_path(log_dir) is None:
                yield f"data: {json.dumps({'event': 'session_ended'})}\n\n"
                return
            idle_polls += 1
            if max_idle_polls is not None and idle_polls >= max_idle_polls:
                return
            time.sleep(poll_interval_s)
