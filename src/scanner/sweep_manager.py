"""Frequency sweep / hop manager.

Allows a single SDR scanner to step through a list of :class:`FrequencyBand`
segments, dwelling at each centre frequency for a configurable time before
moving to the next.  This provides broad coverage without requiring one
physical device per band.

Usage::

    from src.scanner.sweep_manager import SweepManager, FrequencyBand
    from src.scanner.rtlsdr_scanner import RTLSDRScanner

    bands = [
        FrequencyBand(center_hz=433_000_000, dwell_s=0.5),
        FrequencyBand(center_hz=915_000_000, dwell_s=0.5),
        FrequencyBand(center_hz=2_450_000_000, dwell_s=1.0),
    ]
    scanner = RTLSDRScanner(unit_id="sweep-0", center_freq_hz=433_000_000)
    sweep = SweepManager(scanner, bands)
    sweep.start(callback=my_callback)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .base_scanner import BaseScanner, ScanResult

logger = logging.getLogger(__name__)


@dataclass
class FrequencyBand:
    """A frequency segment for the sweep cycle.

    Attributes:
        center_hz: Centre frequency in Hz.
        dwell_s: Time to spend capturing at this frequency (seconds).
        label: Human-readable band name (optional).
        sample_rate_hz: Override the scanner sample rate for this band
            (``None`` keeps the scanner's current rate).
        gain_db: Override gain for this band (``None`` keeps current gain).
    """

    center_hz: float
    dwell_s: float = 0.5
    label: str = ""
    sample_rate_hz: Optional[float] = None
    gain_db: Optional[float] = None


class SweepManager:
    """Steps an SDR scanner through a list of :class:`FrequencyBand` entries.

    For each band the scanner is retuned, then :meth:`~BaseScanner.scan_once`
    is called repeatedly for ``dwell_s`` seconds before moving to the next
    band.  Results are delivered to a caller-supplied callback.

    Args:
        scanner: An open :class:`~src.scanner.base_scanner.BaseScanner` instance.
        bands: Ordered list of :class:`FrequencyBand` entries.
        loop: If ``True`` (default) the sweep restarts from the first band
            after completing the last one.
    """

    def __init__(
        self,
        scanner: BaseScanner,
        bands: List[FrequencyBand],
        loop: bool = True,
    ) -> None:
        if not bands:
            raise ValueError("bands list must not be empty.")
        self.scanner = scanner
        self.bands = bands
        self.loop = loop

        self._stop_event = threading.Event()
        self._sweep_thread: Optional[threading.Thread] = None
        self._current_band_index: int = 0
        self._completed_cycles: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_band(self) -> FrequencyBand:
        """Return the :class:`FrequencyBand` currently being scanned."""
        return self.bands[self._current_band_index]

    @property
    def completed_cycles(self) -> int:
        """Number of complete sweeps through all bands."""
        return self._completed_cycles

    def start(self, callback: Callable[[ScanResult], None]) -> None:
        """Begin the sweep in a background thread.

        Args:
            callback: Called with each :class:`ScanResult` collected during
                the sweep.
        """
        if self._sweep_thread and self._sweep_thread.is_alive():
            logger.warning("SweepManager: sweep already running.")
            return

        self._stop_event.clear()
        self._sweep_thread = threading.Thread(
            target=self._sweep_loop,
            args=(callback,),
            daemon=True,
            name=f"sweep-{self.scanner.unit_id}",
        )
        self._sweep_thread.start()
        logger.info(
            "SweepManager: started sweep over %d bands on %s.",
            len(self.bands),
            self.scanner.unit_id,
        )

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop the sweep and wait for the thread to finish.

        Args:
            timeout_s: Seconds to wait for clean shutdown.
        """
        self._stop_event.set()
        if self._sweep_thread:
            self._sweep_thread.join(timeout=timeout_s)
        logger.info("SweepManager: stopped.")

    # ------------------------------------------------------------------
    # Internal sweep loop
    # ------------------------------------------------------------------

    def _sweep_loop(self, callback: Callable[[ScanResult], None]) -> None:
        """Main sweep thread – iterates over bands indefinitely."""
        while not self._stop_event.is_set():
            for idx, band in enumerate(self.bands):
                if self._stop_event.is_set():
                    break
                self._current_band_index = idx
                self._tune_to_band(band)
                self._dwell(band, callback)

            if not self._stop_event.is_set():
                self._completed_cycles += 1
                if not self.loop:
                    break

        logger.info("SweepManager: sweep loop exited after %d cycles.", self._completed_cycles)

    def _tune_to_band(self, band: FrequencyBand) -> None:
        """Retune the scanner to *band*.

        Real hardware re-tuning is hardware-specific; for simulation mode
        we just update the scanner's ``center_freq_hz`` attribute.
        """
        self.scanner.center_freq_hz = band.center_hz
        if band.sample_rate_hz is not None:
            self.scanner.sample_rate_hz = band.sample_rate_hz
        if band.gain_db is not None:
            self.scanner.gain_db = band.gain_db

        logger.debug(
            "SweepManager: tuned %s to %.3f MHz (%s).",
            self.scanner.unit_id,
            band.center_hz / 1e6,
            band.label or "—",
        )

    def _dwell(self, band: FrequencyBand, callback: Callable[[ScanResult], None]) -> None:
        """Scan at the current band for ``band.dwell_s`` seconds."""
        deadline = time.monotonic() + band.dwell_s
        while time.monotonic() < deadline and not self._stop_event.is_set():
            try:
                result = self.scanner.scan_once()
                callback(result)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "SweepManager: scan error at %.3f MHz: %s",
                    band.center_hz / 1e6,
                    exc,
                )
            # Brief yield to prevent hammering CPU
            time.sleep(0.01)

    # ------------------------------------------------------------------
    # Class-method factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, scanner: BaseScanner, sweep_cfg: dict) -> "SweepManager":
        """Build a :class:`SweepManager` from a YAML config dict.

        Expected shape::

            sweep:
              enabled: true
              loop: true
              bands:
                - center_hz: 433000000
                  dwell_s: 0.5
                  label: "433 MHz ISM"
                - center_hz: 915000000
                  dwell_s: 0.5
                  label: "915 MHz ISM"

        Args:
            scanner: The scanner to attach to the sweep.
            sweep_cfg: The ``sweep`` sub-dict from the YAML config.

        Returns:
            A configured :class:`SweepManager`.
        """
        raw_bands = sweep_cfg.get("bands", [])
        bands = [
            FrequencyBand(
                center_hz=float(b["center_hz"]),
                dwell_s=float(b.get("dwell_s", 0.5)),
                label=b.get("label", ""),
                sample_rate_hz=b.get("sample_rate_hz"),
                gain_db=b.get("gain_db"),
            )
            for b in raw_bands
        ]
        return cls(scanner=scanner, bands=bands, loop=sweep_cfg.get("loop", True))
