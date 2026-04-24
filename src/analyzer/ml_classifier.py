"""Machine-learning signal classifier (scikit-learn SVM).

Replaces the heuristic ``_classify()`` function in
:class:`~src.analyzer.signal_detector.SignalDetector` with a lightweight
Support Vector Machine trained on feature vectors derived from known
drone / jammer RF signatures.

The model is trained on synthetic feature vectors in simulation mode.
In production, replace ``_generate_training_data()`` with real IQ
recordings or annotated detection logs.

Feature vector (7 dimensions):
    [freq_mhz_norm, bw_mhz, power_dbm, freq_bin_2g4, freq_bin_5g8,
     freq_bin_gps, freq_bin_cellular]

Where ``freq_bin_*`` are binary indicators for known threat bands.

Usage::

    from src.analyzer.ml_classifier import MLSignalClassifier

    clf = MLSignalClassifier()
    clf.train()                          # or clf.load("model.joblib")
    label = clf.classify(freq_hz=2.45e9, bandwidth_hz=10e6, power_db=-72.0)
    # → "drone_control_2.4GHz"
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_SKLEARN_AVAILABLE = False
try:
    from sklearn.preprocessing import LabelEncoder  # type: ignore
    from sklearn.svm import SVC  # type: ignore

    _SKLEARN_AVAILABLE = True
except ImportError:
    logger.warning(
        "scikit-learn not installed – MLSignalClassifier will fall back to heuristic rules. "
        "Install with: pip install scikit-learn"
    )

# Known signal classes
SIGNAL_CLASSES = [
    "drone_control_2.4GHz",
    "drone_control_5.8GHz",
    "gps_jammer",
    "cellular_jammer",
    "vhf_air_band",
    "ism_433_rogue",
    "unknown",
]

# Frequency band boundaries (MHz)
_BAND_BOUNDARIES = {
    "2g4": (2390.0, 2490.0),
    "5g8": (5725.0, 5875.0),
    "gps": (1560.0, 1590.0),
    "cellular": (700.0, 960.0),
    "vhf_atc": (118.0, 137.0),
    "ism_433": (430.0, 440.0),
}


def _extract_features(
    freq_hz: float, bandwidth_hz: float, power_db: float
) -> np.ndarray:
    """Build a 10-dimensional feature vector for a signal observation.

    Args:
        freq_hz: Centre frequency in Hz.
        bandwidth_hz: Signal bandwidth in Hz.
        power_db: Peak power in dBm.

    Returns:
        Feature vector as a 1-D numpy array.
    """
    freq_mhz = freq_hz / 1e6
    bw_mhz = bandwidth_hz / 1e6

    # Continuous features (normalised to roughly [0, 1])
    freq_norm = freq_mhz / 6000.0
    bw_norm = min(bw_mhz / 100.0, 1.0)
    power_norm = (power_db + 120.0) / 80.0  # maps [-120, -40] → [0, 1]

    # Binary band indicators
    def _in_band(name: str) -> float:
        lo, hi = _BAND_BOUNDARIES[name]
        return 1.0 if lo <= freq_mhz <= hi else 0.0

    return np.array(
        [
            freq_norm,
            bw_norm,
            power_norm,
            _in_band("2g4"),
            _in_band("5g8"),
            _in_band("gps"),
            _in_band("cellular"),
            _in_band("vhf_atc"),
            _in_band("ism_433"),
            1.0 if bw_mhz > 5.0 else 0.0,  # broadband indicator
        ],
        dtype=np.float32,
    )


class MLSignalClassifier:
    """SVM-based RF signal classifier.

    Args:
        kernel: SVM kernel type (default ``"rbf"``).
        C: SVM regularisation parameter.
        probability: Whether to enable probability calibration (slower).
    """

    def __init__(
        self,
        kernel: str = "rbf",
        C: float = 10.0,
        probability: bool = False,
    ) -> None:
        self.kernel = kernel
        self.C = C
        self.probability = probability
        self._model: Optional[object] = None  # SVC instance
        self._encoder: Optional[object] = None  # LabelEncoder instance
        self._trained: bool = False

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self, n_samples_per_class: int = 300) -> None:
        """Train the classifier on synthetic data.

        Args:
            n_samples_per_class: Number of synthetic examples per class.
                More samples → better generalisation (but slower training).
        """
        if not _SKLEARN_AVAILABLE:
            logger.warning("MLSignalClassifier: scikit-learn not available; skipping training.")
            return

        X, y = self._generate_training_data(n_samples_per_class)
        enc = LabelEncoder()
        y_enc = enc.fit_transform(y)

        model = SVC(
            kernel=self.kernel,
            C=self.C,
            probability=self.probability,
            gamma="scale",
        )
        model.fit(X, y_enc)

        self._model = model
        self._encoder = enc
        self._trained = True
        logger.info(
            "MLSignalClassifier: trained on %d samples (%d classes).",
            len(X),
            len(enc.classes_),
        )

    def save(self, path: str) -> None:
        """Serialise the trained model to *path* using joblib.

        Args:
            path: Output file path (e.g. ``"model.joblib"``).
        """
        if not self._trained:
            raise RuntimeError("Model has not been trained yet.")
        try:
            import joblib  # type: ignore

            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            joblib.dump({"model": self._model, "encoder": self._encoder}, path)
            logger.info("MLSignalClassifier: model saved to %s", path)
        except ImportError:
            logger.error("joblib not installed – cannot save model.")

    def load(self, path: str) -> None:
        """Load a previously saved model from *path*.

        Args:
            path: File path produced by :meth:`save`.
        """
        try:
            import joblib  # type: ignore

            obj = joblib.load(path)
            self._model = obj["model"]
            self._encoder = obj["encoder"]
            self._trained = True
            logger.info("MLSignalClassifier: model loaded from %s", path)
        except Exception as exc:  # noqa: BLE001
            logger.error("MLSignalClassifier: failed to load model: %s", exc)

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def classify(
        self, freq_hz: float, bandwidth_hz: float, power_db: float
    ) -> str:
        """Return the predicted signal class for given RF parameters.

        Falls back to heuristic rules when the model is not trained.

        Args:
            freq_hz: Centre frequency (Hz).
            bandwidth_hz: Signal bandwidth (Hz).
            power_db: Peak power (dBm).

        Returns:
            Signal class string (e.g. ``"drone_control_2.4GHz"``).
        """
        if not self._trained or not _SKLEARN_AVAILABLE:
            return self._heuristic_classify(freq_hz, bandwidth_hz, power_db)

        features = _extract_features(freq_hz, bandwidth_hz, power_db).reshape(1, -1)
        try:
            pred = self._model.predict(features)  # type: ignore[union-attr]
            label = self._encoder.inverse_transform(pred)[0]  # type: ignore[union-attr]
            return str(label)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MLSignalClassifier: inference failed: %s", exc)
            return self._heuristic_classify(freq_hz, bandwidth_hz, power_db)

    def predict_proba(
        self, freq_hz: float, bandwidth_hz: float, power_db: float
    ) -> dict:
        """Return class probabilities (requires ``probability=True``).

        Args:
            freq_hz: Centre frequency (Hz).
            bandwidth_hz: Signal bandwidth (Hz).
            power_db: Peak power (dBm).

        Returns:
            Dict mapping class labels to probability floats, or empty dict
            if probabilities are not enabled.
        """
        if not self._trained or not self.probability or not _SKLEARN_AVAILABLE:
            return {}

        features = _extract_features(freq_hz, bandwidth_hz, power_db).reshape(1, -1)
        try:
            probs = self._model.predict_proba(features)[0]  # type: ignore[union-attr]
            classes = self._encoder.classes_  # type: ignore[union-attr]
            return {str(c): float(p) for c, p in zip(classes, probs)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("MLSignalClassifier: predict_proba failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Synthetic training data generator
    # ------------------------------------------------------------------

    def _generate_training_data(
        self, n_per_class: int
    ) -> Tuple[np.ndarray, list]:
        """Generate synthetic labelled training examples.

        Each class is modelled as a Gaussian distribution centred on its
        typical RF parameters.  Replace with real IQ-derived features in
        production.

        Args:
            n_per_class: Examples per class.

        Returns:
            Tuple of ``(X, y)`` where X has shape ``(n_total, 10)`` and y
            is a list of class label strings.
        """
        rng = np.random.default_rng(seed=42)
        X_list, y_list = [], []

        def _add(freq_mhz: float, bw_mhz: float, pwr_dbm: float, label: str) -> None:
            for _ in range(n_per_class):
                f = (freq_mhz + rng.normal(0, 5.0)) * 1e6
                b = max(0.1, bw_mhz + rng.normal(0, bw_mhz * 0.2)) * 1e6
                p = pwr_dbm + rng.normal(0, 4.0)
                X_list.append(_extract_features(f, b, p))
                y_list.append(label)

        _add(2450.0, 15.0, -72.0, "drone_control_2.4GHz")
        _add(5800.0, 25.0, -70.0, "drone_control_5.8GHz")
        _add(1575.42, 3.0, -65.0, "gps_jammer")
        _add(850.0, 30.0, -68.0, "cellular_jammer")
        _add(127.0, 0.5, -75.0, "vhf_air_band")
        _add(433.0, 1.0, -78.0, "ism_433_rogue")
        # Unknown – random frequencies in non-classified bands
        for _ in range(n_per_class):
            f = rng.uniform(200.0, 600.0) * 1e6
            b = rng.uniform(0.5, 5.0) * 1e6
            p = rng.uniform(-90.0, -60.0)
            X_list.append(_extract_features(f, b, p))
            y_list.append("unknown")

        return np.stack(X_list), y_list

    # ------------------------------------------------------------------
    # Heuristic fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _heuristic_classify(
        freq_hz: float, bandwidth_hz: float, power_db: float
    ) -> str:
        """Same heuristic rules as SignalDetector._classify()."""
        freq_mhz = freq_hz / 1e6
        bw_mhz = bandwidth_hz / 1e6
        if 2390 <= freq_mhz <= 2490 and bw_mhz < 30:
            return "drone_control_2.4GHz"
        if 5725 <= freq_mhz <= 5875 and bw_mhz < 50:
            return "drone_control_5.8GHz"
        if 1560 <= freq_mhz <= 1590 and power_db > -70:
            return "gps_jammer"
        if 700 <= freq_mhz <= 960 and bw_mhz > 5:
            return "cellular_jammer"
        if 118 <= freq_mhz <= 137:
            return "vhf_air_band"
        return "unknown"
