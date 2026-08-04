"""Splunk HEC (HTTP Event Collector) alert sink.

Posts :class:`~src.alerts.alert_manager.AlertEvent` payloads to Splunk via
the HTTP Event Collector endpoint so that all detections appear in Splunk
SIEM dashboards and correlation searches.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .alert_manager import AlertEvent

logger = logging.getLogger(__name__)


class SplunkAlert:
    """Splunk HEC alert sink.

    Args:
        hec_url: Full URL to the Splunk HEC endpoint, e.g.
            ``"https://splunk.example.com:8088/services/collector/event"``.
        token: Splunk HEC token (kept in memory only; never logged).
        index: Splunk index to target.
        source: Splunk ``source`` field value.
        sourcetype: Splunk ``sourcetype`` field value.
        timeout_s: HTTP request timeout in seconds.
        verify_ssl: Whether to verify the TLS certificate.
    """

    name: str = "splunk"

    def __init__(
        self,
        hec_url: str = "https://localhost:8088/services/collector/event",
        token: str = "",
        index: str = "rf_spectrum",
        source: str = "emp_monitor",
        sourcetype: str = "rf:threat",
        timeout_s: float = 5.0,
        verify_ssl: bool = True,
    ) -> None:
        self.hec_url = hec_url
        self._token = token  # intentionally private – not exposed in repr
        self.index = index
        self.source = source
        self.sourcetype = sourcetype
        self.timeout_s = timeout_s
        self.verify_ssl = verify_ssl

    # ------------------------------------------------------------------
    # AlertSink protocol
    # ------------------------------------------------------------------

    def send(self, event: AlertEvent) -> bool:
        """POST *event* to the Splunk HEC endpoint.

        If ``token`` is empty the event is logged locally (simulation).

        Args:
            event: Normalised :class:`~src.alerts.alert_manager.AlertEvent`.

        Returns:
            ``True`` on success or simulation, ``False`` on HTTP/network error.
        """
        payload = {
            "time": event.timestamp,
            "host": event.unit_id,
            "source": self.source,
            "sourcetype": self.sourcetype,
            "index": self.index,
            "event": event.to_dict(),
        }

        if not self._token:
            logger.info("[SPLUNK-SIMULATED] %s", json.dumps(payload, indent=2))
            return True

        return self._post(payload)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _post(self, payload: dict) -> bool:
        """Send HTTP POST to Splunk HEC."""
        body = json.dumps(payload).encode()
        headers = {
            "Authorization": f"Splunk {self._token}",
            "Content-Type": "application/json",
        }
        req = Request(self.hec_url, data=body, headers=headers, method="POST")

        import ssl
        ctx: Optional[ssl.SSLContext] = None
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            with urlopen(req, timeout=self.timeout_s, context=ctx) as resp:
                if resp.status == 200:
                    logger.debug("SplunkAlert: event delivered (HTTP 200).")
                    return True
                body_resp = resp.read().decode(errors="replace")
                logger.warning(
                    "SplunkAlert: unexpected HTTP %d: %s", resp.status, body_resp
                )
                return False
        except URLError as exc:
            logger.error("SplunkAlert: network error: %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("SplunkAlert: unexpected error: %s", exc)
            return False

    def __repr__(self) -> str:
        return f"<SplunkAlert url={self.hec_url!r} index={self.index!r}>"
