"""Base scanner abstraction for SDR hardware interfaces.

Defines the common contract that all scanner backends (RTL-SDR, USRP) must
implement and the shared :class:`ScanResult` data container.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# Frequency range supported by the monitor (100 MHz – 6 GHz)
FREQ_MIN_HZ = 100_000_000   # 100 MHz
FREQ_MAX_HZ = 6_000_000_000  # 6 GHz


@dataclass
class ScanResult:
    """A single frequency-domain scan snapshot.

    Attributes:
        center_freq_hz: Centre frequency of the scan window in Hz.
        sample_rate_hz: Sample rate used for the capture in Hz.
        frequencies_hz: Array of frequency bin centres (Hz).
        power_db: Array of power spectral density values (dBm) aligned with
            ``frequencies_hz``.
        timestamp: Unix timestamp of the capture.
        unit_id: Identifier of the monitoring unit that produced this result.
        location: Optional (latitude, longitude, altitude_m) tuple.
    """

    center_freq_hz: float
    sample_rate_hz: float
    frequencies_hz: List[float]
    power_db: List[float]
    timestamp: float = field(default_factory=time.time)
    unit_id: str = "unit-0"
    location: Optional[tuple] = None  # (lat, lon, alt_m)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def peak_power_db(self) -> float:
        """Return the maximum power value observed in this scan."""
        return max(self.power_db) if self.power_db else float("-inf")

    @property
    def bandwidth_hz(self) -> float:
        """Return the total captured bandwidth in Hz."""
        return self.sample_rate_hz

    def bins_above_threshold(self, threshold_db: float) -> List[tuple]:
        """Return (freq_hz, power_db) pairs where power exceeds *threshold_db*."""
        return [
            (f, p)
            for f, p in zip(self.frequencies_hz, self.power_db)
            if p > threshold_db
        ]


class BaseScanner(ABC):
    """Abstract base class for SDR scanner hardware backends.

    Subclasses must implement :meth:`open`, :meth:`close`,
    :meth:`scan_once`, and :meth:`get_hardware_info`.

    The :meth:`start_continuous_scan` / :meth:`stop_continuous_scan` methods
    provide a default threading implementation that repeatedly calls
    :meth:`scan_once` and delivers results to a callback.
    """

    def __init__(
        self,
        unit_id: str = "unit-0",
        center_freq_hz: float = 915_000_000,
        sample_rate_hz: float = 2_400_000,
        gain_db: float = 30.0,
    ) -> None:
        if not (FREQ_MIN_HZ <= center_freq_hz <= FREQ_MAX_HZ):
            raise ValueError(
                f"center_freq_hz {center_freq_hz} Hz is outside the supported range "
                f"[{FREQ_MIN_HZ}, {FREQ_MAX_HZ}] Hz."
            )
        self.unit_id = unit_id
        self.center_freq_hz = center_freq_hz
        self.sample_rate_hz = sample_rate_hz
        self.gain_db = gain_db
        self._is_open: bool = False
        self._scan_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def open(self) -> None:
        """Open and configure the SDR hardware."""

    @abstractmethod
    def close(self) -> None:
        """Release the SDR hardware resources."""

    @abstractmethod
    def scan_once(self) -> ScanResult:
        """Capture one spectrum snapshot and return a :class:`ScanResult`."""

    @abstractmethod
    def get_hardware_info(self) -> dict:
        """Return a dict describing the hardware (driver, serial, etc.)."""

    # ------------------------------------------------------------------
    # Continuous scan helper (uses scan_once internally)
    # ------------------------------------------------------------------

    def start_continuous_scan(
        self,
        callback: Callable[[ScanResult], None],
        interval_s: float = 0.1,
    ) -> None:
        """Start a background thread that calls *callback* with each new :class:`ScanResult`.

        Args:
            callback: Function to invoke with each new :class:`ScanResult`.
            interval_s: Minimum pause between successive scans (seconds).
        """
        if self._scan_thread and self._scan_thread.is_alive():
            logger.warning("Continuous scan already running on %s", self.unit_id)
            return

        self._stop_event.clear()

        def _loop() -> None:
            logger.info("Continuous scan started on %s", self.unit_id)
            while not self._stop_event.is_set():
                try:
                    result = self.scan_once()
                    callback(result)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Scan error on %s: %s", self.unit_id, exc)
                time.sleep(interval_s)
            logger.info("Continuous scan stopped on %s", self.unit_id)

        self._scan_thread = threading.Thread(target=_loop, daemon=True)
        self._scan_thread.start()

    def stop_continuous_scan(self, timeout_s: float = 5.0) -> None:
        """Signal the continuous scan thread to stop and wait for it.

        Args:
            timeout_s: Seconds to wait for the thread to finish.
        """
        self._stop_event.set()
        if self._scan_thread:
            self._scan_thread.join(timeout=timeout_s)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "BaseScanner":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Representation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} unit_id={self.unit_id!r} "
            f"freq={self.center_freq_hz / 1e6:.1f} MHz "
            f"sr={self.sample_rate_hz / 1e6:.2f} MHz>"
        )
