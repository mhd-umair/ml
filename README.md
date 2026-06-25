# Slow-Moving Parts Dashboard

A local [Streamlit](https://streamlit.io/) dashboard that reads the Perseus
equipment SQLite database directly and helps you decide **which parts to stop
buying for stock**, based on sales velocity and recency.

Because the database has no on-hand quantity, "slow moving" is measured from
actual demand (finalized, non-voided invoices) rather than shelf quantity:

- **Hits** - number of sales lines in the trailing window (demand frequency)
- **Hits/yr** - hits normalized to a yearly rate
- **Qty sold**, **Demand $** (`NetExt`), **Margin $** (via `SalePart.AvgCost`)
- **Months since last sale** - recency
- **Velocity class** - Dead / Slow / Steady / Fast
- **Stop-stocking candidate** - a part you are set up to reorder
  (`PartLocation.MinStock`/`MaxStock` > 0 or an `OFCCode` is set) **and** that is
  Dead or Slow in the window.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | Streamlit dashboard (filters, KPIs, charts, table, CSV export) |
| `analysis.py` | Pure data layer: SQL + pandas metrics, classification, recommendation |
| `profile.py` | One-off profiling script to verify data assumptions |
| `requirements.txt` | Python dependencies |

## Setup

```powershell
cd C:\Temp\ML_HackEx2
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Verify the data first (optional but recommended)

```powershell
python profile.py
```

This prints the distinct `InvoiceType`/`Status` values, the `FinalizedDate`
range, and the row counts that flow through the
`SalePart -> InvoiceDetail -> InvoiceHeader` join under the "real sales" filter.

## Run the dashboard

```powershell
streamlit run app.py
```

It opens in your browser and reads
`C:\Temp\ML_HackEx2\perseus_equipment_database.db` by default. You can point it
at a different file via the **Data source** box in the sidebar or the
`PERSEUS_DB` environment variable.

## How to use it

1. Pick a **location** and a **trailing window** (default 12 months).
2. Tune the **velocity thresholds** (hits/yr) to match how you define "slow".
3. Review the **Stop-stocking candidates** table - parts that keep getting
   reordered but are not selling.
4. **Download the CSV** to action the "do not restock" list.

The **Data diagnostics** expander at the bottom shows the underlying invoice
type/status distribution and date range so you can confirm the demand filter is
capturing the right transactions.

## Notes / assumptions

- A "sale" is a `SalePart` line on an `InvoiceHeader` that is `IsActive = 1`,
  has a `FinalizedDate`, and is not voided. This naturally excludes open quotes.
- The **as-of date** defaults to the latest sale in the data (the DB is a
  snapshot), so recency is measured against the data, not the wall clock.
- There are no enforced foreign keys in this database; joins use the
  `*Id` naming conventions.
