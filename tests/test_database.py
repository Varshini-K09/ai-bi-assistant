import sqlite3

import pytest

from assistant.database import DataLoadError, build_database, get_readonly_connection

HEADER = "Transaction_Id,Order_date,Product,Category,Region,Sales_channel,Quantity,Unit_price,Discount_amount,Status\n"


def _csv(tmp_path, body: str):
    p = tmp_path / "x.csv"
    p.write_text(HEADER + body)
    return p


def test_supplied_csv_loads_49_valid_and_rejects_2(db_path, conn):
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 49
    rejected = {r["transaction_id"]: r["reason"] for r in conn.execute("SELECT * FROM rejected_records")}
    assert set(rejected) == {"TXN0034", "TXN0051"}
    assert "Quantity" in rejected["TXN0034"]
    assert "Unit_price" in rejected["TXN0051"]


def test_dates_are_stored_as_iso(conn):
    row = conn.execute("SELECT order_date FROM sales WHERE transaction_id='TXN0001'").fetchone()
    assert row[0] == "2026-01-03"         

def test_returned_order_is_kept_but_flagged(conn):
    row = conn.execute("SELECT status FROM sales WHERE transaction_id='TXN0033'").fetchone()
    assert row["status"] == "Returned"


def test_dirty_rows_are_quarantined(tmp_path):
    csv_path = _csv(tmp_path, "\n".join([
        "T1,01-01-2026,A,Cat,North,Online,1,100,0,Completed",
        "T1,02-01-2026,A,Cat,North,Online,1,100,0,Completed",      # duplicate id
        "T3,31-02-2026,A,Cat,North,Online,1,100,0,Completed",      # impossible date
        "T4,01-01-2026,A,Cat,North,Online,-2,100,0,Completed",     # negative qty
        "T5,01-01-2026,A,Cat,North,Online,1,100,-5,Completed",     # negative discount
        "T6,01-01-2026,A,Cat,North,Online,1,100,500,Completed",    # discount > gross
        "T7,01-01-2026,A,Cat,North,Online,abc,100,0,Completed",    # non-numeric qty
        "T8,01-01-2026,A,Cat,North,Online,1,100,0,Shipped",        # unknown status
        "T9,01-01-2026,,Cat,North,Online,1,100,0,Completed",       # missing product
        "T10,01-01-2026,A,cat ,  north,online,2,50,0,completed",   # messy but valid -> normalised
    ]) + "\n")
    report = build_database(csv_path, tmp_path / "d.db")
    assert report.loaded_rows == 2 and report.rejected_rows == 8
    conn = get_readonly_connection(tmp_path / "d.db")
    row = conn.execute("SELECT category, region, sales_channel, status FROM sales WHERE transaction_id='T10'").fetchone()
    assert tuple(row) == ("Cat", "North", "Online", "Completed")


def test_missing_file_and_missing_columns_raise(tmp_path):
    with pytest.raises(DataLoadError):
        build_database(tmp_path / "nope.csv", tmp_path / "d.db")
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    with pytest.raises(DataLoadError, match="missing required columns"):
        build_database(bad, tmp_path / "d.db")


def test_connection_is_read_only(conn):
    for stmt in ("DELETE FROM sales", "DROP TABLE sales", "UPDATE sales SET quantity = 0",
                 "INSERT INTO sales VALUES ('x','2026-01-01','p','c','r','s',1,1,0,'Completed')"):
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(stmt)
