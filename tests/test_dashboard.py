"""Tests for the web dashboard server."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_mock_monitor():
    """Build a minimal mock RFSpectrumMonitor for dashboard tests."""
    monitor = MagicMock()
    monitor._running = True
    monitor._scanners = []
    monitor._mesh = None
    monitor._geo_tagger = None
    monitor._analyzer = MagicMock()
    monitor._analyzer.get_history.return_value = []
    monitor._analyzer.get_waterfall_matrix.return_value = (
        __import__("numpy").empty((0, 0)),
        __import__("numpy").array([]),
        __import__("numpy").array([]),
    )
    return monitor


# ---------------------------------------------------------------------------
# DashboardServer unit tests (no HTTP – test logic directly)
# ---------------------------------------------------------------------------

class TestDashboardServer:
    def setup_method(self):
        pytest.importorskip("flask")
        from src.dashboard.app import DashboardServer

        self.monitor = _make_mock_monitor()
        self.server = DashboardServer(self.monitor, host="127.0.0.1", port=0)

    def test_record_detection_stores_item(self):
        self.server.record_detection({"unit_id": "u1", "signal_class": "test"})
        assert len(self.server._recent_detections) == 1

    def test_record_detection_respects_limit(self):
        self.server.detection_history_limit = 5
        for i in range(10):
            self.server.record_detection({"unit_id": f"u{i}"})
        assert len(self.server._recent_detections) == 5

    def test_record_detection_oldest_dropped(self):
        self.server.detection_history_limit = 3
        for i in range(5):
            self.server.record_detection({"id": i})
        ids = [d["id"] for d in self.server._recent_detections]
        assert ids == [2, 3, 4]  # oldest (0, 1) dropped

    def test_build_app_returns_flask_app(self):
        app = self.server._build_app()
        assert app is not None

    def test_api_status_endpoint(self):
        app = self.server._build_app()
        with app.test_client() as client:
            resp = client.get("/api/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "status" in data
            assert "scanners" in data

    def test_api_detections_endpoint_empty(self):
        app = self.server._build_app()
        with app.test_client() as client:
            resp = client.get("/api/detections")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["detections"] == []
            assert data["total"] == 0

    def test_api_detections_endpoint_with_data(self):
        self.server.record_detection({"signal_class": "drone", "severity": "warning"})
        app = self.server._build_app()
        with app.test_client() as client:
            resp = client.get("/api/detections")
            data = resp.get_json()
            assert data["total"] == 1
            assert data["detections"][0]["signal_class"] == "drone"

    def test_api_detections_limit_param(self):
        for i in range(20):
            self.server.record_detection({"id": i})
        app = self.server._build_app()
        with app.test_client() as client:
            resp = client.get("/api/detections?limit=5")
            data = resp.get_json()
            assert len(data["detections"]) == 5

    def test_api_mesh_nodes_disabled(self):
        app = self.server._build_app()
        with app.test_client() as client:
            resp = client.get("/api/mesh/nodes")
            data = resp.get_json()
            assert data["enabled"] is False
            assert data["nodes"] == []

    def test_api_waterfall_no_scanners(self):
        app = self.server._build_app()
        with app.test_client() as client:
            resp = client.get("/api/waterfall")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["rows"] == []

    def test_dashboard_html_served(self):
        app = self.server._build_app()
        with app.test_client() as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert b"RF Spectrum Monitor" in resp.data

    def test_config_reload_endpoint(self, tmp_path):
        import yaml

        cfg = {"logging": {"level": "DEBUG"}}
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text(yaml.dump(cfg))

        app = self.server._build_app()
        with app.test_client() as client:
            resp = client.post(
                "/api/config/reload",
                json={"config_path": str(cfg_file)},
                content_type="application/json",
            )
            data = resp.get_json()
            assert data["status"] == "ok"

    def test_config_reload_bad_path(self):
        app = self.server._build_app()
        with app.test_client() as client:
            resp = client.post(
                "/api/config/reload",
                json={"config_path": "/nonexistent/path.yaml"},
                content_type="application/json",
            )
            assert resp.status_code == 500
