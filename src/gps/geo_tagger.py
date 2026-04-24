"""GPS geo-tagger module.

Reads NMEA sentences from a serial-connected GPS receiver (or GPSD daemon)
and provides geo-location data for stamping scan results.  Falls back to a
configurable static location when GPS hardware is unavailable.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_SERIAL_AVAILABLE = False
try:
    import serial  # type: ignore
    _SERIAL_AVAILABLE = True
except ImportError:
    logger.warning("pyserial not installed – GeoTagger will use static/simulated position.")

_GPSD_AVAILABLE = False
try:
    import gpsd  # type: ignore
    _GPSD_AVAILABLE = True
except ImportError:
    pass


@dataclass
class GeoLocation:
    """A geographic position fix.

    Attributes:
        latitude: Decimal degrees, positive = North.
        longitude: Decimal degrees, positive = East.
        altitude_m: Altitude above sea level in metres.
        accuracy_m: Estimated horizontal accuracy in metres.
        timestamp: Unix epoch of the fix.
        fix_quality: ``"gps"``, ``"static"``, or ``"none"``.
    """

    latitude: float
    longitude: float
    altitude_m: float = 0.0
    accuracy_m: float = 999.0
    timestamp: float = 0.0
    fix_quality: str = "none"

    def to_tuple(self) -> tuple:
        """Return ``(latitude, longitude, altitude_m)`` tuple."""
        return (self.latitude, self.longitude, self.altitude_m)

    def to_dict(self) -> dict:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude_m": self.altitude_m,
            "accuracy_m": self.accuracy_m,
            "timestamp": self.timestamp,
            "fix_quality": self.fix_quality,
        }


class GeoTagger:
    """Provides real-time GPS location for geo-stamping detections.

    Tries backends in this order:
    1. GPSD (if ``gpsd`` Python library available and daemon running)
    2. Serial NMEA (if ``pyserial`` available and a ``serial_port`` is given)
    3. Static fallback (always available)

    Args:
        serial_port: Serial device path for direct NMEA input, e.g.
            ``"/dev/ttyUSB0"``.
        baud_rate: Serial baud rate (default 9600 for most GPS receivers).
        static_lat: Fallback latitude when GPS is unavailable.
        static_lon: Fallback longitude when GPS is unavailable.
        static_alt: Fallback altitude when GPS is unavailable.
        poll_interval_s: Seconds between GPS polls in background thread.
    """

    def __init__(
        self,
        serial_port: Optional[str] = None,
        baud_rate: int = 9600,
        static_lat: float = 0.0,
        static_lon: float = 0.0,
        static_alt: float = 0.0,
        poll_interval_s: float = 1.0,
    ) -> None:
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.poll_interval_s = poll_interval_s

        self._static_location = GeoLocation(
            latitude=static_lat,
            longitude=static_lon,
            altitude_m=static_alt,
            accuracy_m=0.0,
            timestamp=time.time(),
            fix_quality="static",
        )
        self._current: GeoLocation = self._static_location
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._backend: str = "static"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the GPS polling background thread."""
        if self._thread and self._thread.is_alive():
            return

        # Choose backend
        if _GPSD_AVAILABLE:
            self._backend = "gpsd"
        elif _SERIAL_AVAILABLE and self.serial_port:
            self._backend = "serial"
        else:
            self._backend = "static"
            logger.info("GeoTagger: using static location (fix_quality=%s).",
                        self._static_location.fix_quality)
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("GeoTagger: started (%s backend).", self._backend)

    def stop(self) -> None:
        """Stop the GPS polling background thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("GeoTagger: stopped.")

    @property
    def location(self) -> GeoLocation:
        """Return the most recent location fix (thread-safe)."""
        with self._lock:
            return self._current

    def get_tuple(self) -> tuple:
        """Return ``(lat, lon, alt_m)`` – convenience shortcut."""
        return self.location.to_tuple()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._backend == "gpsd":
                    self._read_gpsd()
                elif self._backend == "serial":
                    self._read_serial()
            except Exception as exc:  # noqa: BLE001
                logger.warning("GeoTagger: read error (%s): %s", self._backend, exc)
            time.sleep(self.poll_interval_s)

    def _read_gpsd(self) -> None:
        """Read a fix from the GPSD daemon."""
        gpsd.connect()
        packet = gpsd.get_current()
        if packet.mode >= 2:  # 2D or 3D fix
            with self._lock:
                self._current = GeoLocation(
                    latitude=packet.lat,
                    longitude=packet.lon,
                    altitude_m=getattr(packet, "alt", 0.0) or 0.0,
                    accuracy_m=getattr(packet, "position_precision", (999.0, 999.0))[0],
                    timestamp=time.time(),
                    fix_quality="gps",
                )

    def _read_serial(self) -> None:
        """Read and parse NMEA sentences from a serial GPS receiver."""
        with serial.Serial(self.serial_port, self.baud_rate, timeout=2) as ser:
            for _ in range(20):  # read up to 20 sentences looking for GGA
                line = ser.readline().decode(errors="replace").strip()
                if line.startswith("$GPGGA") or line.startswith("$GNGGA"):
                    loc = self._parse_gga(line)
                    if loc:
                        with self._lock:
                            self._current = loc
                        return

    @staticmethod
    def _parse_gga(sentence: str) -> Optional[GeoLocation]:
        """Parse an NMEA GGA sentence into a :class:`GeoLocation`."""
        try:
            parts = sentence.split(",")
            if len(parts) < 10:
                return None
            fix_quality = int(parts[6]) if parts[6] else 0
            if fix_quality == 0:
                return None

            lat_raw = float(parts[2])
            lat_deg = int(lat_raw / 100)
            lat_min = lat_raw - lat_deg * 100
            lat = lat_deg + lat_min / 60.0
            if parts[3] == "S":
                lat = -lat

            lon_raw = float(parts[4])
            lon_deg = int(lon_raw / 100)
            lon_min = lon_raw - lon_deg * 100
            lon = lon_deg + lon_min / 60.0
            if parts[5] == "W":
                lon = -lon

            alt = float(parts[9]) if parts[9] else 0.0
            hdop = float(parts[8]) if parts[8] else 999.0

            return GeoLocation(
                latitude=lat,
                longitude=lon,
                altitude_m=alt,
                accuracy_m=hdop * 2.5,  # HDOP → approx metres
                timestamp=time.time(),
                fix_quality="gps",
            )
        except (ValueError, IndexError):
            return None
