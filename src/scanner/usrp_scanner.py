"""USRP B200 scanner backend.

Wraps the UHD Python API (``uhd``) and falls back to simulation mode when
UHD is not available.  The USRP B200 supports 70 MHz – 6 GHz, making it
the preferred hardware for full-band coverage.
"""

from __future__ import annotations

import logging
import math
import time
from typing import List, Optional

import numpy as np

from .base_scanner import BaseScanner, ScanResult

logger = logging.getLogger(__name__)

_UHD_AVAILABLE = False
try:
    import uhd  # type: ignore

    _UHD_AVAILABLE = True
except ImportError:
    logger.warning("uhd Python bindings not installed – USRPScanner will run in simulation mode.")


class USRPScanner(BaseScanner):
    """USRP B200 RF scanner backend.

    Supports the full 70 MHz – 6 GHz tuning range of the Ettus USRP B200.
    Falls back to simulation when UHD is unavailable.

    Args:
        device_args: UHD device address string, e.g. ``"serial=12345"`` or
            ``""`` to use the first available device.
        subdev_spec: Sub-device specification, default ``"A:A"``.
        antenna: Antenna port, e.g. ``"TX/RX"`` or ``"RX2"``.
        fft_size: Number of FFT bins for PSD estimation.
        num_samples: IQ samples collected per scan window.
        **kwargs: Forwarded to :class:`~src.scanner.base_scanner.BaseScanner`.
    """

    DEFAULT_SAMPLE_RATE_HZ = 10_000_000  # 10 MS/s – USRP B200 handle up to 56 MS/s

    def __init__(
        self,
        unit_id: str = "usrp-0",
        center_freq_hz: float = 915_000_000,
        sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
        gain_db: float = 40.0,
        device_args: str = "",
        subdev_spec: str = "A:A",
        antenna: str = "RX2",
        fft_size: int = 2048,
        num_samples: int = 1_000_000,
    ) -> None:
        super().__init__(
            unit_id=unit_id,
            center_freq_hz=center_freq_hz,
            sample_rate_hz=sample_rate_hz,
            gain_db=gain_db,
        )
        self.device_args = device_args
        self.subdev_spec = subdev_spec
        self.antenna = antenna
        self.fft_size = fft_size
        self.num_samples = num_samples
        self._usrp: Optional[object] = None
        self._streamer: Optional[object] = None
        self._simulation = not _UHD_AVAILABLE

    # ------------------------------------------------------------------
    # BaseScanner interface
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the USRP B200 device or enter simulation mode."""
        if self._is_open:
            return

        if self._simulation:
            logger.info("%s: opening in SIMULATION mode.", self.unit_id)
            self._is_open = True
            return

        try:
            self._usrp = uhd.usrp.MultiUSRP(self.device_args)
            self._usrp.set_rx_rate(self.sample_rate_hz, 0)
            self._usrp.set_rx_freq(uhd.libpyuhd.types.tune_request(self.center_freq_hz), 0)
            self._usrp.set_rx_gain(self.gain_db, 0)
            self._usrp.set_rx_antenna(self.antenna, 0)

            st_args = uhd.usrp.StreamArgs("fc32", "sc16")
            st_args.channels = [0]
            self._streamer = self._usrp.get_rx_stream(st_args)
            self._is_open = True
            logger.info("%s: USRP B200 opened.", self.unit_id)
        except Exception as exc:
            logger.error("%s: failed to open USRP: %s", self.unit_id, exc)
            logger.info("%s: falling back to simulation mode.", self.unit_id)
            self._simulation = True
            self._is_open = True

    def close(self) -> None:
        """Release the USRP device."""
        if not self._is_open:
            return
        self.stop_continuous_scan()
        if self._streamer is not None:
            try:
                stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
                self._streamer.issue_stream_cmd(stream_cmd)
            except Exception:  # noqa: BLE001
                pass
            self._streamer = None
        self._usrp = None
        self._is_open = False
        logger.info("%s: closed.", self.unit_id)

    def scan_once(self) -> ScanResult:
        """Capture one FFT snapshot from the USRP and return a :class:`ScanResult`."""
        if not self._is_open:
            raise RuntimeError(f"{self.unit_id}: scanner is not open.")

        if self._simulation:
            return self._simulate_scan()

        return self._capture_and_process()

    def get_hardware_info(self) -> dict:
        """Return hardware metadata."""
        if self._simulation:
            return {"driver": "simulation", "device_args": self.device_args, "unit_id": self.unit_id}
        info: dict = {"driver": "uhd", "device_args": self.device_args, "unit_id": self.unit_id}
        if self._usrp is not None:
            try:
                info["mboard_name"] = self._usrp.get_mboard_name()
                info["serial"] = self._usrp.get_usrp_info().get("mboard_serial", "unknown")
            except Exception:  # noqa: BLE001
                pass
        return info

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _capture_and_process(self) -> ScanResult:
        """Collect IQ samples from the USRP and compute PSD."""
        stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_samps_and_done)
        stream_cmd.num_samps = self.num_samples
        stream_cmd.stream_now = True
        self._streamer.issue_stream_cmd(stream_cmd)

        recv_buffer = np.zeros((1, self.num_samples), dtype=np.complex64)
        metadata = uhd.types.RXMetadata()
        samples_recv = 0
        chunks: List["np.ndarray"] = []

        while samples_recv < self.num_samples:
            samps = self._streamer.recv(recv_buffer, metadata)
            if metadata.error_code != uhd.types.RXMetadataErrorCode.none:
                logger.warning("%s: RX metadata error: %s", self.unit_id, metadata.strerror())
                break
            chunks.append(recv_buffer[0, :samps].copy())
            samples_recv += samps

        samples = np.concatenate(chunks) if chunks else np.zeros(self.fft_size, dtype=np.complex64)
        return self._compute_psd(samples)

    def _compute_psd(self, samples: "np.ndarray") -> ScanResult:
        """Estimate PSD using averaged FFT windows."""
        segment_len = self.fft_size
        hop = segment_len // 2
        segments: List["np.ndarray"] = []

        for start in range(0, len(samples) - segment_len + 1, hop):
            seg = samples[start : start + segment_len]
            windowed = seg * np.hanning(segment_len)
            segments.append(np.abs(np.fft.fftshift(np.fft.fft(windowed))) ** 2)

        if not segments:
            psd = np.zeros(self.fft_size)
        else:
            psd = np.mean(segments, axis=0)

        psd_db = 10.0 * np.log10(psd + 1e-20) - 10.0 * math.log10(self.fft_size)

        freqs = (
            np.fft.fftshift(np.fft.fftfreq(self.fft_size, d=1.0 / self.sample_rate_hz))
            + self.center_freq_hz
        )

        return ScanResult(
            center_freq_hz=self.center_freq_hz,
            sample_rate_hz=self.sample_rate_hz,
            frequencies_hz=freqs.tolist(),
            power_db=psd_db.tolist(),
            timestamp=time.time(),
            unit_id=self.unit_id,
        )

    def _simulate_scan(self) -> ScanResult:
        """Generate synthetic spectrum data for CI/test environments."""
        freqs = (
            np.fft.fftshift(np.fft.fftfreq(self.fft_size, d=1.0 / self.sample_rate_hz))
            + self.center_freq_hz
        )
        noise = np.random.normal(-100, 3, self.fft_size)
        # Inject a synthetic jammer signal occasionally
        if np.random.random() < 0.08:
            jammer_bin = np.random.randint(50, self.fft_size - 50)
            width = np.random.randint(3, 20)
            noise[jammer_bin : jammer_bin + width] += np.random.uniform(25, 40)
        return ScanResult(
            center_freq_hz=self.center_freq_hz,
            sample_rate_hz=self.sample_rate_hz,
            frequencies_hz=freqs.tolist(),
            power_db=noise.tolist(),
            timestamp=time.time(),
            unit_id=self.unit_id,
        )
