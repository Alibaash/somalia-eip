"""
Database query functions for the Streamlit dashboard.

All functions return pandas DataFrames or plain Python dicts.
No business logic here — pure data retrieval.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.connection import get_connection, check_database_status
from ingestion.exchange_rates import MODERN_REGIME

logger = logging.getLogger(__name__)


def _parse_timestamps(series: pd.Series) -> pd.Series:
    """
    Coerce a string timestamp column to timezone-naive UTC datetime64.

    Handles all formats stored in the DB:
      - date-only          '2022-01-01'
      - ISO with time      '2014-10-15T00:00:00'
      - SQLite datetime    '2026-05-30 16:30:43'

    Using utc=True first ensures any tz-aware and tz-naive strings are
    unified before tz_convert(None) strips the timezone, producing a
    consistent naive datetime64[us] column that works with both Plotly
    and pandas period arithmetic (dt.to_period).
    """
    return (
        pd.to_datetime(series, format="mixed", utc=True, errors="coerce")
        .dt.tz_convert(None)
    )


# ---------------------------------------------------------------------------
# Exchange rates
# ---------------------------------------------------------------------------

def get_exchange_rates(limit: int = 365, regime: Optional[str] = None) -> pd.DataFrame:
    """
    Return exchange_rates ordered by timestamp ascending.

    Args:
        limit:  Maximum rows to return.
        regime: If given, filter to this regime label only.
                Pass MODERN_REGIME to get only the active analytics series.

    Timestamps are coerced with format='mixed' to handle both date-only
    ('2022-01-01') and ISO-datetime strings stored in the database.
    """
    try:
        with get_connection() as conn:
            if regime:
                df = pd.read_sql_query(
                    f"""
                    SELECT timestamp, city, rate, regime, source
                    FROM exchange_rates
                    WHERE regime = ?
                    ORDER BY timestamp ASC
                    LIMIT {limit}
                    """,
                    conn,
                    params=(regime,),
                )
            else:
                df = pd.read_sql_query(
                    f"""
                    SELECT timestamp, city, rate, regime, source
                    FROM exchange_rates
                    ORDER BY timestamp ASC
                    LIMIT {limit}
                    """,
                    conn,
                )
        if not df.empty:
            df["timestamp"] = _parse_timestamps(df["timestamp"])
            df = (
                df.dropna(subset=["timestamp"])
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
        return df
    except Exception as exc:
        logger.error("get_exchange_rates failed: %s", exc)
        return pd.DataFrame()


def get_latest_exchange_rate() -> Optional[dict]:
    """
    Return the single most recent exchange rate record from the active
    analytics regime (MODERN_REGIME) only, so the KPI card never shows
    a rate from an incompatible historical era.
    """
    try:
        with get_connection() as conn:
            # Prefer the modern analytics regime
            row = conn.execute(
                """
                SELECT timestamp, city, rate, regime, source
                FROM exchange_rates
                WHERE regime = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (MODERN_REGIME,),
            ).fetchone()

            if row:
                return dict(row)

            # Fallback: return the latest available exchange rate from any regime
            fallback = conn.execute(
                """
                SELECT timestamp, city, rate, regime, source
                FROM exchange_rates
                ORDER BY timestamp DESC
                LIMIT 1
                """
            ).fetchone()

        if fallback:
            logger.warning(
                "No modern exchange rate found (regime=%s). Falling back to latest available regime=%s.",
                MODERN_REGIME,
                fallback[3],
            )
            return dict(fallback)

        return None
    except Exception as exc:
        logger.error("get_latest_exchange_rate failed: %s", exc)
        return None


def get_exchange_rate_validation_table() -> pd.DataFrame:
    """
    Return a per-regime validation summary:
      regime | count | min_rate | max_rate | median_rate | first_date | last_date

    Used by the audit/validation section of the dashboard.
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql_query(
                """
                SELECT
                    regime,
                    COUNT(*)        AS record_count,
                    MIN(rate)       AS min_rate,
                    MAX(rate)       AS max_rate,
                    AVG(rate)       AS avg_rate,
                    MIN(timestamp)  AS first_date,
                    MAX(timestamp)  AS last_date
                FROM exchange_rates
                GROUP BY regime
                ORDER BY MIN(timestamp)
                """,
                conn,
            )

        # Compute median per regime (SQLite has no native MEDIAN)
        medians: list[float] = []
        with get_connection() as conn:
            regimes = df["regime"].tolist()
            for reg in regimes:
                rates = [
                    r[0]
                    for r in conn.execute(
                        "SELECT rate FROM exchange_rates WHERE regime = ? ORDER BY rate",
                        (reg,),
                    ).fetchall()
                ]
                if rates:
                    mid = len(rates) // 2
                    if len(rates) % 2 == 0:
                        median = (rates[mid - 1] + rates[mid]) / 2
                    else:
                        median = rates[mid]
                else:
                    median = 0.0
                medians.append(round(median, 2))

        df["median_rate"] = medians
        df["min_rate"] = df["min_rate"].round(2)
        df["max_rate"] = df["max_rate"].round(2)
        df["avg_rate"] = df["avg_rate"].round(2)
        df["first_date"] = df["first_date"].str[:10]
        df["last_date"] = df["last_date"].str[:10]

        return df
    except Exception as exc:
        logger.error("get_exchange_rate_validation_table failed: %s", exc)
        return pd.DataFrame()


def get_all_regimes() -> list[str]:
    """Return the distinct regime labels stored in exchange_rates."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT regime FROM exchange_rates ORDER BY MIN(timestamp)"
            ).fetchall()
        return [r[0] for r in rows]
    except Exception as exc:
        logger.error("get_all_regimes failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Fuel prices
# ---------------------------------------------------------------------------

def get_fuel_prices(fuel_type: Optional[str] = None, limit: int = 2000) -> pd.DataFrame:
    """
    Return validated HDX/WFP fuel price records.

    Filters applied at query time:
      - source = 'HDX/WFP Somalia Food Prices'  (excludes test-seeded rows)
      - timestamp >= '2011-01-01'                (WFP Somalia data starts 2014)
      - price > 0                                (excludes data-entry zeroes)

    Timestamps are coerced with format='mixed' after reading to handle any
    variation between date-only ('2014-10-15') and ISO-datetime strings
    ('2014-10-15T00:00:00') that may be stored in the same column.
    """
    _BASE_WHERE = (
        "source = 'HDX/WFP Somalia Food Prices' "
        "AND timestamp >= '2011-01-01' "
        "AND price > 0"
    )
    try:
        with get_connection() as conn:
            if fuel_type:
                df = pd.read_sql_query(
                    f"""
                    SELECT timestamp, city, fuel_type, price, source
                    FROM fuel_prices
                    WHERE {_BASE_WHERE}
                      AND LOWER(fuel_type) LIKE ?
                    ORDER BY timestamp ASC
                    LIMIT {limit}
                    """,
                    conn,
                    params=(f"%{fuel_type.lower()}%",),
                )
            else:
                df = pd.read_sql_query(
                    f"""
                    SELECT timestamp, city, fuel_type, price, source
                    FROM fuel_prices
                    WHERE {_BASE_WHERE}
                    ORDER BY timestamp ASC
                    LIMIT {limit}
                    """,
                    conn,
                )
        if not df.empty:
            df["timestamp"] = _parse_timestamps(df["timestamp"])
            df = (
                df.dropna(subset=["timestamp"])
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
        return df
    except Exception as exc:
        logger.error("get_fuel_prices failed: %s", exc)
        return pd.DataFrame()


def get_latest_fuel_price() -> Optional[dict]:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT timestamp, city, fuel_type, price, source "
                "FROM fuel_prices "
                "WHERE source = 'HDX/WFP Somalia Food Prices' "
                "  AND timestamp >= '2011-01-01' "
                "  AND price > 0 "
                "ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.error("get_latest_fuel_price failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Telecom prices
# ---------------------------------------------------------------------------

def get_telecom_prices(limit: int = 200) -> pd.DataFrame:
    try:
        with get_connection() as conn:
            df = pd.read_sql_query(
                f"""
                SELECT timestamp, provider, service_type, price, source
                FROM telecom_prices
                ORDER BY timestamp ASC
                LIMIT {limit}
                """,
                conn,
            )
        if not df.empty:
            df["timestamp"] = _parse_timestamps(df["timestamp"])
            df = (
                df.dropna(subset=["timestamp"])
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
        return df
    except Exception as exc:
        logger.error("get_telecom_prices failed: %s", exc)
        return pd.DataFrame()


def get_latest_telecom_price() -> Optional[dict]:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT timestamp, provider, service_type, price, source "
                "FROM telecom_prices ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.error("get_latest_telecom_price failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# KPI metrics
# ---------------------------------------------------------------------------

def get_kpi_metrics(limit: int = 20) -> pd.DataFrame:
    try:
        with get_connection() as conn:
            df = pd.read_sql_query(
                f"""
                SELECT timestamp, metric_name, metric_value
                FROM kpi_metrics
                ORDER BY timestamp DESC
                LIMIT {limit}
                """,
                conn,
            )
        if not df.empty:
            df["timestamp"] = _parse_timestamps(df["timestamp"])
            df = df.dropna(subset=["timestamp"]).reset_index(drop=True)
        return df
    except Exception as exc:
        logger.error("get_kpi_metrics failed: %s", exc)
        return pd.DataFrame()


def get_kpi_series(metric_name: str, limit: int = 100) -> pd.DataFrame:
    try:
        with get_connection() as conn:
            df = pd.read_sql_query(
                f"""
                SELECT timestamp, metric_value
                FROM kpi_metrics
                WHERE metric_name = ?
                ORDER BY timestamp ASC
                LIMIT {limit}
                """,
                conn,
                params=(metric_name,),
            )
        if not df.empty:
            df["timestamp"] = _parse_timestamps(df["timestamp"])
            df = (
                df.dropna(subset=["timestamp"])
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
        return df
    except Exception as exc:
        logger.error("get_kpi_series failed: %s", exc)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Pipeline logs
# ---------------------------------------------------------------------------

def get_pipeline_logs(limit: int = 20) -> pd.DataFrame:
    try:
        with get_connection() as conn:
            df = pd.read_sql_query(
                f"""
                SELECT timestamp, job_name, status, message
                FROM pipeline_logs
                ORDER BY timestamp DESC
                LIMIT {limit}
                """,
                conn,
            )
        if not df.empty:
            df["timestamp"] = _parse_timestamps(df["timestamp"])
            df = df.dropna(subset=["timestamp"]).reset_index(drop=True)
        return df
    except Exception as exc:
        logger.error("get_pipeline_logs failed: %s", exc)
        return pd.DataFrame()


def get_latest_pipeline_result() -> Optional[dict]:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT timestamp, job_name, status, message "
                "FROM pipeline_logs ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.error("get_latest_pipeline_result failed: %s", exc)
        return None


def get_per_job_pipeline_status() -> dict[str, str]:
    """
    Return the latest pipeline status for each job that has ever run.
    Keys are job names; values are status strings (SUCCESS / WARNING / FAILED / RUNNING).

    Used to compute core vs optional health without mixing job statuses.
    """
    try:
        with get_connection() as conn:
            # Use MAX(id) — id is AUTOINCREMENT so it is strictly monotonic even
            # when two rows land in the same second (datetime('now') is second-precision).
            rows = conn.execute(
                """
                SELECT pl.job_name, pl.status
                FROM pipeline_logs pl
                INNER JOIN (
                    SELECT job_name, MAX(id) AS max_id
                    FROM pipeline_logs
                    GROUP BY job_name
                ) latest
                    ON pl.id = latest.max_id
                """
            ).fetchall()
        return {r["job_name"]: r["status"] for r in rows}
    except Exception as exc:
        logger.error("get_per_job_pipeline_status failed: %s", exc)
        return {}


def compute_pipeline_health() -> dict[str, object]:
    """
    Derive pipeline health from whether data is actually present in the core
    tables, not from pipeline_logs status strings.

    Using pipeline_logs for health is unreliable because:
    - RUNNING entries and terminal entries share the same second-resolution
      timestamp, making MAX(timestamp) non-deterministic.
    - Pytest test runs write to the same production database and can leave
      WARNING entries with higher ids than real SUCCESS entries.

    Data-presence check:
      Core sources healthy  → exchange_rates has classified records
                              AND fuel_prices has records.
      Optional unavailable  → telecom_prices is empty (expected — WFP does not
                              monitor Somalia airtime prices).

    Returns:
      core_status          — "Healthy" or "Issue"
      core_detail          — list of (label, status_str) for display
      optional_unavailable — count of optional tables with zero rows
      overall              — "SUCCESS" or "WARNING"
    """
    try:
        with get_connection() as conn:
            fx_total_count = conn.execute(
                "SELECT COUNT(*) FROM exchange_rates"
            ).fetchone()[0]
            fuel_count = conn.execute(
                "SELECT COUNT(*) FROM fuel_prices"
            ).fetchone()[0]
            tel_count = conn.execute(
                "SELECT COUNT(*) FROM telecom_prices"
            ).fetchone()[0]
    except Exception as exc:
        logger.error("compute_pipeline_health DB query failed: %s", exc)
        fx_total_count = fuel_count = tel_count = 0

    core_ok = (fx_total_count > 0) and (fuel_count > 0)

    if fx_total_count > 0:
        fx_status = f"✓ {fx_total_count} records"
    else:
        fx_status = "No data"

    core_detail = [
        ("Exchange Rates", fx_status),
        ("Fuel Prices",    f"✓ {fuel_count} records" if fuel_count > 0 else "No data"),
    ]

    optional_unavailable = 1 if tel_count == 0 else 0

    return {
        "core_status":          "Healthy" if core_ok else "Issue",
        "core_detail":          core_detail,
        "optional_unavailable": optional_unavailable,
        "overall":              "SUCCESS" if core_ok else "WARNING",
    }


# ---------------------------------------------------------------------------
# Summary / system info
# ---------------------------------------------------------------------------

def get_database_status() -> dict:
    return check_database_status()


def get_total_records() -> int:
    status = get_database_status()
    return int(status.get("total_records", 0))


def get_data_source_count() -> int:
    try:
        with get_connection() as conn:
            sql = """
            SELECT COUNT(DISTINCT source) AS cnt FROM (
                SELECT source FROM exchange_rates
                UNION ALL SELECT source FROM fuel_prices
                UNION ALL SELECT source FROM telecom_prices
            )
            """
            row = conn.execute(sql).fetchone()
        return int(row[0]) if row else 0
    except Exception as exc:
        logger.error("get_data_source_count failed: %s", exc)
        return 0
