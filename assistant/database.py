from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCHEMA = """
DROP TABLE IF EXISTS sales;
DROP TABLE IF EXISTS rejected_records;

CREATE TABLE sales (
    transaction_id  TEXT PRIMARY KEY,
    order_date      TEXT NOT NULL,                    -- ISO YYYY-MM-DD
    product         TEXT NOT NULL,
    category        TEXT NOT NULL,
    region          TEXT NOT NULL,
    sales_channel   TEXT NOT NULL,
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price      REAL    NOT NULL CHECK (unit_price > 0),
    discount_amount REAL    NOT NULL CHECK (discount_amount >= 0),
    status          TEXT NOT NULL
);
CREATE INDEX idx_sales_date   ON sales(order_date);
CREATE INDEX idx_sales_status ON sales(status);

CREATE TABLE rejected_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_line    INTEGER NOT NULL,                  -- line number in the CSV
    transaction_id TEXT,
    reason         TEXT NOT NULL,
    raw_row        TEXT NOT NULL                      -- JSON of the original row
);
"""

REQUIRED_COLUMNS = [
    "Transaction_Id", "Order_date", "Product", "Category", "Region",
    "Sales_channel", "Quantity", "Unit_price", "Discount_amount", "Status",
]
VALID_STATUSES = {"Completed", "Returned"}


class DataLoadError(Exception):
    """Raised when the CSV file is missing or structurally unusable."""


@dataclass
class LoadReport:
    total_rows: int = 0
    loaded_rows: int = 0
    rejected_rows: int = 0
    rejections: list[dict] = field(default_factory=list)


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _validate_row(raw: dict[str, str], seen_ids: set[str]) -> tuple[dict | None, str | None]:
    """Return (clean_row, None) when valid, else (None, reason)."""
    txn = _clean_text(raw.get("Transaction_Id"))
    if not txn:
        return None, "Missing Transaction_Id"
    if txn in seen_ids:
        return None, "Duplicate Transaction_Id"

    for col in ("Product", "Category", "Region", "Sales_channel", "Status"):
        if not _clean_text(raw.get(col)):
            return None, f"Missing {col}"

    try:
        order_date = datetime.strptime(_clean_text(raw["Order_date"]), "%d-%m-%Y").date()
    except ValueError:
        return None, f"Invalid Order_date '{raw.get('Order_date')}' (expected DD-MM-YYYY)"

    try:
        quantity = int(float(_clean_text(raw.get("Quantity"))))
    except ValueError:
        return None, f"Invalid Quantity '{raw.get('Quantity')}'"
    if quantity <= 0:
        return None, f"Quantity must be > 0 (got {quantity})"

    price_txt = _clean_text(raw.get("Unit_price"))
    if not price_txt:
        return None, "Missing Unit_price"
    try:
        unit_price = float(price_txt)
    except ValueError:
        return None, f"Invalid Unit_price '{price_txt}'"
    if unit_price <= 0:
        return None, f"Unit_price must be > 0 (got {unit_price})"

    discount_txt = _clean_text(raw.get("Discount_amount")) or "0"
    try:
        discount = float(discount_txt)
    except ValueError:
        return None, f"Invalid Discount_amount '{discount_txt}'"
    if discount < 0:
        return None, "Discount_amount cannot be negative"
    if discount > quantity * unit_price:
        return None, "Discount_amount exceeds gross sales value"

    status = _clean_text(raw["Status"]).title()
    if status not in VALID_STATUSES:
        return None, f"Unknown Status '{raw['Status']}'"

    return {
        "transaction_id": txn,
        "order_date": order_date.isoformat(),
        "product": _clean_text(raw["Product"]),
        "category": _clean_text(raw["Category"]).title(),
        "region": _clean_text(raw["Region"]).title(),
        "sales_channel": _clean_text(raw["Sales_channel"]).title(),
        "quantity": quantity,
        "unit_price": unit_price,
        "discount_amount": discount,
        "status": status,
    }, None


def build_database(csv_path: Path, db_path: Path) -> LoadReport:
    """(Re)build the SQLite database from the CSV. Idempotent."""
    csv_path, db_path = Path(csv_path), Path(db_path)
    if not csv_path.exists():
        raise DataLoadError(f"CSV file not found: {csv_path}")

    report = LoadReport()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise DataLoadError(f"CSV is missing required columns: {missing}")

        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(SCHEMA)
            seen_ids: set[str] = set()
            for line_no, raw in enumerate(reader, start=2):  # line 1 = header
                report.total_rows += 1
                clean, reason = _validate_row(raw, seen_ids)
                if clean is None:
                    report.rejected_rows += 1
                    report.rejections.append(
                        {"line": line_no, "transaction_id": raw.get("Transaction_Id"), "reason": reason}
                    )
                    conn.execute(
                        "INSERT INTO rejected_records (source_line, transaction_id, reason, raw_row)"
                        " VALUES (?, ?, ?, ?)",
                        (line_no, _clean_text(raw.get("Transaction_Id")) or None, reason, json.dumps(raw)),
                    )
                    continue
                seen_ids.add(clean["transaction_id"])
                conn.execute(
                    "INSERT INTO sales VALUES (:transaction_id, :order_date, :product, :category,"
                    " :region, :sales_channel, :quantity, :unit_price, :discount_amount, :status)",
                    clean,
                )
                report.loaded_rows += 1
            conn.commit()
        finally:
            conn.close()
    return report


def get_readonly_connection(db_path: Path) -> sqlite3.Connection:
    """Open the DB read-only. Any INSERT/UPDATE/DELETE/DROP raises an error."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise DataLoadError(f"Database not found: {db_path}. Run the app once to build it.")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
