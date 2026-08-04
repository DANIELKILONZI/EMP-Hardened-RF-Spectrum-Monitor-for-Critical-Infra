"""Tests for alert manager, SNMP sink, and Splunk sink."""

import time

import pytest

from src.alerts.alert_manager import AlertEvent, AlertManager, AlertSeverity
from src.alerts.snmp_alert import SNMPAlert
from src.alerts.splunk_alert import SplunkAlert
from src.analyzer.signal_detector import DetectedSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_detection(
    unit_id="u0",
    freq_hz=915e6,
    bw_hz=200e3,
    power=-75.0,
    persistence_s=35.0,
    signal_class="unknown",
    location=None,
):
    d = DetectedSignal(
        unit_id=unit_id,
        center_freq_hz=freq_hz,
        bandwidth_hz=bw_hz,
        peak_power_db=power,
        first_seen=time.time() - persistence_s,
        last_seen=time.time(),
        persistence_s=persistence_s,
        location=location,
        signal_class=signal_class,
    )
    return d


def make_event(
    severity=AlertSeverity.WARNING,
    unit_id="u0",
    description="test signal",
    details=None,
):
    return AlertEvent(
        event_id=f"test-{time.time()}",
        severity=severity,
        unit_id=unit_id,
        description=description,
        details=details or {},
    )


# ---------------------------------------------------------------------------
# AlertEvent tests
# ---------------------------------------------------------------------------

class TestAlertEvent:
    def test_to_dict_contains_required_fields(self):
        ev = make_event()
        d = ev.to_dict()
        assert "event_id" in d
        assert "severity" in d
        assert "unit_id" in d
        assert "description" in d
        assert "timestamp" in d

    def test_severity_serialised_as_string(self):
        ev = make_event(severity=AlertSeverity.CRITICAL)
        assert ev.to_dict()["severity"] == "critical"

    def test_timestamp_is_recent(self):
        before = time.time()
        ev = make_event()
        assert ev.timestamp >= before


# ---------------------------------------------------------------------------
# AlertManager tests
# ---------------------------------------------------------------------------

class _CaptureSink:
    """Test double that captures delivered events."""
    name = "capture"

    def __init__(self, should_fail=False):
        self.events = []
        self.should_fail = should_fail

    def send(self, event: AlertEvent) -> bool:
        self.events.append(event)
        return not self.should_fail


class TestAlertManager:
    def setup_method(self):
        self.mgr = AlertManager(dedup_window_s=1.0)
        self.sink = _CaptureSink()
        self.mgr.register_sink(self.sink)

    def test_send_alert_reaches_sink(self):
        ev = make_event()
        self.mgr.send_alert(ev)
        assert len(self.sink.events) == 1
        assert self.sink.events[0].event_id == ev.event_id

    def test_deduplication_suppresses_repeat(self):
        ev = make_event(description="dup-test")
        self.mgr.send_alert(ev)
        self.mgr.send_alert(ev)  # duplicate within window
        assert len(self.sink.events) == 1

    def test_deduplication_allows_after_window(self):
        mgr = AlertManager(dedup_window_s=0.05)
        sink = _CaptureSink()
        mgr.register_sink(sink)
        ev = make_event(description="timed-dup")
        mgr.send_alert(ev)
        time.sleep(0.1)
        mgr.send_alert(ev)
        assert len(sink.events) == 2

    def test_multiple_sinks_all_receive(self):
        sink2 = _CaptureSink()
        self.mgr.register_sink(sink2)
        self.mgr.send_alert(make_event())
        assert len(self.sink.events) == 1
        assert len(sink2.events) == 1

    def test_failing_sink_does_not_break_others(self):
        fail_sink = _CaptureSink(should_fail=True)
        self.mgr.register_sink(fail_sink)
        self.mgr.send_alert(make_event())
        assert len(self.sink.events) == 1  # good sink still received

    def test_unregister_sink(self):
        self.mgr.unregister_sink("capture")
        self.mgr.send_alert(make_event())
        assert len(self.sink.events) == 0

    def test_alert_from_detection_dispatched(self):
        d = make_detection(signal_class="gps_jammer")
        ev = self.mgr.alert_from_detection(d)
        assert len(self.sink.events) == 1
        assert "gps_jammer" in ev.description

    def test_severity_inferred_jammer(self):
        d = make_detection(signal_class="gps_jammer", power=-55.0)
        ev = self.mgr.alert_from_detection(d)
        assert ev.severity == AlertSeverity.CRITICAL

    def test_severity_inferred_drone(self):
        d = make_detection(signal_class="drone_control_2.4GHz", power=-75.0)
        ev = self.mgr.alert_from_detection(d)
        assert ev.severity == AlertSeverity.WARNING

    def test_severity_inferred_info(self):
        d = make_detection(signal_class="unknown", power=-78.0)
        ev = self.mgr.alert_from_detection(d)
        assert ev.severity == AlertSeverity.INFO

    def test_severity_override(self):
        d = make_detection(signal_class="unknown", power=-78.0)
        ev = self.mgr.alert_from_detection(d, severity=AlertSeverity.EMERGENCY)
        assert ev.severity == AlertSeverity.EMERGENCY


# ---------------------------------------------------------------------------
# SNMPAlert (simulation mode) tests
# ---------------------------------------------------------------------------

class TestSNMPAlert:
    def setup_method(self):
        self.snmp = SNMPAlert(host="127.0.0.1", port=162, community="public")

    def test_send_returns_true_in_simulation(self):
        ev = make_event(severity=AlertSeverity.CRITICAL)
        ev.details = make_detection().to_dict()
        result = self.snmp.send(ev)
        assert result is True

    def test_name_property(self):
        assert self.snmp.name == "snmp"

    def test_trap_oid_jammer(self):
        ev = make_event()
        ev.details = {"signal_class": "gps_jammer"}
        oid = self.snmp._select_trap_oid(ev)
        assert "2" in oid  # TRAP_OID_JAMMER ends in .2

    def test_trap_oid_drone(self):
        ev = make_event()
        ev.details = {"signal_class": "drone_control_2.4GHz"}
        oid = self.snmp._select_trap_oid(ev)
        assert "3" in oid  # TRAP_OID_DRONE ends in .3

    def test_trap_oid_default(self):
        ev = make_event()
        ev.details = {"signal_class": "unknown"}
        oid = self.snmp._select_trap_oid(ev)
        assert oid.endswith(".1")


# ---------------------------------------------------------------------------
# SplunkAlert (simulation mode) tests
# ---------------------------------------------------------------------------

class TestSplunkAlert:
    def setup_method(self):
        # No token → simulation mode (local log only)
        self.splunk = SplunkAlert(token="")

    def test_send_returns_true_simulation(self):
        ev = make_event()
        result = self.splunk.send(ev)
        assert result is True

    def test_name_property(self):
        assert self.splunk.name == "splunk"

    def test_repr_contains_url(self):
        r = repr(self.splunk)
        assert "SplunkAlert" in r
        assert "localhost" in r

    def test_send_with_real_token_handles_network_error(self):
        """Should return False (not raise) when network is unreachable."""
        splunk = SplunkAlert(
            hec_url="http://192.0.2.1:8088/services/collector/event",
            token="fake-token",
            timeout_s=0.5,
        )
        result = splunk.send(make_event())
        assert result is False


# ---------------------------------------------------------------------------
# DetectedSignal helper tests
# ---------------------------------------------------------------------------

class TestDetectedSignal:
    def test_frequency_mhz_property(self):
        d = make_detection(freq_hz=1575.42e6)
        assert abs(d.frequency_mhz - 1575.42) < 0.01

    def test_to_dict_complete(self):
        d = make_detection(location=(38.8, -77.0, 10.0))
        dd = d.to_dict()
        assert dd["location"] == (38.8, -77.0, 10.0)
        assert "center_freq_mhz" in dd
        assert "bandwidth_khz" in dd
        assert "peak_power_dbm" in dd
        assert "persistence_s" in dd
        assert "signal_class" in dd
