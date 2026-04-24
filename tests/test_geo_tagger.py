"""Tests for GPS geo-tagger module."""

import time

import pytest

from src.gps.geo_tagger import GeoLocation, GeoTagger


# ---------------------------------------------------------------------------
# GeoLocation tests
# ---------------------------------------------------------------------------

class TestGeoLocation:
    def test_to_tuple(self):
        loc = GeoLocation(latitude=38.8977, longitude=-77.0365, altitude_m=10.0)
        t = loc.to_tuple()
        assert t == (38.8977, -77.0365, 10.0)

    def test_to_dict_fields(self):
        loc = GeoLocation(
            latitude=51.5074,
            longitude=-0.1278,
            altitude_m=5.0,
            accuracy_m=3.0,
            timestamp=1000.0,
            fix_quality="gps",
        )
        d = loc.to_dict()
        assert d["latitude"] == 51.5074
        assert d["longitude"] == -0.1278
        assert d["altitude_m"] == 5.0
        assert d["accuracy_m"] == 3.0
        assert d["fix_quality"] == "gps"

    def test_default_fix_quality_is_none(self):
        loc = GeoLocation(latitude=0.0, longitude=0.0)
        assert loc.fix_quality == "none"


# ---------------------------------------------------------------------------
# GeoTagger – static mode (no GPS hardware)
# ---------------------------------------------------------------------------

class TestGeoTaggerStatic:
    def setup_method(self):
        self.tagger = GeoTagger(
            serial_port=None,
            static_lat=38.8977,
            static_lon=-77.0365,
            static_alt=10.0,
        )

    def test_initial_location_before_start(self):
        loc = self.tagger.location
        assert loc.latitude == 38.8977
        assert loc.longitude == -77.0365
        assert loc.altitude_m == 10.0
        assert loc.fix_quality == "static"

    def test_start_stop_in_static_mode(self):
        self.tagger.start()  # should not raise
        self.tagger.stop()

    def test_get_tuple(self):
        t = self.tagger.get_tuple()
        assert t == (38.8977, -77.0365, 10.0)

    def test_thread_not_started_in_static_mode(self):
        self.tagger.start()
        assert self.tagger._thread is None
        self.tagger.stop()

    def test_location_is_thread_safe(self):
        """Accessing location from multiple threads should not raise."""
        import threading
        results = []
        errors = []

        def read_loc():
            try:
                results.append(self.tagger.get_tuple())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_loc) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 20


# ---------------------------------------------------------------------------
# GeoTagger NMEA GGA parser
# ---------------------------------------------------------------------------

class TestGeoTaggerNMEAParser:
    """Unit-test the static NMEA GGA sentence parser directly."""

    def test_valid_gpgga(self):
        # GPGGA sentence: time, lat, N, lon, W, fix=1, sats, hdop, alt, M
        sentence = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
        loc = GeoTagger._parse_gga(sentence)
        assert loc is not None
        assert abs(loc.latitude - 48.117) < 0.001
        assert abs(loc.longitude - 11.517) < 0.001
        assert loc.fix_quality == "gps"

    def test_valid_south_west(self):
        sentence = "$GPGGA,123519,3348.500,S,07030.000,W,1,08,1.2,10.0,M,0.0,M,,*00"
        loc = GeoTagger._parse_gga(sentence)
        assert loc is not None
        assert loc.latitude < 0   # South → negative
        assert loc.longitude < 0  # West → negative

    def test_no_fix_returns_none(self):
        sentence = "$GPGGA,123519,0000.000,N,00000.000,E,0,00,99.9,0.0,M,0.0,M,,*00"
        loc = GeoTagger._parse_gga(sentence)
        assert loc is None

    def test_invalid_sentence_returns_none(self):
        loc = GeoTagger._parse_gga("NOT,A,VALID,SENTENCE")
        assert loc is None

    def test_short_sentence_returns_none(self):
        loc = GeoTagger._parse_gga("$GPGGA,123519")
        assert loc is None

    def test_altitude_parsed(self):
        sentence = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,100.5,M,46.9,M,,*47"
        loc = GeoTagger._parse_gga(sentence)
        assert loc is not None
        assert abs(loc.altitude_m - 100.5) < 0.1

    def test_hdop_used_for_accuracy(self):
        sentence = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,2.0,545.4,M,46.9,M,,*47"
        loc = GeoTagger._parse_gga(sentence)
        assert loc is not None
        # accuracy_m = hdop * 2.5 = 2.0 * 2.5 = 5.0
        assert abs(loc.accuracy_m - 5.0) < 0.01

    def test_gngga_prefix_handled(self):
        sentence = "$GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
        loc = GeoTagger._parse_gga(sentence)
        assert loc is not None
