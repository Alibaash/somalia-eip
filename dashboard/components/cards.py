"""
KPI card components for the Somalia Economic Intelligence Platform.

Each card shows:
  - Metric label
  - Current value (formatted)
  - Delta / percentage change with green / red indicator
  - Data timestamp and source in help tooltip
"""

import sys
from pathlib import Path
from typing import Optional

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dashboard.queries import (
    get_latest_exchange_rate,
    get_latest_fuel_price,
    get_latest_telecom_price,
)
from processing.metrics import classify_risk


def _fmt_timestamp(ts) -> str:
    """Return a short human-readable string from a timestamp value."""
    if ts is None:
        return "N/A"
    try:
        import pandas as pd
        return pd.to_datetime(ts).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)[:10]


def render_executive_kpi_cards(metrics: dict) -> None:
    """
    Render the five executive KPI cards in a single row.
    metrics: result dict from processing.metrics.run_metrics()

    Telecom is an optional source — WFP does not monitor Somalia airtime prices.
    When no telecom data is available the card shows "Unavailable" rather than
    "No data", and does not affect the overall risk or volatility scores.
    """
    col1, col2, col3, col4, col5 = st.columns(5, gap="small")

    # ---- USD / SOS Exchange Rate ------------------------------------------ #
    with col1:
        rate_row = get_latest_exchange_rate()
        current_rate = metrics.get("current_usd_sos")
        delta_pct    = metrics.get("exchange_daily_change_pct")
        ts_str       = _fmt_timestamp(rate_row["timestamp"] if rate_row else None)

        st.metric(
            label="USD / SOS Rate",
            value=f"{current_rate:,.2f}" if current_rate is not None else "No data",
            delta=f"{delta_pct:+.2f}%" if delta_pct is not None else None,
            delta_color="inverse",
            help=f"Source: World Bank PA.NUS.FCRF · As of {ts_str}",
        )

    # ---- Fuel Price ------------------------------------------------------- #
    with col2:
        fuel_row    = get_latest_fuel_price()
        current_fuel = metrics.get("current_fuel_price_usd")
        fuel_delta  = metrics.get("fuel_daily_change_pct")
        fuel_ts     = _fmt_timestamp(fuel_row["timestamp"] if fuel_row else None)
        fuel_type   = fuel_row["fuel_type"] if fuel_row else "Fuel"

        st.metric(
            label="Fuel Price (USD/unit)",
            value=f"${current_fuel:.4f}" if current_fuel is not None else "No data",
            delta=f"{fuel_delta:+.2f}%" if fuel_delta is not None else None,
            delta_color="inverse",
            help=f"{fuel_type} · HDX/WFP Somalia · As of {fuel_ts}",
        )

    # ---- Telecom Price — optional source ---------------------------------- #
    with col3:
        tel_row     = get_latest_telecom_price()
        current_tel = metrics.get("current_telecom_price_usd")
        tel_delta   = metrics.get("telecom_movement_pct")
        tel_ts      = _fmt_timestamp(tel_row["timestamp"] if tel_row else None)

        if current_tel is not None:
            tel_value = f"${current_tel:.4f}"
            tel_delta_str = f"{tel_delta:+.2f}%" if tel_delta is not None else None
            tel_help = f"HDX/WFP · As of {tel_ts}"
        else:
            tel_value     = "Unavailable"
            tel_delta_str = None
            tel_help      = (
                "Telecom data is not currently available. "
                "WFP does not monitor airtime prices in Somalia. "
                "This is an optional indicator and does not affect the risk score."
            )

        st.metric(
            label="Telecom Price (USD)",
            value=tel_value,
            delta=tel_delta_str,
            delta_color="inverse",
            help=tel_help,
        )

    # ---- Volatility Score ------------------------------------------------- #
    with col4:
        vol_score = metrics.get("composite_volatility_score")
        ex_vol    = metrics.get("exchange_volatility_30d")
        vol_pct   = vol_score * 100 if vol_score is not None else None

        ex_vol_detail = f" Exchange rate CV: {ex_vol:.6f}." if ex_vol is not None else ""

        st.metric(
            label="Volatility Score",
            value=f"{vol_pct:.1f}%" if vol_pct is not None else "No data",
            delta=None,
            help=f"Composite price volatility across all tracked sectors (0–100%).{ex_vol_detail}",
        )

    # ---- Economic Risk Score --------------------------------------------- #
    with col5:
        risk_score = metrics.get("economic_risk_score")
        risk_label, _ = classify_risk(risk_score)
        risk_pct   = risk_score * 100 if risk_score is not None else None

        colour_map = {"Low Risk": "🟢", "Medium Risk": "🟡", "High Risk": "🔴", "Unknown": "⚪"}
        indicator  = colour_map.get(risk_label, "⚪")

        st.metric(
            label=f"Economic Risk {indicator}",
            value=f"{risk_pct:.1f}%" if risk_pct is not None else "No data",
            delta=None,
            help="Composite risk derived from exchange rate, fuel, and available market volatility.",
        )
