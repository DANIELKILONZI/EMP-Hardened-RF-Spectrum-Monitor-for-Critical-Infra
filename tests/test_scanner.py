"""Tests for the base scanner and RTL-SDR scanner (simulation mode)."""

import time

import numpy as np
import pytest

from src.scanner.base_scanner import BaseScanner, ScanResult, FREQ_MIN_HZ, FREQ_MAX_HZ
from src.scanner.rtlsdr_scanner import RTLSDRScanner
from src.scanner.usrp_scanner import USRPScanner


# ---------------------------------------------------------------------------
# ScanResult tests
# ---------------------------------------------------------------------------

class TestScanResult:
    def _make_result(self, power_values=None):
        freqs = list(np.linspace(900e6, 916e6, 1024))
        if power_values is None:
            power_values = list(np.random.normal(-100, 5, 1024))
        return ScanResult(
            center_freq_hz=908e6,
            sample_rate_hz=16e6,
            frequencies_hz=freqs,
            power_db=power_values,
            unit_id="test-unit",
        )

    def test_peak_power(self):
        powers = [-110.0, -90.0, -80.0, -75.0, -95.0]
        freqs = [900e6, 901e6, 902e6, 903e6, 904e6]
        r = ScanResult(
            center_freq_hz=902e6, sample_rate_hz=4e6,
            frequencies_hz=freqs, power_db=powers
        )
        assert r.peak_power_db == -75.0

    def test_bandwidth_hz(self):
        r = self._make_result()
        assert r.bandwidth_hz == 16e6

    def test_bins_above_threshold(self):
        powers = [-110.0, -85.0, -79.0, -70.0, -95.0]
        freqs = [900e6, 901e6, 902e6, 903e6, 904e6]
        r = ScanResult(
            center_freq_hz=902e6, sample_rate_hz=4e6,
            frequencies_hz=freqs, power_db=powers
        )
        above = r.bins_above_threshold(-80.0)
        assert len(above) == 2
        assert above[0][0] == 902e6
        assert above[1][0] == 903e6

    def test_empty_power_peak(self):
        r = ScanResult(
            center_freq_hz=915e6, sample_rate_hz=2.4e6,
            frequencies_hz=[], power_db=[]
        )
        assert r.peak_power_db == float("-inf")

    def test_timestamp_set(self):
        before = time.time()
        r = ScanResult(
            center_freq_hz=915e6, sample_rate_hz=2.4e6,
            frequencies_hz=[], power_db=[]
        )
        assert r.timestamp >= before


# ---------------------------------------------------------------------------
# BaseScanner validation
# ---------------------------------------------------------------------------

class TestBaseScannerValidation:
    def test_freq_below_min_raises(self):
        with pytest.raises(ValueError, match="outside the supported range"):
            RTLSDRScanner(center_freq_hz=50e6)  # 50 MHz < 100 MHz

    def test_freq_above_max_raises(self):
        with pytest.raises(ValueError, match="outside the supported range"):
            RTLSDRScanner(center_freq_hz=7e9)  # 7 GHz > 6 GHz

    def test_freq_at_min_ok(self):
        scanner = RTLSDRScanner(center_freq_hz=FREQ_MIN_HZ)
        assert scanner.center_freq_hz == FREQ_MIN_HZ

    def test_freq_at_max_ok(self):
        scanner = RTLSDRScanner(center_freq_hz=FREQ_MAX_HZ)
        assert scanner.center_freq_hz == FREQ_MAX_HZ


# ---------------------------------------------------------------------------
# RTLSDRScanner simulation tests
# ---------------------------------------------------------------------------

class TestRTLSDRScannerSimulation:
    def setup_method(self):
        self.scanner = RTLSDRScanner(unit_id="rtlsdr-sim", center_freq_hz=433e6)
        self.scanner.open()

    def teardown_method(self):
        self.scanner.close()

    def test_open_in_simulation_mode(self):
        assert self.scanner._is_open is True
        assert self.scanner._simulation is True

    def test_scan_once_returns_result(self):
        result = self.scanner.scan_once()
        assert isinstance(result, ScanResult)
        assert result.unit_id == "rtlsdr-sim"
        assert result.center_freq_hz == 433e6

    def test_scan_result_has_correct_bins(self):
        result = self.scanner.scan_once()
        assert len(result.frequencies_hz) == self.scanner.fft_size
        assert len(result.power_db) == self.scanner.fft_size

    def test_frequencies_centred_on_tuned_freq(self):
        result = self.scanner.scan_once()
        freqs = np.array(result.frequencies_hz)
        centre = (freqs[0] + freqs[-1]) / 2.0
        assert abs(centre - 433e6) < 1e6  # within 1 MHz of nominal

    def test_scan_not_open_raises(self):
        scanner = RTLSDRScanner(center_freq_hz=433e6)
        with pytest.raises(RuntimeError, match="not open"):
            scanner.scan_once()

    def test_hardware_info(self):
        info = self.scanner.get_hardware_info()
        assert info["driver"] == "simulation"
        assert info["unit_id"] == "rtlsdr-sim"

    def test_context_manager(self):
        with RTLSDRScanner(unit_id="ctx-test", center_freq_hz=915e6) as s:
            result = s.scan_once()
        assert isinstance(result, ScanResult)
        assert not s._is_open

    def test_continuous_scan(self):
        results = []
        self.scanner.start_continuous_scan(callback=results.append, interval_s=0.01)
        time.sleep(0.15)
        self.scanner.stop_continuous_scan()
        assert len(results) >= 2

    def test_continuous_scan_already_running_warning(self, caplog):
        import logging
        self.scanner.start_continuous_scan(callback=lambda r: None, interval_s=0.01)
        with caplog.at_level(logging.WARNING):
            self.scanner.start_continuous_scan(callback=lambda r: None, interval_s=0.01)
        self.scanner.stop_continuous_scan()
        assert any("already running" in msg for msg in caplog.messages)

    def test_repr(self):
        r = repr(self.scanner)
        assert "RTLSDRScanner" in r
        assert "rtlsdr-sim" in r


# ---------------------------------------------------------------------------
# USRPScanner simulation tests
# ---------------------------------------------------------------------------

class TestUSRPScannerSimulation:
    def setup_method(self):
        self.scanner = USRPScanner(unit_id="usrp-sim", center_freq_hz=5.8e9)
        self.scanner.open()

    def teardown_method(self):
        self.scanner.close()

    def test_open_simulation_mode(self):
        assert self.scanner._is_open
        assert self.scanner._simulation

    def test_scan_once(self):
        result = self.scanner.scan_once()
        assert isinstance(result, ScanResult)
        assert result.unit_id == "usrp-sim"

    def test_hardware_info_simulation(self):
        info = self.scanner.get_hardware_info()
        assert info["driver"] == "simulation"
