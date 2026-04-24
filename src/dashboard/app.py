"""Flask-based web dashboard for the RF Spectrum Monitor.

Exposes a live browser UI and REST API over HTTP.

Endpoints:
    GET  /                     – HTML dashboard
    GET  /api/status           – Overall system status
    GET  /api/waterfall        – Latest waterfall rows as JSON
    GET  /api/detections       – Recent confirmed detections
    GET  /api/mesh/nodes       – Mesh node registry
    POST /api/config/reload    – Reload YAML config without restarting

Run standalone (for development):

    from src.dashboard.app import DashboardServer
    server = DashboardServer(monitor_ref)
    server.start(port=8080)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_FLASK_AVAILABLE = False
try:
    from flask import Flask, jsonify, render_template, request  # type: ignore

    _FLASK_AVAILABLE = True
except ImportError:
    logger.warning("Flask not installed – web dashboard disabled.  pip install flask>=3.0")

if TYPE_CHECKING:
    from ..monitor import RFSpectrumMonitor


class DashboardServer:
    """HTTP dashboard server that exposes monitor state via REST + HTML UI.

    Args:
        monitor: The running :class:`~src.monitor.RFSpectrumMonitor` instance.
        host: Interface to bind.  Defaults to ``"0.0.0.0"``.
        port: TCP port.  Defaults to ``8080``.
        max_waterfall_rows: Maximum waterfall rows returned per API call.
        detection_history_limit: Maximum recent detections kept in memory.
    """

    def __init__(
        self,
        monitor: "RFSpectrumMonitor",
        host: str = "0.0.0.0",
        port: int = 8080,
        max_waterfall_rows: int = 100,
        detection_history_limit: int = 500,
    ) -> None:
        self.monitor = monitor
        self.host = host
        self.port = port
        self.max_waterfall_rows = max_waterfall_rows
        self.detection_history_limit = detection_history_limit

        self._recent_detections: List[Dict[str, Any]] = []
        self._detections_lock = threading.Lock()
        self._server_thread: Optional[threading.Thread] = None
        self._app: Optional[Any] = None  # Flask app

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def record_detection(self, detection_dict: Dict[str, Any]) -> None:
        """Record a confirmed detection for the dashboard history.

        Called by the monitor whenever a new :class:`DetectedSignal` fires.

        Args:
            detection_dict: Serialised detection (from ``DetectedSignal.to_dict()``).
        """
        with self._detections_lock:
            self._recent_detections.append(detection_dict)
            if len(self._recent_detections) > self.detection_history_limit:
                self._recent_detections.pop(0)

    def start(self) -> None:
        """Build the Flask app and start it in a background daemon thread."""
        if not _FLASK_AVAILABLE:
            logger.error("Cannot start dashboard: Flask is not installed.")
            return

        self._app = self._build_app()
        self._server_thread = threading.Thread(
            target=self._run_flask,
            daemon=True,
            name="dashboard-http",
        )
        self._server_thread.start()
        logger.info(
            "Dashboard started on http://%s:%d/",
            self.host if self.host != "0.0.0.0" else "localhost",
            self.port,
        )

    def stop(self) -> None:
        """Signal the dashboard thread to stop (best-effort for daemon threads)."""
        logger.info("Dashboard server stopping.")

    # ------------------------------------------------------------------
    # Flask app factory
    # ------------------------------------------------------------------

    def _build_app(self) -> Any:
        """Construct and return the Flask application."""
        template_dir = os.path.join(os.path.dirname(__file__), "templates")
        app = Flask(__name__, template_folder=template_dir)
        # Disable Flask request logging noise in production
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

        # Keep a reference to self inside the closures
        server = self

        @app.route("/")
        def index() -> Any:
            return render_template("dashboard.html", port=server.port)

        @app.route("/api/status")
        def api_status() -> Any:
            mon = server.monitor
            scanners = [repr(s) for s in (mon._scanners or [])]
            mesh_online = mon._mesh.online_count() if mon._mesh else 0
            loc = None
            if mon._geo_tagger:
                loc = mon._geo_tagger.location.to_dict()
            return jsonify(
                {
                    "status": "running" if mon._running else "stopped",
                    "scanners": scanners,
                    "mesh_online": mesh_online,
                    "location": loc,
                    "history_records": len(mon._analyzer.get_history()) if mon._analyzer else 0,
                    "timestamp": time.time(),
                }
            )

        @app.route("/api/waterfall")
        def api_waterfall() -> Any:
            mon = server.monitor
            if not mon._analyzer or not mon._scanners:
                return jsonify({"rows": [], "freq_start_hz": 0, "freq_end_hz": 0})

            scanner = mon._scanners[0]
            half_bw = scanner.sample_rate_hz / 2
            freq_start = scanner.center_freq_hz - half_bw
            freq_end = scanner.center_freq_hz + half_bw
            unit_id = scanner.unit_id

            matrix, timestamps, freqs = mon._analyzer.get_waterfall_matrix(
                unit_id, max_rows=server.max_waterfall_rows
            )
            rows = []
            for i, row in enumerate(matrix):
                rows.append(
                    {
                        "timestamp": float(timestamps[i]) if i < len(timestamps) else 0.0,
                        "power_db": [round(float(v), 1) for v in row],
                    }
                )
            return jsonify(
                {
                    "rows": rows,
                    "freq_start_hz": freq_start,
                    "freq_end_hz": freq_end,
                    "frequencies_hz": [round(float(f), 0) for f in freqs],
                }
            )

        @app.route("/api/detections")
        def api_detections() -> Any:
            limit = int(request.args.get("limit", 50))
            with server._detections_lock:
                detections = list(server._recent_detections[-limit:])
            return jsonify({"detections": detections, "total": len(server._recent_detections)})

        @app.route("/api/mesh/nodes")
        def api_mesh_nodes() -> Any:
            mon = server.monitor
            if not mon._mesh:
                return jsonify({"nodes": [], "enabled": False})
            nodes = [n.to_dict() for n in mon._mesh.get_nodes()]
            return jsonify({"nodes": nodes, "enabled": True, "online_count": mon._mesh.online_count()})

        @app.route("/api/config/reload", methods=["POST"])
        def api_config_reload() -> Any:
            mon = server.monitor
            config_path = request.json.get("config_path") if request.is_json else None
            if config_path is None:
                config_path = os.path.join(
                    os.path.dirname(__file__), "..", "..", "config", "default.yaml"
                )
            try:
                import yaml  # type: ignore

                with open(config_path) as fh:
                    new_config = yaml.safe_load(fh)
                mon.config = new_config
                logger.info("Config reloaded from %s", config_path)
                return jsonify({"status": "ok", "config_path": config_path})
            except Exception as exc:  # noqa: BLE001
                logger.error("Config reload failed: %s", exc)
                return jsonify({"status": "error", "message": str(exc)}), 500

        return app

    def _run_flask(self) -> None:
        """Target for the dashboard background thread."""
        try:
            self._app.run(
                host=self.host,
                port=self.port,
                debug=False,
                use_reloader=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Dashboard server crashed: %s", exc)
