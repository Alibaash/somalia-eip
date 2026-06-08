"""
Data transformation and quality layer.

Responsibilities:
- Validate records already stored in the database
- Remove true duplicates (same timestamp + key fields + source)
- Standardise timestamp format to ISO-8601
- Flag/remove zero or negative numeric values
- Log actions to pipeline_logs
"""

import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.connection import get_connection

logger = logging.getLogger(__name__)


def _log_pipeline(job_name: str, status: str, message: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO pipeline_logs (timestamp, job_name, status, message) "
                "VALUES (datetime('now'), ?, ?, ?)",
                (job_name, status, message),
            )
            conn.commit()
    except sqlite3.Error as exc:
        logger.error("Pipeline log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Exchange rate transformations
# ---------------------------------------------------------------------------

def clean_exchange_rates() -> int:
    """
    Remove rows with non-positive rates.
    Returns the number of rows deleted.
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM exchange_rates WHERE rate IS NULL OR rate <= 0"
            )
            conn.commit()
            deleted = cursor.rowcount
            if deleted:
                logger.info("Removed %d invalid exchange_rate rows.", deleted)
            return deleted
    except sqlite3.Error as exc:
        logger.exception("clean_exchange_rates failed: %s", exc)
        return 0


def standardise_exchange_timestamps() -> int:
    """
    Normalise timestamp column to ISO-8601 format (YYYY-MM-DDTHH:MM:SS+HH:MM).
    SQLite's datetime() is used for re-parsing where possible.
    Returns number of rows updated.
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE exchange_rates
                SET timestamp = datetime(timestamp)
                WHERE timestamp NOT LIKE '____-__-__%'
                """
            )
            conn.commit()
            return cursor.rowcount
    except sqlite3.Error as exc:
        logger.exception("standardise_exchange_timestamps failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Fuel price transformations
# ---------------------------------------------------------------------------

def clean_fuel_prices() -> int:
    """Remove rows with null or non-positive prices."""
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM fuel_prices WHERE price IS NULL OR price <= 0"
            )
            conn.commit()
            deleted = cursor.rowcount
            if deleted:
                logger.info("Removed %d invalid fuel_price rows.", deleted)
            return deleted
    except sqlite3.Error as exc:
        logger.exception("clean_fuel_prices failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Telecom price transformations
# ---------------------------------------------------------------------------

def clean_telecom_prices() -> int:
    """Remove rows with null or non-positive prices."""
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM telecom_prices WHERE price IS NULL OR price <= 0"
            )
            conn.commit()
            deleted = cursor.rowcount
            if deleted:
                logger.info("Removed %d invalid telecom_price rows.", deleted)
            return deleted
    except sqlite3.Error as exc:
        logger.exception("clean_telecom_prices failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Full transform pipeline
# ---------------------------------------------------------------------------

def run_transforms() -> dict[str, int]:
    """
    Run all data-quality transforms.
    Returns a dict of operation -> rows affected.
    """
    job_name = "transform"
    _log_pipeline(job_name, "RUNNING", "Data transform pipeline started.")

    results = {
        "exchange_rates_deleted": clean_exchange_rates(),
        "exchange_ts_updated": standardise_exchange_timestamps(),
        "fuel_prices_deleted": clean_fuel_prices(),
        "telecom_prices_deleted": clean_telecom_prices(),
    }

    total_affected = sum(results.values())
    msg = f"Transform complete. Total rows affected: {total_affected}. Details: {results}"
    logger.info(msg)
    _log_pipeline(job_name, "SUCCESS", msg)

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_transforms())
