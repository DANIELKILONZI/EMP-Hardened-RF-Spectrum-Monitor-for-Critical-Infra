"""Tests for spectrum analyzer and signal detector."""

import time

import numpy as np
import pytest

from src.scanner.base_scanner import ScanResult
from src.analyzer.spectrum_analyzer import SpectrumAnalyzer
from src.analyzer.signal_detector import SignalDetector, DetectedSignal
from src.analyzer.waterfall import WaterfallDisplay


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_scan(
    unit_id="u0",
    center_freq=915e6,
    sample_rate=2.4e6,
    n_bins=256,
    floor_db=-100.0,
    carrier_bin=None,
    carrier_power=-70.0,
    timestamp=None,
):
    freqs = np.linspace(center_freq - sample_rate / 2, center_freq + sample_rate / 2, n_bins)
    power = np.full(n_bins, floor_db)
    if carrier_bin is not None:
        power[carrier_bin] = carrier_power
    return ScanResult(
        center_freq_hz=center_freq,
        sample_rate_hz=sample_rate,
        frequencies_hz=freqs.tolist(),
        power_db=power.tolist(),
        unit_id=unit_id,
        timestamp=timestamp or time.time(),
    )


# ---------------------------------------------------------------------------
# SpectrumAnalyzer tests
# ---------------------------------------------------------------------------

class TestSpectrumAnalyzer:
    def setup_method(self):
        self.analyzer = SpectrumAnalyzer(
            history_seconds=10.0,
            calibration_offset_db=0.0,
            smoothing_alpha=1.0,  # no smoothing – use raw values
        )

    def test_process_returns_scan_result(self):
        r = make_scan()
        processed = self.analyzer.process(r)
        assert isinstance(processed, ScanResult)

    def test_calibration_offset_applied(self):
        analyzer = SpectrumAnalyzer(calibration_offset_db=5.0, smoothing_alpha=1.0)
        r = make_scan(floor_db=-100.0)
        processed = analyzer.process(r)
        assert abs(processed.power_db[0] - (-95.0)) < 0.5

    def test_history_stored(self):
        for _ in range(5):
            self.analyzer.process(make_scan())
        assert len(self.analyzer.get_history()) == 5

    def test_history_eviction(self):
        analyzer = SpectrumAnalyzer(history_seconds=0.05, smoothing_alpha=1.0)
        r = make_scan(timestamp=time.time() - 1.0)  # 1 second old
        analyzer.process(r)
        time.sleep(0.1)
        # Process a new result to trigger eviction
        analyzer.process(make_scan())
        history = analyzer.get_history()
        assert len(history) == 1  # only the new one survives

    def test_unit_filter(self):
        self.analyzer.process(make_scan(unit_id="a"))
        self.analyzer.process(make_scan(unit_id="b"))
        assert len(self.analyzer.get_history(unit_id="a")) == 1
        assert len(self.analyzer.get_history(unit_id="b")) == 1

    def test_last_n_seconds_filter(self):
        old = make_scan(timestamp=time.time() - 100)
        self.analyzer.process(old)
        self.analyzer.process(make_scan())
        recent = self.analyzer.get_history(last_n_seconds=5.0)
        assert len(recent) == 1

    def test_clear_history(self):
        self.analyzer.process(make_scan())
        self.analyzer.clear_history()
        assert len(self.analyzer.get_history()) == 0

    def test_baseline_initialised_after_first_process(self):
        r = make_scan(unit_id="u0")
        self.analyzer.process(r)
        baseline = self.analyzer.get_baseline("u0")
        assert baseline is not None
        assert len(baseline) == len(r.power_db)

    def test_anomaly_score_zero_no_baseline(self):
        r = make_scan(unit_id="fresh")
        score, _ = self.analyzer.compute_anomaly_score(r)
        assert score == 0.0

    def test_anomaly_score_positive_for_elevated_signal(self):
        floor = make_scan(unit_id="u1", floor_db=-100.0)
        self.analyzer.process(floor)
        # Force baseline to -100 dBm
        self.analyzer._baseline_psd["u1"] = np.full(256, -100.0)
        elevated = make_scan(unit_id="u1", floor_db=-80.0)
        score, excess = self.analyzer.compute_anomaly_score(elevated)
        assert score > 0.0
        assert np.all(excess >= 0.0)

    def test_waterfall_matrix_shape(self):
        for _ in range(5):
            self.analyzer.process(make_scan(unit_id="wf"))
        matrix, timestamps, freqs = self.analyzer.get_waterfall_matrix("wf", max_rows=10)
        assert matrix.shape[0] == 5
        assert matrix.shape[1] == 256

    def test_smoothing_alpha_zero_preserves_old_value(self):
        analyzer = SpectrumAnalyzer(smoothing_alpha=0.0, calibration_offset_db=0.0)
        r1 = make_scan(unit_id="s", floor_db=-100.0)
        r2 = make_scan(unit_id="s", floor_db=-80.0)
        analyzer.process(r1)
        processed2 = analyzer.process(r2)
        # With alpha=0, new values should not change the smoothed output at all
        # (it stays at the first measurement)
        assert abs(processed2.power_db[0] - (-100.0)) < 1.0


# ---------------------------------------------------------------------------
# SignalDetector tests
# ---------------------------------------------------------------------------

class TestSignalDetector:
    def setup_method(self):
        self.detector = SignalDetector(
            threshold_db=-80.0,
            persistence_s=1.0,   # 1 s for fast tests
            bin_merge_hz=500_000,
            max_track_age_s=5.0,
        )

    def _scan_with_carrier(self, timestamp, unit_id="u0", carrier_power=-70.0):
        """Make a scan with a carrier above threshold at bin 128."""
        return make_scan(
            unit_id=unit_id,
            carrier_bin=128,
            carrier_power=carrier_power,
            timestamp=timestamp,
        )

    def test_no_detection_below_threshold(self):
        r = make_scan(floor_db=-100.0)
        detections = self.detector.process(r)
        assert detections == []

    def test_no_detection_before_persistence(self):
        now = time.time()
        r = self._scan_with_carrier(timestamp=now)
        detections = self.detector.process(r)
        assert detections == []

    def test_detection_after_persistence(self):
        now = time.time()
        # Feed scans spanning > 1 second
        self.detector.process(self._scan_with_carrier(timestamp=now - 1.5))
        detections = self.detector.process(self._scan_with_carrier(timestamp=now))
        assert len(detections) >= 1
        assert isinstance(detections[0], DetectedSignal)

    def test_detection_attributes(self):
        now = time.time()
        self.detector.process(self._scan_with_carrier(timestamp=now - 2.0))
        detections = self.detector.process(self._scan_with_carrier(timestamp=now))
        assert len(detections) >= 1
        d = detections[0]
        assert d.unit_id == "u0"
        assert d.peak_power_db >= -70.0
        assert d.persistence_s >= 1.0

    def test_detection_to_dict(self):
        now = time.time()
        self.detector.process(self._scan_with_carrier(timestamp=now - 2.0))
        detections = self.detector.process(self._scan_with_carrier(timestamp=now))
        assert len(detections) >= 1
        d = detections[0].to_dict()
        assert "unit_id" in d
        assert "center_freq_mhz" in d
        assert "peak_power_dbm" in d
        assert "persistence_s" in d

    def test_track_expires_after_silence(self):
        now = time.time()
        self.detector.process(self._scan_with_carrier(timestamp=now - 10.0))
        # Silent scan – no carrier
        self.detector.process(make_scan(timestamp=now))
        tracks = self.detector.get_active_tracks("u0")
        assert len(tracks) == 0

    def test_multiple_units_tracked_independently(self):
        now = time.time()
        for uid in ("unit-A", "unit-B"):
            self.detector.process(self._scan_with_carrier(timestamp=now - 2.0, unit_id=uid))
            self.detector.process(self._scan_with_carrier(timestamp=now, unit_id=uid))
        assert len(self.detector.get_active_tracks("unit-A")) >= 1
        assert len(self.detector.get_active_tracks("unit-B")) >= 1

    def test_reset_clears_unit_tracks(self):
        now = time.time()
        self.detector.process(self._scan_with_carrier(timestamp=now - 0.5))
        self.detector.reset("u0")
        assert self.detector.get_active_tracks("u0") == []

    def test_reset_all(self):
        now = time.time()
        for uid in ("a", "b"):
            self.detector.process(self._scan_with_carrier(timestamp=now, unit_id=uid))
        self.detector.reset()
        assert self.detector.get_active_tracks("a") == []
        assert self.detector.get_active_tracks("b") == []

    def test_location_stamped_on_detection(self):
        now = time.time()
        r = make_scan(unit_id="loc-test", carrier_bin=128, carrier_power=-70.0, timestamp=now - 2.0)
        r.location = (38.8, -77.0, 10.0)
        self.detector.process(r)
        r2 = make_scan(unit_id="loc-test", carrier_bin=128, carrier_power=-70.0, timestamp=now)
        r2.location = (38.8, -77.0, 10.0)
        detections = self.detector.process(r2)
        if detections:
            assert detections[0].location == (38.8, -77.0, 10.0)

    # ---- Classification tests --------------------------------------------

    def test_classify_drone_24ghz(self):
        cls = SignalDetector._classify(2450e6, 10e6, -75.0)
        assert "drone_control" in cls

    def test_classify_drone_58ghz(self):
        cls = SignalDetector._classify(5800e6, 20e6, -75.0)
        assert "drone" in cls.lower() or "5.8" in cls

    def test_classify_gps_jammer(self):
        cls = SignalDetector._classify(1575.42e6, 5e6, -65.0)
        assert "gps" in cls.lower()

    def test_classify_cellular_jammer(self):
        cls = SignalDetector._classify(850e6, 20e6, -75.0)
        assert "jammer" in cls.lower() or cls == "unknown"

    def test_classify_unknown(self):
        cls = SignalDetector._classify(300e6, 1e6, -75.0)
        assert isinstance(cls, str)


# ---------------------------------------------------------------------------
# WaterfallDisplay tests
# ---------------------------------------------------------------------------

class TestWaterfallDisplay:
    def setup_method(self):
        self.wf = WaterfallDisplay(min_db=-120.0, max_db=-40.0, width_chars=40)

    def test_add_and_render(self):
        for _ in range(5):
            self.wf.add_row(list(np.random.normal(-90, 5, 256)))
        output = self.wf.render_ascii(last_n_rows=5)
        assert len(output.splitlines()) == 5

    def test_render_empty(self):
        output = self.wf.render_ascii()
        assert output == "(no data)"

    def test_header_contains_frequencies(self):
        header = self.wf.render_header(900e6, 916e6)
        assert "900.0" in header
        assert "916.0" in header

    def test_clear(self):
        self.wf.add_row([-90.0] * 100)
        self.wf.clear()
        assert self.wf.render_ascii() == "(no data)"

    def test_save_csv(self, tmp_path):
        for _ in range(3):
            self.wf.add_row([-90.0] * 10)
        csv_path = str(tmp_path / "wf.csv")
        self.wf.save_csv(csv_path)
        with open(csv_path) as fh:
            lines = fh.readlines()
        assert len(lines) == 3
        assert len(lines[0].strip().split(",")) == 10

    def test_render_ascii_respects_last_n_rows(self):
        for i in range(20):
            self.wf.add_row([-90.0] * 50)
        output = self.wf.render_ascii(last_n_rows=5)
        assert len(output.splitlines()) == 5
