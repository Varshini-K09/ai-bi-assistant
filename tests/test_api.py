import json

from rest_framework.test import APIClient

from assistant import runtime
from assistant.llm import BaseProvider
from tests.conftest import CSV


def post(client, question):
    return client.post("/api/ask", {"question": question}, format="json")


def test_health_and_ui_are_served(client):
    h = client.get("/api/health").json()
    assert h["rows_loaded"] == 49 and h["rows_rejected"] == 2 and "rule-based" in h["llm_provider"]
    page = client.get("/")
    assert page.status_code == 200 and "Mini AI" in b"".join(page.streaming_content).decode()
    assert len(client.get("/api/examples").json()["examples"]) >= 5


def test_valid_question_returns_answer_supporting_data_and_sql(client):
    r = post(client, "Which product generated the highest revenue?")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok"
    assert body["intent"] == "top_by" and "Monitor" in body["answer"]
    assert body["columns"][0] == "product" and body["rows"][0][0] == "Monitor"
    assert body["queries"][0]["sql"].count("?") == len(body["queries"][0]["params"])   
    assert body["interpreted_by"] == "rules" and body["notes"]


def test_all_five_example_questions_from_the_brief(client):
    expected = {
        "What is the total revenue?": "₹14,39,200",
        "Which product generated the highest revenue?": "Monitor",
        "What is the monthly revenue trend?": "May 2026",
        "Which region generated the highest sales?": "South",
        "How did revenue change between January and February?": "+11.58%",
    }
    for question, fragment in expected.items():
        r = post(client, question)
        assert r.status_code == 200 and fragment in r.json()["answer"], question


def test_empty_and_malformed_requests_return_400(client):
    assert post(client, "   ").status_code == 400
    assert post(client, "x" * 501).status_code == 400
    assert client.post("/api/ask", {}, format="json").status_code == 400
    assert client.post("/api/ask", {"question": 123}, format="json").status_code == 400
    assert client.post("/api/ask", ["a"], format="json").status_code == 400
    assert client.post("/api/ask", "not json", content_type="application/json").status_code == 400


def test_unsupported_question_is_422_with_suggestions(client):
    r = post(client, "What is our profit margin?")
    assert r.status_code == 422 and r.json()["status"] == "unsupported" and r.json()["suggestions"]


def test_invalid_month_is_422_with_clear_message(client):
    r = post(client, "revenue in September")
    assert r.status_code == 422 and r.json()["status"] == "invalid"
    assert "Data covers January 2026 to June 2026" in r.json()["answer"]


def test_destructive_requests_never_change_data(client):
    for q in ("DROP TABLE sales;", "delete all records", "revenue'; DELETE FROM sales; --"):
        assert post(client, q).status_code == 422
    assert client.get("/api/health").json()["rows_loaded"] == 49
    assert post(client, "What is the total revenue?").json()["rows"][0][-1] == 1_439_200


def test_response_never_leaks_internals_on_unexpected_error(client, monkeypatch):
    import assistant.views as views

    def boom(*a, **k):
        raise RuntimeError("secret stack detail /etc/passwd")
    monkeypatch.setattr(views, "answer_question", boom)
    r = post(client, "What is the total revenue?")
    assert r.status_code == 500 and "secret" not in r.text and "passwd" not in r.text


class _Fake(BaseProvider):
    name = "fake"

    def __init__(self, reply):
        self.reply = reply

    def complete_json(self, s, u):
        return json.dumps(self.reply)


def test_llm_hallucinated_region_is_caught_by_validation(tmp_path):
    """The LLM invents region 'Central' while the question itself looks innocent."""
    runtime.initialize(CSV, tmp_path / "h.db", provider=_Fake({"intent": "total_revenue", "params": {"region": "Central"}}))
    r = post(APIClient(), "What is the total revenue?")
    assert r.status_code == 422 and "Unknown region 'Central'" in r.json()["answer"]


def test_llm_path_end_to_end(tmp_path):
    runtime.initialize(CSV, tmp_path / "l.db", provider=_Fake({"intent": "top_by", "params": {"dimension": "region"}}))
    c = APIClient()
    body = post(c, "where do we sell the most?").json()
    assert body["interpreted_by"] == "llm:fake" and "South" in body["answer"]
    assert c.get("/api/health").json()["llm_provider"] == "fake"


def test_app_starts_and_reports_clearly_when_csv_missing(tmp_path):
    import pytest
    from assistant.database import DataLoadError
    with pytest.raises(DataLoadError):
        runtime.initialize(tmp_path / "missing.csv", tmp_path / "x.db", provider=None)
