"""
Fuel Price Ingestion — Somalia

Primary source : HDX / WFP — WFP Food Prices for Somalia
  Package ID   : wfp-food-prices-for-somalia
  CKAN API     : https://data.humdata.org/api/3/action/package_show?id=wfp-food-prices-for-somalia

The HDX CKAN API is queried to discover the data resource, then the CSV is
downloaded and filtered for fuel-related commodities (diesel, petrol, kerosene).

Fallback      : Graceful error logging; no fake data inserted.
"""

import io
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.connection import get_connection, init_database

logger = logging.getLogger(__name__)

SOURCE_NAME = "HDX/WFP Somalia Food Prices"
HDX_PACKAGE_URL = "https://data.humdata.org/api/3/action/package_show"
HDX_PACKAGE_ID = "wfp-food-prices-for-somalia"
FUEL_KEYWORDS = {"fuel", "diesel", "petrol", "kerosene", "benzine", "benzene", "gasoline"}
REQUEST_TIMEOUT = 30


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


def _get_csv_resource_url() -> Optional[str]:
    """
    Query the HDX CKAN API to find the direct download URL for the
    WFP Somalia prices CSV resource.
    """
    try:
        resp = requests.get(
            HDX_PACKAGE_URL,
            params={"id": HDX_PACKAGE_ID},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("HDX CKAN API request failed: %s", exc)
        return None
    except ValueError as exc:
        logger.error("HDX CKAN API returned invalid JSON: %s", exc)
        return None

    if not data.get("success"):
        logger.error("HDX CKAN API returned failure: %s", data.get("error"))
        return None

    resources: list[dict] = data.get("result", {}).get("resources", [])
    for resource in resources:
        fmt = resource.get("format", "").upper()
        url = resource.get("download_url") or resource.get("url", "")
        if fmt == "CSV" and url:
            logger.info("Found HDX CSV resource: %s", url)
            return url

    logger.warning("No CSV resource found in HDX package '%s'.", HDX_PACKAGE_ID)
    return None


def _download_and_filter_csv(csv_url: str) -> pd.DataFrame:
    """
    Download the WFP Somalia prices CSV and return rows related to fuel.
    """
    try:
        resp = requests.get(csv_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to download CSV from %s: %s", csv_url, exc)
        return pd.DataFrame()

    try:
        df = pd.read_csv(io.BytesIO(resp.content), low_memory=False)
    except Exception as exc:
        logger.error("Failed to parse CSV: %s", exc)
        return pd.DataFrame()

    # Normalise column names (lowercase, strip spaces)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Identify commodity column (varies slightly across WFP datasets)
    commodity_col = next(
        (c for c in df.columns if "commodity" in c),
        None,
    )
    if commodity_col is None:
        logger.error("CSV missing commodity column. Columns: %s", list(df.columns))
        return pd.DataFrame()

    # Filter for fuel commodities
    mask = df[commodity_col].str.lower().str.contains(
        "|".join(FUEL_KEYWORDS), na=False
    )
    fuel_df = df[mask].copy()
    logger.info("Found %d fuel-related rows in WFP Somalia dataset.", len(fuel_df))
    return fuel_df


def _normalise_fuel_df(df: pd.DataFrame) -> list[dict]:
    """
    Map WFP CSV columns to the fuel_prices table schema.
    Expected WFP columns (approximate):
        date | adm1_name | market | category | commodity | unit |
        priceflag | pricetype | currency | price | usdprice
    """
    if df.empty:
        return []

    col_map = {
        "date": ["date"],
        "city": ["adm1_name", "admin1", "market", "adm1"],
        "commodity": ["commodity", "commodity_name"],
        "price": ["usdprice", "price_usd", "price"],
    }

    def pick(candidates: list[str]) -> Optional[str]:
        return next((c for c in candidates if c in df.columns), None)

    date_col = pick(col_map["date"])
    city_col = pick(col_map["city"])
    commodity_col = pick(col_map["commodity"])
    price_col = pick(col_map["price"])

    if not all([date_col, commodity_col, price_col]):
        logger.error(
            "Could not map required columns. Available: %s", list(df.columns)
        )
        return []

    records: list[dict] = []
    for _, row in df.iterrows():
        try:
            ts = pd.to_datetime(row[date_col], errors="coerce")
            if pd.isnull(ts):
                continue
            price_val = float(row[price_col]) if pd.notna(row[price_col]) else None
            if price_val is None or price_val <= 0:
                continue

            city = str(row[city_col]).strip() if city_col and pd.notna(row[city_col]) else "Somalia"
            fuel_type = str(row[commodity_col]).strip() if pd.notna(row[commodity_col]) else "Fuel"

            records.append(
                {
                    "timestamp": ts.isoformat(),
                    "city": city,
                    "fuel_type": fuel_type,
                    "price": price_val,
                    "source": SOURCE_NAME,
                }
            )
        except (ValueError, KeyError, TypeError) as exc:
            logger.debug("Skipping row due to error: %s", exc)
            continue

    return records


def _insert_records(records: list[dict]) -> int:
    if not records:
        return 0

    inserted = 0
    try:
        with get_connection() as conn:
            for rec in records:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO fuel_prices
                        (timestamp, city, fuel_type, price, source)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        rec["timestamp"],
                        rec["city"],
                        rec["fuel_type"],
                        rec["price"],
                        rec["source"],
                    ),
                )
                inserted += cursor.rowcount
            conn.commit()
    except sqlite3.Error as exc:
        logger.exception("Database insert failed for fuel_prices: %s", exc)

    return inserted


def run_ingestion() -> dict[str, object]:
    """Execute the full fuel price ingestion pipeline."""
    job_name = "fuel_prices"
    init_database()
    _log_pipeline(job_name, "RUNNING", "Fuel price ingestion started.")

    csv_url = _get_csv_resource_url()
    if not csv_url:
        msg = "Could not locate HDX/WFP Somalia CSV resource URL."
        logger.warning(msg)
        _log_pipeline(job_name, "WARNING", msg)
        return {"job": job_name, "status": "WARNING", "records_fetched": 0,
                "records_inserted": 0, "message": msg}

    fuel_df = _download_and_filter_csv(csv_url)
    if fuel_df.empty:
        msg = "No fuel price rows found in WFP Somalia dataset."
        logger.warning(msg)
        _log_pipeline(job_name, "WARNING", msg)
        return {"job": job_name, "status": "WARNING", "records_fetched": 0,
                "records_inserted": 0, "message": msg}

    records = _normalise_fuel_df(fuel_df)
    inserted = _insert_records(records)

    msg = (
        f"Fetched {len(records)} fuel price records from HDX/WFP; "
        f"inserted {inserted} new rows."
    )
    logger.info(msg)
    _log_pipeline(job_name, "SUCCESS", msg)

    return {
        "job": job_name,
        "status": "SUCCESS",
        "records_fetched": len(records),
        "records_inserted": inserted,
        "message": msg,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_ingestion()
    print(result)
