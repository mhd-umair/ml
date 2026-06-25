"""Slow-moving parts analysis for the Perseus equipment database.

Pure data layer (no Streamlit dependency) so it can be reused by profile.py
and unit-tested. Demand is derived from finalized, non-voided invoices:

    SalePart (PartId, Qty, NetExt, AvgCost)
        -> InvoiceDetail (ItemId)
            -> InvoiceHeader (InvoiceDocId, FinalizedDate, LocationId)

There is no on-hand quantity in this database, so "slow moving" is measured by
sales velocity (hits / quantity) and recency (months since last sale), enriched
with demand dollars and margin via SalePart.AvgCost.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pandas as pd

DB_PATH_DEFAULT = r"C:\Temp\ML_HackEx2\perseus_equipment_database.db"

# A part counts as a sale line only on a finalized, active, non-voided invoice.
SALES_SQL = """
SELECT
    sp.PartId   AS PartId,
    sp.Qty      AS Qty,
    sp.NetExt   AS NetExt,
    sp.AvgCost  AS AvgCost,
    ih.LocationId AS LocationId,
    COALESCE(NULLIF(ih.FinalizedDate, ''), ih.ActivityDate) AS SaleDate
FROM SalePart sp
JOIN InvoiceDetail d  ON d.ItemId = sp.ItemId
JOIN InvoiceHeader ih ON ih.InvoiceDocId = d.InvoiceDocId
WHERE ih.IsActive = 1
  AND d.IsActive = 1
  AND ih.FinalizedDate IS NOT NULL
  AND ih.FinalizedDate <> ''
  AND (ih.VoidedDate IS NULL OR ih.VoidedDate = '')
"""

PARTS_SQL = """
SELECT
    pm.PartId,
    pm.PartNo,
    pm.Description,
    pm.PartType,
    pm.PartStatus,
    pm.IsActive AS PartIsActive,
    pm.MfgId,
    COALESCE(NULLIF(mf.DisplayText, ''), mf.MfgCode) AS Manufacturer
FROM PartMaster pm
LEFT JOIN PartManufacturer mf ON mf.MfgId = pm.MfgId
"""

PART_LOCATION_SQL = """
SELECT
    pl.PartId,
    pl.LocationId,
    pl.MinStock,
    pl.MaxStock,
    pl.OFCCode,
    pl.IsActive AS LocIsActive
FROM PartLocation pl
"""

LOCATIONS_SQL = "SELECT LocationId, DisplayText FROM SettingsLocation ORDER BY DisplayText"

# Human-readable names for PartMaster.PartType codes.
# These are best-guess defaults for a Perseus equipment dealership - EDIT the
# wording here to match how your shop actually uses each code. Any code not
# listed will simply display as the raw code.
PARTTYPE_LABELS = {
    "R": "Regular / Resale part",
    "S": "Stock part",
    "N": "Non-stock (special order)",
    "C": "Core",
    "K": "Kit / Package",
    "L": "Labor",
    "M": "Miscellaneous",
    "F": "Freight",
    "P": "Package / Promo",
    "A": "Accessory",
    "G": "Gas / Oil / Fluids",
}


def part_type_label(code: str | None) -> str:
    """Return a readable 'Name (CODE)' label, or just the code if unknown."""
    code = (code or "").strip()
    if not code:
        return "(blank)"
    name = PARTTYPE_LABELS.get(code.upper())
    return f"{name} ({code})" if name else code


DEAD = "Dead"
SLOW = "Slow"
STEADY = "Steady"
FAST = "Fast"
CLASS_ORDER = [DEAD, SLOW, STEADY, FAST]


@dataclass
class Thresholds:
    """Velocity class boundaries, expressed in sales hits per year."""

    slow_max_hits_yr: float = 3.0
    steady_max_hits_yr: float = 11.0


def get_connection(db_path: str = DB_PATH_DEFAULT) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def load_sales(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return one row per real parts sale line with a parsed SaleDate."""
    df = pd.read_sql_query(SALES_SQL, conn)
    df["SaleDate"] = pd.to_datetime(df["SaleDate"], errors="coerce")
    df = df.dropna(subset=["SaleDate"]).copy()
    df["Qty"] = pd.to_numeric(df["Qty"], errors="coerce").fillna(0.0)
    df["NetExt"] = pd.to_numeric(df["NetExt"], errors="coerce").fillna(0.0)
    df["AvgCost"] = pd.to_numeric(df["AvgCost"], errors="coerce")
    df["CostExt"] = df["AvgCost"].fillna(0.0) * df["Qty"]
    return df


def load_parts(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(PARTS_SQL, conn)
    df["Manufacturer"] = df["Manufacturer"].fillna("(unknown)").replace("", "(unknown)")
    df["PartType"] = df["PartType"].fillna("").astype(str).str.strip()
    df["PartTypeLabel"] = df["PartType"].map(part_type_label)
    return df


def load_part_locations(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(PART_LOCATION_SQL, conn)
    df["MinStock"] = pd.to_numeric(df["MinStock"], errors="coerce").fillna(0)
    df["MaxStock"] = pd.to_numeric(df["MaxStock"], errors="coerce").fillna(0)
    df["OFCCode"] = df["OFCCode"].fillna("").astype(str).str.strip()
    df["HasOFC"] = df["OFCCode"] != ""
    df["StockedHere"] = (
        (df["MinStock"] > 0) | (df["MaxStock"] > 0) | df["HasOFC"]
    )
    return df


def load_locations(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(LOCATIONS_SQL, conn)


def profile_db(conn: sqlite3.Connection) -> dict:
    """Lightweight runtime diagnostics used by the dashboard's diagnostics panel."""
    inv_type = pd.read_sql_query(
        "SELECT InvoiceType, COUNT(*) AS n FROM InvoiceHeader "
        "GROUP BY InvoiceType ORDER BY n DESC",
        conn,
    )
    status = pd.read_sql_query(
        "SELECT Status, COUNT(*) AS n FROM InvoiceHeader "
        "GROUP BY Status ORDER BY n DESC",
        conn,
    )
    date_range = conn.execute(
        "SELECT MIN(FinalizedDate), MAX(FinalizedDate) FROM InvoiceHeader "
        "WHERE FinalizedDate IS NOT NULL AND FinalizedDate <> '' "
        "AND (VoidedDate IS NULL OR VoidedDate = '')"
    ).fetchone()
    (sales_lines,) = conn.execute(
        "SELECT COUNT(*) FROM SalePart sp "
        "JOIN InvoiceDetail d ON d.ItemId = sp.ItemId "
        "JOIN InvoiceHeader ih ON ih.InvoiceDocId = d.InvoiceDocId "
        "WHERE ih.IsActive = 1 AND d.IsActive = 1 "
        "AND ih.FinalizedDate IS NOT NULL AND ih.FinalizedDate <> '' "
        "AND (ih.VoidedDate IS NULL OR ih.VoidedDate = '')"
    ).fetchone()
    return {
        "invoice_types": inv_type,
        "statuses": status,
        "finalized_min": date_range[0],
        "finalized_max": date_range[1],
        "real_sales_lines": sales_lines,
    }


def _aggregate_locations(part_loc: pd.DataFrame) -> pd.DataFrame:
    """Collapse per-location stocking rows to one row per part."""
    if part_loc.empty:
        return pd.DataFrame(
            columns=["PartId", "MinStock", "MaxStock", "IsStocked", "StockLocations"]
        )
    grouped = part_loc.groupby("PartId").agg(
        MinStock=("MinStock", "max"),
        MaxStock=("MaxStock", "max"),
        IsStocked=("StockedHere", "any"),
        StockLocations=("StockedHere", "sum"),
    )
    return grouped.reset_index()


def classify(hits_per_year: pd.Series, hits: pd.Series, thr: Thresholds) -> pd.Series:
    cls = pd.Series(STEADY, index=hits.index, dtype=object)
    cls[hits_per_year > thr.steady_max_hits_yr] = FAST
    cls[hits_per_year <= thr.steady_max_hits_yr] = STEADY
    cls[hits_per_year <= thr.slow_max_hits_yr] = SLOW
    cls[hits <= 0] = DEAD
    return cls


def build_part_analysis(
    parts: pd.DataFrame,
    sales: pd.DataFrame,
    part_loc: pd.DataFrame,
    *,
    asof: pd.Timestamp,
    window_months: int,
    thresholds: Thresholds,
    location_id: int | None = None,
    active_parts_only: bool = True,
) -> pd.DataFrame:
    """Return one row per part with velocity metrics, class and recommendation."""
    parts = parts.copy()
    if active_parts_only:
        parts = parts[parts["PartIsActive"] == 1].copy()

    sales_scope = sales
    loc_scope = part_loc
    if location_id is not None:
        sales_scope = sales_scope[sales_scope["LocationId"] == location_id]
        loc_scope = loc_scope[loc_scope["LocationId"] == location_id]

    window_start = asof - pd.DateOffset(months=window_months)
    in_window = sales_scope[
        (sales_scope["SaleDate"] > window_start) & (sales_scope["SaleDate"] <= asof)
    ].copy()

    win_cols = ["PartId", "Hits", "QtySold", "DemandDollars", "CostDollars", "ActiveMonths"]
    if in_window.empty:
        win_metrics = pd.DataFrame(columns=win_cols)
    else:
        in_window["ym"] = in_window["SaleDate"].dt.to_period("M")
        win_metrics = (
            in_window.groupby("PartId")
            .agg(
                Hits=("PartId", "size"),
                QtySold=("Qty", "sum"),
                DemandDollars=("NetExt", "sum"),
                CostDollars=("CostExt", "sum"),
                ActiveMonths=("ym", "nunique"),
            )
            .reset_index()
        )

    if sales_scope.empty:
        last_sold_ever = pd.DataFrame(columns=["PartId", "LastSoldEver"])
    else:
        last_sold_ever = (
            sales_scope.groupby("PartId")["SaleDate"]
            .max()
            .rename("LastSoldEver")
            .reset_index()
        )

    df = parts.merge(win_metrics, how="left", on="PartId")
    df = df.merge(last_sold_ever, how="left", on="PartId")
    df = df.merge(_aggregate_locations(loc_scope), how="left", on="PartId")

    fill_zero = ["Hits", "QtySold", "DemandDollars", "CostDollars", "ActiveMonths"]
    df[fill_zero] = df[fill_zero].fillna(0)
    df["MinStock"] = df["MinStock"].fillna(0)
    df["MaxStock"] = df["MaxStock"].fillna(0)
    df["IsStocked"] = df["IsStocked"].fillna(False).astype(bool)
    df["StockLocations"] = df["StockLocations"].fillna(0).astype(int)

    df["MarginDollars"] = df["DemandDollars"] - df["CostDollars"]
    df["HitsPerYear"] = df["Hits"] * 12.0 / max(window_months, 1)
    df["AvgMonthlyQty"] = df["QtySold"] / max(window_months, 1)

    df["LastSoldEver"] = pd.to_datetime(df["LastSoldEver"], errors="coerce")
    df["MonthsSinceLastSale"] = (asof - df["LastSoldEver"]).dt.days / 30.44

    df["VelocityClass"] = classify(df["HitsPerYear"], df["Hits"], thresholds)
    df["VelocityClass"] = pd.Categorical(
        df["VelocityClass"], categories=CLASS_ORDER, ordered=True
    )

    is_slow_or_dead = df["VelocityClass"].isin([DEAD, SLOW])
    df["StopStockingCandidate"] = df["IsStocked"] & is_slow_or_dead

    df["Recommendation"] = "Keep"
    df.loc[df["VelocityClass"] == FAST, "Recommendation"] = "Keep - fast mover"
    df.loc[df["VelocityClass"] == STEADY, "Recommendation"] = "Keep - steady"
    df.loc[
        df["StopStockingCandidate"] & (df["VelocityClass"] == SLOW),
        "Recommendation",
    ] = "Review - slow, reduce min/max"
    df.loc[
        df["StopStockingCandidate"] & (df["VelocityClass"] == DEAD),
        "Recommendation",
    ] = "Stop stocking - dead, do not reorder"

    return df.sort_values(
        ["StopStockingCandidate", "MaxStock", "MonthsSinceLastSale"],
        ascending=[False, False, False],
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Equipment (unit) sales - for the Top grossing "Equipment" view
# ---------------------------------------------------------------------------

UNIT_SALES_SQL = """
SELECT
    su.UnitsDetailId AS UnitsDetailId,
    su.UnitId        AS UnitId,
    COALESCE(NULLIF(su.Model, ''), '(unknown)') AS Model,
    su.NetExt        AS NetExt,
    su.InvoiceCost   AS InvoiceCost,
    su.IsNew         AS IsNew,
    ub.Make          AS Make,
    ih.LocationId    AS LocationId,
    COALESCE(NULLIF(ih.FinalizedDate, ''), ih.ActivityDate) AS SaleDate
FROM SaleUnit su
JOIN InvoiceDetail d  ON d.ItemId = su.ItemId
JOIN InvoiceHeader ih ON ih.InvoiceDocId = d.InvoiceDocId
LEFT JOIN UnitBase ub ON ub.UnitId = su.UnitId
WHERE ih.IsActive = 1
  AND d.IsActive = 1
  AND ih.FinalizedDate IS NOT NULL
  AND ih.FinalizedDate <> ''
  AND (ih.VoidedDate IS NULL OR ih.VoidedDate = '')
"""


def load_unit_sales(conn: sqlite3.Connection) -> pd.DataFrame:
    """One row per equipment (unit) sale line on a finalized, non-voided invoice."""
    df = pd.read_sql_query(UNIT_SALES_SQL, conn)
    df["SaleDate"] = pd.to_datetime(df["SaleDate"], errors="coerce")
    df = df.dropna(subset=["SaleDate"]).copy()
    df["NetExt"] = pd.to_numeric(df["NetExt"], errors="coerce").fillna(0.0)
    df["InvoiceCost"] = pd.to_numeric(df["InvoiceCost"], errors="coerce").fillna(0.0)
    df["Make"] = df["Make"].fillna("").astype(str).str.strip()
    df["Model"] = df["Model"].fillna("(unknown)").astype(str).str.strip()
    return df


def build_equipment_grossing(
    unit_sales: pd.DataFrame,
    *,
    asof: pd.Timestamp,
    window_months: int,
    location_id: int | None = None,
) -> pd.DataFrame:
    """Equipment revenue/margin grouped by make + model over the window."""
    cols = [
        "ModelLabel", "Make", "Model", "UnitsSold",
        "Revenue", "Cost", "Margin", "LastSold",
    ]
    us = unit_sales
    if location_id is not None:
        us = us[us["LocationId"] == location_id]

    window_start = asof - pd.DateOffset(months=window_months)
    win = us[(us["SaleDate"] > window_start) & (us["SaleDate"] <= asof)].copy()
    if win.empty:
        return pd.DataFrame(columns=cols)

    grp = (
        win.groupby(["Make", "Model"])
        .agg(
            UnitsSold=("UnitsDetailId", "nunique"),
            Revenue=("NetExt", "sum"),
            Cost=("InvoiceCost", "sum"),
            LastSold=("SaleDate", "max"),
        )
        .reset_index()
    )
    grp["Margin"] = grp["Revenue"] - grp["Cost"]
    label = (grp["Make"].fillna("") + " " + grp["Model"].fillna("")).str.strip()
    grp["ModelLabel"] = label.where(label != "", grp["Model"])
    return grp.sort_values("Revenue", ascending=False).reset_index(drop=True)[cols]


# ---------------------------------------------------------------------------
# Repeat repairs (service work orders)
# ---------------------------------------------------------------------------

# A repair work order is an active, non-voided invoice that references a machine
# (WOUnitId). One row per work order = one repair visit for that machine.
WORK_ORDERS_SQL = """
SELECT
    ih.InvoiceDocId      AS InvoiceDocId,
    ih.WOUnitId          AS WOUnitId,
    ih.WOUnitNo          AS WOUnitNo,
    ih.WOUnitModel       AS WOUnitModel,
    ih.WOUnitBaseSerial  AS WOUnitBaseSerial,
    ih.WOUnitDescription AS WOUnitDescription,
    ih.CustomerName      AS WOCustomerName,
    ih.LocationId        AS LocationId,
    COALESCE(NULLIF(ih.FinalizedDate, ''), ih.ActivityDate) AS VisitDate
FROM InvoiceHeader ih
WHERE ih.IsActive = 1
  AND (ih.VoidedDate IS NULL OR ih.VoidedDate = '')
  AND ih.WOUnitId IS NOT NULL
  AND ih.WOUnitId <> 0
"""

SEGMENT_AGG_SQL = """
SELECT
    seg.InvDocId AS InvoiceDocId,
    SUM(COALESCE(seg.ActualHrs, 0)) AS LaborHrs,
    SUM(COALESCE(seg.NetExt, 0))    AS RepairDollars
FROM InvoiceSegment seg
WHERE seg.IsActive = 1
GROUP BY seg.InvDocId
"""

UNITS_SQL = """
SELECT
    ub.UnitId   AS WOUnitId,
    ub.StockNo  AS StockNo,
    ub.Make     AS Make,
    ub.Model    AS UnitModel,
    ub.BaseSerial AS BaseSerial,
    ub.Description AS UnitDescription,
    ub.CurrentCustomerId AS CurrentCustomerId,
    c.CustomerName AS OwnerName
FROM UnitBase ub
LEFT JOIN Customer c ON c.CustomerId = ub.CurrentCustomerId
"""


def load_work_orders(conn: sqlite3.Connection) -> pd.DataFrame:
    """One row per repair work order, with labor hours and repair dollars."""
    wo = pd.read_sql_query(WORK_ORDERS_SQL, conn)
    wo["VisitDate"] = pd.to_datetime(wo["VisitDate"], errors="coerce")
    wo = wo.dropna(subset=["VisitDate"]).copy()
    wo["WOUnitId"] = pd.to_numeric(wo["WOUnitId"], errors="coerce").astype("Int64")
    seg = pd.read_sql_query(SEGMENT_AGG_SQL, conn)
    wo = wo.merge(seg, how="left", on="InvoiceDocId")
    wo["LaborHrs"] = pd.to_numeric(wo["LaborHrs"], errors="coerce").fillna(0.0)
    wo["RepairDollars"] = pd.to_numeric(wo["RepairDollars"], errors="coerce").fillna(0.0)
    return wo


def load_units(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(UNITS_SQL, conn)
    df["WOUnitId"] = pd.to_numeric(df["WOUnitId"], errors="coerce").astype("Int64")
    return df


def build_repeat_repairs(
    work_orders: pd.DataFrame,
    units: pd.DataFrame,
    *,
    asof: pd.Timestamp,
    window_months: int,
    location_id: int | None = None,
    min_visits: int = 3,
) -> pd.DataFrame:
    """Return one row per machine with its count of repair visits in the window."""
    cols = [
        "WOUnitId", "MachineLabel", "Owner", "Make", "Model", "Serial",
        "Description", "Visits", "VisitsPerYear", "LaborHrs", "RepairDollars",
        "FirstVisit", "LastVisit", "MonthsSinceLastVisit", "RepeatOffender",
    ]
    wo = work_orders
    if location_id is not None:
        wo = wo[wo["LocationId"] == location_id]

    window_start = asof - pd.DateOffset(months=window_months)
    win = wo[(wo["VisitDate"] > window_start) & (wo["VisitDate"] <= asof)].copy()
    if win.empty:
        return pd.DataFrame(columns=cols)

    grp = (
        win.groupby("WOUnitId")
        .agg(
            Visits=("InvoiceDocId", "nunique"),
            LaborHrs=("LaborHrs", "sum"),
            RepairDollars=("RepairDollars", "sum"),
            FirstVisit=("VisitDate", "min"),
            LastVisit=("VisitDate", "max"),
        )
        .reset_index()
    )

    ident = (
        win.sort_values("VisitDate")
        .groupby("WOUnitId")
        .agg(
            WOUnitNo=("WOUnitNo", "last"),
            WOUnitModel=("WOUnitModel", "last"),
            WOUnitBaseSerial=("WOUnitBaseSerial", "last"),
            WOUnitDescription=("WOUnitDescription", "last"),
            WOCustomerName=("WOCustomerName", "last"),
        )
        .reset_index()
    )

    df = grp.merge(ident, on="WOUnitId", how="left").merge(
        units, on="WOUnitId", how="left"
    )

    def _coalesce(primary: str, fallback: str) -> pd.Series:
        a = df[primary].fillna("").astype(str).str.strip()
        b = df[fallback].fillna("").astype(str).str.strip()
        return a.where(a != "", b)

    df["Make"] = df["Make"].fillna("").astype(str).str.strip()
    df["Model"] = _coalesce("UnitModel", "WOUnitModel")
    df["Serial"] = _coalesce("BaseSerial", "WOUnitBaseSerial")
    df["Description"] = _coalesce("UnitDescription", "WOUnitDescription")
    df["Owner"] = _coalesce("OwnerName", "WOCustomerName")

    stock = _coalesce("StockNo", "WOUnitNo")
    label = stock.where(stock != "", df["Serial"])
    label = label.where(label != "", "Unit " + df["WOUnitId"].astype(str))
    df["MachineLabel"] = label

    df["VisitsPerYear"] = df["Visits"] * 12.0 / max(window_months, 1)
    df["MonthsSinceLastVisit"] = (asof - df["LastVisit"]).dt.days / 30.44
    df["RepeatOffender"] = df["Visits"] >= min_visits

    return df.sort_values(
        ["Visits", "RepairDollars"], ascending=[False, False]
    ).reset_index(drop=True)[cols]


def default_asof(sales: pd.DataFrame) -> pd.Timestamp:
    """Use the latest sale in the data as 'today' (the DB is a snapshot)."""
    if sales.empty:
        return pd.Timestamp.today().normalize()
    return sales["SaleDate"].max().normalize()
