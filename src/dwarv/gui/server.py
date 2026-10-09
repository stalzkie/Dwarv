"""Step 10A: FastAPI app for the optional, read-only session-transparency
panel. No write endpoints -- this process only ever reads the event log a
real `dwarv chat` session writes; it never touches the user's repo.
Swagger/ReDoc are disabled since they'd otherwise try to pull their JS/CSS
from a CDN, violating the "no CDN, no external requests" hard constraint
(DWARV_PLAN.md Step 10A).
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from dwarv.gui.data import build_session_summary
from dwarv.gui.live import sse_tail

STATIC_DIR = Path(__file__).parent / "static"


def create_app(log_dir: str | Path) -> FastAPI:
    app = FastAPI(title="dwarv gui", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/session")
    def get_session() -> JSONResponse:
        return JSONResponse(build_session_summary(log_dir))

    @app.get("/api/live/stream")
    def get_live_stream() -> StreamingResponse:
        return StreamingResponse(
            sse_tail(log_dir),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    # Everything else (index.html, app.js, style.css, flow.json, vendor/) is
    # served as-is from gui/static/ -- registered last so the API routes
    # above take priority.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app
