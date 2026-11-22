"""Ward-relative risk bands for prototype presentation.

Dengue case counts are zero-inflated and strongly seasonal (about 58% of ward-weeks
are 0, and Nov-Mar averages ~0.01 cases). Percentiles over ALL weeks therefore give a
median of 0, which makes any forecast of 0 -> LOW and 0.1 -> MEDIUM.
Fix: percentiles are computed over *active* weeks (cases > 0) only, and a forecast
below one case is always LOW.

These are engineering heuristics, not validated public-health thresholds.
"""
from __future__ import annotations

import pandas as pd

RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
MIN_ACTIVE_WEEKS = 8
NEAR_ZERO = 1.0  # < 1 expected case is always LOW


def risk_thresholds(historical_cases: pd.Series) -> dict:
    values = pd.to_numeric(historical_cases, errors="coerce").dropna().clip(lower=0)
    if values.empty:
        raise ValueError("Cannot classify risk without historical ward case counts.")
    active = values[values > 0]
    if len(active) >= MIN_ACTIVE_WEEKS:
        q50, q75, q90 = (float(active.quantile(q)) for q in (0.50, 0.75, 0.90))
    else:  # too little history for this ward: conservative fixed fallback
        q50, q75, q90 = 3.0, 6.0, 10.0
    q50 = max(q50, NEAR_ZERO)
    q75 = max(q75, q50 + 1)
    q90 = max(q90, q75 + 1)
    return {"low_max": q50, "medium_max": q75, "high_max": q90}


def classify_risk(predicted_cases: float, historical_cases: pd.Series) -> dict:
    t = risk_thresholds(historical_cases)
    value = max(0.0, float(predicted_cases))
    if value <= t["low_max"]:
        level = "LOW"
    elif value <= t["medium_max"]:
        level = "MEDIUM"
    elif value <= t["high_max"]:
        level = "HIGH"
    else:
        level = "CRITICAL"
    return {
        "level": level,
        "method": "ward_active_week_percentiles",
        "thresholds": t,
        "warning": "Prototype ward-relative bands (50/75/90th percentile of weeks with cases); not clinically validated.",
    }


def _relative_label(value: float, series: pd.Series, unit: str = "") -> str:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return f"{value:.1f}{unit} (historical comparison unavailable)"
    p75 = float(clean.quantile(0.75))
    q = "above the ward's historical 75th percentile" if value > p75 else "within the ward's usual historical range"
    return f"{value:.1f}{unit} — {q}"


def describe_drivers(ward_frame: pd.DataFrame, latest: pd.Series) -> dict[str, str]:
    """Observed inputs at the forecast origin (context, not SHAP)."""
    return {
        "rainfall": _relative_label(float(latest["rainfall_mm"]), ward_frame["rainfall_mm"], " mm"),
        "temperature": _relative_label(float(latest["avg_temp_C"]), ward_frame["avg_temp_C"], " °C"),
        "humidity": _relative_label(float(latest["relative_humidity_pct"]), ward_frame["relative_humidity_pct"], "%"),
        "recentCases": f"{int(latest['dengue_cases'])} reported cases in the latest observed week",
    }
