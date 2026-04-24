"""Main RF Spectrum Monitor orchestrator.

Ties together scanners, spectrum analysis, signal detection, geo-tagging,
mesh coordination, and alerting into a single runnable monitor process.

Usage::

    python -m src.monitor --config config/default.yaml

Or programmatically::

    from src.monitor import RFSpectrumMonitor
    monitor = RFSpectrumMonitor.from_config("config/default.yaml")
    monitor.start()
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from typing import List, Optional

import yaml

from .alerts.alert_manager import AlertManager, AlertSeverity
from .alerts.snmp_alert import SNMPAlert
from .alerts.splunk_alert import SplunkAlert
from .analyzer.signal_detector import SignalDetector
from .analyzer.spectrum_analyzer import SpectrumAnalyzer
from .analyzer.waterfall import WaterfallDisplay
from .gps.geo_tagger import GeoTagger
from .mesh.mesh_coordinator import MeshCoordinator
from .scanner.base_scanner import ScanResult
from .scanner.rtlsdr_scanner import RTLSDRScanner
from .scanner.usrp_scanner import USRPScanner

logger = logging.getLogger(__name__)


class RFSpectrumMonitor:
    """Top-level monitor that orchestrates all subsystems.

    Args:
        config: Configuration dictionary (see ``config/default.yaml``).
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self._scanners: List = []
        self._analyzer: Optional[SpectrumAnalyzer] = None
        self._detector: Optional[SignalDetector] = None
        self._waterfall: Optional[WaterfallDisplay] = None
        self._alert_mgr: Optional[AlertManager] = None
        self._geo_tagger: Optional[GeoTagger] = None
        self._mesh: Optional[MeshCoordinator] = None
        self._running = False

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config_path: str) -> "RFSpectrumMonitor":
        """Instantiate from a YAML configuration file.

        Args:
            config_path: Path to the YAML configuration file.

        Returns:
            Configured :class:`RFSpectrumMonitor` instance.
        """
        with open(config_path) as fh:
            config = yaml.safe_load(fh)
        return cls(config)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialise all subsystems and begin monitoring."""
        self._setup_logging()
        logger.info("=== EMP-Hardened RF Spectrum Monitor starting ===")

        self._init_geo_tagger()
        self._init_analyzer()
        self._init_detector()
        self._init_waterfall()
        self._init_alerts()
        self._init_scanners()
        self._init_mesh()

        self._running = True
        logger.info("All subsystems initialised. Monitoring started.")

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        self._run_loop()

    def stop(self) -> None:
        """Gracefully shut down all subsystems."""
        logger.info("Shutting down RF Spectrum Monitor…")
        self._running = False

        for scanner in self._scanners:
            try:
                scanner.stop_continuous_scan()
                scanner.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing scanner: %s", exc)

        if self._mesh:
            self._mesh.stop()

        if self._geo_tagger:
            self._geo_tagger.stop()

        logger.info("RF Spectrum Monitor stopped.")

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------

    def _setup_logging(self) -> None:
        log_cfg = self.config.get("logging", {})
        level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
        fmt = log_cfg.get("format", "%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        logging.basicConfig(level=level, format=fmt)

    def _init_geo_tagger(self) -> None:
        gps_cfg = self.config.get("gps", {})
        self._geo_tagger = GeoTagger(
            serial_port=gps_cfg.get("serial_port"),
            baud_rate=gps_cfg.get("baud_rate", 9600),
            static_lat=gps_cfg.get("static_lat", 0.0),
            static_lon=gps_cfg.get("static_lon", 0.0),
            static_alt=gps_cfg.get("static_alt", 0.0),
        )
        self._geo_tagger.start()

    def _init_analyzer(self) -> None:
        an_cfg = self.config.get("analyzer", {})
        self._analyzer = SpectrumAnalyzer(
            history_seconds=an_cfg.get("history_seconds", 120.0),
            calibration_offset_db=an_cfg.get("calibration_offset_db", 0.0),
            smoothing_alpha=an_cfg.get("smoothing_alpha", 0.7),
        )

    def _init_detector(self) -> None:
        det_cfg = self.config.get("detection", {})
        self._detector = SignalDetector(
            threshold_db=det_cfg.get("threshold_db", -80.0),
            persistence_s=det_cfg.get("persistence_s", 30.0),
            bin_merge_hz=det_cfg.get("bin_merge_hz", 500_000),
            max_track_age_s=det_cfg.get("max_track_age_s", 60.0),
        )

    def _init_waterfall(self) -> None:
        wf_cfg = self.config.get("waterfall", {})
        self._waterfall = WaterfallDisplay(
            min_db=wf_cfg.get("min_db", -120.0),
            max_db=wf_cfg.get("max_db", -40.0),
            width_chars=wf_cfg.get("width_chars", 80),
        )

    def _init_alerts(self) -> None:
        alert_cfg = self.config.get("alerts", {})
        self._alert_mgr = AlertManager(
            dedup_window_s=alert_cfg.get("dedup_window_s", 60.0)
        )

        snmp_cfg = alert_cfg.get("snmp", {})
        if snmp_cfg.get("enabled", True):
            snmp = SNMPAlert(
                host=snmp_cfg.get("host", "127.0.0.1"),
                port=snmp_cfg.get("port", 162),
                community=snmp_cfg.get("community", "public"),
            )
            self._alert_mgr.register_sink(snmp)

        splunk_cfg = alert_cfg.get("splunk", {})
        if splunk_cfg.get("enabled", False):
            splunk = SplunkAlert(
                hec_url=splunk_cfg.get("hec_url", ""),
                token=splunk_cfg.get("token", ""),
                index=splunk_cfg.get("index", "rf_spectrum"),
            )
            self._alert_mgr.register_sink(splunk)

    def _init_scanners(self) -> None:
        scanner_cfgs = self.config.get("scanners", [])
        if not scanner_cfgs:
            # Default: single simulated RTL-SDR
            scanner_cfgs = [{"type": "rtlsdr", "unit_id": "rtlsdr-0"}]

        for cfg in scanner_cfgs:
            scanner_type = cfg.get("type", "rtlsdr").lower()
            unit_id = cfg.get("unit_id", "unit-0")
            center_freq = cfg.get("center_freq_hz", 915_000_000)
            sample_rate = cfg.get("sample_rate_hz", 2_400_000)
            gain = cfg.get("gain_db", 30.0)

            if scanner_type == "usrp":
                scanner = USRPScanner(
                    unit_id=unit_id,
                    center_freq_hz=center_freq,
                    sample_rate_hz=cfg.get("sample_rate_hz", 10_000_000),
                    gain_db=gain,
                    device_args=cfg.get("device_args", ""),
                )
            else:
                scanner = RTLSDRScanner(
                    unit_id=unit_id,
                    center_freq_hz=center_freq,
                    sample_rate_hz=sample_rate,
                    gain_db=gain,
                    device_index=cfg.get("device_index", 0),
                )

            scanner.open()
            scanner.start_continuous_scan(
                callback=self._on_scan_result,
                interval_s=cfg.get("scan_interval_s", 0.1),
            )
            self._scanners.append(scanner)
            logger.info("Scanner started: %r", scanner)

    def _init_mesh(self) -> None:
        mesh_cfg = self.config.get("mesh", {})
        if not mesh_cfg.get("enabled", False):
            return

        self._mesh = MeshCoordinator(
            host=mesh_cfg.get("host", "0.0.0.0"),
            port=mesh_cfg.get("port", 5555),
            on_detection=self._on_mesh_detection,
            max_units=mesh_cfg.get("max_units", 50),
        )
        self._mesh.start()

    # ------------------------------------------------------------------
    # Scan callback
    # ------------------------------------------------------------------

    def _on_scan_result(self, result: ScanResult) -> None:
        """Called by scanner background thread for each new scan snapshot."""
        # Stamp location
        if self._geo_tagger:
            result.location = self._geo_tagger.get_tuple()

        # Analyse
        processed = self._analyzer.process(result)

        # Feed waterfall
        if self._waterfall:
            self._waterfall.add_row(processed.power_db)

        # Detect threats
        detections = self._detector.process(processed)
        for detection in detections:
            logger.warning(
                "THREAT DETECTED: %s | %.3f MHz | %.1f dBm | %s | persisted %.0fs",
                detection.unit_id,
                detection.frequency_mhz,
                detection.peak_power_db,
                detection.signal_class,
                detection.persistence_s,
            )
            self._alert_mgr.alert_from_detection(detection)

    def _on_mesh_detection(self, unit_id: str, data: dict) -> None:
        """Callback for detections reported by remote mesh nodes."""
        logger.info("Mesh detection from %s: %s", unit_id, data)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Main event loop – periodically prints status to console."""
        display_cfg = self.config.get("display", {})
        show_waterfall = display_cfg.get("waterfall", True)
        status_interval = display_cfg.get("status_interval_s", 10.0)
        last_status = 0.0

        try:
            while self._running:
                now = time.time()
                if now - last_status >= status_interval:
                    self._print_status(show_waterfall)
                    last_status = now
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _print_status(self, show_waterfall: bool) -> None:
        """Print a status summary to stdout."""
        scanners_info = ", ".join(repr(s) for s in self._scanners)
        mesh_info = ""
        if self._mesh:
            mesh_info = f" | Mesh nodes online: {self._mesh.online_count()}"

        loc = self._geo_tagger.location if self._geo_tagger else None
        loc_str = (
            f"fix_quality={loc.fix_quality}" if loc else "unknown"
        )

        print(
            f"\n--- RF Spectrum Monitor Status ---\n"
            f"  Scanners : {scanners_info}\n"
            f"  Location : {loc_str}{mesh_info}\n"
            f"  History  : {len(self._analyzer.get_history())} records\n"
        )

        if show_waterfall and self._waterfall and self._scanners:
            scanner = self._scanners[0]
            half_bw = scanner.sample_rate_hz / 2
            header = self._waterfall.render_header(
                scanner.center_freq_hz - half_bw,
                scanner.center_freq_hz + half_bw,
            )
            print(header)
            print(self._waterfall.render_ascii(last_n_rows=10))

    def _handle_signal(self, signum: int, frame: object) -> None:
        logger.info("Received signal %d – stopping.", signum)
        self._running = False


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EMP-Hardened RF Spectrum Monitor for Critical Infrastructure"
    )
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(__file__), "..", "config", "default.yaml"),
        help="Path to YAML configuration file.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    monitor = RFSpectrumMonitor.from_config(args.config)
    monitor.start()


if __name__ == "__main__":
    main()
