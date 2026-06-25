"""One-off data profiling for the slow-moving parts analysis.

Run this once to confirm the assumptions the dashboard relies on:
- which InvoiceType / Status values exist (and how common they are)
- the format and min/max of FinalizedDate / ActivityDate
- how many rows survive the SalePart -> InvoiceDetail -> InvoiceHeader join
  under the "real sales" filter.

Usage:
    python profile.py
    python profile.py "C:\\path\\to\\perseus_equipment_database.db"
"""

import sys
import sqlite3

import analysis


def _print_counts(conn, sql, title):
    print(f"\n== {title} ==")
    cur = conn.execute(sql)
    rows = cur.fetchall()
    if not rows:
        print("  (no rows)")
        return
    for row in rows:
        label = row[0] if row[0] not in (None, "") else "(null/empty)"
        print(f"  {label!s:<28} {row[1]:>10,}")


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else analysis.DB_PATH_DEFAULT
    print(f"Profiling: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        _print_counts(
            conn,
            "SELECT InvoiceType, COUNT(*) FROM InvoiceHeader "
            "GROUP BY InvoiceType ORDER BY COUNT(*) DESC",
            "InvoiceHeader.InvoiceType",
        )
        _print_counts(
            conn,
            "SELECT Status, COUNT(*) FROM InvoiceHeader "
            "GROUP BY Status ORDER BY COUNT(*) DESC",
            "InvoiceHeader.Status",
        )

        print("\n== Date range (FinalizedDate, finalized & not voided) ==")
        cur = conn.execute(
            "SELECT MIN(FinalizedDate), MAX(FinalizedDate) FROM InvoiceHeader "
            "WHERE FinalizedDate IS NOT NULL AND FinalizedDate <> '' "
            "AND (VoidedDate IS NULL OR VoidedDate = '')"
        )
        lo, hi = cur.fetchone()
        print(f"  min={lo!r}  max={hi!r}")

        print("\n== Row counts through the join ==")
        counts = {
            "SalePart rows": "SELECT COUNT(*) FROM SalePart",
            "InvoiceDetail rows": "SELECT COUNT(*) FROM InvoiceDetail",
            "InvoiceHeader rows": "SELECT COUNT(*) FROM InvoiceHeader",
            "PartMaster rows": "SELECT COUNT(*) FROM PartMaster",
            "PartLocation rows": "SELECT COUNT(*) FROM PartLocation",
            "Sales lines (real-sales filter)": (
                "SELECT COUNT(*) FROM SalePart sp "
                "JOIN InvoiceDetail d ON d.ItemId = sp.ItemId "
                "JOIN InvoiceHeader ih ON ih.InvoiceDocId = d.InvoiceDocId "
                "WHERE ih.IsActive = 1 AND d.IsActive = 1 "
                "AND ih.FinalizedDate IS NOT NULL AND ih.FinalizedDate <> '' "
                "AND (ih.VoidedDate IS NULL OR ih.VoidedDate = '')"
            ),
        }
        for label, sql in counts.items():
            (n,) = conn.execute(sql).fetchone()
            print(f"  {label:<34} {n:>10,}")

        print("\n== Loaded sales DataFrame (via analysis.load_sales) ==")
        sales = analysis.load_sales(conn)
        print(f"  rows with parseable SaleDate: {len(sales):,}")
        if not sales.empty:
            print(f"  SaleDate min={sales['SaleDate'].min()}  "
                  f"max={sales['SaleDate'].max()}")
            print(f"  distinct parts sold: {sales['PartId'].nunique():,}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
