import csv
from collections import defaultdict

import pytest

from assistant.analytics import QueryValidationError, run_intent
from assistant.interpreter import interpret
from tests.conftest import CSV


def _valid_completed():
    rows = []
    with open(CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                qty, price = int(r["Quantity"]), float(r["Unit_price"])
                disc = float(r["Discount_amount"] or 0)
            except (ValueError, KeyError):
                continue
            if qty > 0 and r["Status"].strip() == "Completed":
                rows.append({**r, "qty": qty, "net": qty * price - disc})
    return rows


RAW = _valid_completed()


def test_independent_baseline_matches_known_total():
    assert sum(r["net"] for r in RAW) == 1_439_200 and len(RAW) == 48


def test_top_n_products(conn):
    by = defaultdict(float)
    for r in RAW:
        by[r["Product"]] += r["net"]
    expected = sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    r = run_intent(conn, "top_by", {"dimension": "product", "limit": 3})
    assert [(row[0], row[3]) for row in r.rows] == expected
    assert r.answer.startswith("Top 3 products")
    assert r.rows[0][4] == pytest.approx(expected[0][1] / sum(by.values()) * 100, abs=0.01)


def test_bottom_n_and_limit_larger_than_groups(conn):
    r = run_intent(conn, "top_by", {"dimension": "region", "order": "lowest", "limit": 2})
    assert [row[0] for row in r.rows] == ["West", "East"]
    r = run_intent(conn, "top_by", {"dimension": "channel", "limit": 10})
    assert len(r.rows) == 2 and any("fewer than 10" in n for n in r.notes)


@pytest.mark.parametrize("bad", [0, -1, 999, "abc"])
def test_invalid_limit_rejected(conn, bad):
    with pytest.raises(QueryValidationError):
        run_intent(conn, "top_by", {"dimension": "product", "limit": bad})


def test_average_order_value(conn):
    r = run_intent(conn, "average_order_value", {})
    assert r.rows[0][2] == pytest.approx(sum(x["net"] for x in RAW) / len(RAW), abs=0.01)
    north = [x for x in RAW if x["Region"] == "North"]
    r = run_intent(conn, "average_order_value", {"region": "North"})
    assert r.rows[0][2] == pytest.approx(sum(x["net"] for x in north) / len(north), abs=0.01)


def test_average_order_value_by_dimension_sorted(conn):
    r = run_intent(conn, "average_order_value", {"dimension": "region"})
    values = [row[3] for row in r.rows]
    assert values == sorted(values, reverse=True) and len(r.rows) == 4
    low = run_intent(conn, "average_order_value", {"dimension": "region", "order": "lowest"})
    assert [row[3] for row in low.rows] == sorted(values)


def test_sales_volume_overall_and_by_product(conn):
    r = run_intent(conn, "sales_volume", {})
    assert r.rows[0][:2] == [48, sum(x["qty"] for x in RAW)]
    by = defaultdict(int)
    for x in RAW:
        by[x["Product"]] += x["qty"]
    top = max(by.items(), key=lambda kv: kv[1])
    r = run_intent(conn, "sales_volume", {"dimension": "product"})
    assert (r.rows[0][0], r.rows[0][2]) == top


def test_month_extreme(conn):
    by = defaultdict(float)
    for x in RAW:
        by[x["Order_date"][3:5]] += x["net"]          
    best, worst = max(by, key=by.get), min(by, key=by.get)
    assert f"2026-{best}" in [row[0] for row in run_intent(conn, "month_extreme", {}).rows]
    assert "May 2026" in run_intent(conn, "month_extreme", {}).answer and best == "05"
    assert "January 2026" in run_intent(conn, "month_extreme", {"order": "lowest"}).answer and worst == "01"


def test_month_extreme_with_filter_ignores_month_param(conn):
    r = run_intent(conn, "month_extreme", {"product": "Monitor", "month": "2026-03"})
    assert "Monitor" in r.answer and len(r.rows) == 6


def test_unknown_values_still_rejected_for_new_intents(conn):
    with pytest.raises(QueryValidationError):
        run_intent(conn, "average_order_value", {"region": "Central"})
    with pytest.raises(QueryValidationError):
        run_intent(conn, "sales_volume", {"dimension": "customer"})
    with pytest.raises(QueryValidationError):
        run_intent(conn, "month_extreme", {"month": "2026-09", "product": "Nope"})


@pytest.mark.parametrize("question,intent,expect", [
    ("Top 3 products by revenue", "top_by", {"dimension": "product", "limit": 3, "order": "highest"}),
    ("bottom two regions", "top_by", {"dimension": "region", "limit": 2, "order": "lowest"}),
    ("What is the average order value?", "average_order_value", {}),
    ("revenue per order", "average_order_value", {}),
    ("average order value by region", "average_order_value", {"dimension": "region"}),
    ("average order value for North in March", "average_order_value", {"region": "North", "month": "2026-03"}),
    ("Which product has the lowest average order value?", "average_order_value",
     {"dimension": "product", "order": "lowest"}),
    ("How many units did we sell?", "sales_volume", {}),
    ("How many orders?", "sales_volume", {}),
    ("Which product sold the most units?", "sales_volume", {"dimension": "product", "order": "highest"}),
    ("units of Monitor sold in May", "sales_volume", {"product": "Monitor", "month": "2026-05"}),
    ("Which month had the lowest revenue?", "month_extreme", {"order": "lowest"}),
    ("best month for Monitor", "month_extreme", {"product": "Monitor", "order": "highest"}),
])
def test_rule_interpreter_routes_new_questions(catalog, question, intent, expect):
    r = interpret(question, catalog, provider=None)
    assert r.intent == intent, r.message
    for k, v in expect.items():
        assert r.params.get(k) == v, (k, r.params)


@pytest.mark.parametrize("question", [
    "top 3 customers", "What is our profit margin?", "average order value in September",
    "Which month sold the most units?", "average order value for Central region", "top 3 products in 2025",
])
def test_new_phrasings_do_not_weaken_guards(catalog, question):
    r = interpret(question, catalog, provider=None)
    if r.intent is not None:       
        with pytest.raises(QueryValidationError):
            from assistant.analytics import normalise_request
            normalise_request(r.intent, r.params, catalog)
    else:
        assert r.message


def test_existing_five_questions_unchanged(catalog):
    expected = {"What is the total revenue?": "total_revenue", "Which product generated the highest revenue?": "top_by",
                "What is the monthly revenue trend?": "monthly_trend",
                "Which region generated the highest sales?": "top_by",
                "How did revenue change between January and February?": "month_comparison"}
    for q, i in expected.items():
        assert interpret(q, catalog, provider=None).intent == i


def test_api_end_to_end_new_intents(client):
    for q, frag in [("Top 3 products by revenue", "Monitor"), ("What is the average order value?", "₹29,983.33"),
                    ("Which month had the lowest revenue?", "January 2026"),
                    ("Which product sold the most units?", "Wireless Mouse")]:
        r = client.post("/api/ask", {"question": q}, format="json")
        assert r.status_code == 200 and frag in r.json()["answer"], q
        assert r.json()["queries"][0]["sql"].count("?") == len(r.json()["queries"][0]["params"])
