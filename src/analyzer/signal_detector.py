"""Signal detector with configurable threshold RSSI rules.

Implements the detection rule from the problem statement:

    "Flag unknown signals >-80 dBm persisting 30 seconds."

Signals are tracked per frequency bin and per monitoring unit.  When a
signal has been continuously above the threshold for ``persistence_s``
seconds it is promoted to a confirmed :class:`DetectedSignal`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..scanner.base_scanner import ScanResult

logger = logging.getLogger(__name__)

# Default detection parameters (match problem statement)
DEFAULT_THRESHOLD_DB = -80.0  # dBm
DEFAULT_PERSISTENCE_S = 30.0  # seconds


@dataclass
class DetectedSignal:
    """A confirmed rogue/threat signal detection.

    Attributes:
        unit_id: ID of the monitoring unit that detected the signal.
        center_freq_hz: Approximate centre frequency of the signal (Hz).
        bandwidth_hz: Estimated bandwidth of the signal (Hz).
        peak_power_db: Peak power observed (dBm).
        first_seen: Unix timestamp of first detection.
        last_seen: Unix timestamp of most recent detection.
        persistence_s: Duration the signal has been continuously above
            threshold (seconds).
        location: (lat, lon, alt_m) from GPS if available.
        signal_class: Classification label (e.g. ``"jammer"``, ``"drone"``,
            ``"unknown"``).
    """

    unit_id: str
    center_freq_hz: float
    bandwidth_hz: float
    peak_power_db: float
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    persistence_s: float = 0.0
    location: Optional[Tuple[float, float, float]] = None
    signal_class: str = "unknown"

    @property
    def frequency_mhz(self) -> float:
        """Centre frequency in MHz."""
        return self.center_freq_hz / 1e6

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary (for SNMP/Splunk payloads)."""
        return {
            "unit_id": self.unit_id,
            "center_freq_mhz": self.frequency_mhz,
            "bandwidth_khz": self.bandwidth_hz / 1e3,
            "peak_power_dbm": self.peak_power_db,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "persistence_s": self.persistence_s,
            "location": self.location,
            "signal_class": self.signal_class,
        }


# Internal tracking entry
@dataclass
class _TrackEntry:
    first_seen: float
    last_seen: float
    peak_power: float
    freq_hz: float
    bandwidth_hz: float
    confirmed: bool = False


class SignalDetector:
    """Stateful signal detector implementing threshold + persistence rules.

    Args:
        threshold_db: Power threshold in dBm.  Signals above this level are
            candidates for detection.
        persistence_s: Seconds a candidate signal must persist before it is
            elevated to a confirmed :class:`DetectedSignal`.
        bin_merge_hz: Frequency window within which adjacent active bins are
            merged into a single signal track (Hz).
        max_track_age_s: Seconds of silence after which a track is removed.
    """

    def __init__(
        self,
        threshold_db: float = DEFAULT_THRESHOLD_DB,
        persistence_s: float = DEFAULT_PERSISTENCE_S,
        bin_merge_hz: float = 500_000,   # 500 kHz merge window
        max_track_age_s: float = 60.0,
    ) -> None:
        self.threshold_db = threshold_db
        self.persistence_s = persistence_s
        self.bin_merge_hz = bin_merge_hz
        self.max_track_age_s = max_track_age_s

        # Active tracks: { unit_id -> { track_key -> _TrackEntry } }
        self._tracks: Dict[str, Dict[str, _TrackEntry]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, result: ScanResult) -> List[DetectedSignal]:
        """Process a :class:`ScanResult` and return newly confirmed detections.

        Args:
            result: A (possibly pre-processed) scan result.

        Returns:
            List of :class:`DetectedSignal` objects that have just crossed
            the persistence threshold in this call.  Already-confirmed
            signals are returned again only if they are still active.
        """
        now = result.timestamp
        unit_id = result.unit_id

        # 1. Find bins above threshold
        active_bins = result.bins_above_threshold(self.threshold_db)

        # 2. Merge adjacent bins into clusters
        clusters = self._merge_bins(active_bins)

        # 3. Update / create tracks
        self._ensure_unit(unit_id)
        matched_keys = set()
        for cluster_freq, cluster_bw, cluster_peak in clusters:
            key = self._find_or_create_track(unit_id, cluster_freq, cluster_bw, cluster_peak, now)
            matched_keys.add(key)

        # 4. Age out silent tracks
        self._age_tracks(unit_id, matched_keys, now)

        # 5. Collect confirmed detections
        confirmed: List[DetectedSignal] = []
        for key, track in list(self._tracks[unit_id].items()):
            duration = track.last_seen - track.first_seen
            if duration >= self.persistence_s:
                sig = DetectedSignal(
                    unit_id=unit_id,
                    center_freq_hz=track.freq_hz,
                    bandwidth_hz=track.bandwidth_hz,
                    peak_power_db=track.peak_power,
                    first_seen=track.first_seen,
                    last_seen=track.last_seen,
                    persistence_s=duration,
                    location=result.location,
                    signal_class=self._classify(track.freq_hz, track.bandwidth_hz, track.peak_power),
                )
                track.confirmed = True
                confirmed.append(sig)

        return confirmed

    def get_active_tracks(self, unit_id: str) -> List[dict]:
        """Return all active (not yet confirmed) tracks for *unit_id*."""
        tracks = self._tracks.get(unit_id, {})
        result = []
        for key, t in tracks.items():
            result.append({
                "key": key,
                "freq_mhz": t.freq_hz / 1e6,
                "peak_power_dbm": t.peak_power,
                "duration_s": t.last_seen - t.first_seen,
                "confirmed": t.confirmed,
            })
        return result

    def reset(self, unit_id: Optional[str] = None) -> None:
        """Clear tracking state.

        Args:
            unit_id: If given, only clear tracks for that unit; otherwise
                clear all units.
        """
        if unit_id:
            self._tracks.pop(unit_id, None)
        else:
            self._tracks.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_unit(self, unit_id: str) -> None:
        if unit_id not in self._tracks:
            self._tracks[unit_id] = {}

    def _merge_bins(
        self, active_bins: List[Tuple[float, float]]
    ) -> List[Tuple[float, float, float]]:
        """Merge adjacent frequency bins into clusters.

        Returns list of (center_freq_hz, bandwidth_hz, peak_power_db).
        """
        if not active_bins:
            return []

        sorted_bins = sorted(active_bins, key=lambda x: x[0])
        clusters: List[List[Tuple[float, float]]] = [[sorted_bins[0]]]

        for freq, power in sorted_bins[1:]:
            if freq - clusters[-1][-1][0] <= self.bin_merge_hz:
                clusters[-1].append((freq, power))
            else:
                clusters.append([(freq, power)])

        result = []
        for cluster in clusters:
            freqs = [b[0] for b in cluster]
            powers = [b[1] for b in cluster]
            center = (freqs[0] + freqs[-1]) / 2.0
            bw = max(freqs[-1] - freqs[0], 1.0)
            peak = max(powers)
            result.append((center, bw, peak))
        return result

    def _find_or_create_track(
        self,
        unit_id: str,
        freq_hz: float,
        bandwidth_hz: float,
        peak_power: float,
        now: float,
    ) -> str:
        """Match a cluster to an existing track or create a new one."""
        for key, track in self._tracks[unit_id].items():
            if abs(track.freq_hz - freq_hz) <= self.bin_merge_hz:
                track.last_seen = now
                track.peak_power = max(track.peak_power, peak_power)
                track.bandwidth_hz = max(track.bandwidth_hz, bandwidth_hz)
                return key

        key = f"{freq_hz:.0f}"
        self._tracks[unit_id][key] = _TrackEntry(
            first_seen=now,
            last_seen=now,
            peak_power=peak_power,
            freq_hz=freq_hz,
            bandwidth_hz=bandwidth_hz,
        )
        logger.debug("%s: new signal track at %.3f MHz (%.1f dBm)", unit_id, freq_hz / 1e6, peak_power)
        return key

    def _age_tracks(
        self, unit_id: str, matched_keys: set, now: float
    ) -> None:
        """Remove tracks that have not been seen recently."""
        stale = [
            k
            for k, t in self._tracks[unit_id].items()
            if k not in matched_keys and (now - t.last_seen) > self.max_track_age_s
        ]
        for k in stale:
            del self._tracks[unit_id][k]
            logger.debug("%s: removed stale track %s", unit_id, k)

    @staticmethod
    def _classify(freq_hz: float, bandwidth_hz: float, power_db: float) -> str:
        """Heuristic signal classification.

        Rules are intentionally simple; a production system would use a
        trained classifier or knowledge-base lookup.
        """
        freq_mhz = freq_hz / 1e6
        bw_mhz = bandwidth_hz / 1e6

        # ISM drone control links (2.4 GHz, 5.8 GHz)
        if 2390 <= freq_mhz <= 2490 and bw_mhz < 30:
            return "drone_control_2.4GHz"
        if 5725 <= freq_mhz <= 5875 and bw_mhz < 50:
            return "drone_control_5.8GHz"

        # GPS jamming (L1 band: 1575.42 MHz)
        if 1560 <= freq_mhz <= 1590 and power_db > -70:
            return "gps_jammer"

        # GSM/LTE jamming (700–900 MHz, broadband)
        if 700 <= freq_mhz <= 960 and bw_mhz > 5:
            return "cellular_jammer"

        # ATC / VHF (118–137 MHz)
        if 118 <= freq_mhz <= 137:
            return "vhf_air_band"

        return "unknown"
