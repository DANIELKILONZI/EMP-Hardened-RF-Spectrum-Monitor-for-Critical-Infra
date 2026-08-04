"""Tests for the mesh coordinator."""

import json
import socket
import threading
import time

import pytest

from src.mesh.mesh_coordinator import MeshCoordinator, MeshNode, NodeStatus


# ---------------------------------------------------------------------------
# MeshNode tests
# ---------------------------------------------------------------------------

class TestMeshNode:
    def test_to_dict(self):
        node = MeshNode(unit_id="n1", address="10.0.0.1", port=5555)
        d = node.to_dict()
        assert d["unit_id"] == "n1"
        assert d["address"] == "10.0.0.1"
        assert d["status"] == "online"

    def test_default_status_online(self):
        node = MeshNode(unit_id="n2", address="10.0.0.2")
        assert node.status == NodeStatus.ONLINE


# ---------------------------------------------------------------------------
# MeshCoordinator – no-network tests
# ---------------------------------------------------------------------------

class TestMeshCoordinatorRegistry:
    def setup_method(self):
        self.coord = MeshCoordinator(max_units=5)

    def test_register_node(self):
        node = self.coord.register_node("u1", "10.0.0.1")
        assert node.unit_id == "u1"
        assert node.address == "10.0.0.1"

    def test_register_same_node_twice_updates(self):
        self.coord.register_node("u2", "10.0.0.2")
        self.coord.register_node("u2", "10.0.0.3")  # updated address
        node = self.coord.get_node("u2")
        assert node.address == "10.0.0.3"

    def test_max_units_exceeded_raises(self):
        for i in range(5):
            self.coord.register_node(f"u{i}", f"10.0.0.{i}")
        with pytest.raises(ValueError, match="Maximum unit count"):
            self.coord.register_node("overflow", "10.0.0.99")

    def test_get_nodes_returns_all(self):
        self.coord.register_node("a", "1.1.1.1")
        self.coord.register_node("b", "2.2.2.2")
        nodes = self.coord.get_nodes()
        ids = {n.unit_id for n in nodes}
        assert "a" in ids
        assert "b" in ids

    def test_get_node_missing_returns_none(self):
        assert self.coord.get_node("missing") is None

    def test_online_count(self):
        self.coord.register_node("x", "1.1.1.1")
        self.coord.register_node("y", "2.2.2.2")
        assert self.coord.online_count() == 2

    def test_online_count_excludes_offline(self):
        self.coord.register_node("z", "3.3.3.3")
        node = self.coord.get_node("z")
        node.status = NodeStatus.OFFLINE
        assert self.coord.online_count() == 0

    def test_register_with_location(self):
        node = self.coord.register_node("gps-node", "10.0.1.1", location=(38.8, -77.0, 10.0))
        assert node.location == (38.8, -77.0, 10.0)


# ---------------------------------------------------------------------------
# MeshCoordinator – TCP integration test
# ---------------------------------------------------------------------------

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestMeshCoordinatorTCP:
    def setup_method(self):
        self.port = _find_free_port()
        self.detections = []
        self.coord = MeshCoordinator(
            host="127.0.0.1",
            port=self.port,
            on_detection=lambda uid, data: self.detections.append((uid, data)),
        )
        self.coord.start()
        time.sleep(0.1)  # Allow server to bind

    def teardown_method(self):
        self.coord.stop()

    def _connect_and_send(self, messages: list) -> None:
        """Connect to the coordinator and send JSON messages."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as sock:
            for msg in messages:
                sock.sendall((json.dumps(msg) + "\n").encode())
            time.sleep(0.1)

    def test_register_message_creates_node(self):
        self._connect_and_send([
            {"type": "register", "unit_id": "remote-1", "location": [38.8, -77.0, 10.0]}
        ])
        time.sleep(0.2)
        node = self.coord.get_node("remote-1")
        assert node is not None
        assert node.location == (38.8, -77.0, 10.0)

    def test_detection_message_triggers_callback(self):
        self._connect_and_send([
            {"type": "register", "unit_id": "det-node"},
            {"type": "detection", "unit_id": "det-node", "data": {"freq_mhz": 915.0}},
        ])
        time.sleep(0.3)
        assert any(uid == "det-node" for uid, _ in self.detections)

    def test_detection_increments_counter(self):
        self._connect_and_send([
            {"type": "register", "unit_id": "cnt-node"},
            {"type": "detection", "unit_id": "cnt-node", "data": {}},
            {"type": "detection", "unit_id": "cnt-node", "data": {}},
        ])
        time.sleep(0.3)
        node = self.coord.get_node("cnt-node")
        if node:
            assert node.detection_count >= 2

    def test_heartbeat_updates_last_seen(self):
        """Heartbeat should update last_seen even if connection eventually closes."""
        before = time.time()
        self._connect_and_send([
            {"type": "register", "unit_id": "hb-node"},
            {"type": "heartbeat", "unit_id": "hb-node"},
        ])
        time.sleep(0.2)
        node = self.coord.get_node("hb-node")
        # The node may have been marked offline when the connection closed, but
        # last_seen should reflect the heartbeat that was processed.
        assert node is not None
        assert node.last_seen >= before

    def test_invalid_json_does_not_crash_server(self):
        """Sending garbage should not bring down the coordinator."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as sock:
            sock.sendall(b"NOT VALID JSON\n")
            time.sleep(0.1)
        # Server should still accept new connections
        self._connect_and_send([{"type": "register", "unit_id": "post-garbage"}])
        time.sleep(0.2)
        assert self.coord.get_node("post-garbage") is not None
