import pytest

from assistant.analytics import QueryValidationError, run_intent
from assistant.database import build_database, get_readonly_connection


def test_total_revenue(conn):
    r = run_intent(conn, "total_revenue", {})
    assert r.rows[0][-1] == 1_439_200          
    assert r.rows[0][0] == 48                  
    assert "₹14,39,200" in r.answer
    assert any("returned" in n.lower() for n in r.notes)   


def test_top_product_is_monitor(conn):
    r = run_intent(conn, "top_by", {"dimension": "product"})
    assert r.rows[0][0] == "Monitor" and r.rows[0][3] == 473_000


def test_top_region_is_south_and_lowest_is_west(conn):
    assert run_intent(conn, "top_by", {"dimension": "region"}).rows[0][0] == "South"
    assert run_intent(conn, "top_by", {"dimension": "region", "order": "lowest"}).rows[0][0] == "West"


def test_monthly_trend(conn):
    r = run_intent(conn, "monthly_trend", {})
    assert [row[0] for row in r.rows] == [f"2026-0{i}" for i in range(1, 7)]
    assert [row[2] for row in r.rows] == [210250, 234600, 223900, 240700, 318500, 211250]
    assert r.rows[0][3] is None and r.rows[1][3] == pytest.approx(11.58)


def test_month_comparison_jan_feb(conn):
    r = run_intent(conn, "month_comparison", {"month_a": "2026-01", "month_b": "2026-02"})
    assert r.rows[-1] == ["TOTAL", 210250, 234600, 24350]
    assert "increased" in r.answer and "+11.58%" in r.answer


def test_month_comparison_earlier_month_is_always_baseline(conn):
    r = run_intent(conn, "month_comparison", {"month_a": "2026-02", "month_b": "2026-01"})
    assert "from ₹2,10,250 in January 2026" in r.answer


def test_returned_orders_excluded_from_april(conn):
    r = run_intent(conn, "total_revenue", {"month": "2026-04"})
    assert r.rows[0][-1] == 240_700              


def test_filters_combine_and_are_case_insensitive(conn):
    r = run_intent(conn, "total_revenue", {"region": "north", "month": "2026-03"})
    assert r.params["filters"] == {"region": "North", "month": "2026-03"}
    assert r.rows[0][-1] == 3 * 7800 + 10 * 1800


def test_edge_case_empty_scope_returns_zero_not_error(conn):
    r = run_intent(conn, "total_revenue", {"region": "North", "product": "Desk", "month": "2026-01"})
    assert r.rows[0][-1] == 0 and "no completed orders" in r.answer


def test_edge_case_empty_ranking_is_handled(conn):
    r = run_intent(conn, "top_by", {"dimension": "channel", "product": "Desk", "region": "North", "month": "2026-01"})
    assert r.rows == [] and "no completed orders" in r.answer


def test_ties_are_reported_not_hidden(tmp_path):
    csv_path = tmp_path / "t.csv"
    csv_path.write_text(
        "Transaction_Id,Order_date,Product,Category,Region,Sales_channel,Quantity,Unit_price,Discount_amount,Status\n"
        "T1,01-01-2026,A,C,North,Online,1,100,0,Completed\n"
        "T2,02-01-2026,B,C,South,Online,1,100,0,Completed\n")
    build_database(csv_path, tmp_path / "t.db")
    c = get_readonly_connection(tmp_path / "t.db")
    r = run_intent(c, "top_by", {"dimension": "product"})
    assert "tied" in r.answer and "A, B" in r.answer


@pytest.mark.parametrize("intent,params,fragment", [
    ("total_revenue", {"region": "Central"}, "Unknown region"),
    ("total_revenue", {"month": "2026-09"}, "No sales data for September 2026"),
    ("total_revenue", {"month": "March"}, "Invalid month"),
    ("top_by", {"dimension": "customer"}, "Unknown dimension"),
    ("month_comparison", {"month_a": "2026-01"}, "needs two months"),
    ("month_comparison", {"month_a": "2026-01", "month_b": "2026-01"}, "different months"),
    ("drop_table", {}, "Unsupported intent"),
])
def test_invalid_requests_are_rejected(conn, intent, params, fragment):
    with pytest.raises(QueryValidationError, match=fragment):
        run_intent(conn, intent, params)


def test_sql_injection_in_parameter_is_just_an_unknown_value(conn):
    with pytest.raises(QueryValidationError):
        run_intent(conn, "total_revenue", {"region": "North' OR '1'='1"})
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 49


def test_data_quality_lists_rejected_and_returned(conn):
    r = run_intent(conn, "data_quality", {})
    assert {row[1] for row in r.rows} == {"TXN0034", "TXN0051", "TXN0033"}
