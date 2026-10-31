"""Django REST Framework views: POST /api/ask, GET /api/health, GET /api/examples, and the UI page."""
from __future__ import annotations

import logging
from pathlib import Path

from django.http import FileResponse
from rest_framework import status as http
from rest_framework.exceptions import ParseError, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import runtime
from .database import DataLoadError, get_readonly_connection
from .serializers import AskSerializer
from .service import EXAMPLE_QUESTIONS, answer_question

log = logging.getLogger("bi-assistant")
INDEX_HTML = Path(__file__).parent / "static" / "assistant" / "index.html"


def _first_message(detail) -> str:
    """Flatten a DRF error payload to a single human-readable string."""
    if isinstance(detail, dict):
        detail = next(iter(detail.values()), "")
    while isinstance(detail, (list, tuple)):
        detail = detail[0] if detail else ""
    return str(detail)


def api_exception_handler(exc, context):
    """Keep the original error shape: {"status": "error", "answer": "..."}."""
    if isinstance(exc, ParseError):
        return Response({"status": "error", "answer": "Request body must be valid JSON "
                         "like {\"question\": \"...\"}."}, status=http.HTTP_400_BAD_REQUEST)
    if isinstance(exc, ValidationError):
        return Response({"status": "error", "answer": _first_message(exc.detail)}, status=http.HTTP_400_BAD_REQUEST)
    from rest_framework.views import exception_handler
    response = exception_handler(exc, context)
    if response is not None:
        response.data = {"status": "error", "answer": _first_message(response.data)}
    return response


class AskView(APIView):
    def post(self, request):
        if not isinstance(request.data, dict):
            return Response({"status": "error", "answer": "Request body must be a JSON object."},
                            status=http.HTTP_400_BAD_REQUEST)
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = serializer.validated_data["question"]
        try:
            state = runtime.ensure_ready()
            code, payload = answer_question(question, state.db_path, state.provider)
        except DataLoadError as exc:
            log.exception("data error")
            return Response({"status": "error", "answer": str(exc)}, status=http.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception:                      # never leak internals to the client
            log.exception("unexpected error while answering")
            return Response({"status": "error", "answer": "Something went wrong on the server. "
                             "Please try again."}, status=http.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(payload, status=code)


class HealthView(APIView):
    def get(self, request):
        state = runtime.ensure_ready()
        conn = get_readonly_connection(state.db_path)
        try:
            n = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        finally:
            conn.close()
        prov = state.provider
        return Response({"status": "ok", "rows_loaded": n,
                         "rows_rejected": state.load_report.rejected_rows,
                         "llm_provider": prov.name if prov else "none (rule-based)",
                         "llm_model": getattr(prov, "model", None)})


class ExamplesView(APIView):
    def get(self, request):
        return Response({"examples": EXAMPLE_QUESTIONS})


def index(request):
    return FileResponse(open(INDEX_HTML, "rb"), content_type="text/html; charset=utf-8")
