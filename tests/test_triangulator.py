"""Tests for signal geo-location / triangulation."""

import json
import math
import os

import pytest

from src.geo.triangulator import (
    RSSTriangulator,
    SignalObservation,
    TriangulationResult,
    _haversine_m,
)


# ---------------------------------------------------------------------------
# Haversine distance helper
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_same_point(self):
        assert _haversine_m(38.9, -77.0, 38.9, -77.0) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance(self):
        # Approx 1 degree latitude ≈ 111 km
        d = _haversine_m(0.0, 0.0, 1.0, 0.0)
        assert 110_000 < d < 112_000


# ---------------------------------------------------------------------------
# SignalObservation
# ---------------------------------------------------------------------------

class TestSignalObservation:
    def test_creation(self):
        obs = SignalObservation("node-1", 38.9, -77.0, -72.0, 2.45e9)
        assert obs.unit_id == "node-1"
        assert obs.power_db == -72.0

    def test_timestamp_auto(self):
        import time
        before = time.time()
        obs = SignalObservation("n", 0, 0, -80, 1e9)
        assert obs.timestamp >= before


# ---------------------------------------------------------------------------
# RSSTriangulator
# ---------------------------------------------------------------------------

def _make_obs(unit_id, lat, lon, power_db, freq=2.45e9, t=None):
    import time
    return SignalObservation(unit_id, lat, lon, power_db, freq, timestamp=t or time.time())


class TestRSSTriangulator:
    def setup_method(self):
        self.tri = RSSTriangulator(min_observations=3, max_age_s=60.0)

    def test_add_observation(self):
        self.tri.add_observation(_make_obs("n1", 38.9, -77.0, -70.0))
        assert len(self.tri._observations) == 1

    def test_estimate_returns_none_too_few_obs(self):
        self.tri.add_observation(_make_obs("n1", 38.9, -77.0, -70.0))
        self.tri.add_observation(_make_obs("n2", 38.91, -77.01, -75.0))
        result = self.tri.estimate()
        assert result is None

    def test_estimate_with_three_obs_returns_result(self):
        # Three nodes surrounding a known emitter at (38.90, -77.02)
        self.tri.add_observation(_make_obs("n1", 38.88, -77.00, -65.0))
        self.tri.add_observation(_make_obs("n2", 38.92, -77.04, -70.0))
        self.tri.add_observation(_make_obs("n3", 38.90, -77.06, -75.0))
        result = self.tri.estimate(freq_hz=2.45e9, signal_class="drone")
        assert result is not None
        assert isinstance(result, TriangulationResult)
        assert -90.0 <= result.lat <= 90.0
        assert -180.0 <= result.lon <= 180.0

    def test_estimate_result_attrs(self):
        for i, (lat, lon, pwr) in enumerate([(38.88, -77.00, -65), (38.92, -77.04, -70), (38.90, -77.06, -75)]):
            self.tri.add_observation(_make_obs(f"n{i}", lat, lon, pwr))
        result = self.tri.estimate(signal_class="gps_jammer")
        assert result.signal_class == "gps_jammer"
        assert result.confidence_m >= 0.0
        assert result.method in ("least_squares", "centroid")

    def test_estimate_attaches_observations(self):
        for i, (lat, lon, pwr) in enumerate([(38.88, -77.00, -65), (38.92, -77.04, -70), (38.90, -77.06, -75)]):
            self.tri.add_observation(_make_obs(f"n{i}", lat, lon, pwr))
        result = self.tri.estimate()
        assert len(result.observations) == 3

    def test_stale_observations_purged(self):
        import time
        old_time = time.time() - 120.0  # older than max_age_s=60
        self.tri.add_observation(_make_obs("old", 38.9, -77.0, -70.0, t=old_time))
        self.tri.add_observation(_make_obs("n2", 38.91, -77.01, -72.0))
        result = self.tri.estimate()
        assert result is None  # only 1 fresh obs after purge

    def test_clear(self):
        self.tri.add_observation(_make_obs("n1", 38.9, -77.0, -70.0))
        self.tri.clear()
        assert len(self.tri._observations) == 0

    def test_to_dict(self):
        for i, (lat, lon, pwr) in enumerate([(38.88, -77.00, -65), (38.92, -77.04, -70), (38.90, -77.06, -75)]):
            self.tri.add_observation(_make_obs(f"n{i}", lat, lon, pwr))
        result = self.tri.estimate()
        d = result.to_dict()
        assert "lat" in d
        assert "lon" in d
        assert "confidence_m" in d
        assert "method" in d


class TestTriangulatorExport:
    def setup_method(self):
        self.tri = RSSTriangulator(min_observations=3, max_age_s=60.0)
        for i, (lat, lon, pwr) in enumerate([(38.88, -77.00, -65), (38.92, -77.04, -70), (38.90, -77.06, -75)]):
            self.tri.add_observation(_make_obs(f"n{i}", lat, lon, pwr))
        self.result = self.tri.estimate(signal_class="test")

    def test_export_geojson(self, tmp_path):
        out = str(tmp_path / "output" / "emitter.geojson")
        self.tri.export_geojson(self.result, out)
        assert os.path.exists(out)
        with open(out) as fh:
            gj = json.load(fh)
        assert gj["type"] == "FeatureCollection"
        assert len(gj["features"]) >= 1
        # Emitter feature should be first
        assert gj["features"][0]["geometry"]["type"] == "Point"

    def test_export_kml(self, tmp_path):
        out = str(tmp_path / "output" / "emitter.kml")
        self.tri.export_kml(self.result, out)
        assert os.path.exists(out)
        content = open(out).read()
        assert '<?xml version' in content
        assert '<kml' in content
        assert '<Placemark>' in content
