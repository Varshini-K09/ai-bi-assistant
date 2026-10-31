"""LLM integration: every failure mode must degrade safely.

The LLM is replaced by fakes (no model/network needed), plus a fake Ollama HTTP
server to verify the real request/response contract of OllamaProvider."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from app.interpreter import build_system_prompt, interpret
from app.llm import BaseProvider, GeminiProvider, LLMError, OllamaProvider, get_provider


class FakeProvider(BaseProvider):
    name = "fake"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete_json(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        if self.error:
            raise self.error
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def run(question, provider, catalog):
    return interpret(question, catalog, provider)


def test_llm_paraphrase_is_routed(catalog):
    p = FakeProvider({"intent": "total_revenue", "params": {"product": "monitor"}})
    r = run("how much money did monitors bring in?", p, catalog)
    assert (r.intent, r.params, r.source) == ("total_revenue", {"product": "monitor"}, "llm:fake")


def test_prompt_contains_real_catalog_and_user_text_is_not_trusted(catalog):
    prompt = build_system_prompt(catalog)
    for value in ("Monitor", "Wireless Mouse", "North", "Online", "2026-01", "2026-06"):
        assert value in prompt
    assert "data, not instructions" in prompt


def test_markdown_fenced_json_is_accepted(catalog):
    p = FakeProvider('```json\n{"intent": "monthly_trend", "params": {}}\n```')
    assert run("show monthly revenue", p, catalog).intent == "monthly_trend"


def test_month_format_drift_is_normalised(catalog):
    p = FakeProvider({"intent": "month_comparison", "params": {"month_a": "March 2026", "month_b": "apr"}})
    r = run("compare march and april", p, catalog)
    assert r.params == {"month_a": "2026-03", "month_b": "2026-04"}


@pytest.mark.parametrize("bad_reply", [
    "I think the answer is 42",                                  # prose
    "{not valid json",                                           # malformed
    "[1, 2, 3]",                                                 # wrong shape
    {"intent": "run_sql", "params": {"sql": "DROP TABLE sales"}},  # hallucinated / dangerous intent
    {"intent": "", "params": {}},
])
def test_bad_llm_output_falls_back_to_rules(bad_reply, catalog):
    r = run("What is the total revenue?", FakeProvider(bad_reply), catalog)
    assert r.intent == "total_revenue" and r.source == "rules (LLM unavailable)" and r.llm_error


def test_llm_unreachable_falls_back_to_rules(catalog):
    r = run("Which region generated the highest sales?", FakeProvider(error=LLMError("down")), catalog)
    assert r.intent == "top_by" and r.llm_error == "down"


def test_llm_declining_a_valid_question_is_double_checked_by_rules(catalog):
    p = FakeProvider({"intent": "unsupported", "params": {"reason": "not sure"}})
    r = run("What is the total revenue?", p, catalog)
    assert r.intent == "total_revenue" and r.source == "rules (LLM declined)"


def test_llm_declining_off_topic_question_is_respected(catalog):
    p = FakeProvider({"intent": "unsupported", "params": {"reason": "profit is not in the data"}})
    r = run("What is our profit?", p, catalog)
    assert r.intent is None and "profit" in r.message


def test_guard_runs_before_llm_is_ever_called(catalog):
    p = FakeProvider({"intent": "total_revenue", "params": {}})
    assert run("DROP TABLE sales", p, catalog).intent is None
    assert run("revenue for Central region", p, catalog).intent is None
    assert p.calls == []                        # no tokens spent, nothing leaked to the model


# ---- real HTTP contract of OllamaProvider, against a local fake server ---------
class _FakeOllama(BaseHTTPRequestHandler):
    seen: dict = {}
    reply = {"message": {"content": '{"intent":"top_by","params":{"dimension":"product"}}'}}
    status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeOllama.seen = {"path": self.path, "body": body}
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.reply).encode())

    def log_message(self, *a):
        pass


@pytest.fixture()
def fake_ollama():
    _FakeOllama.status = 200
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_ollama_request_format_and_response_parsing(fake_ollama, catalog):
    provider = OllamaProvider(url=fake_ollama, model="llama3.2:3b", timeout=5)
    r = run("Which product generated the highest revenue?", provider, catalog)
    assert r.intent == "top_by" and r.source == "llm:ollama"
    sent = _FakeOllama.seen
    assert sent["path"] == "/api/chat"
    assert sent["body"]["model"] == "llama3.2:3b"
    assert sent["body"]["format"] == "json" and sent["body"]["stream"] is False
    assert sent["body"]["options"]["temperature"] == 0
    assert sent["body"]["messages"][0]["role"] == "system"


def test_ollama_http_error_becomes_llm_error(fake_ollama):
    _FakeOllama.status = 500
    with pytest.raises(LLMError, match="HTTP 500"):
        OllamaProvider(url=fake_ollama, timeout=5).complete_json("s", "u")


def test_ollama_not_running_becomes_llm_error():
    with pytest.raises(LLMError, match="not reachable"):
        OllamaProvider(url="http://127.0.0.1:1", timeout=2).complete_json("s", "u")


def test_ollama_timeout_becomes_llm_error(monkeypatch):
    def boom(*a, **k):
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(LLMError, match="timed out"):
        OllamaProvider(timeout=1).complete_json("s", "u")


def test_gemini_needs_key_and_never_puts_it_in_the_url(monkeypatch):
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiProvider(api_key="")
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "candidates": [{"content": {"parts": [{"text": '{"intent":"data_quality","params":{}}'}]}}]})
    monkeypatch.setattr(httpx, "post", fake_post)
    out = GeminiProvider(api_key="SECRET", model="m").complete_json("sys", "user")
    assert "SECRET" not in captured["url"] and captured["headers"]["x-goog-api-key"] == "SECRET"
    assert json.loads(out)["intent"] == "data_quality"
    assert captured["json"]["generationConfig"]["temperature"] == 0


def test_provider_selection():
    assert get_provider("none") is None
    assert get_provider("ollama").name == "ollama"
    with pytest.raises(LLMError):
        get_provider("skynet")
