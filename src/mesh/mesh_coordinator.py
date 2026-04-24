"""Mesh network coordinator for multi-unit deployments.

Manages up to 50 fixed/mobile monitoring units, collects their scan
results over a lightweight TCP/JSON protocol, and exposes aggregated
detections to the main monitor.

Protocol (newline-delimited JSON over TCP):
  Client → Server:  { "type": "register", "unit_id": "...", "location": [...] }
  Client → Server:  { "type": "detection", "unit_id": "...", "data": {...} }
  Server → Client:  { "type": "ack", "status": "ok" }
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_MESH_UNITS = 50
DEFAULT_MESH_PORT = 5555
HEARTBEAT_INTERVAL_S = 10.0
NODE_TIMEOUT_S = 30.0
RECV_BUFFER = 4096


class NodeStatus(Enum):
    """Operational status of a mesh node."""
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


@dataclass
class MeshNode:
    """Represents one monitoring unit in the mesh.

    Attributes:
        unit_id: Unique node identifier.
        address: IP address of the node.
        port: TCP port the node uses to connect.
        location: (lat, lon, alt_m) from GPS sync.
        status: Current :class:`NodeStatus`.
        last_seen: Unix timestamp of last contact.
        detection_count: Total confirmed detections reported.
    """

    unit_id: str
    address: str
    port: int = DEFAULT_MESH_PORT
    location: Optional[Tuple[float, float, float]] = None
    status: NodeStatus = NodeStatus.ONLINE
    last_seen: float = field(default_factory=time.time)
    detection_count: int = 0

    def to_dict(self) -> dict:
        return {
            "unit_id": self.unit_id,
            "address": self.address,
            "port": self.port,
            "location": self.location,
            "status": self.status.value,
            "last_seen": self.last_seen,
            "detection_count": self.detection_count,
        }


class MeshCoordinator:
    """Central coordinator for a mesh of up to 50 monitoring units.

    Listens for incoming TCP connections from field nodes, aggregates their
    detection reports, and exposes them to the main monitoring loop.

    Args:
        host: Interface to bind (``"0.0.0.0"`` for all).
        port: TCP port to listen on.
        on_detection: Callback invoked with ``(unit_id, detection_dict)``
            when a node reports a detection.
        max_units: Maximum registered nodes (default 50).
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = DEFAULT_MESH_PORT,
        on_detection: Optional[Callable[[str, dict], None]] = None,
        max_units: int = MAX_MESH_UNITS,
    ) -> None:
        self.host = host
        self.port = port
        self.on_detection = on_detection
        self.max_units = max_units

        self._nodes: Dict[str, MeshNode] = {}
        self._nodes_lock = threading.Lock()
        self._server_socket: Optional[socket.socket] = None
        self._stop_event = threading.Event()
        self._accept_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the mesh coordinator server."""
        self._stop_event.clear()
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self.host, self.port))
        self._server_socket.listen(self.max_units)
        self._server_socket.settimeout(1.0)
        logger.info("MeshCoordinator: listening on %s:%d (max %d nodes).", self.host, self.port, self.max_units)

        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()

    def stop(self) -> None:
        """Stop the coordinator and close all connections."""
        self._stop_event.set()
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:  # noqa: BLE001
                pass
        if self._accept_thread:
            self._accept_thread.join(timeout=5.0)
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=5.0)
        logger.info("MeshCoordinator: stopped.")

    # ------------------------------------------------------------------
    # Node registry
    # ------------------------------------------------------------------

    def register_node(
        self,
        unit_id: str,
        address: str,
        port: int = DEFAULT_MESH_PORT,
        location: Optional[Tuple[float, float, float]] = None,
    ) -> MeshNode:
        """Manually register a node (used for local/direct-attach units).

        Args:
            unit_id: Unique node ID.
            address: Node IP address.
            port: TCP port.
            location: GPS coordinates.

        Returns:
            The newly created :class:`MeshNode`.

        Raises:
            ValueError: If max_units has been reached.
        """
        with self._nodes_lock:
            if unit_id in self._nodes:
                node = self._nodes[unit_id]
                node.address = address
                node.location = location
                node.status = NodeStatus.ONLINE
                node.last_seen = time.time()
                return node

            if len(self._nodes) >= self.max_units:
                raise ValueError(f"Maximum unit count ({self.max_units}) reached.")

            node = MeshNode(unit_id=unit_id, address=address, port=port, location=location)
            self._nodes[unit_id] = node
            logger.info("MeshCoordinator: registered node %r (%s).", unit_id, address)
            return node

    def get_nodes(self) -> List[MeshNode]:
        """Return a snapshot of all registered nodes."""
        with self._nodes_lock:
            return list(self._nodes.values())

    def get_node(self, unit_id: str) -> Optional[MeshNode]:
        """Return a specific node by ID."""
        with self._nodes_lock:
            return self._nodes.get(unit_id)

    def online_count(self) -> int:
        """Return the number of currently online nodes."""
        with self._nodes_lock:
            return sum(1 for n in self._nodes.values() if n.status == NodeStatus.ONLINE)

    # ------------------------------------------------------------------
    # TCP server loops
    # ------------------------------------------------------------------

    def _accept_loop(self) -> None:
        """Accept incoming connections from field nodes."""
        while not self._stop_event.is_set():
            try:
                conn, addr = self._server_socket.accept()
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True,
                )
                client_thread.start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, conn: socket.socket, addr: tuple) -> None:
        """Handle messages from a single field node connection."""
        unit_id = f"unknown-{addr[0]}"
        try:
            conn.settimeout(NODE_TIMEOUT_S)
            buffer = ""
            while not self._stop_event.is_set():
                try:
                    chunk = conn.recv(RECV_BUFFER).decode(errors="replace")
                    if not chunk:
                        break
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line:
                            unit_id = self._dispatch_message(line, addr) or unit_id
                except socket.timeout:
                    logger.warning("MeshCoordinator: timeout from %s (%s).", addr, unit_id)
                    break
        except Exception as exc:  # noqa: BLE001
            logger.error("MeshCoordinator: error handling %s: %s", addr, exc)
        finally:
            conn.close()
            self._mark_offline(unit_id)

    def _dispatch_message(self, raw: str, addr: tuple) -> Optional[str]:
        """Parse and dispatch a single JSON message.  Returns unit_id."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("MeshCoordinator: invalid JSON from %s.", addr)
            return None

        msg_type = msg.get("type")
        unit_id = msg.get("unit_id", f"unknown-{addr[0]}")

        if msg_type == "register":
            location = msg.get("location")
            self.register_node(unit_id, addr[0], location=tuple(location) if location else None)

        elif msg_type == "detection":
            self._handle_detection_msg(unit_id, msg.get("data", {}))

        elif msg_type == "heartbeat":
            self._touch_node(unit_id)

        return unit_id

    def _handle_detection_msg(self, unit_id: str, data: dict) -> None:
        """Process an incoming detection report."""
        with self._nodes_lock:
            node = self._nodes.get(unit_id)
            if node:
                node.detection_count += 1
                node.last_seen = time.time()

        if self.on_detection:
            try:
                self.on_detection(unit_id, data)
            except Exception as exc:  # noqa: BLE001
                logger.error("MeshCoordinator: on_detection callback error: %s", exc)

    def _touch_node(self, unit_id: str) -> None:
        with self._nodes_lock:
            node = self._nodes.get(unit_id)
            if node:
                node.last_seen = time.time()
                node.status = NodeStatus.ONLINE

    def _mark_offline(self, unit_id: str) -> None:
        with self._nodes_lock:
            node = self._nodes.get(unit_id)
            if node:
                node.status = NodeStatus.OFFLINE
                logger.info("MeshCoordinator: node %r marked offline.", unit_id)

    def _watchdog_loop(self) -> None:
        """Periodically check for nodes that have stopped reporting."""
        while not self._stop_event.is_set():
            now = time.time()
            with self._nodes_lock:
                for node in self._nodes.values():
                    if node.status == NodeStatus.ONLINE and (now - node.last_seen) > NODE_TIMEOUT_S:
                        node.status = NodeStatus.DEGRADED
                        logger.warning(
                            "MeshCoordinator: node %r has not reported in %.0fs – DEGRADED.",
                            node.unit_id,
                            now - node.last_seen,
                        )
            time.sleep(HEARTBEAT_INTERVAL_S)
