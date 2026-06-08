"""
Telecom Price Ingestion — Somalia

Primary source  : HDX / WFP Somalia food prices dataset
  WFP monitors mobile phone credit (airtime) as a proxy commodity in some
  markets. We search the same WFP dataset for telecom-related rows.

Secondary source: ITU ICT Price Basket — Somalia
  https://www.itu.int/en/ITU-D/Statistics/Pages/ICTpricebasket.aspx
  (No structured public API; document as unavailable.)

Behaviour:
  - Queries the HDX/WFP CSV for telecom-related commodity rows.
  - If none found, logs clearly and returns a WARNING result.
  - No synthetic data is ever inserted.
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

SOURCE_NAME = "HDX/WFP Somalia (Telecom Proxy)"
HDX_PACKAGE_URL = "https://data.humdata.org/api/3/action/package_show"
HDX_PACKAGE_ID = "wfp-food-prices-for-somalia"
TELECOM_KEYWORDS = {
    "mobile", "airtime", "phone", "telecom", "sms",
    "internet", "data", "credit", "sim", "hormuud",
    "somtel", "golis", "amtel",
}
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


def _get_csv_url() -> Optional[str]:
    """Retrieve the WFP Somalia CSV download URL from HDX CKAN."""
    try:
        resp = requests.get(
            HDX_PACKAGE_URL,
            params={"id": HDX_PACKAGE_ID},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("HDX CKAN request failed: %s", exc)
        return None

    if not data.get("success"):
        logger.error("HDX CKAN returned failure: %s", data.get("error"))
        return None

    resources = data.get("result", {}).get("resources", [])
    for r in resources:
        if r.get("format", "").upper() == "CSV":
            url = r.get("download_url") or r.get("url", "")
            if url:
                return url
    return None


def _search_telecom_rows(csv_url: str) -> pd.DataFrame:
    """Download WFP CSV and filter for telecom-related commodity rows."""
    try:
        resp = requests.get(csv_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        df = pd.read_csv(io.BytesIO(resp.content), low_memory=False)
    except Exception as exc:
        logger.error("CSV download/parse error: %s", exc)
        return pd.DataFrame()

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    commodity_col = next((c for c in df.columns if "commodity" in c), None)
    if not commodity_col:
        return pd.DataFrame()

    mask = df[commodity_col].str.lower().str.contains(
        "|".join(TELECOM_KEYWORDS), na=False
    )
    result = df[mask].copy()
    logger.info("Found %d telecom-related rows in WFP dataset.", len(result))
    return result


def _normalise(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []

    col_date = next((c for c in df.columns if c == "date"), None)
    col_city = next(
        (c for c in ["adm1_name", "admin1", "market", "adm1"] if c in df.columns), None
    )
    col_commodity = next((c for c in df.columns if "commodity" in c), None)
    col_price = next(
        (c for c in ["usdprice", "price_usd", "price"] if c in df.columns), None
    )

    if not col_date or not col_commodity or not col_price:
        logger.error("Required columns missing for telecom normalisation.")
        return []

    records: list[dict] = []
    for _, row in df.iterrows():
        try:
            ts = pd.to_datetime(row[col_date], errors="coerce")
            if pd.isnull(ts):
                continue
            price_val = float(row[col_price]) if pd.notna(row[col_price]) else None
            if price_val is None or price_val <= 0:
                continue

            provider = "WFP Monitored Market"
            service_type = str(row[col_commodity]).strip() if pd.notna(row[col_commodity]) else "Telecom"
            city = str(row[col_city]).strip() if col_city and pd.notna(row[col_city]) else "Somalia"

            records.append(
                {
                    "timestamp": ts.isoformat(),
                    "provider": provider,
                    "service_type": f"{service_type} ({city})",
                    "price": price_val,
                    "source": SOURCE_NAME,
                }
            )
        except (ValueError, KeyError, TypeError):
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
                    INSERT OR IGNORE INTO telecom_prices
                        (timestamp, provider, service_type, price, source)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        rec["timestamp"],
                        rec["provider"],
                        rec["service_type"],
                        rec["price"],
                        rec["source"],
                    ),
                )
                inserted += cursor.rowcount
            conn.commit()
    except sqlite3.Error as exc:
        logger.exception("Database insert failed for telecom_prices: %s", exc)

    return inserted


def run_ingestion() -> dict[str, object]:
    """Execute the full telecom price ingestion pipeline."""
    job_name = "telecom_prices"
    init_database()
    _log_pipeline(job_name, "RUNNING", "Telecom price ingestion started.")

    csv_url = _get_csv_url()
    if not csv_url:
        msg = (
            "Could not retrieve HDX/WFP Somalia CSV. "
            "Note: Dedicated Somalia telecom price APIs (ITU, GSMA) require "
            "registration and are not publicly accessible. "
            "WFP CSV is the only available proxy source."
        )
        logger.warning(msg)
        _log_pipeline(job_name, "WARNING", msg)
        return {"job": job_name, "status": "WARNING", "records_fetched": 0,
                "records_inserted": 0, "message": msg}

    df = _search_telecom_rows(csv_url)
    if df.empty:
        msg = (
            "No telecom-related commodity rows found in WFP Somalia dataset. "
            "WFP does not currently monitor airtime/mobile credit prices in Somalia."
        )
        logger.warning(msg)
        _log_pipeline(job_name, "WARNING", msg)
        return {"job": job_name, "status": "WARNING", "records_fetched": 0,
                "records_inserted": 0, "message": msg}

    records = _normalise(df)
    inserted = _insert_records(records)
    msg = f"Fetched {len(records)} telecom rows; inserted {inserted} new rows."
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
    print(run_ingestion())
