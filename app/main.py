"""FastAPI application: POST /api/ask plus a tiny static UI."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import config
from .database import DataLoadError, build_database, get_readonly_connection
from .llm import BaseProvider, LLMError, get_provider
from .service import EXAMPLE_QUESTIONS, QuestionError, answer_question, validate_question

log = logging.getLogger("bi-assistant")
STATIC_DIR = Path(__file__).parent / "static"
_UNSET = object()


def create_app(csv_path: Path | None = None, db_path: Path | None = None,
               provider: BaseProvider | None | object = _UNSET) -> FastAPI:
    csv_path = Path(csv_path or config.CSV_PATH)
    db_path = Path(db_path or config.DB_PATH)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        report = build_database(csv_path, db_path)   # fails fast if the CSV is unusable
        app.state.load_report = report
        log.info("Loaded %d rows, rejected %d", report.loaded_rows, report.rejected_rows)
        if provider is _UNSET:
            try:
                app.state.provider = get_provider()
            except LLMError as exc:
                log.warning("LLM disabled, using rule-based interpreter: %s", exc)
                app.state.provider = None
        else:
            app.state.provider = provider
        yield

    app = FastAPI(title="Mini AI BI Assistant", version="1.0.0", lifespan=lifespan)

    @app.post("/api/ask")
    async def ask(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "answer": "Request body must be valid JSON "
                                 "like {\"question\": \"...\"}."}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"status": "error", "answer": "Request body must be a JSON object."},
                                status_code=400)
        try:
            question = validate_question(body.get("question"))
        except QuestionError as exc:
            return JSONResponse({"status": "error", "answer": str(exc)}, status_code=400)
        try:
            status, payload = await run_in_threadpool(answer_question, question, db_path, app.state.provider)
        except DataLoadError as exc:
            log.exception("data error")
            return JSONResponse({"status": "error", "answer": str(exc)}, status_code=503)
        except Exception:                      # never leak internals to the client
            log.exception("unexpected error while answering")
            return JSONResponse({"status": "error", "answer": "Something went wrong on the server. "
                                 "Please try again."}, status_code=500)
        return JSONResponse(payload, status_code=status)

    @app.get("/api/health")
    def health():
        conn = get_readonly_connection(db_path)
        try:
            n = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        finally:
            conn.close()
        prov = app.state.provider
        return {"status": "ok", "rows_loaded": n,
                "rows_rejected": app.state.load_report.rejected_rows,
                "llm_provider": prov.name if prov else "none (rule-based)",
                "llm_model": getattr(prov, "model", None)}

    @app.get("/api/examples")
    def examples():
        return {"examples": EXAMPLE_QUESTIONS}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
