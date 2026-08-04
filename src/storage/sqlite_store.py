"""SQLite-backed persistent storage for detections and spectrum history.

Replaces the in-memory-only approach so that:
- Detections survive a process restart.
- Long-term trend analysis is possible.
- Grafana (via the SQLite data-source plugin) can visualise historical data.

Schema
------
``detections`` table – one row per confirmed :class:`DetectedSignal`:
    id, unit_id, center_freq_mhz, bandwidth_khz, peak_power_dbm,
    first_seen, last_seen, persistence_s, lat, lon, alt_m, signal_class

``spectrum_snapshots`` table – compressed power arrays (optional, off by default
to keep the DB lean; enable with ``store_snapshots=True``):
    id, unit_id, center_freq_hz, timestamp, power_json

Usage::

    from src.storage.sqlite_store import DetectionStore

    store = DetectionStore("data/rf_monitor.db")
    store.open()
    store.save_detection(detected_signal)
    recent = store.query_detections(last_n_hours=24)
    store.close()
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from ..analyzer.signal_detector import DetectedSignal

logger = logging.getLogger(__name__)

# DDL
_CREATE_DETECTIONS = """
CREATE TABLE IF NOT EXISTS detections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id         TEXT    NOT NULL,
    center_freq_mhz REAL    NOT NULL,
    bandwidth_khz   REAL    NOT NULL,
    peak_power_dbm  REAL    NOT NULL,
    first_seen      REAL    NOT NULL,
    last_seen       REAL    NOT NULL,
    persistence_s   REAL    NOT NULL,
    lat             REAL,
    lon             REAL,
    alt_m           REAL,
    signal_class    TEXT    NOT NULL DEFAULT 'unknown',
    severity        TEXT    NOT NULL DEFAULT 'info'
);
"""

_CREATE_DETECTIONS_IDX = """
CREATE INDEX IF NOT EXISTS idx_detections_last_seen
    ON detections (last_seen DESC);
"""

_CREATE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS spectrum_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id         TEXT    NOT NULL,
    center_freq_hz  REAL    NOT NULL,
    timestamp       REAL    NOT NULL,
    power_json      TEXT    NOT NULL
);
"""

_CREATE_SNAPSHOTS_IDX = """
CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
    ON spectrum_snapshots (timestamp DESC);
"""


class DetectionStore:
    """Thread-safe SQLite store for RF detections and spectrum snapshots.

    Args:
        db_path: Path to the SQLite database file.
            Intermediate directories are created automatically.
        store_snapshots: If ``True``, also persist spectrum power arrays.
            Increases disk usage significantly; useful for Grafana waterfall.
        snapshot_interval_s: Minimum seconds between stored snapshots per unit.
    """

    def __init__(
        self,
        db_path: str = "data/rf_monitor.db",
        store_snapshots: bool = False,
        snapshot_interval_s: float = 5.0,
    ) -> None:
        self.db_path = db_path
        self.store_snapshots = store_snapshots
        self.snapshot_interval_s = snapshot_interval_s

        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        # { unit_id -> last_snapshot_ts }
        self._last_snapshot: dict = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open (or create) the database and run DDL migrations."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,  # We use our own lock
            timeout=10,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                _CREATE_DETECTIONS
                + _CREATE_DETECTIONS_IDX
                + (_CREATE_SNAPSHOTS + _CREATE_SNAPSHOTS_IDX if self.store_snapshots else "")
            )
            self._conn.commit()
        logger.info("DetectionStore: opened %s", self.db_path)

    def close(self) -> None:
        """Flush and close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("DetectionStore: closed.")

    def __enter__(self) -> "DetectionStore":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def save_detection(
        self,
        detection: "DetectedSignal",
        severity: str = "info",
    ) -> int:
        """Persist a confirmed detection.

        Args:
            detection: The :class:`~src.analyzer.signal_detector.DetectedSignal`
                to store.
            severity: Alert severity string (``"info"``, ``"warning"``,
                ``"critical"``).

        Returns:
            Row ID of the inserted row.
        """
        if self._conn is None:
            raise RuntimeError("DetectionStore is not open.")

        loc = detection.location
        lat = loc[0] if loc else None
        lon = loc[1] if loc else None
        alt = loc[2] if loc else None

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO detections
                    (unit_id, center_freq_mhz, bandwidth_khz, peak_power_dbm,
                     first_seen, last_seen, persistence_s, lat, lon, alt_m,
                     signal_class, severity)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    detection.unit_id,
                    detection.center_freq_hz / 1e6,
                    detection.bandwidth_hz / 1e3,
                    detection.peak_power_db,
                    detection.first_seen,
                    detection.last_seen,
                    detection.persistence_s,
                    lat,
                    lon,
                    alt,
                    detection.signal_class,
                    severity,
                ),
            )
            self._conn.commit()
            row_id = cur.lastrowid
        logger.debug(
            "DetectionStore: saved detection #%d (%.3f MHz, %s).",
            row_id,
            detection.center_freq_hz / 1e6,
            detection.signal_class,
        )
        return row_id

    def save_snapshot(
        self,
        unit_id: str,
        center_freq_hz: float,
        power_db: List[float],
        timestamp: Optional[float] = None,
    ) -> None:
        """Persist a spectrum snapshot (only if ``store_snapshots=True``).

        Rate-limited by ``snapshot_interval_s`` per unit to keep the DB lean.

        Args:
            unit_id: Scanner unit identifier.
            center_freq_hz: Centre frequency in Hz.
            power_db: PSD values in dBm.
            timestamp: Unix epoch; defaults to ``time.time()``.
        """
        if not self.store_snapshots or self._conn is None:
            return

        now = timestamp or time.time()
        last = self._last_snapshot.get(unit_id, 0.0)
        if now - last < self.snapshot_interval_s:
            return
        self._last_snapshot[unit_id] = now

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO spectrum_snapshots (unit_id, center_freq_hz, timestamp, power_json) "
                "VALUES (?,?,?,?)",
                (unit_id, center_freq_hz, now, json.dumps(power_db)),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def query_detections(
        self,
        last_n_hours: float = 24.0,
        unit_id: Optional[str] = None,
        signal_class: Optional[str] = None,
        min_severity: Optional[str] = None,
        limit: int = 1000,
    ) -> List[dict]:
        """Query stored detections with optional filters.

        Args:
            last_n_hours: Only return detections from the past N hours.
            unit_id: Filter by monitoring unit ID.
            signal_class: Filter by signal classification.
            min_severity: Minimum severity (``"info"`` < ``"warning"`` < ``"critical"``).
            limit: Maximum rows to return.

        Returns:
            List of detection dicts (column name → value).
        """
        if self._conn is None:
            raise RuntimeError("DetectionStore is not open.")

        cutoff = time.time() - last_n_hours * 3600.0
        sql = "SELECT * FROM detections WHERE last_seen >= ?"
        params: list = [cutoff]

        if unit_id:
            sql += " AND unit_id = ?"
            params.append(unit_id)
        if signal_class:
            sql += " AND signal_class = ?"
            params.append(signal_class)
        if min_severity:
            severity_order = {"info": 0, "warning": 1, "critical": 2, "emergency": 3}
            min_rank = severity_order.get(min_severity, 0)
            qualified = [s for s, r in severity_order.items() if r >= min_rank]
            sql += " AND severity IN ({})".format(",".join("?" * len(qualified)))
            params.extend(qualified)

        sql += " ORDER BY last_seen DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        return [dict(row) for row in rows]

    def get_detection_stats(self) -> dict:
        """Return aggregate statistics over all stored detections.

        Returns:
            Dict with keys ``total``, ``by_class``, ``by_unit``,
            ``last_24h``.
        """
        if self._conn is None:
            raise RuntimeError("DetectionStore is not open.")

        cutoff_24h = time.time() - 86400.0
        with self._lock:
            cur = self._conn.cursor()

            cur.execute("SELECT COUNT(*) FROM detections")
            total = cur.fetchone()[0]

            cur.execute(
                "SELECT signal_class, COUNT(*) AS n FROM detections GROUP BY signal_class ORDER BY n DESC"
            )
            by_class = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute(
                "SELECT unit_id, COUNT(*) AS n FROM detections GROUP BY unit_id ORDER BY n DESC"
            )
            by_unit = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute(
                "SELECT COUNT(*) FROM detections WHERE last_seen >= ?", (cutoff_24h,)
            )
            last_24h = cur.fetchone()[0]

        return {
            "total": total,
            "by_class": by_class,
            "by_unit": by_unit,
            "last_24h": last_24h,
        }

    def purge_old_records(self, older_than_days: int = 90) -> int:
        """Delete records older than *older_than_days*.

        Args:
            older_than_days: Records with ``last_seen`` older than this many
                days are deleted.

        Returns:
            Number of rows deleted.
        """
        if self._conn is None:
            raise RuntimeError("DetectionStore is not open.")

        cutoff = time.time() - older_than_days * 86400.0
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM detections WHERE last_seen < ?", (cutoff,))
            deleted = cur.rowcount
            self._conn.commit()

        logger.info("DetectionStore: purged %d records older than %d days.", deleted, older_than_days)
        return deleted
