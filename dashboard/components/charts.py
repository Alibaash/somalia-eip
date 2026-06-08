"""
Chart components for the Somalia Economic Intelligence Platform.

Exchange rate trend and moving-average charts filter exclusively to
MODERN_REGIME so they never mix incompatible currency-era data on the
same Y-axis.

Date formatting conventions:
  Annual data  (World Bank, yearly)  → tick format "%Y"
  Monthly data (HDX/WFP, monthly)    → tick format "%b %Y"

All charts use config={"responsive": True} for mobile-friendly rendering.
"""

import re
import sys
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dashboard.queries import (
    get_exchange_rates,
    get_fuel_prices,
    get_telecom_prices,
    get_kpi_series,
)
from ingestion.exchange_rates import MODERN_REGIME

PALETTE = {
    "blue":   "#1f77b4",
    "orange": "#ff7f0e",
    "green":  "#2ca02c",
    "red":    "#d62728",
    "purple": "#9467bd",
    "teal":   "#17becf",
}

_BASE_LAYOUT = dict(
    margin=dict(l=40, r=20, t=50, b=40),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(size=12),
    hovermode="x unified",
    autosize=True,
)

_ANNUAL_XAXIS = dict(
    tickformat="%Y",
    dtick="M12",
    tickangle=-30,
)

_MONTHLY_XAXIS = dict(
    tickformat="%b %Y",
    dtick="M6",
    tickangle=-30,
)

_CHART_CONFIG = {"responsive": True, "displayModeBar": False}


def _empty_chart(message: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=13, color="grey"),
        align="center",
    )
    fig.update_layout(**_BASE_LAYOUT)
    return fig


# ---------------------------------------------------------------------------
# 1. USD/SOS Trend — MODERN_REGIME only
# ---------------------------------------------------------------------------

def chart_exchange_rate_trend() -> go.Figure:
    """
    Line chart of USD/SOS using ONLY the Reissued SOS / Official (2022+) series.
    Annual World Bank data — X-axis formatted as years.

    Because the World Bank PA.NUS.FCRF official series for Somalia began in
    2022 (post-central-bank reconstitution), this chart may show only 2–3
    annual observations. Point labels and a subtitle make the data limitation
    explicit so the sparse chart is clearly intentional, not a pipeline error.
    """
    df = get_exchange_rates(limit=200, regime=MODERN_REGIME)

    if df.empty:
        return _empty_chart(
            f"No data for regime:\n'{MODERN_REGIME}'\n\n"
            "Click 'Refresh Economic Data' to ingest current data."
        )

    obs_count  = len(df)
    first_year = int(df["timestamp"].min().year)
    last_year  = int(df["timestamp"].max().year)
    if obs_count >= 2:
        pct_change = (df["rate"].iloc[-1] - df["rate"].iloc[0]) / df["rate"].iloc[0] * 100
        pct_str = f"{pct_change:+.1f}%"
    else:
        pct_str = "N/A"

    subtitle = (
        f"Source: World Bank PA.NUS.FCRF  ·  "
        f"{obs_count} official annual observation{'s' if obs_count != 1 else ''}  ·  "
        f"{first_year}–{last_year}  ·  "
        f"Total change: {pct_str}"
    )

    fig = px.line(
        df,
        x="timestamp",
        y="rate",
        title=f"USD/SOS Exchange Rate — {MODERN_REGIME}",
        labels={"timestamp": "Year", "rate": "SOS per USD (Official)"},
        color_discrete_sequence=[PALETTE["blue"]],
    )
    fig.update_traces(
        mode="lines+markers+text",
        text=[f"{int(ts.year)}: {rate:,.0f}" for ts, rate in zip(df["timestamp"], df["rate"])],
        textposition="top center",
        textfont=dict(size=11, color=PALETTE["blue"]),
        marker=dict(size=9),
    )
    y_pad = max((df["rate"].max() - df["rate"].min()) * 0.5, df["rate"].max() * 0.05)
    fig.update_layout(
        **{**_BASE_LAYOUT, "margin": dict(l=40, r=20, t=80, b=60)},
        xaxis=dict(**_ANNUAL_XAXIS, title="Year"),
        yaxis=dict(
            title="SOS per USD (Official Rate)",
            range=[df["rate"].min() * 0.95, df["rate"].max() + y_pad],
        ),
    )
    fig.add_annotation(
        text=subtitle,
        xref="paper", yref="paper",
        x=0, y=-0.18,
        showarrow=False,
        xanchor="left", yanchor="top",
        font=dict(size=10, color="#888888"),
    )
    return fig


# ---------------------------------------------------------------------------
# 2. Fuel Price Trend
# ---------------------------------------------------------------------------

def _clean_fuel_label(raw: str) -> str:
    """'Fuel (diesel)' → 'Diesel', 'Fuel (petrol)' → 'Petrol', etc."""
    m = re.match(r"Fuel\s*\((.+?)\)", raw, re.IGNORECASE)
    return m.group(1).strip().title() if m else raw.title()


def chart_fuel_price_trend() -> go.Figure:
    """
    Monthly-median fuel price trend aggregated across all WFP-monitored cities.

    Raw data: one row per city/commodity/month → aggregated to monthly median
    per fuel type.  This removes per-city noise while preserving the national
    price trend signal.  Only validated HDX/WFP records are used; test-seeded
    rows and pre-2011 records are excluded at query time.
    """
    df = get_fuel_prices()
    if df.empty:
        return _empty_chart(
            "No fuel price data available.\nRun 'Refresh Economic Data' to fetch HDX/WFP data."
        )

    df["_month"] = df["timestamp"].dt.to_period("M").dt.to_timestamp()
    df["fuel_label"] = df["fuel_type"].apply(_clean_fuel_label)

    monthly = (
        df.groupby(["_month", "fuel_label"])["price"]
        .median()
        .reset_index()
        .rename(columns={"_month": "timestamp", "fuel_label": "Fuel Type"})
        .sort_values("timestamp")
    )

    n_obs  = len(df)
    n_mo   = monthly["timestamp"].nunique()
    first  = df["timestamp"].min().strftime("%b %Y")
    last   = df["timestamp"].max().strftime("%b %Y")
    subtitle = (
        f"Source: HDX/WFP Somalia Market Monitoring  ·  "
        f"{n_obs:,} city-level observations  ·  "
        f"{n_mo} months  ·  {first}–{last}  ·  national median"
    )

    fig = px.line(
        monthly,
        x="timestamp",
        y="price",
        color="Fuel Type",
        title="Fuel Price Trend — Somalia (USD/unit)",
        labels={"timestamp": "Date", "price": "Price (USD/unit)"},
    )
    fig.update_traces(mode="lines", line=dict(width=2))
    fig.update_layout(
        **{**_BASE_LAYOUT, "margin": dict(l=40, r=20, t=80, b=60)},
        xaxis=dict(**_MONTHLY_XAXIS, title="Date"),
        yaxis_title="Price (USD/unit)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.add_annotation(
        text=subtitle,
        xref="paper", yref="paper",
        x=0, y=-0.18,
        showarrow=False,
        xanchor="left", yanchor="top",
        font=dict(size=10, color="#888888"),
    )
    return fig


# ---------------------------------------------------------------------------
# 3. Telecom Price Trend
# ---------------------------------------------------------------------------

def chart_telecom_price_trend() -> go.Figure:
    """
    Telecom price trend chart.

    WFP Somalia Market Monitoring does not track mobile airtime or data
    pricing.  When no data is available this chart renders a styled
    informational panel that makes the gap intentional and explains why
    core analytics are unaffected.
    """
    df = get_telecom_prices(limit=200)
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text=(
                "<b>Telecom Pricing — Known Data Gap</b><br><br>"
                "WFP Somalia Market Monitoring does not currently<br>"
                "track mobile airtime or mobile data tariffs.<br><br>"
                "<b>Why this indicator is unavailable:</b><br>"
                "Systematic telecom price surveillance in Somalia<br>"
                "is conducted by ITU and GSMA, whose datasets<br>"
                "require institutional registration and are not<br>"
                "available through public humanitarian APIs.<br><br>"
                "<i>This gap does not affect core analytics.</i><br>"
                "<i>Exchange rate and fuel price indicators<br>"
                "are fully operational.</i>"
            ),
            xref="paper", yref="paper",
            x=0.5, y=0.5,
            showarrow=False,
            align="center",
            font=dict(size=11.5, color="#2c5282"),
            bgcolor="rgba(235,248,255,0.95)",
            bordercolor="#63b3ed",
            borderwidth=1.5,
            borderpad=18,
        )
        fig.update_layout(
            title="Mobile Airtime & Data Prices — Somalia",
            **_BASE_LAYOUT,
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
        )
        return fig

    fig = px.line(
        df,
        x="timestamp",
        y="price",
        color="service_type",
        title="Telecom Price Trend — Somalia (USD)",
        labels={"timestamp": "Date", "price": "Price (USD)", "service_type": "Service"},
    )
    fig.update_traces(mode="lines", line=dict(width=1.5))
    fig.update_layout(
        **_BASE_LAYOUT,
        xaxis=dict(**_MONTHLY_XAXIS, title="Date"),
        yaxis_title="Price (USD)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


# ---------------------------------------------------------------------------
# 4. Moving Average — MODERN_REGIME only
# ---------------------------------------------------------------------------

def chart_moving_average() -> go.Figure:
    """
    Exchange rate with rolling mean, using MODERN_REGIME data only.
    Annual World Bank data — X-axis formatted as years.

    A dataset note is appended below the chart because the MODERN_REGIME
    series has only 2–3 annual observations; the rolling window is capped
    at len(df) so the moving average is still computable.
    """
    df = get_exchange_rates(limit=200, regime=MODERN_REGIME)

    if df.empty or len(df) < 2:
        return _empty_chart(
            "Insufficient data for moving average.\n"
            "At least 2 records in the current regime are required.\n"
            "Click 'Refresh Economic Data' to fetch data."
        )

    df = df.sort_values("timestamp").reset_index(drop=True)
    window = min(5, len(df))
    df["ma"] = df["rate"].rolling(window=window, min_periods=1).mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=df["rate"],
        mode="lines+markers",
        name="USD/SOS (Official)",
        line=dict(color=PALETTE["blue"], width=2),
        marker=dict(size=7),
    ))
    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=df["ma"],
        mode="lines",
        name=f"Moving Avg (n={window})",
        line=dict(color=PALETTE["orange"], width=2, dash="dash"),
    ))
    obs_count  = len(df)
    first_year = int(df["timestamp"].min().year)
    last_year  = int(df["timestamp"].max().year)
    subtitle = (
        f"Source: World Bank PA.NUS.FCRF  ·  "
        f"{obs_count} official annual observation{'s' if obs_count != 1 else ''}  ·  "
        f"{first_year}–{last_year}  ·  rolling window n={window}"
    )

    fig.update_layout(
        title=f"USD/SOS Moving Average — {MODERN_REGIME}",
        xaxis=dict(**_ANNUAL_XAXIS, title="Year"),
        yaxis_title="SOS per USD (Official)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        **{**_BASE_LAYOUT, "margin": dict(l=40, r=20, t=80, b=60)},
    )
    fig.add_annotation(
        text=subtitle,
        xref="paper", yref="paper",
        x=0, y=-0.18,
        showarrow=False,
        xanchor="left", yanchor="top",
        font=dict(size=10, color="#888888"),
    )
    return fig


# ---------------------------------------------------------------------------
# 5. Volatility Trend
# ---------------------------------------------------------------------------

def chart_volatility_trend() -> go.Figure:
    df = get_kpi_series("composite_volatility_score", limit=100)
    if df.empty:
        return _empty_chart(
            "No volatility metrics recorded yet.\nRun 'Refresh Economic Data' to calculate metrics."
        )

    fig = px.area(
        df,
        x="timestamp",
        y="metric_value",
        title="Composite Volatility Score Over Time",
        labels={"timestamp": "Date", "metric_value": "Volatility Score (0–1)"},
        color_discrete_sequence=[PALETTE["orange"]],
    )
    fig.update_layout(
        **_BASE_LAYOUT,
        xaxis=dict(**_MONTHLY_XAXIS, title="Date"),
        yaxis=dict(title="Volatility Score (0–1)", range=[0, 1]),
    )
    return fig


# ---------------------------------------------------------------------------
# 6. Economic Risk Trend
# ---------------------------------------------------------------------------

def chart_economic_risk_trend() -> go.Figure:
    df = get_kpi_series("economic_risk_score", limit=100)
    if df.empty:
        return _empty_chart(
            "No risk score metrics recorded yet.\nRun 'Refresh Economic Data' to calculate metrics."
        )

    fig = go.Figure()
    fig.add_hrect(y0=0,    y1=0.33, fillcolor="green",  opacity=0.08, line_width=0,
                  annotation_text="Low",    annotation_position="left")
    fig.add_hrect(y0=0.33, y1=0.66, fillcolor="orange", opacity=0.08, line_width=0,
                  annotation_text="Medium", annotation_position="left")
    fig.add_hrect(y0=0.66, y1=1.0,  fillcolor="red",    opacity=0.08, line_width=0,
                  annotation_text="High",   annotation_position="left")

    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=df["metric_value"],
        mode="lines+markers",
        fill="tozeroy",
        fillcolor="rgba(214, 39, 40, 0.12)",
        line=dict(color=PALETTE["red"], width=2),
        marker=dict(size=4),
        name="Risk Score",
    ))
    fig.update_layout(
        title="Economic Risk Score Over Time",
        xaxis=dict(**_MONTHLY_XAXIS, title="Date"),
        yaxis=dict(title="Risk Score (0–1)", range=[0, 1]),
        **_BASE_LAYOUT,
    )
    return fig


# ---------------------------------------------------------------------------
# Render all six charts
# ---------------------------------------------------------------------------

def render_market_analytics() -> None:
    """Render all six market analytics charts in a 2-column grid."""
    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(chart_exchange_rate_trend(), width="stretch", config=_CHART_CONFIG)
    with col_b:
        st.plotly_chart(chart_fuel_price_trend(), width="stretch", config=_CHART_CONFIG)

    col_c, col_d = st.columns(2)
    with col_c:
        st.plotly_chart(chart_telecom_price_trend(), width="stretch", config=_CHART_CONFIG)
    with col_d:
        st.plotly_chart(chart_moving_average(), width="stretch", config=_CHART_CONFIG)

    col_e, col_f = st.columns(2)
    with col_e:
        st.plotly_chart(chart_volatility_trend(), width="stretch", config=_CHART_CONFIG)
    with col_f:
        st.plotly_chart(chart_economic_risk_trend(), width="stretch", config=_CHART_CONFIG)
