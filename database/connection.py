"""
SQLite connection management for the Somalia Economic Intelligence Platform.

Provides:
- get_connection() — thread-safe context-managed connection
- init_database()  — idempotent schema initialisation + migrations
- migrate_database() — add columns that may not exist in older DB files
"""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from config import DATABASE_PATH

_HERE = Path(__file__).parent
_DB_PATH = DATABASE_PATH
_SCHEMA_PATH = _HERE / "schema.sql"

logger = logging.getLogger(__name__)


def get_db_path() -> Path:
    return _DB_PATH


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """
    Yield a SQLite connection with row_factory set.
    
    Attempts WAL mode for better concurrency (local/Codespaces).
    Gracefully degrades to default journal mode if WAL is not available
    (e.g., read-only or network filesystems in Streamlit Cloud).
    """
    conn = sqlite3.connect(str(_DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    # Try WAL mode for better concurrency (local environments)
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
    except sqlite3.OperationalError:
        # Fall back to default journal mode if WAL fails (read-only or no permissions)
        # This is safe for Streamlit Cloud and other restricted environments
        logger.debug(
            "WAL mode not available for %s; using default journal mode",
            _DB_PATH,
        )
    
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_database(conn: sqlite3.Connection) -> None:
    """
    Apply incremental schema migrations for databases that pre-date a column.
    Uses try/except because SQLite does not support ALTER TABLE IF NOT EXISTS.
    """
    migrations = [
        # Add 'regime' column to exchange_rates if it does not yet exist
        "ALTER TABLE exchange_rates ADD COLUMN regime TEXT NOT NULL DEFAULT 'Unclassified'",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
            logger.info("Migration applied: %s", sql[:60])
        except sqlite3.OperationalError:
            pass  # Column already exists — skip silently


def init_database() -> bool:
    """
    Run schema.sql to create all tables if they do not yet exist, then
    apply any pending column migrations.

    Safe to call multiple times (idempotent).
    Returns True on success, False on failure.
    """
    if not _SCHEMA_PATH.exists():
        logger.error("schema.sql not found at %s", _SCHEMA_PATH)
        return False

    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")

    try:
        with get_connection() as conn:
            conn.executescript(schema_sql)
            conn.commit()
            _migrate_database(conn)
        logger.info("Database initialised at %s", _DB_PATH)
        return True
    except sqlite3.Error as exc:
        logger.exception("Failed to initialise database: %s", exc)
        return False


def get_table_counts() -> dict[str, int]:
    """Return row counts for all data tables."""
    tables = ["exchange_rates", "fuel_prices", "telecom_prices", "kpi_metrics", "pipeline_logs"]
    counts: dict[str, int] = {}
    try:
        with get_connection() as conn:
            for table in tables:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                counts[table] = row[0] if row else 0
    except sqlite3.Error as exc:
        logger.exception("Error fetching table counts: %s", exc)
    return counts


def check_database_status() -> dict[str, object]:
    """Return a status dict suitable for the dashboard pipeline panel."""
    try:
        counts = get_table_counts()
        total = sum(counts.values())
        return {
            "connected": True,
            "path": str(_DB_PATH),
            "total_records": total,
            "table_counts": counts,
        }
    except Exception as exc:
        return {
            "connected": False,
            "path": str(_DB_PATH),
            "total_records": 0,
            "table_counts": {},
            "error": str(exc),
        }
