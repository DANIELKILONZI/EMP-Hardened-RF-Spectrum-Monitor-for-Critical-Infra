"""Signal geo-location / triangulation engine.

When the same RF signal is observed by three or more mesh nodes
simultaneously, this module estimates the emitter's 2-D position using
**Weighted Least-Squares RSS (Received Signal Strength)** ranging.

The underlying model is:

    RSS(i) = P_tx - 10 * n * log10(d_i)  + noise

where ``n`` is the path-loss exponent (default 2.0 for free-space),
``P_tx`` is the unknown transmit power, and ``d_i`` is the distance from
node *i* to the emitter.  Given RSS measurements from N ≥ 3 nodes the
system is solved iteratively via scipy (or falls back to a centroid estimate).

GeoJSON and KML export helpers are included so results can be loaded
directly into ArcGIS, Google Earth, QGIS, etc.

Typical usage::

    from src.geo.triangulator import RSSTriangulator, SignalObservation

    tri = RSSTriangulator()
    tri.add_observation(SignalObservation("node-A", lat=38.90, lon=-77.04, power_db=-65.0, freq_hz=2.45e9))
    tri.add_observation(SignalObservation("node-B", lat=38.91, lon=-77.03, power_db=-72.0, freq_hz=2.45e9))
    tri.add_observation(SignalObservation("node-C", lat=38.89, lon=-77.02, power_db=-78.0, freq_hz=2.45e9))
    result = tri.estimate()
    tri.export_geojson(result, "output/emitter.geojson")
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Attempt to use scipy for non-linear least-squares
_SCIPY_AVAILABLE = False
try:
    from scipy.optimize import minimize  # type: ignore

    _SCIPY_AVAILABLE = True
except ImportError:
    logger.info(
        "scipy not installed – triangulation will use weighted centroid fallback."
    )

# Earth radius for lat/lon ↔ metres conversions
_EARTH_RADIUS_M = 6_371_000.0


def _deg_to_rad(deg: float) -> float:
    return deg * math.pi / 180.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in metres between two WGS-84 points."""
    dlat = _deg_to_rad(lat2 - lat1)
    dlon = _deg_to_rad(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(_deg_to_rad(lat1)) * math.cos(_deg_to_rad(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2.0 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


@dataclass
class SignalObservation:
    """A single RSS observation of a signal from one mesh node.

    Attributes:
        unit_id: Identifier of the observing node.
        lat: Observer latitude (decimal degrees).
        lon: Observer longitude (decimal degrees).
        power_db: Received power in dBm.
        freq_hz: Observed frequency in Hz.
        timestamp: Unix epoch of the observation.
    """

    unit_id: str
    lat: float
    lon: float
    power_db: float
    freq_hz: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class TriangulationResult:
    """Estimated emitter position.

    Attributes:
        lat: Estimated latitude (decimal degrees).
        lon: Estimated longitude (decimal degrees).
        confidence_m: 1-sigma radius of uncertainty (metres).
        method: Algorithm used (``"least_squares"`` or ``"centroid"``).
        freq_hz: Centre frequency of the triangulated signal.
        signal_class: Classification label (passed through from detections).
        timestamp: Unix epoch of the estimate.
        observations: Raw observations used.
    """

    lat: float
    lon: float
    confidence_m: float
    method: str
    freq_hz: float
    signal_class: str = "unknown"
    timestamp: float = field(default_factory=time.time)
    observations: List[SignalObservation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "lat": self.lat,
            "lon": self.lon,
            "confidence_m": self.confidence_m,
            "method": self.method,
            "freq_hz": self.freq_hz,
            "signal_class": self.signal_class,
            "timestamp": self.timestamp,
            "num_observers": len(self.observations),
        }


class RSSTriangulator:
    """Estimates an RF emitter's 2-D position from multi-node RSS measurements.

    Args:
        path_loss_exp: Path-loss exponent *n* (2.0 = free-space, 3–4 = urban).
        min_observations: Minimum number of unique nodes required for a valid
            estimate (default 3).
        max_age_s: Observations older than this (seconds) are discarded.
    """

    def __init__(
        self,
        path_loss_exp: float = 2.0,
        min_observations: int = 3,
        max_age_s: float = 10.0,
    ) -> None:
        self.path_loss_exp = path_loss_exp
        self.min_observations = min_observations
        self.max_age_s = max_age_s
        self._observations: List[SignalObservation] = []

    # ------------------------------------------------------------------
    # Observation management
    # ------------------------------------------------------------------

    def add_observation(self, obs: SignalObservation) -> None:
        """Add an RSS observation and purge stale entries.

        Args:
            obs: A new :class:`SignalObservation` from a mesh node.
        """
        cutoff = time.time() - self.max_age_s
        self._observations = [o for o in self._observations if o.timestamp >= cutoff]
        self._observations.append(obs)

    def clear(self) -> None:
        """Clear all stored observations."""
        self._observations.clear()

    # ------------------------------------------------------------------
    # Estimation
    # ------------------------------------------------------------------

    def estimate(
        self, freq_hz: Optional[float] = None, signal_class: str = "unknown"
    ) -> Optional[TriangulationResult]:
        """Estimate the emitter position from current observations.

        Args:
            freq_hz: If provided, only use observations near this frequency
                (within 1 MHz).
            signal_class: Label to attach to the result.

        Returns:
            :class:`TriangulationResult`, or ``None`` if fewer than
            ``min_observations`` valid observations exist.
        """
        now = time.time()
        obs = [
            o for o in self._observations
            if now - o.timestamp <= self.max_age_s
            and (freq_hz is None or abs(o.freq_hz - freq_hz) < 1e6)
        ]

        if len(obs) < self.min_observations:
            logger.debug(
                "RSSTriangulator: only %d observations – need at least %d.",
                len(obs),
                self.min_observations,
            )
            return None

        if _SCIPY_AVAILABLE:
            result = self._estimate_least_squares(obs)
        else:
            result = self._estimate_centroid(obs)

        if result is None:
            return None

        est_lat, est_lon, conf_m, method = result
        return TriangulationResult(
            lat=est_lat,
            lon=est_lon,
            confidence_m=conf_m,
            method=method,
            freq_hz=freq_hz or obs[0].freq_hz,
            signal_class=signal_class,
            observations=obs,
        )

    # ------------------------------------------------------------------
    # Algorithms
    # ------------------------------------------------------------------

    def _rss_to_distance_m(self, rss_db: float, ref_power_db: float) -> float:
        """Convert an RSS value to an approximate range estimate (metres).

        Uses the log-distance path loss model with a 1-metre reference.
        The reference power ``ref_power_db`` is the strongest observation
        used as the proxy for 1-m RSSI.

        Args:
            rss_db: Received power at the observer (dBm).
            ref_power_db: Estimated received power at 1 m (dBm).

        Returns:
            Range in metres (minimum 1 m).
        """
        exponent = (ref_power_db - rss_db) / (10.0 * self.path_loss_exp)
        return max(1.0, 10.0 ** exponent)

    def _estimate_least_squares(
        self, obs: List[SignalObservation]
    ) -> Optional[Tuple[float, float, float, str]]:
        """Non-linear WLS position estimate via scipy.

        Returns:
            ``(lat, lon, confidence_m, method)`` or ``None`` on failure.
        """
        ref_power = max(o.power_db for o in obs)
        distances = [self._rss_to_distance_m(o.power_db, ref_power) for o in obs]
        weights = [1.0 / max(d, 1.0) for d in distances]  # closer = more weight

        # Convert observer lat/lon to local X/Y (metres) centred on centroid
        clat = np.mean([o.lat for o in obs])
        clon = np.mean([o.lon for o in obs])
        cos_lat = math.cos(_deg_to_rad(clat))
        lat_m = _EARTH_RADIUS_M * math.pi / 180.0
        lon_m = lat_m * cos_lat

        ox = np.array([(o.lon - clon) * lon_m for o in obs])
        oy = np.array([(o.lat - clat) * lat_m for o in obs])
        d  = np.array(distances)
        w  = np.array(weights)

        def cost(p: np.ndarray) -> float:
            px, py = p
            residuals = np.sqrt((ox - px) ** 2 + (oy - py) ** 2) - d
            return float(np.sum(w * residuals ** 2))

        x0 = np.array([np.average(ox, weights=w), np.average(oy, weights=w)])
        try:
            res = minimize(cost, x0, method="Nelder-Mead", options={"xatol": 1.0, "fatol": 0.1, "maxiter": 2000})
            est_lat = clat + res.x[1] / lat_m
            est_lon = clon + res.x[0] / lon_m
            # Confidence: RMS residual converted to metres
            px, py = res.x
            residuals = np.sqrt((ox - px) ** 2 + (oy - py) ** 2) - d
            conf_m = float(np.sqrt(np.mean(residuals ** 2)))
            return est_lat, est_lon, conf_m, "least_squares"
        except Exception as exc:  # noqa: BLE001
            logger.warning("RSSTriangulator: least-squares failed: %s", exc)
            return self._estimate_centroid(obs)

    def _estimate_centroid(
        self, obs: List[SignalObservation]
    ) -> Optional[Tuple[float, float, float, str]]:
        """Weighted centroid fallback (no scipy required).

        Uses received power (linear watts) as the weight so that closer
        (stronger) nodes pull the estimate towards themselves.

        Returns:
            ``(lat, lon, confidence_m, "centroid")`` or ``None``.
        """
        # Convert dBm → mW for weights
        weights_mw = [10.0 ** (o.power_db / 10.0) for o in obs]
        total_w = sum(weights_mw) or 1.0
        est_lat = sum(o.lat * w for o, w in zip(obs, weights_mw)) / total_w
        est_lon = sum(o.lon * w for o, w in zip(obs, weights_mw)) / total_w

        # Confidence = weighted std-dev of distances from centroid
        dists = [_haversine_m(o.lat, o.lon, est_lat, est_lon) for o in obs]
        conf_m = float(np.average(dists, weights=weights_mw))
        return est_lat, est_lon, conf_m, "centroid"

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------

    def export_geojson(
        self, result: TriangulationResult, path: str
    ) -> None:
        """Write the triangulation result to a GeoJSON file.

        Args:
            result: The :class:`TriangulationResult` to export.
            path: Output file path.
        """
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [result.lon, result.lat]},
                "properties": {
                    "marker-color": "#f85149",
                    "marker-size": "large",
                    "title": f"Emitter ({result.signal_class})",
                    **result.to_dict(),
                },
            }
        ]
        # Add observer positions
        for obs in result.observations:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [obs.lon, obs.lat]},
                    "properties": {
                        "marker-color": "#58a6ff",
                        "marker-size": "small",
                        "title": f"Observer: {obs.unit_id}",
                        "unit_id": obs.unit_id,
                        "power_db": obs.power_db,
                        "freq_hz": obs.freq_hz,
                    },
                }
            )

        geojson = {"type": "FeatureCollection", "features": features}
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(geojson, fh, indent=2)
        logger.info("GeoJSON exported to %s", path)

    def export_kml(
        self, result: TriangulationResult, path: str
    ) -> None:
        """Write the triangulation result to a KML file (Google Earth).

        Args:
            result: The :class:`TriangulationResult` to export.
            path: Output file path.
        """
        freq_mhz = result.freq_hz / 1e6
        obs_placemarks = ""
        for obs in result.observations:
            obs_placemarks += (
                f"<Placemark>"
                f"<name>{obs.unit_id}</name>"
                f"<description>Power: {obs.power_db:.1f} dBm</description>"
                f"<Point><coordinates>{obs.lon},{obs.lat},0</coordinates></Point>"
                f"</Placemark>\n"
            )

        kml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
            "<Document>\n"
            f"<name>RF Emitter {freq_mhz:.1f} MHz</name>\n"
            "<Placemark>\n"
            f"<name>Estimated Emitter ({result.signal_class})</name>\n"
            f"<description>"
            f"Freq: {freq_mhz:.3f} MHz | "
            f"Confidence: {result.confidence_m:.0f} m | "
            f"Method: {result.method}"
            f"</description>\n"
            "<Style><IconStyle><color>ff0000ff</color></IconStyle></Style>\n"
            f"<Point><coordinates>{result.lon},{result.lat},0</coordinates></Point>\n"
            "</Placemark>\n"
            f"{obs_placemarks}"
            "</Document>\n"
            "</kml>"
        )
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(kml)
        logger.info("KML exported to %s", path)
