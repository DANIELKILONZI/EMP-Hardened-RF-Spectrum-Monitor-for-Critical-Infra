"""Tests for the frequency sweep manager."""

import time

import pytest

from src.scanner.rtlsdr_scanner import RTLSDRScanner
from src.scanner.sweep_manager import FrequencyBand, SweepManager


# ---------------------------------------------------------------------------
# FrequencyBand tests
# ---------------------------------------------------------------------------

class TestFrequencyBand:
    def test_default_dwell(self):
        b = FrequencyBand(center_hz=433e6)
        assert b.dwell_s == 0.5

    def test_custom_params(self):
        b = FrequencyBand(center_hz=915e6, dwell_s=1.0, label="ISM", gain_db=25.0)
        assert b.dwell_s == 1.0
        assert b.label == "ISM"
        assert b.gain_db == 25.0

    def test_sample_rate_none_by_default(self):
        b = FrequencyBand(center_hz=2.45e9)
        assert b.sample_rate_hz is None


# ---------------------------------------------------------------------------
# SweepManager tests
# ---------------------------------------------------------------------------

class TestSweepManager:
    def _make_scanner(self, freq=433e6):
        s = RTLSDRScanner(unit_id="sweep-test", center_freq_hz=freq)
        s.open()
        return s

    def _make_bands(self):
        return [
            FrequencyBand(center_hz=433e6, dwell_s=0.05, label="Band A"),
            FrequencyBand(center_hz=915e6, dwell_s=0.05, label="Band B"),
            FrequencyBand(center_hz=2450e6, dwell_s=0.05, label="Band C"),
        ]

    def test_empty_bands_raises(self):
        scanner = self._make_scanner()
        with pytest.raises(ValueError, match="bands list must not be empty"):
            SweepManager(scanner, bands=[])
        scanner.close()

    def test_current_band_initial(self):
        scanner = self._make_scanner()
        bands = self._make_bands()
        sw = SweepManager(scanner, bands)
        assert sw.current_band == bands[0]
        scanner.close()

    def test_start_receives_callbacks(self):
        scanner = self._make_scanner()
        bands = self._make_bands()
        results = []
        sw = SweepManager(scanner, bands)
        sw.start(callback=results.append)
        time.sleep(0.4)
        sw.stop()
        scanner.close()
        # Should have received callbacks from at least 2 different bands
        assert len(results) >= 2

    def test_sweep_tunes_scanner_freq(self):
        scanner = self._make_scanner(freq=433e6)
        bands = [
            FrequencyBand(center_hz=433e6, dwell_s=0.05),
            FrequencyBand(center_hz=915e6, dwell_s=0.05),
        ]
        observed_freqs = set()

        def capture(result):
            observed_freqs.add(result.center_freq_hz)

        sw = SweepManager(scanner, bands)
        sw.start(callback=capture)
        time.sleep(0.3)
        sw.stop()
        scanner.close()
        assert len(observed_freqs) >= 2

    def test_completed_cycles_increments(self):
        scanner = self._make_scanner()
        bands = [
            FrequencyBand(center_hz=433e6, dwell_s=0.02),
            FrequencyBand(center_hz=915e6, dwell_s=0.02),
        ]
        sw = SweepManager(scanner, bands, loop=True)
        sw.start(callback=lambda r: None)
        time.sleep(0.2)
        sw.stop()
        scanner.close()
        assert sw.completed_cycles >= 1

    def test_stop_is_idempotent(self):
        scanner = self._make_scanner()
        bands = self._make_bands()
        sw = SweepManager(scanner, bands)
        sw.start(callback=lambda r: None)
        sw.stop()
        sw.stop()  # second call should not raise
        scanner.close()

    def test_start_already_running_warns(self, caplog):
        import logging

        scanner = self._make_scanner()
        bands = self._make_bands()
        sw = SweepManager(scanner, bands)
        with caplog.at_level(logging.WARNING):
            sw.start(callback=lambda r: None)
            sw.start(callback=lambda r: None)  # second start
        sw.stop()
        scanner.close()
        assert any("already running" in m for m in caplog.messages)

    def test_loop_false_stops_after_one_cycle(self):
        scanner = self._make_scanner()
        bands = [
            FrequencyBand(center_hz=433e6, dwell_s=0.02),
        ]
        sw = SweepManager(scanner, bands, loop=False)
        sw.start(callback=lambda r: None)
        time.sleep(0.3)
        # Thread should have exited by now
        assert not sw._sweep_thread.is_alive()
        scanner.close()

    def test_from_config(self):
        scanner = self._make_scanner()
        cfg = {
            "enabled": True,
            "loop": True,
            "bands": [
                {"center_hz": 433000000, "dwell_s": 0.5, "label": "ISM"},
                {"center_hz": 915000000, "dwell_s": 0.5},
            ],
        }
        sw = SweepManager.from_config(scanner, cfg)
        assert len(sw.bands) == 2
        assert sw.bands[0].label == "ISM"
        assert sw.bands[0].dwell_s == 0.5
        assert sw.loop is True
        scanner.close()
