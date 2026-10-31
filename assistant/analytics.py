from __future__ import annotations

import calendar
import re
import sqlite3
from dataclasses import dataclass, field

CURRENCY_SYMBOL = "₹"

DIMENSIONS = {
    "product": "product",
    "category": "category",
    "region": "region",
    "channel": "sales_channel",
}
FILTER_COLUMNS = {**DIMENSIONS}  

NET_REVENUE = "(quantity * unit_price - discount_amount)"
GROSS_SALES = "(quantity * unit_price)"

INTENTS = {
    "total_revenue": "Total net revenue, optionally filtered (product/category/region/channel/month).",
    "top_by": "Rank a dimension (product, category, region or channel) by revenue; "
              "param 'dimension', 'order' ('highest' or 'lowest') and optional 'limit' for top-N (e.g. 3).",
    "monthly_trend": "Revenue for every month, with month-over-month change.",
    "month_comparison": "Compare revenue between two months; params 'month_a' and 'month_b' as YYYY-MM.",
    "average_order_value": "Average revenue per completed order, optionally filtered; "
                           "add param 'dimension' to break it down by product/category/region/channel.",
    "sales_volume": "Number of completed orders and units sold, optionally filtered; "
                    "add param 'dimension' to break it down (e.g. 'which product sold the most units').",
    "month_extreme": "Which single month had the highest or lowest revenue; param 'order' "
                     "('highest' or 'lowest'), optionally filtered by product/category/region/channel.",
    "data_quality": "Which CSV records were rejected or excluded, and why.",
}
MAX_LIMIT = 50


class QueryValidationError(ValueError):
    """The request is understandable but its parameters are invalid
    (unknown region, month outside the data range, ...)."""


@dataclass
class AnalyticsResult:
    intent: str
    params: dict
    answer: str
    columns: list[str]
    rows: list[list]
    queries: list[dict] = field(default_factory=list)  
    notes: list[str] = field(default_factory=list)


def plural(dim: str) -> str:
    return "categories" if dim == "category" else f"{dim}s"


def fmt_money(value: float | int | None) -> str:
    """Indian digit grouping (12,34,567) with the currency symbol."""
    value = value or 0
    sign = "-" if value < 0 else ""
    whole, _, frac = f"{abs(value):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        whole = f"{head},{tail}"
    frac_part = "" if frac == "00" else f".{frac}"
    return f"{sign}{CURRENCY_SYMBOL}{whole}{frac_part}"


def month_label(month_key: str) -> str:
    year, month = month_key.split("-")
    return f"{calendar.month_name[int(month)]} {year}"


def pct_change(old: float, new: float) -> float | None:
    if not old:
        return None
    return round((new - old) / old * 100, 2)


def _fmt_pct(p: float | None) -> str:
    return "n/a (previous value is zero)" if p is None else f"{p:+.2f}%"


def load_catalog(conn: sqlite3.Connection) -> dict:
    catalog: dict = {}
    for key, col in DIMENSIONS.items():
        rows = conn.execute(f"SELECT DISTINCT {col} FROM sales ORDER BY {col}").fetchall()
        catalog[key] = [r[0] for r in rows]
    months = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(order_date, 1, 7) FROM sales ORDER BY 1").fetchall()]
    catalog["months"] = months
    return catalog


def _canonical(kind: str, value: str, catalog: dict) -> str:
    for known in catalog[kind]:
        if known.lower() == str(value).strip().lower():
            return known
    raise QueryValidationError(
        f"Unknown {kind} '{value}'. Available {kind} values: {', '.join(catalog[kind])}.")


def _check_month(value: str, catalog: dict) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise QueryValidationError(f"Invalid month '{value}'. Expected format YYYY-MM.")
    months = catalog["months"]
    if value not in months:
        raise QueryValidationError(
            f"No sales data for {month_label(value)}. "
            f"Data covers {month_label(months[0])} to {month_label(months[-1])}.")
    return value


def normalise_request(intent: str, params: dict | None, catalog: dict) -> dict:
    """Validate + canonicalise (intent, params). Raises QueryValidationError.

    Anything not on the whitelist is dropped or rejected, so a hallucinated
    intent/parameter from the LLM can never reach SQL.
    """
    if intent not in INTENTS:
        raise QueryValidationError(f"Unsupported intent '{intent}'.")
    params = dict(params or {})
    clean: dict = {}

    if intent in ("total_revenue", "top_by", "monthly_trend", "average_order_value",
                  "sales_volume", "month_extreme"):
        filters: dict = {}
        for key in FILTER_COLUMNS:
            if params.get(key) not in (None, ""):
                filters[key] = _canonical(key, params[key], catalog)
        if params.get("month") not in (None, ""):
            filters["month"] = _check_month(params["month"], catalog)
        clean["filters"] = filters

    if intent == "top_by":
        dim = str(params.get("dimension", "")).strip().lower()
        if dim not in DIMENSIONS:
            raise QueryValidationError(
                f"Unknown dimension '{dim}'. Choose one of: {', '.join(DIMENSIONS)}.")
        order = str(params.get("order", "highest")).strip().lower()
        if order not in ("highest", "lowest"):
            order = "highest"
        clean["dimension"] = dim
        clean["order"] = order
        clean["filters"].pop(dim, None)  # filtering on the ranked dimension is meaningless
        if params.get("limit") not in (None, ""):
            try:
                limit = int(float(str(params["limit"]).strip()))
            except ValueError:
                raise QueryValidationError(f"Invalid top-N value '{params['limit']}'. Use a whole number like 3.")
            if not 1 <= limit <= MAX_LIMIT:
                raise QueryValidationError(f"Top-N must be between 1 and {MAX_LIMIT}.")
            clean["limit"] = limit

    if intent in ("average_order_value", "sales_volume"):
        dim = str(params.get("dimension") or "").strip().lower()
        if dim:
            if dim not in DIMENSIONS:
                raise QueryValidationError(
                    f"Unknown dimension '{dim}'. Choose one of: {', '.join(DIMENSIONS)}.")
            clean["dimension"] = dim
            clean["filters"].pop(dim, None)
        order = str(params.get("order", "highest")).strip().lower()
        clean["order"] = order if order in ("highest", "lowest") else "highest"

    if intent == "month_extreme":
        order = str(params.get("order", "highest")).strip().lower()
        clean["order"] = order if order in ("highest", "lowest") else "highest"

    if intent in ("monthly_trend", "month_extreme"):
        clean["filters"].pop("month", None)  # these span all months

    if intent == "month_comparison":
        a, b = params.get("month_a"), params.get("month_b")
        if not a or not b:
            raise QueryValidationError("Comparing months needs two months, e.g. 'January and February'.")
        a, b = _check_month(a, catalog), _check_month(b, catalog)
        if a == b:
            raise QueryValidationError("Please pick two different months to compare.")
        clean["month_a"], clean["month_b"] = sorted((a, b))  
    return clean


def _where(filters: dict, status: str | None = "Completed") -> tuple[str, list]:
    clauses: list[str] = []
    args: list = []
    if status:
        clauses.append("status = ?")
        args.append(status)
    for key, value in filters.items():
        if key == "month":
            clauses.append("substr(order_date, 1, 7) = ?")
        else:
            clauses.append(f"{FILTER_COLUMNS[key]} = ?")
        args.append(value)
    return " WHERE " + " AND ".join(clauses), args


def _describe(filters: dict) -> str:
    if not filters:
        return ""
    parts = []
    for key, value in filters.items():
        if key == "month":
            parts.append(f"in {month_label(value)}")
        elif key == "channel":
            parts.append(f"via the {value} channel")
        else:
            parts.append(f"for {key} '{value}'")
    return " " + " ".join(parts)


def _common_notes(conn: sqlite3.Connection, filters: dict, queries: list[dict],
                  months: tuple[str, ...] | None = None) -> list[str]:
    notes = ["Revenue = quantity × unit price − discount, counting only 'Completed' orders."]
    where, args = _where(filters, status="Returned")
    if months:  # e.g. month comparison: only look at the months being compared
        where += " AND substr(order_date, 1, 7) IN (%s)" % ", ".join("?" * len(months))
        args = args + list(months)
    sql = f"SELECT COUNT(*), COALESCE(SUM{NET_REVENUE}, 0) FROM sales{where}"
    count, value = conn.execute(sql, args).fetchone()
    queries.append({"label": "Returned orders excluded from revenue", "sql": sql, "params": args})
    if count:
        notes.append(f"Excluded {count} returned order(s) worth {fmt_money(value)} in this scope.")
    rejected = conn.execute("SELECT COUNT(*) FROM rejected_records").fetchone()[0]
    if rejected:
        notes.append(f"{rejected} CSV row(s) failed validation and are not included "
                     f"(ask 'which records were rejected?').")
    return notes


def _total_revenue(conn, p) -> AnalyticsResult:
    filters = p["filters"]
    where, args = _where(filters)
    sql = (f"SELECT COUNT(*) AS orders, COALESCE(SUM(quantity), 0) AS units, "
           f"COALESCE(SUM{GROSS_SALES}, 0) AS gross_sales, "
           f"COALESCE(SUM(discount_amount), 0) AS discounts, "
           f"COALESCE(SUM{NET_REVENUE}, 0) AS net_revenue FROM sales{where}")
    row = conn.execute(sql, args).fetchone()
    queries = [{"label": "Revenue totals", "sql": sql, "params": args}]
    scope = _describe(filters)
    if row["orders"] == 0:
        answer = f"There are no completed orders{scope}, so revenue is {fmt_money(0)}."
    else:
        answer = (f"Total revenue{scope} is {fmt_money(row['net_revenue'])} "
                  f"from {row['orders']} completed orders ({row['units']} units sold, "
                  f"after {fmt_money(row['discounts'])} in discounts).")
    return AnalyticsResult(
        "total_revenue", p, answer,
        ["orders", "units", "gross_sales", "discounts", "net_revenue"],
        [[row["orders"], row["units"], row["gross_sales"], row["discounts"], row["net_revenue"]]],
        queries, _common_notes(conn, filters, queries))


def _top_by(conn, p) -> AnalyticsResult:
    dim, order, filters, limit = p["dimension"], p["order"], p["filters"], p.get("limit")
    col = DIMENSIONS[dim]
    where, args = _where(filters)
    direction = "DESC" if order == "highest" else "ASC"
    sql = (f"SELECT {col} AS {dim}, COUNT(*) AS orders, SUM(quantity) AS units, "
           f"SUM{NET_REVENUE} AS net_revenue FROM sales{where} "
           f"GROUP BY {col} ORDER BY net_revenue {direction}, {col} ASC")
    rows = conn.execute(sql, args).fetchall()
    queries = [{"label": f"Revenue by {dim}", "sql": sql, "params": args}]
    scope = _describe(filters)
    if not rows:
        answer = f"There are no completed orders{scope}, so no {dim} can be ranked."
        return AnalyticsResult("top_by", p, answer, [dim, "orders", "units", "net_revenue"], [],
                               queries, _common_notes(conn, filters, queries))
    total = sum(r["net_revenue"] for r in rows)      
    notes = _common_notes(conn, filters, queries)
    best = rows[0]
    if limit and limit > 1:
        shown = rows[:limit]
        label = "Top" if order == "highest" else "Bottom"
        items = "; ".join(f"{i}. {r[dim]} ({fmt_money(r['net_revenue'])})" for i, r in enumerate(shown, 1))
        answer = f"{label} {len(shown)} {plural(dim)} by revenue{scope}: {items}."
        if len(shown) < limit:
            notes.append(f"Only {len(rows)} {plural(dim)} exist in this scope, so fewer than {limit} are shown.")
        else:
            notes.append(f"Showing {len(shown)} of {len(rows)} {plural(dim)}; percentages are of the all-{dim} total "
                         f"({fmt_money(total)}).")
        rows = shown
    else:
        tied = [r[dim] for r in rows if r["net_revenue"] == best["net_revenue"]]
        share = best["net_revenue"] / total * 100 if total else 0
        if len(tied) > 1:
            answer = (f"{len(tied)} {dim}s are tied for {order} revenue{scope}: "
                      f"{', '.join(tied)} with {fmt_money(best['net_revenue'])} each.")
        else:
            answer = (f"The {dim} with the {order} revenue{scope} is {best[dim]} "
                      f"with {fmt_money(best['net_revenue'])} ({share:.1f}% of {fmt_money(total)} total).")
    data = [[r[dim], r["orders"], r["units"], r["net_revenue"],
             round(r["net_revenue"] / total * 100, 2) if total else 0] for r in rows]
    return AnalyticsResult("top_by", p, answer,
                           [dim, "orders", "units", "net_revenue", "share_pct"], data, queries, notes)


def _monthly_trend(conn, p) -> AnalyticsResult:
    filters = p["filters"]
    where, args = _where(filters)
    sql = (f"SELECT substr(order_date, 1, 7) AS month, COUNT(*) AS orders, "
           f"SUM{NET_REVENUE} AS net_revenue FROM sales{where} GROUP BY month ORDER BY month")
    rows = conn.execute(sql, args).fetchall()
    queries = [{"label": "Revenue by month", "sql": sql, "params": args}]
    scope = _describe(filters)
    if not rows:
        return AnalyticsResult("monthly_trend", p, f"There are no completed orders{scope}.",
                               ["month", "orders", "net_revenue", "mom_change_pct"], [],
                               queries, _common_notes(conn, filters, queries))
    data, prev = [], None
    for r in rows:
        data.append([r["month"], r["orders"], r["net_revenue"],
                     None if prev is None else pct_change(prev, r["net_revenue"])])
        prev = r["net_revenue"]
    peak = max(rows, key=lambda r: r["net_revenue"])
    low = min(rows, key=lambda r: r["net_revenue"])
    first, last = rows[0], rows[-1]
    answer = (f"Monthly revenue{scope} went from {fmt_money(first['net_revenue'])} in "
              f"{month_label(first['month'])} to {fmt_money(last['net_revenue'])} in "
              f"{month_label(last['month'])} ({_fmt_pct(pct_change(first['net_revenue'], last['net_revenue']))}). "
              f"Peak: {month_label(peak['month'])} ({fmt_money(peak['net_revenue'])}); "
              f"lowest: {month_label(low['month'])} ({fmt_money(low['net_revenue'])}).")
    return AnalyticsResult("monthly_trend", p, answer,
                           ["month", "orders", "net_revenue", "mom_change_pct"], data,
                           queries, _common_notes(conn, filters, queries))


def _month_comparison(conn, p) -> AnalyticsResult:
    a, b = p["month_a"], p["month_b"]
    sql = (f"SELECT product, "
           f"COALESCE(SUM(CASE WHEN substr(order_date,1,7) = ? THEN {NET_REVENUE} END), 0) AS rev_a, "
           f"COALESCE(SUM(CASE WHEN substr(order_date,1,7) = ? THEN {NET_REVENUE} END), 0) AS rev_b "
           f"FROM sales WHERE status = ? AND substr(order_date,1,7) IN (?, ?) "
           f"GROUP BY product ORDER BY (rev_b - rev_a) DESC, product")
    args = [a, b, "Completed", a, b]
    rows = conn.execute(sql, args).fetchall()
    queries = [{"label": f"Revenue by product: {a} vs {b}", "sql": sql, "params": args}]
    total_a = sum(r["rev_a"] for r in rows)
    total_b = sum(r["rev_b"] for r in rows)
    diff = total_b - total_a
    pct = pct_change(total_a, total_b)
    verb = "increased" if diff > 0 else "decreased" if diff < 0 else "did not change"
    answer = (f"Revenue {verb} from {fmt_money(total_a)} in {month_label(a)} to "
              f"{fmt_money(total_b)} in {month_label(b)}")
    if diff:
        answer += f" — a change of {fmt_money(diff)} ({_fmt_pct(pct)})."
    else:
        answer += "."
    data = [[r["product"], r["rev_a"], r["rev_b"], r["rev_b"] - r["rev_a"]] for r in rows]
    data.append(["TOTAL", total_a, total_b, diff])
    notes = _common_notes(conn, {}, queries, months=(a, b))
    notes.insert(1, f"Baseline is the earlier month ({month_label(a)}); change = later − earlier.")
    return AnalyticsResult("month_comparison", p, answer,
                           ["product", f"revenue_{a}", f"revenue_{b}", "change"], data, queries, notes)


def _grouped_or_overall(conn, p, select_extra: str):
    """Shared query for AOV / volume: overall row, or one row per dimension value."""
    filters, dim = p["filters"], p.get("dimension")
    where, args = _where(filters)
    if dim:
        col = DIMENSIONS[dim]
        sql = (f"SELECT {col} AS {dim}, COUNT(*) AS orders, COALESCE(SUM(quantity), 0) AS units, "
               f"COALESCE(SUM{NET_REVENUE}, 0) AS net_revenue FROM sales{where} GROUP BY {col}")
    else:
        sql = (f"SELECT COUNT(*) AS orders, COALESCE(SUM(quantity), 0) AS units, "
               f"COALESCE(SUM{NET_REVENUE}, 0) AS net_revenue FROM sales{where}")
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    return rows, [{"label": select_extra, "sql": sql, "params": args}]


def _average_order_value(conn, p) -> AnalyticsResult:
    filters, dim, order = p["filters"], p.get("dimension"), p["order"]
    rows, queries = _grouped_or_overall(conn, p, "Orders and revenue for average order value")
    scope = _describe(filters)
    notes = _common_notes(conn, filters, queries)
    notes.insert(1, "Average order value = net revenue ÷ number of completed orders (one CSV row = one order).")
    if dim:
        for r in rows:
            r["aov"] = r["net_revenue"] / r["orders"] if r["orders"] else 0
        rows.sort(key=lambda r: (-r["aov"] if order == "highest" else r["aov"], r[dim]))
        if not rows:
            return AnalyticsResult("average_order_value", p, f"There are no completed orders{scope}.",
                                   [dim, "orders", "net_revenue", "avg_order_value"], [], queries, notes)
        best = rows[0]
        answer = (f"Average order value by {dim}{scope}: {best[dim]} has the {order} at "
                  f"{fmt_money(best['aov'])} ({best['orders']} orders).")
        data = [[r[dim], r["orders"], r["net_revenue"], round(r["aov"], 2)] for r in rows]
        return AnalyticsResult("average_order_value", p, answer,
                               [dim, "orders", "net_revenue", "avg_order_value"], data, queries, notes)
    row = rows[0]
    if row["orders"] == 0:
        return AnalyticsResult("average_order_value", p, f"There are no completed orders{scope}, "
                               "so there is no average order value.",
                               ["orders", "net_revenue", "avg_order_value"], [], queries, notes)
    aov = row["net_revenue"] / row["orders"]
    answer = (f"The average order value{scope} is {fmt_money(aov)} "
              f"({fmt_money(row['net_revenue'])} across {row['orders']} completed orders).")
    return AnalyticsResult("average_order_value", p, answer, ["orders", "net_revenue", "avg_order_value"],
                           [[row["orders"], row["net_revenue"], round(aov, 2)]], queries, notes)


def _sales_volume(conn, p) -> AnalyticsResult:
    filters, dim, order = p["filters"], p.get("dimension"), p["order"]
    rows, queries = _grouped_or_overall(conn, p, "Orders and units sold")
    scope = _describe(filters)
    notes = _common_notes(conn, filters, queries)
    notes.insert(1, "Orders and units count only 'Completed' orders (one CSV row = one order).")
    if dim:
        rows.sort(key=lambda r: (-r["units"] if order == "highest" else r["units"], r[dim]))
        if not rows:
            return AnalyticsResult("sales_volume", p, f"There are no completed orders{scope}.",
                                   [dim, "orders", "units", "net_revenue"], [], queries, notes)
        best = rows[0]
        total_units = sum(r["units"] for r in rows)
        answer = (f"The {dim} with the {order} units sold{scope} is {best[dim]} with {best['units']} units "
                  f"({best['units'] / total_units * 100:.1f}% of {total_units} total).")
        data = [[r[dim], r["orders"], r["units"], r["net_revenue"]] for r in rows]
        return AnalyticsResult("sales_volume", p, answer, [dim, "orders", "units", "net_revenue"],
                               data, queries, notes)
    row = rows[0]
    answer = (f"There are {row['orders']} completed orders{scope} with {row['units']} units sold.")
    return AnalyticsResult("sales_volume", p, answer, ["orders", "units", "net_revenue"],
                           [[row["orders"], row["units"], row["net_revenue"]]], queries, notes)


def _month_extreme(conn, p) -> AnalyticsResult:
    filters, order = p["filters"], p["order"]
    where, args = _where(filters)
    sql = (f"SELECT substr(order_date, 1, 7) AS month, COUNT(*) AS orders, "
           f"SUM{NET_REVENUE} AS net_revenue FROM sales{where} GROUP BY month ORDER BY month")
    rows = conn.execute(sql, args).fetchall()
    queries = [{"label": "Revenue by month", "sql": sql, "params": args}]
    scope = _describe(filters)
    notes = _common_notes(conn, filters, queries)
    if not rows:
        return AnalyticsResult("month_extreme", p, f"There are no completed orders{scope}.",
                               ["month", "orders", "net_revenue", "share_pct"], [], queries, notes)
    total = sum(r["net_revenue"] for r in rows)
    pick = max if order == "highest" else min
    target = pick(r["net_revenue"] for r in rows)
    winners = [r["month"] for r in rows if r["net_revenue"] == target]
    if len(winners) > 1:
        answer = (f"{len(winners)} months are tied for {order} revenue{scope}: "
                  f"{', '.join(month_label(m) for m in winners)} with {fmt_money(target)} each.")
    else:
        answer = (f"The month with the {order} revenue{scope} is {month_label(winners[0])} "
                  f"with {fmt_money(target)} ({target / total * 100:.1f}% of {fmt_money(total)} total).")
    data = [[r["month"], r["orders"], r["net_revenue"],
             round(r["net_revenue"] / total * 100, 2) if total else 0] for r in rows]
    return AnalyticsResult("month_extreme", p, answer, ["month", "orders", "net_revenue", "share_pct"],
                           data, queries, notes)


def _data_quality(conn, p) -> AnalyticsResult:
    sql = "SELECT source_line, transaction_id, reason FROM rejected_records ORDER BY source_line"
    rejected = conn.execute(sql).fetchall()
    sql2 = ("SELECT transaction_id, order_date, product, region, quantity, unit_price, status "
            "FROM sales WHERE status = ? ORDER BY order_date")
    returned = conn.execute(sql2, ["Returned"]).fetchall()
    queries = [{"label": "Rejected CSV rows", "sql": sql, "params": []},
               {"label": "Returned (excluded) orders", "sql": sql2, "params": ["Returned"]}]
    rows = [["REJECTED (not loaded)", r["transaction_id"], r["reason"]] for r in rejected]
    rows += [["RETURNED (loaded, excluded from revenue)", r["transaction_id"],
              f"{r['product']} × {r['quantity']} in {r['region']} on {r['order_date']}"] for r in returned]
    answer = (f"{len(rejected)} CSV row(s) were rejected during loading and {len(returned)} "
              f"returned order(s) are excluded from revenue.")
    return AnalyticsResult("data_quality", p, answer, ["category", "transaction_id", "detail"], rows,
                           queries, ["Rejected rows are kept in the 'rejected_records' table for auditing."])


_HANDLERS = {
    "total_revenue": _total_revenue,
    "top_by": _top_by,
    "monthly_trend": _monthly_trend,
    "month_comparison": _month_comparison,
    "average_order_value": _average_order_value,
    "sales_volume": _sales_volume,
    "month_extreme": _month_extreme,
    "data_quality": _data_quality,
}


def run_intent(conn: sqlite3.Connection, intent: str, params: dict | None) -> AnalyticsResult:
    """Validate then execute an intent. Raises QueryValidationError on bad input."""
    catalog = load_catalog(conn)
    clean = normalise_request(intent, params, catalog)
    return _HANDLERS[intent](conn, clean)
