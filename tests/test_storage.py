"""Tests for the SQLite persistence store."""

import os
import time

import pytest

from src.storage.sqlite_store import DetectionStore
from src.analyzer.signal_detector import DetectedSignal


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_detection(
    unit_id="u0",
    freq_hz=2_450_000_000,
    bw_hz=10_000_000,
    power=-72.0,
    signal_class="drone_control_2.4GHz",
    location=None,
):
    return DetectedSignal(
        unit_id=unit_id,
        center_freq_hz=freq_hz,
        bandwidth_hz=bw_hz,
        peak_power_db=power,
        first_seen=time.time() - 30,
        last_seen=time.time(),
        persistence_s=30.0,
        location=location,
        signal_class=signal_class,
    )


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

class TestDetectionStoreLifecycle:
    def test_open_close(self, tmp_path):
        store = DetectionStore(db_path=str(tmp_path / "test.db"))
        store.open()
        assert store._conn is not None
        store.close()
        assert store._conn is None

    def test_context_manager(self, tmp_path):
        with DetectionStore(db_path=str(tmp_path / "test.db")) as store:
            assert store._conn is not None

    def test_save_requires_open(self, tmp_path):
        store = DetectionStore(db_path=str(tmp_path / "test.db"))
        with pytest.raises(RuntimeError, match="not open"):
            store.save_detection(_make_detection())

    def test_creates_db_file(self, tmp_path):
        db = str(tmp_path / "subdir" / "rf.db")
        with DetectionStore(db_path=db) as store:
            pass
        assert os.path.exists(db)


# ---------------------------------------------------------------------------
# Write / Read tests
# ---------------------------------------------------------------------------

class TestDetectionStoreReadWrite:
    def setup_method(self, tmp_path=None):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.store = DetectionStore(db_path=os.path.join(self._tmp, "test.db"))
        self.store.open()

    def teardown_method(self):
        self.store.close()

    def test_save_detection_returns_row_id(self):
        row_id = self.store.save_detection(_make_detection())
        assert isinstance(row_id, int)
        assert row_id >= 1

    def test_query_returns_saved_detection(self):
        self.store.save_detection(_make_detection(unit_id="u1"))
        rows = self.store.query_detections(last_n_hours=1)
        assert len(rows) == 1
        assert rows[0]["unit_id"] == "u1"

    def test_query_filter_by_unit_id(self):
        self.store.save_detection(_make_detection(unit_id="A"))
        self.store.save_detection(_make_detection(unit_id="B"))
        rows = self.store.query_detections(unit_id="A")
        assert all(r["unit_id"] == "A" for r in rows)

    def test_query_filter_by_signal_class(self):
        self.store.save_detection(_make_detection(signal_class="gps_jammer"))
        self.store.save_detection(_make_detection(signal_class="drone_control_2.4GHz"))
        rows = self.store.query_detections(signal_class="gps_jammer")
        assert len(rows) == 1
        assert rows[0]["signal_class"] == "gps_jammer"

    def test_query_with_location(self):
        det = _make_detection(location=(38.9, -77.0, 10.0))
        self.store.save_detection(det)
        rows = self.store.query_detections()
        assert rows[0]["lat"] == pytest.approx(38.9)
        assert rows[0]["lon"] == pytest.approx(-77.0)

    def test_query_no_location_null(self):
        self.store.save_detection(_make_detection(location=None))
        rows = self.store.query_detections()
        assert rows[0]["lat"] is None

    def test_query_limit(self):
        for _ in range(10):
            self.store.save_detection(_make_detection())
        rows = self.store.query_detections(limit=5)
        assert len(rows) == 5

    def test_query_last_n_hours_filters_old(self):
        # Save a very old detection manually by adjusting last_seen
        det = _make_detection()
        det.last_seen = time.time() - 48 * 3600  # 48 hours ago
        det.first_seen = det.last_seen - 30
        self.store.save_detection(det)
        rows = self.store.query_detections(last_n_hours=24)
        assert len(rows) == 0

    def test_severity_stored(self):
        self.store.save_detection(_make_detection(), severity="critical")
        rows = self.store.query_detections()
        assert rows[0]["severity"] == "critical"

    def test_get_stats_total(self):
        for _ in range(3):
            self.store.save_detection(_make_detection())
        stats = self.store.get_detection_stats()
        assert stats["total"] == 3

    def test_get_stats_by_class(self):
        self.store.save_detection(_make_detection(signal_class="gps_jammer"))
        self.store.save_detection(_make_detection(signal_class="gps_jammer"))
        self.store.save_detection(_make_detection(signal_class="drone_control_2.4GHz"))
        stats = self.store.get_detection_stats()
        assert stats["by_class"]["gps_jammer"] == 2
        assert stats["by_class"]["drone_control_2.4GHz"] == 1

    def test_purge_old_records(self):
        det = _make_detection()
        det.last_seen = time.time() - 100 * 86400  # 100 days ago
        det.first_seen = det.last_seen - 30
        self.store.save_detection(det)
        self.store.save_detection(_make_detection())  # current
        deleted = self.store.purge_old_records(older_than_days=30)
        assert deleted == 1
        rows = self.store.query_detections(last_n_hours=24 * 365)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------

class TestDetectionStoreSnapshots:
    def setup_method(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.store = DetectionStore(
            db_path=os.path.join(self._tmp, "snap.db"),
            store_snapshots=True,
            snapshot_interval_s=0.0,  # no rate limit for testing
        )
        self.store.open()

    def teardown_method(self):
        self.store.close()

    def test_save_snapshot_stores_row(self):
        self.store.save_snapshot("u0", 2.45e9, [-100.0] * 256)
        with self.store._lock:
            cur = self.store._conn.cursor()
            cur.execute("SELECT COUNT(*) FROM spectrum_snapshots")
            count = cur.fetchone()[0]
        assert count == 1

    def test_save_snapshot_disabled_noop(self, tmp_path):
        store2 = DetectionStore(
            db_path=str(tmp_path / "no_snap.db"),
            store_snapshots=False,
        )
        store2.open()
        store2.save_snapshot("u0", 2.45e9, [-100.0] * 10)
        # No snapshots table should be created when disabled
        with store2._lock:
            cur = store2._conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='spectrum_snapshots'"
            )
            assert cur.fetchone() is None
        store2.close()
