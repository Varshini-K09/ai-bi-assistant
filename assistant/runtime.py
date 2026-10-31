from __future__ import annotations

import logging
import threading
from pathlib import Path

from . import config
from .database import LoadReport, build_database
from .llm import BaseProvider, LLMError, get_provider

log = logging.getLogger("bi-assistant")
_UNSET = object()
_lock = threading.Lock()


class _State:
    ready = False
    csv_path: Path
    db_path: Path
    provider: BaseProvider | None = None
    load_report: LoadReport | None = None


state = _State()


def initialize(csv_path: Path | None = None, db_path: Path | None = None,
               provider: BaseProvider | None | object = _UNSET) -> _State:
    with _lock:
        state.ready = False
        state.csv_path = Path(csv_path or config.CSV_PATH)
        state.db_path = Path(db_path or config.DB_PATH)
        state.load_report = build_database(state.csv_path, state.db_path)   
        log.info("Loaded %d rows, rejected %d", state.load_report.loaded_rows, state.load_report.rejected_rows)
        if provider is _UNSET:
            try:
                state.provider = get_provider()
            except LLMError as exc:
                log.warning("LLM disabled, using rule-based interpreter: %s", exc)
                state.provider = None
        else:
            state.provider = provider
        state.ready = True
    return state


def ensure_ready() -> _State:
    if not state.ready:
        initialize()
    return state
