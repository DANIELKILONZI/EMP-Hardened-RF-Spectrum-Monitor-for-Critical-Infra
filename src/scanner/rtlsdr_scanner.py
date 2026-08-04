"""RTL-SDR scanner backend.

Wraps the ``rtlsdr`` Python library (pyrtlsdr) and falls back to a
simulation mode when real hardware is not present.  This allows the rest
of the system to function in testing/simulation environments without
physical RTL-SDR hardware attached.
"""

from __future__ import annotations

import logging
import math
import time
from typing import List, Optional

import numpy as np

from .base_scanner import BaseScanner, ScanResult

logger = logging.getLogger(__name__)

_RTLSDR_AVAILABLE = False
try:
    import rtlsdr  # type: ignore

    _RTLSDR_AVAILABLE = True
except ImportError:
    logger.warning(
        "pyrtlsdr not installed – RTLSDRScanner will run in simulation mode."
    )


class RTLSDRScanner(BaseScanner):
    """RTL-SDR based RF scanner.

    Uses the ``rtlsdr`` library when available; otherwise produces
    synthetic spectrum data so the full analysis pipeline can be exercised
    in lab/CI environments without hardware.

    Args:
        device_index: Hardware device index (0-based).  Ignored in
            simulation mode.
        fft_size: Number of FFT bins for PSD estimation.
        num_samples: Number of IQ samples to collect per scan.
        **kwargs: Forwarded to :class:`~src.scanner.base_scanner.BaseScanner`.
    """

    DEFAULT_SAMPLE_RATE_HZ = 2_400_000  # 2.4 MS/s – max stable for RTL-SDR

    def __init__(
        self,
        unit_id: str = "rtlsdr-0",
        center_freq_hz: float = 915_000_000,
        sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
        gain_db: float = 30.0,
        device_index: int = 0,
        fft_size: int = 1024,
        num_samples: int = 256_000,
    ) -> None:
        super().__init__(
            unit_id=unit_id,
            center_freq_hz=center_freq_hz,
            sample_rate_hz=sample_rate_hz,
            gain_db=gain_db,
        )
        self.device_index = device_index
        self.fft_size = fft_size
        self.num_samples = num_samples
        self._sdr: Optional[object] = None  # rtlsdr.RtlSdr instance when open
        self._simulation = not _RTLSDR_AVAILABLE

    # ------------------------------------------------------------------
    # BaseScanner interface
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the RTL-SDR device or enter simulation mode."""
        if self._is_open:
            return

        if self._simulation:
            logger.info(
                "%s: opening in SIMULATION mode (no hardware detected).", self.unit_id
            )
            self._is_open = True
            return

        try:
            self._sdr = rtlsdr.RtlSdr(self.device_index)
            self._sdr.sample_rate = self.sample_rate_hz
            self._sdr.center_freq = self.center_freq_hz
            self._sdr.gain = self.gain_db
            self._is_open = True
            logger.info("%s: RTL-SDR device opened (index=%d).", self.unit_id, self.device_index)
        except Exception as exc:
            logger.error("%s: failed to open RTL-SDR device: %s", self.unit_id, exc)
            logger.info("%s: falling back to simulation mode.", self.unit_id)
            self._simulation = True
            self._is_open = True

    def close(self) -> None:
        """Close the RTL-SDR device."""
        if not self._is_open:
            return
        self.stop_continuous_scan()
        if self._sdr is not None:
            try:
                self._sdr.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s: error closing RTL-SDR: %s", self.unit_id, exc)
            self._sdr = None
        self._is_open = False
        logger.info("%s: closed.", self.unit_id)

    def scan_once(self) -> ScanResult:
        """Capture one FFT snapshot and return a :class:`ScanResult`."""
        if not self._is_open:
            raise RuntimeError(f"{self.unit_id}: scanner is not open.")

        if self._simulation:
            return self._simulate_scan()

        samples = self._sdr.read_samples(self.num_samples)
        return self._compute_psd(np.array(samples))

    def get_hardware_info(self) -> dict:
        """Return hardware metadata."""
        if self._simulation:
            return {
                "driver": "simulation",
                "device_index": self.device_index,
                "unit_id": self.unit_id,
            }
        info: dict = {
            "driver": "rtlsdr",
            "device_index": self.device_index,
            "unit_id": self.unit_id,
        }
        if self._sdr is not None:
            try:
                info["serial"] = self._sdr.get_serial_number()
                info["tuner_type"] = str(self._sdr.get_tuner_type())
            except Exception:  # noqa: BLE001
                pass
        return info

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_psd(self, samples: "np.ndarray") -> ScanResult:
        """Compute power spectral density from IQ *samples*."""
        # Welch-style averaged PSD using overlapping windows
        segment_len = self.fft_size
        hop = segment_len // 2
        segments: List["np.ndarray"] = []

        for start in range(0, len(samples) - segment_len + 1, hop):
            seg = samples[start : start + segment_len]
            windowed = seg * np.hanning(segment_len)
            segments.append(np.abs(np.fft.fftshift(np.fft.fft(windowed))) ** 2)

        if not segments:
            psd = np.zeros(self.fft_size)
        else:
            psd = np.mean(segments, axis=0)

        # Convert to dBm (referenced to 50 Ω, normalised)
        psd_db = 10.0 * np.log10(psd + 1e-20) - 10.0 * math.log10(self.fft_size)

        freqs = (
            np.fft.fftshift(np.fft.fftfreq(self.fft_size, d=1.0 / self.sample_rate_hz))
            + self.center_freq_hz
        )

        return ScanResult(
            center_freq_hz=self.center_freq_hz,
            sample_rate_hz=self.sample_rate_hz,
            frequencies_hz=freqs.tolist(),
            power_db=psd_db.tolist(),
            timestamp=time.time(),
            unit_id=self.unit_id,
        )

    def _simulate_scan(self) -> ScanResult:
        """Generate a synthetic spectrum snapshot for testing purposes."""
        freqs = (
            np.fft.fftshift(np.fft.fftfreq(self.fft_size, d=1.0 / self.sample_rate_hz))
            + self.center_freq_hz
        )
        # Thermal noise floor ~-100 dBm
        noise = np.random.normal(-100, 2, self.fft_size)
        # Occasional synthetic carrier to test detection pipeline
        if np.random.random() < 0.1:
            carrier_bin = np.random.randint(10, self.fft_size - 10)
            noise[carrier_bin] += np.random.uniform(20, 35)
        return ScanResult(
            center_freq_hz=self.center_freq_hz,
            sample_rate_hz=self.sample_rate_hz,
            frequencies_hz=freqs.tolist(),
            power_db=noise.tolist(),
            timestamp=time.time(),
            unit_id=self.unit_id,
        )
