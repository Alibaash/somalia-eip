-- Somalia Economic Intelligence Platform — Database Schema
-- SQLite

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Exchange rates: USD → SOS
-- regime column tags each record's currency era to prevent mixing incompatible series.
-- Somalia's World Bank data spans three structurally incompatible regimes:
--   Pre-War SSh (1960–1990): 6–490 SOS/USD (old fixed/pegged rate)
--   Post-War Parallel Market (2009–2017): ~19,000–31,000 SOS/USD (hyperinflation)
--   Reissued SOS / Official (2022+): ~560–571 SOS/USD (new central bank rate)
CREATE TABLE IF NOT EXISTS exchange_rates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    city        TEXT    DEFAULT 'National',
    rate        REAL    NOT NULL,
    regime      TEXT    NOT NULL DEFAULT 'Unclassified',
    source      TEXT    NOT NULL,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uix_exchange_rates
    ON exchange_rates (timestamp, city, source);

-- Fuel prices
CREATE TABLE IF NOT EXISTS fuel_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    city        TEXT    NOT NULL,
    fuel_type   TEXT    NOT NULL,
    price       REAL    NOT NULL,
    source      TEXT    NOT NULL,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uix_fuel_prices
    ON fuel_prices (timestamp, city, fuel_type, source);

-- Telecom prices
CREATE TABLE IF NOT EXISTS telecom_prices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    provider     TEXT    DEFAULT 'Unknown',
    service_type TEXT    NOT NULL,
    price        REAL    NOT NULL,
    source       TEXT    NOT NULL,
    created_at   TEXT    DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uix_telecom_prices
    ON telecom_prices (timestamp, provider, service_type, source);

-- Calculated KPI metrics (time-series snapshots)
CREATE TABLE IF NOT EXISTS kpi_metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    metric_name  TEXT    NOT NULL,
    metric_value REAL,
    created_at   TEXT    DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uix_kpi_metrics
    ON kpi_metrics (timestamp, metric_name);

-- Pipeline execution log
CREATE TABLE IF NOT EXISTS pipeline_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT    NOT NULL DEFAULT (datetime('now')),
    job_name   TEXT    NOT NULL,
    status     TEXT    NOT NULL CHECK (status IN ('SUCCESS', 'FAILED', 'WARNING', 'RUNNING')),
    message    TEXT,
    created_at TEXT    DEFAULT (datetime('now'))
);
