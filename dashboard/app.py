"""
Somalia Economic Intelligence Platform — Main Streamlit Dashboard

Entry point: streamlit run dashboard/app.py

Single-page dashboard. No navigation. No multi-page routing.
"""

import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Page config (must be the first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Somalia Economic Intelligence Platform",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Safely obtain git commit hash for display; fall back to 'unknown' if git is unavailable
try:
    _GIT_HASH = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
except Exception:
    _GIT_HASH = "unknown"

# Sidebar deploy hash display (after page config)
st.sidebar.write("DEPLOY HASH")
st.sidebar.code(_GIT_HASH)

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

from config import APP_VERSION, APP_NAME, DATA_SOURCES, DATABASE_PATH
from database.connection import init_database
from ingestion.exchange_rates import MODERN_REGIME, REGIME_PRE_WAR, REGIME_POST_WAR
from processing.metrics import run_metrics, classify_risk
from dashboard.queries import (
    get_kpi_metrics,
    get_latest_exchange_rate,
    get_latest_fuel_price,
    get_latest_telecom_price,
    get_pipeline_logs,
    get_total_records,
    get_database_status,
    get_exchange_rate_validation_table,
    get_exchange_rates,
    get_data_source_count,
    compute_pipeline_health,
)
from dashboard.components.sidebar import render_sidebar
from dashboard.components.cards import render_executive_kpi_cards
from dashboard.components.charts import render_market_analytics, _CHART_CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _format_data_freshness(timestamp: str | None) -> str:
    if not timestamp:
        return "Unavailable"
    ts = str(timestamp)
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M UTC") if any((dt.hour, dt.minute, dt.second, dt.microsecond)) else dt.strftime("%Y-%m-%d")
    except ValueError:
        return ts


db_ready = init_database()
logger.info("Database startup: path=%s connected=%s", DATABASE_PATH, db_ready)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
refresh_interval = render_sidebar()

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("Somalia Operations Control")
st.subheader("Real-Time Economic Intelligence Platform for Somalia")
st.markdown(
    "Monitor key economic indicators, market conditions, and data pipeline health "
    "using public Somalia economic datasets sourced from the World Bank, "
    "HDX/WFP, and affiliated humanitarian monitoring programmes."
)
st.divider()

# ---------------------------------------------------------------------------
# Pipeline trigger
# ---------------------------------------------------------------------------
run_col, _ = st.columns([2, 8])
with run_col:
    run_pipeline = st.button("Refresh Economic Data", type="primary", width="stretch")

if run_pipeline:
    _CORE_JOBS    = ["Exchange Rates", "Fuel Prices"]
    _OPTIONAL_JOBS = ["Telecom Prices"]

    with st.spinner("Fetching latest economic data…"):
        from ingestion.exchange_rates import run_ingestion as ingest_fx
        from ingestion.fuel_prices    import run_ingestion as ingest_fuel
        from ingestion.telecom_prices import run_ingestion as ingest_tel
        from processing.transform     import run_transforms

        results = []
        for label, fn in [
            ("Exchange Rates", ingest_fx),
            ("Fuel Prices",    ingest_fuel),
            ("Telecom Prices", ingest_tel),
        ]:
            try:
                res = fn()
                results.append((label, res))
            except Exception as exc:
                logger.exception("Ingestion failed for %s: %s", label, exc)
                results.append((label, {"status": "FAILED", "message": str(exc)}))

        run_transforms()

    # Display results with correct severity per source type
    core_failed = False
    for label, res in results:
        status = res.get("status", "UNKNOWN")
        msg    = res.get("message", "")

        if label in _OPTIONAL_JOBS:
            # Optional source — always shown as info, never affects overall health
            if status == "SUCCESS":
                st.success(f"**{label}**: {msg}")
            else:
                st.info(f"**{label}**: Telecom data unavailable — {msg}")
        else:
            # Core source — failures are shown as errors
            if status == "SUCCESS":
                st.success(f"**{label}**: {msg}")
            elif status == "WARNING":
                st.warning(f"**{label}**: {msg}")
            else:
                st.error(f"**{label}**: {msg}")
                core_failed = True

    if core_failed:
        st.error("One or more core data sources failed. Check pipeline logs for details.")
    else:
        st.success("Core economic data refreshed successfully.")

    st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Compute live metrics
# ---------------------------------------------------------------------------
metrics = run_metrics()
# ===========================================================================
# Section 1 — Executive Overview
# ===========================================================================
st.header("Executive Overview")
render_executive_kpi_cards(metrics)
st.divider()

# ===========================================================================
# Section 2 — Pipeline Status
# ===========================================================================
st.header("Pipeline Status")

db_status = get_database_status()
health    = compute_pipeline_health()

# Health summary row
h1, h2, h3, h4, h5 = st.columns(5, gap="small")
with h1:
    st.metric("Database", "Connected" if db_status.get("connected") else "Error")
with h2:
    st.metric("Core Sources", health["core_status"])
with h3:
    opt_unavail = int(health["optional_unavailable"])
    st.metric("Optional Sources", f"{opt_unavail} unavailable" if opt_unavail else "OK")
with h4:
    st.metric("Total Records", f"{db_status.get('total_records', 0):,}")
with h5:
    overall = health["overall"]
    st.metric("Overall Health", overall)

with st.expander("Pipeline Execution Log", expanded=False):
    log_df = get_pipeline_logs(limit=20)
    if log_df.empty:
        st.info("No pipeline runs recorded. Click 'Refresh Economic Data' to start.")
    else:
        st.dataframe(
            log_df, width="stretch", hide_index=True,
            column_config={
                "timestamp": st.column_config.DatetimeColumn("Timestamp", format="YYYY-MM-DD HH:mm"),
                "job_name":  "Job",
                "status":    "Status",
                "message":   st.column_config.TextColumn("Message", width="large"),
            },
        )

st.divider()

# ===========================================================================
# Section 3 — Exchange Rate Data Audit  (kept exactly as implemented)
# ===========================================================================
st.header("Exchange Rate Data Audit")
st.markdown(
    """
    The World Bank **PA.NUS.FCRF** indicator for Somalia contains data from
    **three structurally incompatible currency regimes**. Records from different
    regimes cannot be plotted on the same scale — their rate values are not
    comparable due to different denomination bases, sources, and economic contexts.

    The table below validates each regime independently. Only the
    **Reissued SOS / Official (2022+)** series is used for the KPI card,
    trend chart, and analytics metrics.
    """
)

val_df = get_exchange_rate_validation_table()

if val_df.empty:
    st.info("No exchange rate records found. Run 'Refresh Economic Data' to populate data.")
else:
    st.dataframe(
        val_df,
        width="stretch",
        hide_index=True,
        column_config={
            "regime":       st.column_config.TextColumn("Currency Regime", width="large"),
            "record_count": st.column_config.NumberColumn("Records", format="%d"),
            "min_rate":     st.column_config.NumberColumn("Min (SOS/USD)", format="%.2f"),
            "max_rate":     st.column_config.NumberColumn("Max (SOS/USD)", format="%.2f"),
            "median_rate":  st.column_config.NumberColumn("Median (SOS/USD)", format="%.2f"),
            "avg_rate":     st.column_config.NumberColumn("Avg (SOS/USD)", format="%.2f"),
            "first_date":   "First Date",
            "last_date":    "Last Date",
        },
    )

    with st.expander("Why did the chart previously show values above 30,000?", expanded=False):
        st.markdown(
            f"""
**Root cause:** The initial ingestion stored all World Bank records into a single
table column (`rate`) with no regime label. When the trend chart queried all rows
without filtering, it plotted three incompatible series on one Y-axis:

| Regime | Rate Range | Why it's incompatible |
|--------|-----------|----------------------|
| {REGIME_PRE_WAR} | 6 – 490 SOS/USD | Old Somali Shilling under a functioning central bank (pre-civil war) |
| {REGIME_POST_WAR} | 19,000 – 31,559 SOS/USD | Post-war hyperinflation; parallel market; no central bank 1991–2011 |
| {MODERN_REGIME} | 560 – 571 SOS/USD | New official rate from reconstituted central bank; different source |

The chart's Y-axis scaled to ~31,559 to accommodate the post-war peak, which
compressed the modern 560–571 values to a nearly invisible flat line at the
bottom — making the KPI card (showing 560) appear inconsistent with the chart.

**Fix applied:** Every record is now tagged with its `regime` on ingestion.
The KPI card, trend chart, and all analytics queries filter exclusively to
`regime = '{MODERN_REGIME}'`. The pre-war and post-war records are retained in
the database for historical reference but are quarantined from live analytics.
            """
        )

    with st.expander("View all regimes — historical reference only", expanded=False):
        st.warning(
            "The chart below plots all three regimes together for reference. "
            "Values are NOT comparable across regimes. This is displayed for "
            "audit purposes only and is not used in any analytics."
        )
        all_df = get_exchange_rates(limit=500)
        if not all_df.empty:
            import plotly.express as px
            fig = px.scatter(
                all_df,
                x="timestamp", y="rate", color="regime",
                title="All Exchange Rate Records by Regime (Audit View)",
                labels={"timestamp": "Year", "rate": "SOS per USD", "regime": "Regime"},
            )
            fig.update_layout(
                margin=dict(l=40, r=20, t=50, b=40),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                autosize=True,
                xaxis=dict(tickformat="%Y", dtick="M60", tickangle=-30),
            )
            st.plotly_chart(fig, width="stretch", config=_CHART_CONFIG)

st.divider()

# ===========================================================================
# Section 4 — Market Analytics Charts
# ===========================================================================
st.header("Market Analytics")
st.caption(
    f"Exchange rate charts show **{MODERN_REGIME}** data only. "
    "Pre-war and post-war records are excluded from all trend visualisations."
)
render_market_analytics()
st.divider()

# ===========================================================================
# Section 5 — Latest KPI Metrics Table
# ===========================================================================
st.header("Latest KPI Metrics")
kpi_df = get_kpi_metrics(limit=20)

if kpi_df.empty:
    st.info(
        "No KPI metrics stored yet. "
        "Click 'Refresh Economic Data' to populate exchange rate, fuel, and telecom data."
    )
else:
    st.dataframe(
        kpi_df, width="stretch", hide_index=True,
        column_config={
            "timestamp":    st.column_config.DatetimeColumn("Timestamp", format="YYYY-MM-DD HH:mm"),
            "metric_name":  "Metric",
            "metric_value": st.column_config.NumberColumn("Value", format="%.4f"),
        },
    )

st.divider()

# ===========================================================================
# Section 6 — Economic Risk Summary
# ===========================================================================
st.header("Economic Risk Summary")

risk_score = metrics.get("economic_risk_score")
ex_vol     = metrics.get("exchange_volatility_30d")
fuel_chg   = metrics.get("fuel_daily_change_pct")
tel_chg    = metrics.get("telecom_movement_pct")

risk_label, risk_explanation = classify_risk(risk_score)
colour_map = {"Low Risk": "success", "Medium Risk": "warning", "High Risk": "error", "Unknown": "info"}
getattr(st, colour_map.get(risk_label, "info"))(f"**{risk_label}** — {risk_explanation}")

st.markdown("**Contributing Signals**")
r1, r2, r3 = st.columns(3)
with r1:
    st.metric("Exchange Volatility (CV)", f"{ex_vol:.6f}" if ex_vol is not None else "N/A")
with r2:
    st.metric("Fuel Price Movement", f"{fuel_chg:+.2f}%" if fuel_chg is not None else "N/A")
with r3:
    tel_display = f"{tel_chg:+.2f}%" if tel_chg is not None else "Unavailable"
    st.metric("Telecom Price Movement", tel_display)

st.divider()

# ===========================================================================
# Section 7 — System Information
# ===========================================================================
st.header("System Information")

s1, s2, s3, s4, s5, s6 = st.columns(6, gap="small")
with s1:
    st.metric("App Version", APP_VERSION)
with s2:
    st.metric("Database", "SQLite")
with s3:
    st.metric("Scheduler", "Python schedule")
with s4:
    st.metric("Last Refresh", datetime.now(tz=timezone.utc).strftime("%H:%M UTC"))
with s5:
    st.metric("Total Records", f"{get_total_records():,}")
with s6:
    st.metric("Sources Connected", get_data_source_count())

latest_fx = get_latest_exchange_rate()
latest_fuel = get_latest_fuel_price()
latest_tel = get_latest_telecom_price()

fx_timestamp = _format_data_freshness(latest_fx.get("timestamp") if latest_fx else None)
fuel_timestamp = _format_data_freshness(latest_fuel.get("timestamp") if latest_fuel else None)
telecom_timestamp = (
    _format_data_freshness(latest_tel.get("timestamp"))
    if latest_tel
    else "No telecom data available"
)

st.markdown("**Data Freshness**")
f1, f2, f3 = st.columns(3, gap="small")
with f1:
    st.metric("Latest Exchange Rate", fx_timestamp)
with f2:
    st.metric("Latest Fuel Price", fuel_timestamp)
with f3:
    st.metric("Latest Telecom", telecom_timestamp)

with st.expander("Data Sources", expanded=False):
    for name, url in DATA_SOURCES.items():
        st.markdown(f"- **{name}**: [{url}]({url})")

st.divider()
st.caption(
    f"{APP_NAME} · v{APP_VERSION} · "
    "Data: World Bank Open Data, HDX/WFP Somalia Prices. "
    "For portfolio and research purposes only."
)

# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
if refresh_interval > 0:
    time.sleep(refresh_interval)
    st.rerun()
