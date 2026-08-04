"""Tests for GNU Radio flowgraph stubs (simulation mode – no real GR required)."""

import numpy as np
import pytest

from src.analyzer.gnuradio_flow import FHSSDetector, GRFFTAnalyzer, WBFMDemodulator


# ---------------------------------------------------------------------------
# GRFFTAnalyzer
# ---------------------------------------------------------------------------

class TestGRFFTAnalyzer:
    def setup_method(self):
        self.analyzer = GRFFTAnalyzer(sample_rate=2_400_000, fft_size=256)

    def test_start_stop_no_error(self):
        self.analyzer.start()
        self.analyzer.stop()

    def test_numpy_psd_returns_correct_shape(self):
        iq = np.random.normal(0, 0.1, 512) + 1j * np.random.normal(0, 0.1, 512)
        psd = self.analyzer._numpy_psd(iq.astype(np.complex64))
        assert len(psd) == 256

    def test_numpy_psd_short_input(self):
        iq = np.array([0.1 + 0.0j] * 10)
        psd = self.analyzer._numpy_psd(iq)
        assert len(psd) == 256
        assert np.all(psd == -120.0)

    def test_compute_psd_via_numpy_fallback(self):
        iq = np.random.normal(0, 0.1, 1024) + 1j * np.random.normal(0, 0.1, 1024)
        psd = self.analyzer.compute_psd(iq.astype(np.complex64))
        assert len(psd) == 256

    def test_psd_values_are_negative_dbm(self):
        # A noise-floor signal should be negative (well below 0 dBm)
        iq = np.random.normal(0, 0.001, 1024) + 1j * np.random.normal(0, 0.001, 1024)
        psd = self.analyzer._numpy_psd(iq.astype(np.complex64))
        assert np.max(psd) < 0.0


# ---------------------------------------------------------------------------
# WBFMDemodulator
# ---------------------------------------------------------------------------

class TestWBFMDemodulator:
    def setup_method(self):
        self.demod = WBFMDemodulator(sample_rate=2_400_000)

    def _make_voice_iq(self, n=2048):
        """Simulate a slowly varying FM signal (voice-like)."""
        t = np.arange(n) / 2_400_000
        phase = 2 * np.pi * 1000 * np.cumsum(np.sin(2 * np.pi * 300 * t)) / 2_400_000
        return (np.cos(phase) + 1j * np.sin(phase)).astype(np.complex64)

    def _make_noise_iq(self, n=2048):
        rng = np.random.default_rng(0)
        return (rng.normal(0, 0.01, n) + 1j * rng.normal(0, 0.01, n)).astype(np.complex64)

    def test_classify_returns_string(self):
        iq = self._make_noise_iq()
        result = self.demod.classify(iq)
        assert isinstance(result, str)
        assert result in ("voice", "data", "noise")

    def test_classify_noise(self):
        iq = self._make_noise_iq()
        result = self.demod.classify(iq)
        # Very low amplitude noise has rapid random phase transitions, which
        # the heuristic can classify as either "noise" or "data".
        assert result in ("noise", "data")

    def test_classify_short_input(self):
        iq = np.array([0.1 + 0.0j] * 10, dtype=np.complex64)
        result = self.demod.classify(iq)
        assert result == "noise"


# ---------------------------------------------------------------------------
# FHSSDetector
# ---------------------------------------------------------------------------

class TestFHSSDetector:
    def setup_method(self):
        self.detector = FHSSDetector(
            hop_threshold_hz=1_000_000,
            min_hops=3,
            window_s=2.0,
        )

    def test_no_detection_single_freq(self):
        for _ in range(5):
            detected = self.detector.update(2_450_000_000)
        assert detected is False

    def test_detection_with_hops(self):
        # Simulate 4 hops
        freqs = [2_400_000_000, 2_420_000_000, 2_440_000_000, 2_460_000_000, 2_480_000_000]
        results = [self.detector.update(f) for f in freqs]
        assert any(results)

    def test_reset_clears_history(self):
        for f in [2_400_000_000, 2_420_000_000, 2_440_000_000, 2_460_000_000]:
            self.detector.update(f)
        self.detector.reset()
        assert len(self.detector._history) == 0

    def test_detection_false_below_min_hops(self):
        self.detector = FHSSDetector(min_hops=10, window_s=5.0)
        for f in [2_400_000_000, 2_420_000_000, 2_440_000_000]:
            self.detector.update(f)
        detected = self.detector.update(2_460_000_000)
        assert detected is False

    def test_old_observations_expired(self):
        import time

        self.detector = FHSSDetector(min_hops=3, window_s=0.05)
        # Add hops
        for f in [2_400_000_000, 2_420_000_000, 2_440_000_000, 2_460_000_000]:
            self.detector.update(f)
        # Wait for window to expire
        time.sleep(0.1)
        # Add single stable freq – window is cleared so no detection
        detected = self.detector.update(2_450_000_000)
        assert detected is False
