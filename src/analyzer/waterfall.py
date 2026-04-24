"""Waterfall display renderer for terminal and file output.

Provides ASCII/ANSI waterfall rendering for CLI monitoring dashboards and
PNG/CSV export for post-processing.
"""

from __future__ import annotations

import logging
import os
import time
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_MATPLOTLIB_AVAILABLE = False
try:
    import matplotlib  # type: ignore
    matplotlib.use("Agg")  # non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    _MATPLOTLIB_AVAILABLE = True
except ImportError:
    logger.warning("matplotlib not installed – PNG waterfall export unavailable.")


# ANSI colour gradient (cold → hot):  blue → cyan → green → yellow → red
_ANSI_GRADIENT = [
    "\033[34m",  # blue    (very low)
    "\033[36m",  # cyan
    "\033[32m",  # green
    "\033[33m",  # yellow
    "\033[31m",  # red     (very high)
]
_ANSI_RESET = "\033[0m"
_WATERFALL_CHAR = "█"


class WaterfallDisplay:
    """Renders spectrum data as a waterfall (time × frequency).

    Args:
        min_db: Lower clipping level for the colour scale (dBm).
        max_db: Upper clipping level for the colour scale (dBm).
        width_chars: Number of terminal columns for ASCII rendering.
    """

    def __init__(
        self,
        min_db: float = -120.0,
        max_db: float = -40.0,
        width_chars: int = 80,
    ) -> None:
        self.min_db = min_db
        self.max_db = max_db
        self.width_chars = width_chars
        self._rows: List[np.ndarray] = []  # Each row is a dBm array

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_row(self, power_db: List[float]) -> None:
        """Append a spectrum row to the waterfall buffer."""
        self._rows.append(np.array(power_db))

    def clear(self) -> None:
        """Clear the waterfall buffer."""
        self._rows.clear()

    def render_ascii(self, last_n_rows: int = 24) -> str:
        """Render the most recent *last_n_rows* as an ANSI waterfall string.

        Args:
            last_n_rows: Number of time rows to display.

        Returns:
            Multi-line ANSI string suitable for printing to a terminal.
        """
        rows = self._rows[-last_n_rows:]
        if not rows:
            return "(no data)"

        lines: List[str] = []
        for row in rows:
            # Downsample/upsample row to width_chars columns
            resampled = self._resample(row, self.width_chars)
            line = ""
            for val in resampled:
                colour = self._db_to_ansi(val)
                line += f"{colour}{_WATERFALL_CHAR}{_ANSI_RESET}"
            lines.append(line)

        return "\n".join(lines)

    def render_header(self, freq_start_hz: float, freq_end_hz: float) -> str:
        """Return a frequency axis header string."""
        start_mhz = freq_start_hz / 1e6
        end_mhz = freq_end_hz / 1e6
        label = f"{start_mhz:.1f} MHz"
        rlabel = f"{end_mhz:.1f} MHz"
        mid_label = f"{(start_mhz + end_mhz) / 2:.1f} MHz"
        pad = (self.width_chars - len(label) - len(rlabel) - len(mid_label)) // 2
        return f"{label}{' ' * max(pad, 1)}{mid_label}{' ' * max(pad, 1)}{rlabel}"

    def save_png(
        self,
        path: str,
        freq_start_hz: float,
        freq_end_hz: float,
        last_n_rows: int = 200,
        title: str = "RF Spectrum Waterfall",
    ) -> None:
        """Save the waterfall as a PNG image.

        Args:
            path: Output file path.
            freq_start_hz: Start frequency for the x-axis label (Hz).
            freq_end_hz: End frequency for the x-axis label (Hz).
            last_n_rows: Maximum number of time rows to render.
            title: Plot title.

        Raises:
            RuntimeError: If matplotlib is not installed.
        """
        if not _MATPLOTLIB_AVAILABLE:
            raise RuntimeError("matplotlib is required to save PNG waterfall images.")

        rows = self._rows[-last_n_rows:]
        if not rows:
            logger.warning("No waterfall data to save.")
            return

        matrix = np.array(rows)
        matrix = np.clip(matrix, self.min_db, self.max_db)

        fig, ax = plt.subplots(figsize=(12, 6))
        extent = [
            freq_start_hz / 1e6,
            freq_end_hz / 1e6,
            len(rows),
            0,
        ]
        im = ax.imshow(
            matrix,
            aspect="auto",
            extent=extent,
            cmap="inferno",
            vmin=self.min_db,
            vmax=self.max_db,
            interpolation="nearest",
        )
        ax.set_xlabel("Frequency (MHz)")
        ax.set_ylabel("Time (newest at bottom)")
        ax.set_title(title)
        plt.colorbar(im, ax=ax, label="Power (dBm)")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        plt.tight_layout()
        plt.savefig(path, dpi=100)
        plt.close(fig)
        logger.info("Waterfall saved to %s", path)

    def save_csv(self, path: str, last_n_rows: int = 200) -> None:
        """Export waterfall data as a CSV file.

        Args:
            path: Output file path.
            last_n_rows: Maximum number of rows to export.
        """
        rows = self._rows[-last_n_rows:]
        if not rows:
            logger.warning("No waterfall data to export.")
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as fh:
            for row in rows:
                fh.write(",".join(f"{v:.2f}" for v in row))
                fh.write("\n")
        logger.info("Waterfall CSV saved to %s", path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resample(self, row: np.ndarray, target_len: int) -> np.ndarray:
        """Linearly resample *row* to *target_len* points."""
        if len(row) == target_len:
            return row
        indices = np.linspace(0, len(row) - 1, target_len)
        return np.interp(indices, np.arange(len(row)), row)

    def _db_to_ansi(self, db: float) -> str:
        """Map a dBm value to an ANSI colour escape code."""
        normalised = (db - self.min_db) / max(self.max_db - self.min_db, 1e-9)
        normalised = max(0.0, min(1.0, normalised))
        idx = int(normalised * (len(_ANSI_GRADIENT) - 1))
        return _ANSI_GRADIENT[idx]
