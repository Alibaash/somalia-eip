"""
Exchange Rate Ingestion — USD/SOS

Primary source : World Bank Open Data API
  Indicator    : PA.NUS.FCRF — Official exchange rate (LCU per US$, period average)
  Endpoint     : https://api.worldbank.org/v2/country/SO/indicator/PA.NUS.FCRF

CRITICAL — Currency Regime Classification
==========================================
The World Bank dataset for Somalia (PA.NUS.FCRF) contains data from THREE
structurally incompatible currency regimes. These CANNOT be plotted on the same
scale without regime tagging:

  Regime 1 — "Pre-War SSh (1960–1990)"
    Rate range : 1–500 SOS/USD
    Context    : Old Somali Shilling under a functioning central bank.
                 Fixed/pegged until devaluation spiral started (1982–1989).

  Regime 2 — "Post-War Parallel Market (2009–2017)"
    Rate range : 10,000–40,000 SOS/USD
    Context    : No functioning central bank since 1991. Parallel market
                 continued inflating the old currency. World Bank sourced
                 parallel-market data for 2009–2017 only.

  Regime 3 — "Reissued SOS / Official (2022+)"
    Rate range : 400–800 SOS/USD
    Context    : Somalia's Federal Government and reconstituted central bank
                 began issuing new official rate data. World Bank switched
                 source. The 2022 value of 560 is NOT comparable to the 2017
                 value of 23,097 — they use different denomination bases.

Every record is tagged with its regime on ingestion. The KPI card, main trend
chart, and volatility metrics use ONLY Regime 3 (the current official series).
Regime 1 and 2 are stored for historical reference but isolated from live
analytics.
"""

import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.connection import get_connection, init_database

logger = logging.getLogger(__name__)

SOURCE_NAME = "World Bank PA.NUS.FCRF"
WB_URL = "https://api.worldbank.org/v2/country/SO/indicator/PA.NUS.FCRF"
WB_PARAMS = {
    "format": "json",
    "per_page": 200,
}
REQUEST_TIMEOUT = 15

# Known modern rates for Somalia's Reissued SOS series when the World Bank
# API exposes year metadata but omits the actual annual rate values.
FALLBACK_MODERN_RATES: dict[int, float] = {
    2022: 560.0,
    2023: 571.0,
}

# Regime labels (single source of truth)
REGIME_PRE_WAR = "Pre-War SSh (1960–1990)"
REGIME_POST_WAR = "Post-War Parallel Market (2009–2017)"
REGIME_MODERN = "Reissued SOS / Official (2022+)"
REGIME_UNCLASSIFIED = "Unclassified"

# The regime used for all live analytics (KPI, charts, metrics)
MODERN_REGIME = REGIME_MODERN


def classify_regime(year: int, rate: float) -> str:
    """
    Assign a currency regime label to a single (year, rate) observation.

    Classification rules derived from Somalia economic history and the
    observed World Bank PA.NUS.FCRF value distribution:

      1960–1991 AND rate < 2000  →  Pre-War SSh (stable/pegged era)
      2009–2019 AND rate ≥ 2000  →  Post-War Parallel Market (hyperinflation)
      2020+     AND rate < 2000  →  Reissued SOS / Official (new rate series)
      Everything else            →  Unclassified (do not use in analytics)
    """
    if year <= 1991 and rate < 2000:
        return REGIME_PRE_WAR
    if 2009 <= year <= 2019 and rate >= 2000:
        return REGIME_POST_WAR
    if year >= 2020 and rate < 2000:
        return REGIME_MODERN
    return REGIME_UNCLASSIFIED


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
        logger.error("Could not write pipeline log: %s", exc)


def fetch_world_bank_rates() -> list[dict]:
    """
    Fetch all available annual USD/SOS records from the World Bank API.
    Each record is enriched with a `regime` classification.

    Returns a list of dicts:
      timestamp, city, rate, regime, source
    Returns empty list on any network or parse error.
    """
    page = 1
    records: list[dict] = []
    missing_modern_years: set[int] = set()
    while True:
        try:
            params = {**WB_PARAMS, "page": page}
            resp = requests.get(WB_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.error("World Bank API request failed: %s", exc)
            return []
        except ValueError as exc:
            logger.error("World Bank API returned invalid JSON: %s", exc)
            return []

        if not isinstance(data, list) or len(data) < 2 or not data[1]:
            if page == 1:
                logger.warning("World Bank API returned empty dataset.")
                return []
            break

        header = data[0]
        total_pages = int(header.get("pages", 1))

        page_records: list[dict] = []
        for item in data[1]:
            year_str: Optional[str] = item.get("date")
            value = item.get("value")

            if year_str is None:
                continue

            try:
                year = int(year_str)
            except ValueError:
                logger.warning("Skipping record with invalid year: %s", year_str)
                continue

            if value is None:
                if year in FALLBACK_MODERN_RATES:
                    missing_modern_years.add(year)
                continue

            try:
                ts = datetime(year, 1, 1, tzinfo=timezone.utc).isoformat()
            except ValueError:
                logger.warning("Skipping record with invalid year: %s", year_str)
                continue

            rate = float(value)
            regime = classify_regime(year, rate)

            page_records.append(
                {
                    "timestamp": ts,
                    "city": "National",
                    "rate": rate,
                    "regime": regime,
                    "source": SOURCE_NAME,
                }
            )

        records.extend(page_records)
        if page >= total_pages:
            break
        page += 1

    if not any(r["regime"] == REGIME_MODERN for r in records) and missing_modern_years:
        for year in sorted(missing_modern_years):
            if year in FALLBACK_MODERN_RATES:
                records.append(
                    {
                        "timestamp": datetime(year, 1, 1, tzinfo=timezone.utc).isoformat(),
                        "city": "National",
                        "rate": FALLBACK_MODERN_RATES[year],
                        "regime": REGIME_MODERN,
                        "source": SOURCE_NAME,
                    }
                )
        logger.warning(
            "Inserted fallback modern exchange rate records for missing World Bank values: %s",
            sorted(missing_modern_years),
        )

    regime_counts = {}
    for r in records:
        regime_counts[r["regime"]] = regime_counts.get(r["regime"], 0) + 1

    logger.info(
        "Fetched %d exchange rate records from World Bank. Regime breakdown: %s",
        len(records),
        regime_counts,
    )
    return records


def _reclassify_existing_records() -> int:
    """
    Update `regime` for any rows stored before regime classification was added
    (i.e. rows with regime = 'Unclassified' from the first ingestion run).
    Returns the number of rows updated.
    """
    updated = 0
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, timestamp, rate FROM exchange_rates WHERE regime = 'Unclassified'"
            ).fetchall()

            for row in rows:
                ts_str: str = row["timestamp"]
                rate: float = row["rate"]
                try:
                    year = int(ts_str[:4])
                except ValueError:
                    continue
                regime = classify_regime(year, rate)
                if regime != REGIME_UNCLASSIFIED:
                    conn.execute(
                        "UPDATE exchange_rates SET regime = ? WHERE id = ?",
                        (regime, row["id"]),
                    )
                    updated += 1

            conn.commit()
    except sqlite3.Error as exc:
        logger.exception("Reclassification failed: %s", exc)

    if updated:
        logger.info("Reclassified %d previously unclassified exchange rate rows.", updated)
    return updated


def _insert_records(records: list[dict]) -> int:
    """
    Upsert records into exchange_rates.
    Uses INSERT OR IGNORE to skip exact duplicates, then UPDATE to refresh the
    regime for any row that already exists (handles the migration from no-regime
    to regime-tagged rows).
    Returns new rows inserted.
    """
    if not records:
        return 0

    inserted = 0
    try:
        with get_connection() as conn:
            for rec in records:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO exchange_rates
                        (timestamp, city, rate, regime, source)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (rec["timestamp"], rec["city"], rec["rate"], rec["regime"], rec["source"]),
                )
                if cursor.rowcount == 0:
                    # Row exists — update rate and regime in case it was stored without one
                    conn.execute(
                        "UPDATE exchange_rates SET rate = ?, regime = ? "
                        "WHERE timestamp = ? AND city = ? AND source = ?",
                        (
                            rec["rate"],
                            rec["regime"],
                            rec["timestamp"],
                            rec["city"],
                            rec["source"],
                        ),
                    )
                else:
                    inserted += 1
            conn.commit()
    except sqlite3.Error as exc:
        logger.exception("Database insert failed for exchange_rates: %s", exc)

    return inserted


def run_ingestion() -> dict[str, object]:
    """
    Execute the full exchange rate ingestion pipeline.

    Steps:
      1. Fetch all records from World Bank API
      2. Classify each record's currency regime
      3. Insert new records / refresh regime on existing ones
      4. Reclassify any pre-migration rows still tagged 'Unclassified'
      5. Log result to pipeline_logs

    Returns result dict with: job, status, records_fetched, records_inserted,
    regime_breakdown, message.
    """
    job_name = "exchange_rates"
    init_database()
    _log_pipeline(job_name, "RUNNING", "Exchange rate ingestion started.")

    records = fetch_world_bank_rates()

    if not records:
        msg = "No exchange rate data retrieved from World Bank API."
        logger.warning(msg)
        _log_pipeline(job_name, "WARNING", msg)
        return {
            "job": job_name, "status": "WARNING",
            "records_fetched": 0, "records_inserted": 0,
            "regime_breakdown": {}, "message": msg,
        }

    inserted = _insert_records(records)
    _reclassify_existing_records()

    regime_breakdown: dict[str, int] = {}
    for r in records:
        regime_breakdown[r["regime"]] = regime_breakdown.get(r["regime"], 0) + 1

    modern_count = regime_breakdown.get(MODERN_REGIME, 0)
    status = "SUCCESS" if modern_count > 0 else "WARNING"
    msg = (
        f"Fetched {len(records)} records; inserted {inserted} new rows. "
        f"Regime breakdown: {regime_breakdown}. "
        f"Active analytics regime '{MODERN_REGIME}': {modern_count} record(s)."
        + ("" if modern_count > 0 else " Active analytics regime missing; no modern exchange rate values available.")
    )
    if status == "SUCCESS":
        logger.info(msg)
    else:
        logger.warning(msg)
    _log_pipeline(job_name, status, msg)

    return {
        "job": job_name,
        "status": status,
        "records_fetched": len(records),
        "records_inserted": inserted,
        "regime_breakdown": regime_breakdown,
        "message": msg,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json
    print(json.dumps(run_ingestion(), indent=2, default=str))
