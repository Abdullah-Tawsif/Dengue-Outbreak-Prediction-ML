"""Load trained CatBoost bundles and serve ward-level forecasts from a chosen origin week."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from src.feature_engineering import RAW_COLUMNS, build_features
from src.risk_engine import classify_risk, describe_drivers

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "processed" / "dengue_dataset.csv"
MODEL_DIR = ROOT / "models"
DEBUG_PREDICTIONS = os.getenv("DEBUG_PREDICTIONS", "0") == "1"
MIN_HISTORY_WEEKS = 8  # longest rolling window


@lru_cache(maxsize=1)
def load_raw_data() -> pd.DataFrame:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Processed dataset not found: {DATA_PATH}")
    data = pd.read_csv(DATA_PATH)
    missing = [c for c in RAW_COLUMNS if c not in data.columns]
    if missing:
        raise ValueError(f"Processed dataset lacks required columns: {missing}")
    data = data[RAW_COLUMNS].copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data = data.dropna(subset=RAW_COLUMNS).sort_values(["ward_id", "date"])
    if data.empty:
        raise ValueError("No valid observations found in the processed dataset.")
    return data.reset_index(drop=True)


@lru_cache(maxsize=1)
def load_engineered() -> pd.DataFrame:
    """Feature engineering runs ONCE per process, not once per request."""
    return build_features(load_raw_data())


@lru_cache(maxsize=1)
def seasonal_profile() -> dict[int, float]:
    """Mean weekly cases per ward-week, by calendar month (data driven)."""
    raw = load_raw_data()
    return raw.groupby(raw["date"].dt.month)["dengue_cases"].mean().to_dict()


@lru_cache(maxsize=2)
def load_model_bundle(horizon_weeks: int) -> dict:
    if horizon_weeks not in (2, 4):
        raise ValueError("Only 2-week and 4-week forecasts are available.")
    path = MODEL_DIR / f"catboost_model_{horizon_weeks}w.joblib"
    if not path.exists():
        raise FileNotFoundError(f"Model file not found: {path}")
    bundle = joblib.load(path)
    required = {"model", "feature_columns", "categorical_features", "horizon_weeks"}
    if not isinstance(bundle, dict) or not required.issubset(bundle):
        raise ValueError(f"Unexpected model bundle format in {path}.")
    if int(bundle["horizon_weeks"]) != horizon_weeks:
        raise ValueError(f"Model horizon mismatch in {path}.")
    return bundle


def get_origin_range(ward_id: str) -> dict:
    """Earliest/latest valid forecast origin for a ward (used by the UI date picker)."""
    dates = load_raw_data().query("ward_id == @ward_id")["date"].sort_values()
    if len(dates) <= MIN_HISTORY_WEEKS:
        raise LookupError(f"Not enough history for ward {ward_id}.")
    return {"earliest": dates.iloc[MIN_HISTORY_WEEKS].strftime("%Y-%m-%d"),
            "latest": dates.iloc[-1].strftime("%Y-%m-%d")}


def _season_note(target_date: pd.Timestamp, prediction: float) -> str | None:
    profile = seasonal_profile()
    peak = max(profile.values())
    month_mean = profile.get(target_date.month, 0.0)
    if peak > 0 and month_mean < 0.1 * peak:
        return ("Forecast target falls in the dengue off-season (historically almost no cases "
                "in this month), so a near-zero forecast is expected. Choose a forecast origin "
                "in May-Sep to see peak-season behaviour.")
    return None


def generate_forecast(city: str, ward_id: str, horizon_weeks: int, as_of: str | None = None) -> dict:
    raw = load_raw_data()
    engineered = load_engineered()

    ward_raw = raw[raw["ward_id"].astype(str) == ward_id].sort_values("date")
    if ward_raw.empty:
        raise LookupError(f"No historical data found for ward {ward_id}.")
    if city not in ward_raw["city"].astype(str).unique():
        raise LookupError(f"Ward {ward_id} does not belong to {city}.")
    ward_feat = engineered[engineered["ward_id"].astype(str) == ward_id].sort_values("date").reset_index(drop=True)

    # ---- choose the forecast origin week (latest week <= as_of) ----
    if as_of:
        try:
            cutoff = pd.Timestamp(as_of)
        except Exception as exc:
            raise ValueError("asOf must be a date in YYYY-MM-DD format.") from exc
        eligible = ward_feat[ward_feat["date"] <= cutoff]
    else:
        eligible = ward_feat
    if len(eligible) <= MIN_HISTORY_WEEKS:
        rng = get_origin_range(ward_id)
        raise ValueError(f"Forecast origin must be between {rng['earliest']} and {rng['latest']}.")
    latest = eligible.iloc[-1]
    observed_date = pd.Timestamp(latest["date"])
    target_date = observed_date + pd.Timedelta(weeks=horizon_weeks)

    # no look-ahead: risk bands and driver context use only data up to the origin
    seen = ward_raw[ward_raw["date"] <= observed_date]

    bundle = load_model_bundle(horizon_weeks)
    cols = list(bundle["feature_columns"])
    cats = list(bundle["categorical_features"])
    missing = [c for c in cols if c not in ward_feat.columns]
    if missing:
        raise ValueError(f"Inference feature schema mismatch. Missing features: {missing}")

    X = pd.DataFrame([latest[cols]], columns=cols)
    for c in cats:
        X[c] = X[c].astype(str)
    for c in [c for c in cols if c not in cats]:
        X[c] = pd.to_numeric(X[c], errors="coerce")
    if X.isna().any().any():
        raise ValueError(f"Missing or invalid model inputs: {X.columns[X.isna().any()].tolist()}")

    raw_pred = float(np.asarray(bundle["model"].predict(X)).reshape(-1)[0])
    if not np.isfinite(raw_pred):
        raise ValueError(f"CatBoost returned an invalid prediction: {raw_pred}")
    prediction = max(0.0, raw_pred)

    if DEBUG_PREDICTIONS:
        print(f"[predict] {ward_id} origin={observed_date.date()} h={horizon_weeks}w raw={raw_pred:.3f}")

    risk = classify_risk(prediction, seen["dengue_cases"])
    drivers = describe_drivers(seen, seen.iloc[-1])

    history = [{"date": r.date.strftime("%Y-%m-%d"), "cases": max(0.0, float(r.dengue_cases))}
               for r in seen.tail(12).itertuples(index=False)]

    # backtest: what actually happened at the target week (if it exists in the data)
    actual_row = ward_raw[ward_raw["date"] == target_date]
    actual = float(actual_row["dengue_cases"].iloc[0]) if not actual_row.empty else None

    return {
        "city": city,
        "wardId": ward_id,
        "horizonWeeks": horizon_weeks,
        "predictedCases": round(prediction, 2),
        "riskCategory": risk["level"],
        "riskMethod": risk["method"],
        "riskThresholds": risk["thresholds"],
        "riskWarning": risk["warning"],
        "observationDate": observed_date.strftime("%Y-%m-%d"),
        "dataThrough": ward_raw["date"].max().strftime("%Y-%m-%d"),
        "targetDate": target_date.strftime("%Y-%m-%d"),
        "actualCases": actual,                      # None if target is beyond the dataset
        "seasonNote": _season_note(target_date, prediction),
        "drivers": drivers,
        "history": history,
        "forecast": [{"date": target_date.strftime("%Y-%m-%d"), "cases": round(prediction, 2)}],
        "model": f"CatBoost {horizon_weeks}-week regressor",
        "dataIsHistorical": True,
    }
