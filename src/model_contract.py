"""Shared model artifact contract and prediction output normalization."""

from __future__ import annotations

import math


TARGET_DEFINITION = "Weekly new dengue cases reported for the ward and target observation week; not cumulative."
EVALUATION_METHOD = "expanding_window_rolling_origin_v1"
ARTIFACT_SCHEMA_VERSION = 2
MAX_CURRENT_DATA_AGE_DAYS = 14


class ForecastUnavailableError(RuntimeError):
    """The application lacks sufficiently fresh, valid forecast data."""


class ModelUnavailableError(RuntimeError):
    """No compatible trained model artifact is available."""


def display_case_count(prediction: float) -> int:
    """Clip below zero and round half-up for a whole weekly reported count."""
    value = float(prediction)
    if not math.isfinite(value):
        raise ValueError("Prediction must be finite")
    return int(math.floor(max(0.0, value) + 0.5))
