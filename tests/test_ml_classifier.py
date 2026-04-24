"""Tests for the ML signal classifier."""

import numpy as np
import pytest

from src.analyzer.ml_classifier import (
    MLSignalClassifier,
    SIGNAL_CLASSES,
    _extract_features,
)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

class TestFeatureExtraction:
    def test_shape(self):
        feat = _extract_features(2.45e9, 10e6, -72.0)
        assert feat.shape == (10,)

    def test_dtype(self):
        feat = _extract_features(2.45e9, 10e6, -72.0)
        assert feat.dtype == np.float32

    def test_2g4_band_indicator(self):
        # 2.45 GHz → band indicator index 3 should be 1
        feat = _extract_features(2.45e9, 10e6, -72.0)
        assert feat[3] == 1.0

    def test_out_of_band_indicator_zero(self):
        # 300 MHz → no known band indicators
        feat = _extract_features(300e6, 1e6, -80.0)
        assert feat[3] == 0.0  # 2.4 GHz
        assert feat[4] == 0.0  # 5.8 GHz
        assert feat[5] == 0.0  # GPS

    def test_broadband_indicator(self):
        feat_wide = _extract_features(850e6, 30e6, -70.0)
        feat_narrow = _extract_features(850e6, 0.5e6, -70.0)
        assert feat_wide[9] == 1.0
        assert feat_narrow[9] == 0.0

    def test_power_normalisation_range(self):
        feat = _extract_features(915e6, 1e6, -80.0)
        assert 0.0 <= feat[2] <= 1.0


# ---------------------------------------------------------------------------
# MLSignalClassifier – heuristic fallback (no scikit-learn required)
# ---------------------------------------------------------------------------

class TestMLSignalClassifierHeuristic:
    def setup_method(self):
        self.clf = MLSignalClassifier()

    def test_heuristic_drone_24ghz(self):
        result = self.clf._heuristic_classify(2450e6, 10e6, -75.0)
        assert result == "drone_control_2.4GHz"

    def test_heuristic_gps_jammer(self):
        result = self.clf._heuristic_classify(1575e6, 3e6, -65.0)
        assert "gps" in result.lower()

    def test_heuristic_cellular_jammer(self):
        result = self.clf._heuristic_classify(850e6, 20e6, -70.0)
        assert "jammer" in result.lower() or result == "unknown"

    def test_heuristic_unknown(self):
        result = self.clf._heuristic_classify(300e6, 1e6, -80.0)
        assert result == "unknown"

    def test_classify_falls_back_when_not_trained(self):
        # Not trained – should silently fall back to heuristic
        result = self.clf.classify(2450e6, 10e6, -72.0)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# MLSignalClassifier – trained mode (skip if scikit-learn not installed)
# ---------------------------------------------------------------------------

sklearn = pytest.importorskip("sklearn", reason="scikit-learn not installed")


class TestMLSignalClassifierTrained:
    def setup_method(self):
        self.clf = MLSignalClassifier()
        self.clf.train(n_samples_per_class=50)  # small for test speed

    def test_is_trained(self):
        assert self.clf._trained is True

    def test_classify_returns_valid_class(self):
        result = self.clf.classify(2450e6, 10e6, -72.0)
        assert result in SIGNAL_CLASSES

    def test_classify_drone_24ghz(self):
        result = self.clf.classify(2450e6, 10e6, -72.0)
        assert result == "drone_control_2.4GHz"

    def test_classify_gps_jammer(self):
        result = self.clf.classify(1575.42e6, 2e6, -65.0)
        assert "gps" in result.lower()

    def test_classify_cellular_jammer(self):
        result = self.clf.classify(850e6, 25e6, -68.0)
        assert "jammer" in result.lower()

    def test_classify_vhf(self):
        result = self.clf.classify(127e6, 0.5e6, -75.0)
        assert "vhf" in result.lower() or result == "unknown"

    def test_save_load_roundtrip(self, tmp_path):
        model_path = str(tmp_path / "test_model.joblib")
        self.clf.save(model_path)
        assert os.path.exists(model_path)

        clf2 = MLSignalClassifier()
        clf2.load(model_path)
        assert clf2._trained is True
        r1 = self.clf.classify(2450e6, 10e6, -72.0)
        r2 = clf2.classify(2450e6, 10e6, -72.0)
        assert r1 == r2

    def test_save_without_training_raises(self, tmp_path):
        clf = MLSignalClassifier()
        with pytest.raises(RuntimeError, match="not been trained"):
            clf.save(str(tmp_path / "nope.joblib"))

    def test_generate_training_data_shape(self):
        X, y = self.clf._generate_training_data(10)
        assert X.ndim == 2
        assert X.shape[1] == 10
        assert len(y) == len(X)

    def test_all_classes_represented(self):
        X, y = self.clf._generate_training_data(5)
        unique_labels = set(y)
        for cls in SIGNAL_CLASSES:
            assert cls in unique_labels


import os
