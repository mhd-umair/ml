"""Slow-Moving Parts Dashboard (Streamlit).

Reads the Perseus equipment SQLite database directly and helps decide which
parts to stop buying for stock, based on sales velocity and recency.

Run:
    pip install -r requirements.txt
    streamlit run app.py
"""

from __future__ import annotations

import os
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

import analysis

st.set_page_config(
    page_title="Parts & Service Insights",
    page_icon="📦",
    layout="wide",
)

CLASS_COLORS = {
    analysis.DEAD: "#b3261e",
    analysis.SLOW: "#e8833a",
    analysis.STEADY: "#3a7ca5",
    analysis.FAST: "#2e7d32",
}


@st.cache_data(show_spinner=False)
def _load_all(db_path: str, mtime: float):
    """Load raw frames from the DB. mtime busts the cache when the file changes."""
    conn = sqlite3.connect(db_path)
    try:
        sales = analysis.load_sales(conn)
        parts = analysis.load_parts(conn)
        part_loc = analysis.load_part_locations(conn)
        locations = analysis.load_locations(conn)
        work_orders = analysis.load_work_orders(conn)
        units = analysis.load_units(conn)
        unit_sales = analysis.load_unit_sales(conn)
        diag = analysis.profile_db(conn)
    finally:
        conn.close()
    return sales, parts, part_loc, locations, work_orders, units, unit_sales, diag


def _resolve_db_path() -> str:
    return (
        st.session_state.get("db_path")
        or os.environ.get("PERSEUS_DB")
        or analysis.DB_PATH_DEFAULT
    )


def _money(x: float) -> str:
    return f"${x:,.0f}"


SHOW_COLS = [
    "PartNo",
    "Description",
    "Manufacturer",
    "VelocityClass",
    "HitsPerYear",
    "QtySold",
    "DemandDollars",
    "MarginDollars",
    "MonthsSinceLastSale",
    "LastSoldEver",
    "MinStock",
    "MaxStock",
    "Recommendation",
]

RENAME_MAP = {
    "PartNo": "Part No",
    "PartTypeLabel": "Part type",
    "VelocityClass": "Class",
    "HitsPerYear": "Hits/yr",
    "QtySold": "Qty (window)",
    "DemandDollars": "Demand $",
    "MarginDollars": "Margin $",
    "MonthsSinceLastSale": "Mo. since sale",
    "LastSoldEver": "Last sold",
}

TABLE_COLUMN_CONFIG = {
    "Hits/yr": st.column_config.NumberColumn(format="%.1f"),
    "Qty (window)": st.column_config.NumberColumn(format="%.0f"),
    "Demand $": st.column_config.NumberColumn(format="$%.0f"),
    "Margin $": st.column_config.NumberColumn(format="$%.0f"),
    "Mo. since sale": st.column_config.NumberColumn(format="%.1f"),
    "Last sold": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
}


def render_slow_movers(
    parts: "pd.DataFrame",
    sales: "pd.DataFrame",
    part_loc: "pd.DataFrame",
    asof: "pd.Timestamp",
    window_months: int,
    location_id,
) -> None:
    """KPIs, charts and the 'stop stocking' table for slow/dead parts."""
    with st.expander("Filters for this tab", expanded=True):
        f1, f2, f3 = st.columns(3)
        with f1:
            slow_max = st.slider(
                "Slow if ≤ (hits/yr)", 1.0, 12.0, 3.0, 0.5, key="sm_slow"
            )
            steady_max = st.slider(
                "Steady if ≤ (hits/yr)",
                slow_max,
                36.0,
                max(11.0, slow_max),
                0.5,
                key="sm_steady",
            )
        with f2:
            active_only = st.checkbox("Active parts only", value=True, key="sm_active")
            stocked_only = st.checkbox(
                "Stocked parts only (min/max or OFC set)",
                value=False,
                key="sm_stocked",
            )
        with f3:
            mfg_options = ["All"] + sorted(
                parts["Manufacturer"].dropna().unique().tolist()
            )
            mfg_sel = st.selectbox("Manufacturer", mfg_options, key="sm_mfg")

    thresholds = analysis.Thresholds(
        slow_max_hits_yr=slow_max, steady_max_hits_yr=steady_max
    )
    df = analysis.build_part_analysis(
        parts,
        sales,
        part_loc,
        asof=asof,
        window_months=window_months,
        thresholds=thresholds,
        location_id=location_id,
        active_parts_only=active_only,
    )
    if mfg_sel != "All":
        df = df[df["Manufacturer"] == mfg_sel]
    if stocked_only:
        df = df[df["IsStocked"]]

    total_parts = len(df)
    n_dead = int((df["VelocityClass"] == analysis.DEAD).sum())
    n_slow = int((df["VelocityClass"] == analysis.SLOW).sum())
    n_candidates = int(df["StopStockingCandidate"].sum())
    dead_stocked = df[
        df["StopStockingCandidate"] & (df["VelocityClass"] == analysis.DEAD)
    ]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Parts in scope", f"{total_parts:,}")
    c2.metric("Dead", f"{n_dead:,}")
    c3.metric("Slow", f"{n_slow:,}")
    c4.metric("Stop-stocking candidates", f"{n_candidates:,}")
    c5.metric(
        "Stocked & dead (SKUs)",
        f"{len(dead_stocked):,}",
        help="Parts set up to reorder that had zero sales in the window.",
    )

    st.divider()

    left, mid = st.columns(2)
    with left:
        st.subheader("Parts by velocity class")
        class_counts = (
            df["VelocityClass"].value_counts().reindex(analysis.CLASS_ORDER).fillna(0)
        )
        fig = px.bar(
            x=class_counts.index.astype(str),
            y=class_counts.values,
            color=class_counts.index.astype(str),
            color_discrete_map=CLASS_COLORS,
            labels={"x": "Class", "y": "Parts"},
        )
        fig.update_layout(showlegend=False, height=320, margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    with mid:
        st.subheader("Months since last sale")
        msl = df["MonthsSinceLastSale"].dropna()
        if msl.empty:
            st.info("No sales history to plot.")
        else:
            fig = px.histogram(x=msl, nbins=30, labels={"x": "Months"})
            fig.update_layout(showlegend=False, height=320, margin=dict(t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    st.subheader("Stop-stocking candidates")
    st.caption(
        "Parts currently set up to reorder (min/max or OFC code) that are dead or "
        "slow in the selected window. These are your 'do not restock' review list."
    )

    candidates = df[df["StopStockingCandidate"]].copy()
    table = candidates[SHOW_COLS].rename(columns=RENAME_MAP)
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config=TABLE_COLUMN_CONFIG,
    )

    csv = table.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download 'do not restock' list (CSV)",
        data=csv,
        file_name="stop_stocking_candidates.csv",
        mime="text/csv",
    )

    with st.expander("Browse all parts in scope"):
        st.dataframe(
            df[SHOW_COLS].rename(columns=RENAME_MAP),
            use_container_width=True,
            hide_index=True,
            column_config=TABLE_COLUMN_CONFIG,
        )


def render_top_grossing(
    df: "pd.DataFrame",
    unit_sales: "pd.DataFrame",
    asof: "pd.Timestamp",
    window_months: int,
    location_id,
) -> None:
    """Top grossing, split between parts and equipment sales."""
    view = st.radio(
        "Show",
        ["Parts", "Equipment"],
        horizontal=True,
        key="grossing_view",
    )
    if view == "Parts":
        _render_parts_grossing(df)
    else:
        equip = analysis.build_equipment_grossing(
            unit_sales,
            asof=asof,
            window_months=window_months,
            location_id=location_id,
        )
        _render_equipment_grossing(equip)


def _render_parts_grossing(df: "pd.DataFrame") -> None:
    """Rank the best parts by revenue (or margin) so they get prime shelf space."""
    st.subheader("Top grossing parts")
    st.caption(
        "Your best sellers over the selected window - keep these stocked and at "
        "the front of the parts department."
    )

    ctrl1, ctrl2 = st.columns([1, 1])
    with ctrl1:
        rank_by_label = st.radio(
            "Rank by",
            ["Revenue (Demand $)", "Margin $", "Quantity sold"],
            horizontal=True,
            key="topgross_rank",
        )
    with ctrl2:
        top_n = st.slider(
            "Show top", min_value=10, max_value=200, value=25, step=5,
            key="topgross_n",
        )

    rank_col = {
        "Revenue (Demand $)": "DemandDollars",
        "Margin $": "MarginDollars",
        "Quantity sold": "QtySold",
    }[rank_by_label]

    sold = df[df["DemandDollars"] > 0].copy()
    ranked = sold.sort_values(rank_col, ascending=False)
    top = ranked.head(top_n)

    total_rev = df["DemandDollars"].sum()
    total_margin = df["MarginDollars"].sum()
    top_rev = top["DemandDollars"].sum()
    rev_share = (top_rev / total_rev * 100) if total_rev else 0.0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total revenue (window)", _money(total_rev))
    k2.metric("Total margin (window)", _money(total_margin))
    k3.metric(f"Top {top_n} revenue", _money(top_rev))
    k4.metric(
        f"Top {top_n} share of revenue",
        f"{rev_share:.0f}%",
        help="How much of total parts revenue these few SKUs represent.",
    )

    st.divider()

    if top.empty:
        st.info("No parts with sales in the selected window/filters.")
        return

    chart_label = {
        "DemandDollars": "Revenue $",
        "MarginDollars": "Margin $",
        "QtySold": "Qty sold",
    }[rank_col]
    chart_n = min(len(top), 20)
    plot_df = top.head(chart_n).iloc[::-1]  # highest at top of horizontal bar
    fig = px.bar(
        plot_df,
        x=rank_col,
        y="PartNo",
        orientation="h",
        hover_data=["Description", "Manufacturer"],
        labels={rank_col: chart_label, "PartNo": "Part Number"},
    )
    fig.update_yaxes(type="category")
    fig.update_layout(
        height=max(320, min(720, 28 * chart_n)),
        margin=dict(t=10, b=10),
        bargap=0.25,
    )
    if len(top) > chart_n:
        st.caption(
            f"Chart shows the top {chart_n}; the full top {len(top)} is in the "
            "table below."
        )
    st.plotly_chart(fig, use_container_width=True)

    table = top[SHOW_COLS].rename(columns=RENAME_MAP)
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config=TABLE_COLUMN_CONFIG,
    )

    csv = table.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download top grossing list (CSV)",
        data=csv,
        file_name="top_grossing_parts.csv",
        mime="text/csv",
    )


def _render_equipment_grossing(equip: "pd.DataFrame") -> None:
    """Rank equipment make/models by sales revenue (or margin / units sold)."""
    st.subheader("Top grossing equipment")
    st.caption(
        "Equipment sales over the selected window, grouped by make & model."
    )

    if equip.empty:
        st.info("No equipment sales found in the selected window/filters.")
        return

    ctrl1, ctrl2 = st.columns([1, 1])
    with ctrl1:
        rank_by_label = st.radio(
            "Rank by",
            ["Revenue", "Margin $", "Units sold"],
            horizontal=True,
            key="equip_rank",
        )
    with ctrl2:
        top_n = st.slider(
            "Show top", min_value=5, max_value=100, value=20, step=5,
            key="equip_n",
        )

    rank_col = {
        "Revenue": "Revenue",
        "Margin $": "Margin",
        "Units sold": "UnitsSold",
    }[rank_by_label]

    ranked = equip.sort_values(rank_col, ascending=False)
    top = ranked.head(top_n)

    total_rev = equip["Revenue"].sum()
    total_margin = equip["Margin"].sum()
    total_units = int(equip["UnitsSold"].sum())
    top_rev = top["Revenue"].sum()
    rev_share = (top_rev / total_rev * 100) if total_rev else 0.0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Equipment revenue (window)", _money(total_rev))
    k2.metric("Equipment margin (window)", _money(total_margin))
    k3.metric("Units sold", f"{total_units:,}")
    k4.metric(f"Top {top_n} share of revenue", f"{rev_share:.0f}%")

    st.divider()

    chart_n = min(len(top), 20)
    plot_df = top.head(chart_n).iloc[::-1]
    chart_label = {
        "Revenue": "Revenue $",
        "Margin": "Margin $",
        "UnitsSold": "Units sold",
    }[rank_col]
    fig = px.bar(
        plot_df,
        x=rank_col,
        y="ModelLabel",
        orientation="h",
        hover_data=["Make", "Model", "UnitsSold", "Revenue", "Margin"],
        labels={rank_col: chart_label, "ModelLabel": "Make / Model"},
    )
    fig.update_yaxes(type="category")
    fig.update_layout(
        height=max(320, min(720, 28 * chart_n)),
        margin=dict(t=10, b=10),
        bargap=0.25,
    )
    if len(top) > chart_n:
        st.caption(
            f"Chart shows the top {chart_n}; the full top {len(top)} is in the "
            "table below."
        )
    st.plotly_chart(fig, use_container_width=True)

    table = top[
        ["ModelLabel", "Make", "Model", "UnitsSold", "Revenue", "Margin", "LastSold"]
    ].rename(
        columns={
            "ModelLabel": "Make / Model",
            "UnitsSold": "Units",
            "Revenue": "Revenue $",
            "Margin": "Margin $",
            "LastSold": "Last sold",
        }
    )
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Units": st.column_config.NumberColumn(format="%.0f"),
            "Revenue $": st.column_config.NumberColumn(format="$%.0f"),
            "Margin $": st.column_config.NumberColumn(format="$%.0f"),
            "Last sold": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
        },
    )

    csv = table.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download top grossing equipment (CSV)",
        data=csv,
        file_name="top_grossing_equipment.csv",
        mime="text/csv",
    )


def render_repeat_repairs(
    work_orders: "pd.DataFrame",
    units: "pd.DataFrame",
    asof: "pd.Timestamp",
    window_months: int,
    location_id,
) -> None:
    """Machines that keep coming back to the shop, ranked by repair visits."""
    st.subheader("Machines with repeat repairs")
    st.caption(
        "Each repair work order is one 'visit'. Machines are ranked by how many "
        "separate times they came into the shop in the selected window."
    )

    min_visits = st.slider(
        "Flag machines with at least N repair visits",
        min_value=2,
        max_value=20,
        value=3,
        key="repeat_min_visits",
    )

    repairs = analysis.build_repeat_repairs(
        work_orders,
        units,
        asof=asof,
        window_months=window_months,
        location_id=location_id,
        min_visits=min_visits,
    )

    if repairs.empty:
        st.info(
            "No repair work orders with a machine were found in the selected "
            "window/location."
        )
        return

    machines = len(repairs)
    repeat = int(repairs["RepeatOffender"].sum())
    total_visits = int(repairs["Visits"].sum())

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Machines serviced", f"{machines:,}")
    k2.metric(f"Repeat machines (≥{min_visits})", f"{repeat:,}")
    k3.metric("Total repair visits", f"{total_visits:,}")
    k4.metric("Avg visits / machine", f"{total_visits / machines:.1f}")

    st.divider()

    flagged = repairs[repairs["RepeatOffender"]].copy()
    chart_src = flagged if not flagged.empty else repairs
    chart_n = min(len(chart_src), 20)
    plot_df = chart_src.head(chart_n).iloc[::-1]

    fig = px.bar(
        plot_df,
        x="Visits",
        y="MachineLabel",
        orientation="h",
        hover_data=["Owner", "Make", "Model", "Serial", "Description", "RepairDollars"],
        labels={"Visits": "Repair visits", "MachineLabel": "Machine (stock/serial)"},
    )
    fig.update_yaxes(type="category")
    fig.update_layout(
        height=max(320, min(720, 28 * chart_n)),
        margin=dict(t=10, b=10),
        bargap=0.25,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("Customers with the most returning equipment")
    st.caption(
        f"Customers ranked by how many of their machines came back ≥{min_visits} "
        "times in the window."
    )

    cust = repairs[repairs["Owner"].astype(str).str.strip() != ""].copy()
    cust_agg = (
        cust.groupby("Owner")
        .agg(
            RepeatMachines=("RepeatOffender", "sum"),
            Machines=("WOUnitId", "nunique"),
            TotalVisits=("Visits", "sum"),
        )
        .reset_index()
    )
    ranked_cust = cust_agg[cust_agg["RepeatMachines"] > 0].sort_values(
        ["RepeatMachines", "TotalVisits"], ascending=[False, False]
    )
    rank_metric = "RepeatMachines"
    if ranked_cust.empty:
        ranked_cust = cust_agg.sort_values("TotalVisits", ascending=False)
        rank_metric = "TotalVisits"

    if ranked_cust.empty:
        st.info("No customer/owner information available for these machines.")
    else:
        cust_n = min(len(ranked_cust), 15)
        cust_plot = ranked_cust.head(cust_n).iloc[::-1]
        metric_label = {
            "RepeatMachines": "Returning machines",
            "TotalVisits": "Total repair visits",
        }[rank_metric]
        cfig = px.bar(
            cust_plot,
            x=rank_metric,
            y="Owner",
            orientation="h",
            hover_data=["Machines", "TotalVisits", "RepeatMachines"],
            labels={rank_metric: metric_label, "Owner": "Customer"},
        )
        cfig.update_yaxes(type="category")
        cfig.update_layout(
            height=max(320, min(640, 30 * cust_n)),
            margin=dict(t=10, b=10),
            bargap=0.25,
        )
        st.plotly_chart(cfig, use_container_width=True)

    repair_cols = [
        "MachineLabel",
        "Owner",
        "Make",
        "Model",
        "Serial",
        "Description",
        "Visits",
        "VisitsPerYear",
        "LaborHrs",
        "RepairDollars",
        "FirstVisit",
        "LastVisit",
        "MonthsSinceLastVisit",
    ]
    repair_rename = {
        "MachineLabel": "Machine",
        "VisitsPerYear": "Visits/yr",
        "LaborHrs": "Labor hrs",
        "RepairDollars": "Repair $",
        "FirstVisit": "First visit",
        "LastVisit": "Last visit",
        "MonthsSinceLastVisit": "Mo. since last",
    }
    table = repairs[repair_cols].rename(columns=repair_rename)
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Visits/yr": st.column_config.NumberColumn(format="%.1f"),
            "Labor hrs": st.column_config.NumberColumn(format="%.1f"),
            "Repair $": st.column_config.NumberColumn(format="$%.0f"),
            "Mo. since last": st.column_config.NumberColumn(format="%.1f"),
            "First visit": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
            "Last visit": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
        },
    )

    csv = table.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download repeat-repair machines (CSV)",
        data=csv,
        file_name="repeat_repair_machines.csv",
        mime="text/csv",
    )


def main() -> None:
    st.title("📦 Parts & Service Insights")
    st.caption(
        "Parts stocking decisions, best sellers, and machines with repeat repairs - "
        "all from finalized, non-voided invoices."
    )

    # --- DB path / load ---
    with st.sidebar:
        st.header("Data source")
        db_path = st.text_input(
            "SQLite database path",
            value=_resolve_db_path(),
            key="db_path",
        )

    if not os.path.exists(db_path):
        st.error(f"Database file not found:\n\n`{db_path}`")
        st.stop()

    try:
        mtime = os.path.getmtime(db_path)
        (
            sales, parts, part_loc, locations, work_orders, units,
            unit_sales, diag,
        ) = _load_all(db_path, mtime)
    except Exception as exc:  # noqa: BLE001 - surface load errors to the user
        st.exception(exc)
        st.stop()

    if sales.empty:
        st.warning(
            "No finalized parts sales were found. Check the 'Data diagnostics' "
            "panel below to verify the invoice filters and date format."
        )

    # --- Global filters (apply to every tab) ---
    with st.sidebar:
        st.header("Filters")
        st.caption("These apply to all tabs. Tab-specific filters live in each tab.")

        loc_options = {"All locations": None}
        for _, row in locations.iterrows():
            label = row["DisplayText"] or f"Location {row['LocationId']}"
            loc_options[label] = int(row["LocationId"])
        loc_label = st.selectbox("Location", list(loc_options.keys()))
        location_id = loc_options[loc_label]

        window_months = st.slider(
            "Trailing window (months)", min_value=3, max_value=60, value=12, step=3
        )

        asof_default = analysis.default_asof(sales).date()
        asof = st.date_input("As-of date (snapshot 'today')", value=asof_default)
        asof_ts = pd.Timestamp(asof)

    # Base part metrics (default thresholds) for the Top grossing parts view.
    base_parts_df = analysis.build_part_analysis(
        parts,
        sales,
        part_loc,
        asof=asof_ts,
        window_months=window_months,
        thresholds=analysis.Thresholds(),
        location_id=location_id,
        active_parts_only=True,
    )

    tab_slow, tab_top, tab_repairs = st.tabs(
        [
            "🐌 Slow movers / stop stocking",
            "💰 Top grossing",
            "🔧 Repeat repairs",
        ]
    )
    with tab_slow:
        render_slow_movers(
            parts, sales, part_loc, asof_ts, window_months, location_id
        )
    with tab_top:
        render_top_grossing(
            base_parts_df, unit_sales, asof_ts, window_months, location_id
        )
    with tab_repairs:
        render_repeat_repairs(
            work_orders, units, asof_ts, window_months, location_id
        )

    # --- Diagnostics ---
    with st.expander("Data diagnostics (verify assumptions)"):
        st.write(
            f"Real parts-sale lines (finalized, active, not voided): "
            f"**{diag['real_sales_lines']:,}**"
        )
        st.write(
            f"FinalizedDate range: **{diag['finalized_min']}** → "
            f"**{diag['finalized_max']}**"
        )
        st.write(f"Sales rows with parseable date loaded: **{len(sales):,}**")
        col_a, col_b = st.columns(2)
        with col_a:
            st.caption("InvoiceHeader.InvoiceType")
            st.dataframe(diag["invoice_types"], hide_index=True, use_container_width=True)
        with col_b:
            st.caption("InvoiceHeader.Status")
            st.dataframe(diag["statuses"], hide_index=True, use_container_width=True)


if __name__ == "__main__":
    main()
