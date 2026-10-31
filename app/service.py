"""Orchestrates one question end-to-end:
validate -> interpret (LLM or rules) -> validate params -> run SQL -> build response.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import config
from .analytics import QueryValidationError, load_catalog, run_intent
from .database import get_readonly_connection
from .interpreter import interpret
from .llm import BaseProvider

log = logging.getLogger("bi-assistant")

EXAMPLE_QUESTIONS = [
    "What is the total revenue?",
    "Which product generated the highest revenue?",
    "What is the monthly revenue trend?",
    "Which region generated the highest sales?",
    "How did revenue change between January and February?",
    "Total revenue for the North region in March",
    "Which sales channel performs best?",
    "Which records were rejected?",
]


class QuestionError(ValueError):
    """The request itself is malformed (empty / too long). Maps to HTTP 400."""


def validate_question(question: object) -> str:
    if not isinstance(question, str):
        raise QuestionError("'question' must be a string.")
    question = " ".join(question.split())
    if not question:
        raise QuestionError("Please enter a question.")
    if len(question) > config.MAX_QUESTION_LENGTH:
        raise QuestionError(f"Question is too long (max {config.MAX_QUESTION_LENGTH} characters).")
    return question


def answer_question(question: str, db_path: Path, provider: BaseProvider | None) -> tuple[int, dict]:
    """Return (http_status, body). Never raises for user-caused problems."""
    base = {"question": question, "status": "ok"}
    conn = get_readonly_connection(db_path)
    try:
        catalog = load_catalog(conn)
        interp = interpret(question, catalog, provider)
        base["interpreted_by"] = interp.source
        if interp.llm_error:
            base["llm_error"] = interp.llm_error

        if interp.intent is None:
            return 422, {**base, "status": "unsupported", "answer": interp.message,
                         "suggestions": EXAMPLE_QUESTIONS[:5]}

        try:
            result = run_intent(conn, interp.intent, interp.params)
        except QueryValidationError as exc:
            return 422, {**base, "status": "invalid", "intent": interp.intent, "answer": str(exc),
                         "suggestions": EXAMPLE_QUESTIONS[:5]}

        return 200, {**base, "intent": result.intent, "params": result.params, "answer": result.answer,
                     "columns": result.columns, "rows": result.rows, "queries": result.queries,
                     "notes": interp.notes + result.notes}
    finally:
        conn.close()
