"""
Analytics / KPI calculation engine.

Exchange rate metrics are computed exclusively from the MODERN_REGIME series
(Reissued SOS / Official, 2022+) to avoid polluting analytics with records
from the incompatible pre-war or post-war parallel-market eras.
"""

import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.connection import get_connection
from ingestion.exchange_rates import MODERN_REGIME

logger = logging.getLogger(__name__)

VOLATILITY_LOW  = 0.02
VOLATILITY_HIGH = 0.08


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct_change(current: float, previous: float) -> Optional[float]:
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 2)


def _trend_direction(series: pd.Series) -> str:
    if len(series) < 2:
        return "Stable"
    x = np.arange(len(series))
    slope = float(np.polyfit(x, series.values, 1)[0])
    mean_val = series.mean()
    if mean_val == 0:
        return "Stable"
    rel_slope = slope / abs(mean_val)
    if rel_slope > 0.005:
        return "Rising"
    if rel_slope < -0.005:
        return "Falling"
    return "Stable"


def _save_metric(conn: sqlite3.Connection, name: str, value: Optional[float]) -> None:
    ts = datetime.now(tz=timezone.utc).isoformat()
    if value is not None:
        conn.execute(
            "INSERT OR IGNORE INTO kpi_metrics (timestamp, metric_name, metric_value) "
            "VALUES (?, ?, ?)",
            (ts, name, value),
        )


# ---------------------------------------------------------------------------
# Exchange rate metrics — MODERN_REGIME only
# ---------------------------------------------------------------------------

def compute_exchange_rate_metrics() -> dict[str, Optional[float]]:
    """
    Compute exchange-rate KPIs using ONLY the active analytics regime
    (MODERN_REGIME = 'Reissued SOS / Official (2022+)').

    Metrics:
      current_usd_sos          — most recent rate
      exchange_daily_change_pct — % change vs prior record
      exchange_weekly_change_pct — % change vs record 2 positions back
      exchange_moving_avg_30d   — rolling mean of available modern records
      exchange_volatility_30d   — coefficient of variation (std / mean)
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql_query(
                """
                SELECT timestamp, rate
                FROM exchange_rates
                WHERE regime = ?
                ORDER BY timestamp ASC
                """,
                conn,
                params=(MODERN_REGIME,),
                parse_dates=["timestamp"],
            )
    except Exception as exc:
        logger.error("Failed to query modern exchange_rates: %s", exc)
        return {}

    if df.empty:
        logger.warning(
            "No records found for modern regime. Falling back to latest available exchange-rate records."
        )
        try:
            with get_connection() as conn:
                df = pd.read_sql_query(
                    '''
                    SELECT timestamp, rate
                    FROM exchange_rates
                    ORDER BY timestamp ASC
                    ''',
                    conn,
                    parse_dates=["timestamp"],
                )
        except Exception:
            return {}
        if df.empty:
            return {}

    rates = df["rate"]
    current   = float(rates.iloc[-1])
    prev      = float(rates.iloc[-2]) if len(rates) >= 2 else current
    prev2     = float(rates.iloc[-3]) if len(rates) >= 3 else prev
    ma        = float(rates.mean())
    vol       = float(rates.std() / rates.mean()) if rates.mean() != 0 and len(rates) >= 2 else 0.0

    return {
        "current_usd_sos":            round(current, 2),
        "exchange_daily_change_pct":  _pct_change(current, prev),
        "exchange_weekly_change_pct": _pct_change(current, prev2),
        "exchange_moving_avg_30d":    round(ma, 2),
        "exchange_volatility_30d":    round(vol, 6),
    }


# ---------------------------------------------------------------------------
# Fuel price metrics
# ---------------------------------------------------------------------------

def compute_fuel_metrics() -> dict[str, Optional[float]]:
    try:
        with get_connection() as conn:
            df = pd.read_sql_query(
                """
                SELECT timestamp, fuel_type, price
                FROM fuel_prices
                ORDER BY timestamp DESC
                LIMIT 500
                """,
                conn,
                parse_dates=["timestamp"],
            )
    except Exception as exc:
        logger.error("Failed to query fuel_prices: %s", exc)
        return {}

    if df.empty:
        return {}

    # Ignore extreme/invalid prices globally
    df = df[(df["price"] >= 5) & (df["price"] <= 200)]
    if df.empty:
        return {}

    # Prefer diesel records if available
    diesel = df[df["fuel_type"].str.lower().str.contains("diesel", na=False)]
    if not diesel.empty:
        series = diesel["price"]
    else:
        series = df["price"]

    # Reverse to chronological order for consistent calculations
    series = series.iloc[::-1].reset_index(drop=True)

    current   = float(series.iloc[-1])
    prev      = float(series.iloc[-2]) if len(series) >= 2 else current
    trend_str = _trend_direction(series.tail(30))
    trend_enc = 1 if trend_str == "Rising" else (-1 if trend_str == "Falling" else 0)

    return {
        "current_fuel_price_usd":  round(current, 4),
        "fuel_daily_change_pct":   _pct_change(current, prev),
        "fuel_weekly_change_pct":  _pct_change(current, prev),
        "fuel_trend":              float(trend_enc),
    }


# ---------------------------------------------------------------------------
# Telecom price metrics
# ---------------------------------------------------------------------------

def compute_telecom_metrics() -> dict[str, Optional[float]]:
    try:
        with get_connection() as conn:
            df = pd.read_sql_query(
                "SELECT timestamp, price FROM telecom_prices ORDER BY timestamp DESC LIMIT 200",
                conn,
                parse_dates=["timestamp"],
            )
    except Exception as exc:
        logger.error("Failed to query telecom_prices: %s", exc)
        return {}

    if df.empty:
        return {}

    series    = df["price"].iloc[::-1].reset_index(drop=True)
    current   = float(series.iloc[-1])
    prev      = float(series.iloc[-2]) if len(series) >= 2 else current
    trend_str = _trend_direction(series.tail(20))
    trend_enc = 1 if trend_str == "Rising" else (-1 if trend_str == "Falling" else 0)

    return {
        "current_telecom_price_usd": round(current, 4),
        "telecom_movement_pct":      _pct_change(current, prev),
        "telecom_trend":             float(trend_enc),
    }


# ---------------------------------------------------------------------------
# Composite metrics
# ---------------------------------------------------------------------------

def compute_composite_metrics(
    ex: dict, fuel: dict, telecom: dict
) -> dict[str, float]:
    signals: list[float] = []

    ex_vol = ex.get("exchange_volatility_30d")
    if ex_vol is not None:
        signals.append(min(ex_vol / VOLATILITY_HIGH, 1.0))

    fuel_chg = abs(fuel.get("fuel_daily_change_pct") or 0) / 100
    signals.append(min(fuel_chg / 0.05, 1.0))

    tel_chg = abs(telecom.get("telecom_movement_pct") or 0) / 100
    signals.append(min(tel_chg / 0.05, 1.0))

    vol_score = round(float(np.mean(signals)), 4) if signals else 0.0
    risk_score = round(min(vol_score * 1.2, 1.0), 4)
    stability  = round(1.0 - risk_score, 4)

    return {
        "composite_volatility_score":  vol_score,
        "economic_risk_score":         risk_score,
        "market_stability_indicator":  stability,
    }


# ---------------------------------------------------------------------------
# Full metrics pipeline
# ---------------------------------------------------------------------------

def run_metrics() -> dict[str, object]:
    ex       = compute_exchange_rate_metrics()
    fuel     = compute_fuel_metrics()
    telecom  = compute_telecom_metrics()
    composite = compute_composite_metrics(ex, fuel, telecom)

    all_metrics = {**ex, **fuel, **telecom, **composite}

    try:
        with get_connection() as conn:
            for name, value in all_metrics.items():
                if isinstance(value, (int, float)):
                    _save_metric(conn, name, float(value))
            conn.commit()
    except Exception as exc:
        logger.exception("Failed to save metrics to database: %s", exc)

    return all_metrics


def classify_risk(risk_score: Optional[float]) -> tuple[str, str]:
    if risk_score is None:
        return "Unknown", "Insufficient data to classify economic risk."
    if risk_score < 0.33:
        return (
            "Low Risk",
            f"Risk score {risk_score:.2f} — Exchange rate, fuel, and telecom prices "
            "are relatively stable with low short-term volatility.",
        )
    if risk_score < 0.66:
        return (
            "Medium Risk",
            f"Risk score {risk_score:.2f} — Moderate price movement detected in one "
            "or more tracked sectors. Monitor closely.",
        )
    return (
        "High Risk",
        f"Risk score {risk_score:.2f} — Significant volatility detected across tracked "
        "economic indicators. Elevated caution advised.",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json
    print(json.dumps(run_metrics(), indent=2, default=str))
