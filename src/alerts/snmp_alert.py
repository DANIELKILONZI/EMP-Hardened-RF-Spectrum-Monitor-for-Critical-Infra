"""SNMP alert sink.

Sends SNMP v2c traps to a Network Management System (e.g., Splunk SNMP
input, Zabbix, or Nagios) when threat signals are detected.

Trap OID hierarchy (enterprise arc):
    1.3.6.1.4.1.99999.1.1   – RF Spectrum Monitor enterprise OID
    1.3.6.1.4.1.99999.1.1.1 – rfThreatDetected trap
"""

from __future__ import annotations

import logging
from typing import Optional

from .alert_manager import AlertEvent, AlertSeverity

logger = logging.getLogger(__name__)

_PYSNMP_AVAILABLE = False
try:
    from pysnmp.hlapi import (  # type: ignore
        CommunityData,
        ContextData,
        Integer32,
        NotificationType,
        ObjectIdentity,
        ObjectType,
        OctetString,
        SnmpEngine,
        UdpTransportTarget,
        sendNotification,
    )
    _PYSNMP_AVAILABLE = True
except ImportError:
    logger.warning("pysnmp not installed – SNMPAlert will log traps only (no real SNMP).")

# Enterprise OID base for the RF Spectrum Monitor
ENTERPRISE_OID = "1.3.6.1.4.1.99999.1.1"
TRAP_OID_THREAT_DETECTED = f"{ENTERPRISE_OID}.1"
TRAP_OID_JAMMER = f"{ENTERPRISE_OID}.2"
TRAP_OID_DRONE = f"{ENTERPRISE_OID}.3"

# Severity → SNMP generic trap type mapping
_SEVERITY_TO_GENERIC: dict = {
    AlertSeverity.INFO: 6,
    AlertSeverity.WARNING: 6,
    AlertSeverity.CRITICAL: 6,
    AlertSeverity.EMERGENCY: 6,
}


class SNMPAlert:
    """SNMP v2c trap sender.

    Args:
        host: SNMP trap receiver host (IP or hostname).
        port: UDP port for the trap receiver.
        community: SNMP community string.
        engine_id: Optional engine ID string.
    """

    name: str = "snmp"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 162,
        community: str = "public",
        engine_id: str = "RF-Spectrum-Monitor",
    ) -> None:
        self.host = host
        self.port = port
        self.community = community
        self.engine_id = engine_id

    # ------------------------------------------------------------------
    # AlertSink protocol
    # ------------------------------------------------------------------

    def send(self, event: AlertEvent) -> bool:
        """Send *event* as an SNMP v2c trap.

        Args:
            event: Normalised :class:`~src.alerts.alert_manager.AlertEvent`.

        Returns:
            ``True`` on success (or simulation), ``False`` on error.
        """
        trap_oid = self._select_trap_oid(event)

        if not _PYSNMP_AVAILABLE:
            logger.info(
                "[SNMP-SIMULATED] TRAP → %s:%d community=%r oid=%s  %s",
                self.host,
                self.port,
                self.community,
                trap_oid,
                event.description,
            )
            return True

        try:
            error_indication, error_status, error_index, _ = next(
                sendNotification(
                    SnmpEngine(),
                    CommunityData(self.community, mpModel=1),
                    UdpTransportTarget((self.host, self.port), retries=1, timeout=2),
                    ContextData(),
                    "trap",
                    NotificationType(ObjectIdentity(trap_oid)).addVarBinds(
                        ObjectType(
                            ObjectIdentity(f"{ENTERPRISE_OID}.10"),
                            OctetString(event.unit_id),
                        ),
                        ObjectType(
                            ObjectIdentity(f"{ENTERPRISE_OID}.11"),
                            OctetString(event.description),
                        ),
                        ObjectType(
                            ObjectIdentity(f"{ENTERPRISE_OID}.12"),
                            OctetString(event.severity.value),
                        ),
                        ObjectType(
                            ObjectIdentity(f"{ENTERPRISE_OID}.13"),
                            Integer32(int(event.details.get("peak_power_dbm", 0))),
                        ),
                        ObjectType(
                            ObjectIdentity(f"{ENTERPRISE_OID}.14"),
                            OctetString(str(event.details.get("center_freq_mhz", 0))),
                        ),
                    ),
                )
            )
            if error_indication:
                logger.error("SNMPAlert: send error: %s", error_indication)
                return False
            if error_status:
                logger.error("SNMPAlert: SNMP error status: %s", error_status)
                return False
            logger.debug("SNMPAlert: trap sent for event %s", event.event_id)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("SNMPAlert: exception: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _select_trap_oid(self, event: AlertEvent) -> str:
        signal_class = event.details.get("signal_class", "unknown")
        if "jammer" in signal_class:
            return TRAP_OID_JAMMER
        if "drone" in signal_class:
            return TRAP_OID_DRONE
        return TRAP_OID_THREAT_DETECTED
