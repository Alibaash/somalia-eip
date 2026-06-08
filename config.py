"""
Project-wide configuration for the Somalia Economic Intelligence Platform.
All paths are relative to the project root (directory of this file).

Cloud compatibility: Detects read-only environments (Streamlit Cloud) and uses
ephemeral temp storage for database instead of the read-only repo checkout.
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Project root is the directory containing this file
PROJECT_ROOT: Path = Path(__file__).parent.resolve()

# Database path with Cloud fallback
def _get_database_path() -> Path:
    """
    Determine a writable database path.
    
    In Streamlit Cloud (read-only repo), falls back to system temp directory.
    In local/Codespaces, uses project-local path.
    """
    # Check environment override first (for explicit Cloud configuration)
    if env_db_path := os.getenv('SOMALIA_EIP_DB_PATH'):
        return Path(env_db_path)
    
    # Try project-local path first (local development, Codespaces)
    local_db_dir = PROJECT_ROOT / "database"
    local_db_path = local_db_dir / "somalia_eip.db"
    
    # Test if we can write to the local directory
    try:
        local_db_dir.mkdir(parents=True, exist_ok=True)
        # Verify by testing write access (in case dir exists but is read-only)
        test_file = local_db_dir / ".write_test"
        test_file.touch()
        test_file.unlink()
        return local_db_path
    except (OSError, PermissionError):
        # Fall back to temp directory (Streamlit Cloud or read-only environment)
        temp_path = Path(tempfile.gettempdir()) / "somalia_eip.db"
        if local_db_path.exists() and not temp_path.exists():
            try:
                shutil.copy2(local_db_path, temp_path)
                logger.info(
                    "Local database path %s is not writable. Copied repository DB to ephemeral path: %s",
                    local_db_path,
                    temp_path,
                )
            except Exception as exc:
                logger.exception(
                    "Failed to copy read-only repo database to temp path: %s",
                    exc,
                )
        else:
            logger.info(
                "Local database path %s is not writable. Using ephemeral path: %s "
                "(Streamlit Cloud or read-only environment detected)",
                local_db_path,
                temp_path,
            )
        return temp_path

DATABASE_PATH: Path = _get_database_path()

# Data directories (attempted at import, but failure is non-fatal)
DATA_RAW_DIR: Path = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED_DIR: Path = PROJECT_ROOT / "data" / "processed"

# Logs (attempted at import, but failure is non-fatal)
LOG_DIR: Path = PROJECT_ROOT / "logs"

# Application
APP_VERSION: str = "1.0.0"
APP_NAME: str = "Somalia Economic Intelligence Platform"

# Scheduler: how often to run each ingestion job (in minutes)
SCHEDULER_INTERVAL_MINUTES: int = 60

# Data sources documentation
DATA_SOURCES: dict[str, str] = {
    "World Bank": "https://api.worldbank.org/v2/country/SO/indicator/PA.NUS.FCRF",
    "HDX / WFP Somalia": "https://data.humdata.org/dataset/wfp-food-prices-for-somalia",
    "WFP VAM": "https://dataviz.vam.wfp.org",
}

# Ensure required directories exist at import time (non-fatal if read-only)
for _dir in (DATA_RAW_DIR, DATA_PROCESSED_DIR, LOG_DIR):
    try:
        _dir.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError):
        # Silently skip if read-only; these are optional for Cloud deployment
        pass
