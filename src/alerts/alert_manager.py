"""Alert manager – coordinates all alert backends.

Receives :class:`~src.analyzer.signal_detector.DetectedSignal` events and
dispatches them to registered alert sinks (SNMP, Splunk, etc.) with
de-duplication and severity routing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels aligned with SNMP / Splunk conventions."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class AlertEvent:
    """A normalised alert event ready for dispatch.

    Attributes:
        event_id: Unique event identifier.
        severity: Severity level.
        unit_id: Originating monitoring unit.
        description: Human-readable description.
        details: Arbitrary metadata dict.
        timestamp: Unix epoch of the event.
        location: Optional (lat, lon, alt_m) tuple.
    """

    event_id: str
    severity: AlertSeverity
    unit_id: str
    description: str
    details: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    location: Optional[tuple] = None

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "severity": self.severity.value,
            "unit_id": self.unit_id,
            "description": self.description,
            "details": self.details,
            "timestamp": self.timestamp,
            "location": self.location,
        }


class AlertSink(Protocol):
    """Protocol that every alert backend must satisfy."""

    def send(self, event: AlertEvent) -> bool:
        """Send *event* to the sink.

        Returns:
            ``True`` if delivery succeeded, ``False`` otherwise.
        """
        ...

    @property
    def name(self) -> str:
        """Identifier for this sink."""
        ...


class AlertManager:
    """Coordinates dispatch of :class:`AlertEvent` to multiple sinks.

    Args:
        dedup_window_s: Seconds within which duplicate events (same unit,
            same description) are suppressed.
    """

    def __init__(self, dedup_window_s: float = 60.0) -> None:
        self.dedup_window_s = dedup_window_s
        self._sinks: List[AlertSink] = []
        # { dedup_key -> last_sent_timestamp }
        self._sent_cache: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Sink management
    # ------------------------------------------------------------------

    def register_sink(self, sink: AlertSink) -> None:
        """Register an alert sink.

        Args:
            sink: Object implementing the :class:`AlertSink` protocol.
        """
        self._sinks.append(sink)
        logger.info("AlertManager: registered sink %r", sink.name)

    def unregister_sink(self, name: str) -> None:
        """Remove a sink by name."""
        before = len(self._sinks)
        self._sinks = [s for s in self._sinks if s.name != name]
        if len(self._sinks) < before:
            logger.info("AlertManager: unregistered sink %r", name)

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    def send_alert(self, event: AlertEvent) -> None:
        """Dispatch *event* to all registered sinks.

        Duplicate events (matching unit_id + description) within
        ``dedup_window_s`` are dropped silently.

        Args:
            event: The alert event to dispatch.
        """
        dedup_key = f"{event.unit_id}:{event.description}"
        now = time.time()

        last_sent = self._sent_cache.get(dedup_key)
        if last_sent is not None and (now - last_sent) < self.dedup_window_s:
            logger.debug("AlertManager: suppressed duplicate event %r", dedup_key)
            return

        self._sent_cache[dedup_key] = now

        for sink in self._sinks:
            try:
                ok = sink.send(event)
                if ok:
                    logger.info(
                        "AlertManager: event %s dispatched to %s", event.event_id, sink.name
                    )
                else:
                    logger.warning(
                        "AlertManager: sink %s reported delivery failure for %s",
                        sink.name,
                        event.event_id,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error("AlertManager: exception sending to %s: %s", sink.name, exc)

    def alert_from_detection(
        self, detection: "DetectedSignal", severity: Optional[AlertSeverity] = None  # type: ignore[name-defined]  # noqa: F821
    ) -> AlertEvent:
        """Build and dispatch an :class:`AlertEvent` from a signal detection.

        Args:
            detection: A confirmed :class:`~src.analyzer.signal_detector.DetectedSignal`.
            severity: Override severity.  If ``None``, it is inferred from
                the signal class.

        Returns:
            The created :class:`AlertEvent` (already dispatched).
        """
        if severity is None:
            severity = self._infer_severity(detection.signal_class, detection.peak_power_db)

        event = AlertEvent(
            event_id=f"{detection.unit_id}-{detection.center_freq_hz:.0f}-{detection.first_seen:.0f}",
            severity=severity,
            unit_id=detection.unit_id,
            description=(
                f"{detection.signal_class} signal detected at "
                f"{detection.frequency_mhz:.3f} MHz ({detection.peak_power_db:.1f} dBm, "
                f"{detection.persistence_s:.0f}s)"
            ),
            details=detection.to_dict(),
            timestamp=detection.last_seen,
            location=detection.location,
        )
        self.send_alert(event)
        return event

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_severity(signal_class: str, power_db: float) -> AlertSeverity:
        if "jammer" in signal_class or power_db > -60:
            return AlertSeverity.CRITICAL
        if "drone" in signal_class:
            return AlertSeverity.WARNING
        return AlertSeverity.INFO
