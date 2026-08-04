"""Spectrum analyzer with GNU Radio flowgraph integration.

Processes raw :class:`~src.scanner.base_scanner.ScanResult` objects,
applies calibration, and optionally routes data through a GNU Radio
flowgraph for advanced filtering.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from ..scanner.base_scanner import ScanResult

logger = logging.getLogger(__name__)

_GNURADIO_AVAILABLE = False
try:
    from gnuradio import gr, blocks, fft as gr_fft  # type: ignore
    _GNURADIO_AVAILABLE = True
except ImportError:
    logger.warning("GNU Radio not installed – SpectrumAnalyzer will use NumPy-only PSD processing.")


class SpectrumAnalyzer:
    """Analyses spectrum scan results and maintains a rolling history.

    Args:
        history_seconds: How many seconds of scan history to retain.
        calibration_offset_db: Fixed offset added to every power measurement
            (corrects for cable losses, LNA gain, etc.).
        smoothing_alpha: Exponential moving average coefficient applied to the
            PSD before analysis (0 = no smoothing, 1 = always use latest).
    """

    def __init__(
        self,
        history_seconds: float = 120.0,
        calibration_offset_db: float = 0.0,
        smoothing_alpha: float = 0.7,
    ) -> None:
        self.history_seconds = history_seconds
        self.calibration_offset_db = calibration_offset_db
        self.smoothing_alpha = smoothing_alpha

        # Rolling history: deque of ScanResult objects
        self._history: Deque[ScanResult] = deque()

        # Per-unit smoothed PSD state  { unit_id -> np.ndarray }
        self._smoothed_psd: Dict[str, np.ndarray] = {}

        # Baseline (median) PSD per unit for anomaly detection
        self._baseline_psd: Dict[str, np.ndarray] = {}

        if _GNURADIO_AVAILABLE:
            logger.info("GNU Radio detected – advanced flowgraph processing available.")

    # ------------------------------------------------------------------
    # Primary analysis method
    # ------------------------------------------------------------------

    def process(self, result: ScanResult) -> ScanResult:
        """Apply calibration, smoothing, and store in history.

        Args:
            result: Raw :class:`ScanResult` from a scanner.

        Returns:
            Processed :class:`ScanResult` with calibration applied.
        """
        psd = np.array(result.power_db) + self.calibration_offset_db
        psd = self._apply_smoothing(result.unit_id, psd)

        processed = ScanResult(
            center_freq_hz=result.center_freq_hz,
            sample_rate_hz=result.sample_rate_hz,
            frequencies_hz=result.frequencies_hz,
            power_db=psd.tolist(),
            timestamp=result.timestamp,
            unit_id=result.unit_id,
            location=result.location,
        )

        self._store(processed)
        self._update_baseline(result.unit_id, psd)
        return processed

    # ------------------------------------------------------------------
    # Baseline and anomaly helpers
    # ------------------------------------------------------------------

    def get_baseline(self, unit_id: str) -> Optional[np.ndarray]:
        """Return the median PSD baseline for *unit_id*, or ``None``."""
        return self._baseline_psd.get(unit_id)

    def compute_anomaly_score(
        self, result: ScanResult
    ) -> Tuple[float, np.ndarray]:
        """Compute a scalar anomaly score for a processed *result*.

        The score is the mean excess power above the per-unit baseline.
        Returns ``(score, per_bin_excess_db)`` where negative values
        indicate below-baseline bins.

        Args:
            result: A processed :class:`ScanResult`.

        Returns:
            Tuple of ``(mean_excess_db, per_bin_excess_db_array)``.
        """
        baseline = self._baseline_psd.get(result.unit_id)
        psd = np.array(result.power_db)
        if baseline is None or baseline.shape != psd.shape:
            return 0.0, np.zeros_like(psd)

        excess = psd - baseline
        return float(np.mean(excess)), excess

    # ------------------------------------------------------------------
    # History access
    # ------------------------------------------------------------------

    def get_history(
        self, unit_id: Optional[str] = None, last_n_seconds: Optional[float] = None
    ) -> List[ScanResult]:
        """Return stored scan history, optionally filtered.

        Args:
            unit_id: If provided, only return results from this unit.
            last_n_seconds: If provided, only return results from this
                many seconds in the past.

        Returns:
            List of :class:`ScanResult` objects.
        """
        now = time.time()
        results = list(self._history)
        if unit_id is not None:
            results = [r for r in results if r.unit_id == unit_id]
        if last_n_seconds is not None:
            cutoff = now - last_n_seconds
            results = [r for r in results if r.timestamp >= cutoff]
        return results

    def clear_history(self) -> None:
        """Purge all stored history."""
        self._history.clear()

    # ------------------------------------------------------------------
    # Waterfall data helper
    # ------------------------------------------------------------------

    def get_waterfall_matrix(
        self, unit_id: str, max_rows: int = 100
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return a 2-D power matrix suitable for waterfall rendering.

        Args:
            unit_id: Unit whose history to use.
            max_rows: Maximum number of time rows to return.

        Returns:
            Tuple of ``(matrix, timestamps, frequencies_hz)`` where *matrix*
            has shape ``(time, freq)`` and values are in dBm.
        """
        records = [r for r in self._history if r.unit_id == unit_id][-max_rows:]
        if not records:
            return np.empty((0, 0)), np.array([]), np.array([])

        n_freq = len(records[0].power_db)
        matrix = np.full((len(records), n_freq), -200.0)
        timestamps = np.zeros(len(records))
        for i, rec in enumerate(records):
            row_len = min(len(rec.power_db), n_freq)
            matrix[i, :row_len] = rec.power_db[:row_len]
            timestamps[i] = rec.timestamp

        freqs = np.array(records[0].frequencies_hz)
        return matrix, timestamps, freqs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_smoothing(self, unit_id: str, psd: np.ndarray) -> np.ndarray:
        """Apply exponential moving average smoothing."""
        if unit_id not in self._smoothed_psd or self._smoothed_psd[unit_id].shape != psd.shape:
            self._smoothed_psd[unit_id] = psd.copy()
            return psd
        alpha = self.smoothing_alpha
        self._smoothed_psd[unit_id] = alpha * psd + (1.0 - alpha) * self._smoothed_psd[unit_id]
        return self._smoothed_psd[unit_id].copy()

    def _store(self, result: ScanResult) -> None:
        """Append *result* to history and evict old entries."""
        self._history.append(result)
        cutoff = time.time() - self.history_seconds
        while self._history and self._history[0].timestamp < cutoff:
            self._history.popleft()

    def _update_baseline(self, unit_id: str, psd: np.ndarray) -> None:
        """Incrementally update the per-unit median baseline (lightweight)."""
        if unit_id not in self._baseline_psd or self._baseline_psd[unit_id].shape != psd.shape:
            self._baseline_psd[unit_id] = psd.copy()
        else:
            # Slow-update: nudge baseline towards current PSD
            self._baseline_psd[unit_id] = (
                0.99 * self._baseline_psd[unit_id] + 0.01 * psd
            )
