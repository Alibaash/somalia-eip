"""
Background scheduler for the Somalia Economic Intelligence Platform.

Runs ingestion and transform jobs on a configurable interval.
Execute from the project root:
    python run_scheduler.py

Uses the `schedule` library for simplicity and reliability.
"""

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import schedule

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from config import SCHEDULER_INTERVAL_MINUTES, LOG_DIR
from database.connection import init_database
from ingestion.exchange_rates import run_ingestion as ingest_fx
from ingestion.fuel_prices import run_ingestion as ingest_fuel
from ingestion.telecom_prices import run_ingestion as ingest_tel
from processing.transform import run_transforms
from processing.metrics import run_metrics

# ---------------------------------------------------------------------------
# Logging setup — also write to a rotating log file
# ---------------------------------------------------------------------------
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOG_DIR / "scheduler.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(log_file), encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduler")


def run_full_pipeline() -> None:
    """Execute all ingestion, transformation, and metrics jobs."""
    started_at = datetime.now(tz=timezone.utc).isoformat()
    logger.info("=== Pipeline run started at %s ===", started_at)

    jobs = [
        ("exchange_rates", ingest_fx),
        ("fuel_prices", ingest_fuel),
        ("telecom_prices", ingest_tel),
    ]

    for job_name, fn in jobs:
        try:
            result = fn()
            level = logging.INFO if result.get("status") in ("SUCCESS", "WARNING") else logging.ERROR
            logger.log(level, "[%s] %s", job_name, result.get("message", ""))
        except Exception as exc:
            logger.exception("Unhandled error in job '%s': %s", job_name, exc)

    # Data quality transforms
    try:
        transform_results = run_transforms()
        logger.info("Transforms complete: %s", transform_results)
    except Exception as exc:
        logger.exception("Transform pipeline failed: %s", exc)

    # KPI metrics
    try:
        metrics = run_metrics()
        risk = metrics.get("economic_risk_score")
        vol = metrics.get("composite_volatility_score")
        logger.info(
            "Metrics computed — Risk: %.3f | Volatility: %.3f",
            risk if risk is not None else -1,
            vol if vol is not None else -1,
        )
    except Exception as exc:
        logger.exception("Metrics computation failed: %s", exc)

    finished_at = datetime.now(tz=timezone.utc).isoformat()
    logger.info("=== Pipeline run finished at %s ===", finished_at)


def main() -> None:
    logger.info(
        "Scheduler starting — pipeline interval: %d minutes", SCHEDULER_INTERVAL_MINUTES
    )

    # Initialise database and run once immediately
    init_database()
    run_full_pipeline()

    # Schedule recurring runs
    schedule.every(SCHEDULER_INTERVAL_MINUTES).minutes.do(run_full_pipeline)

    logger.info("Scheduler active. Next run in %d minutes.", SCHEDULER_INTERVAL_MINUTES)

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)  # Check for pending jobs every 30 seconds
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user.")


if __name__ == "__main__":
    main()
