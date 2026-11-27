"""Live weekly-input inference for the reference CatBoost early-warning models.

The live endpoint accepts this week's weather measurements. Dengue case
features are derived only from confirmed observations before the forecast
origin. Ward/city/static context is taken from the latest historical row.
All lag/rolling/cyclic features are then rebuilt by the exact same feature
engineering function used when the reference CatBoost models were trained.
"""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from src.feature_engineering import SUPPORTED_HORIZONS
from src.reference_catboost_features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    RAW_COLUMNS,
    engineer_features,
    usable_feature_mask,
)
from src.risk_engine import classify_risk, describe_drivers
from src.train import display_case_count

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "processed" / "processed_historical_data.csv"
RUNS_DIR = ROOT / "runs"

# The reference models require at least 8 previous weekly observations for
# their rolling features. We also require a one-week continuation from the
# latest stored observation so the weekly lag contract remains valid.
MIN_HISTORY_WEEKS = 8


class LiveInputError(ValueError):
    """The submitted live observation cannot satisfy the feature contract."""


class LiveModelUnavailableError(RuntimeError):
    """A compatible CatBoost artifact is not available."""


@lru_cache(maxsize=1)
def load_historical_source() -> pd.DataFrame:
    if not DATA_PATH.is_file():
        raise LiveInputError(f"Historical source data was not found: {DATA_PATH}")

    try:
        raw = pd.read_csv(DATA_PATH)
        engineered = engineer_features(raw)
        usable = usable_feature_mask(engineered)
        data = engineered.loc[usable].copy()
    except Exception as exc:
        raise LiveInputError("Historical source data cannot produce valid reference features") from exc

    if data.empty:
        raise LiveInputError("Historical source data contains no usable feature rows")

    return data.sort_values(["ward_id", "date"], kind="stable").reset_index(drop=True)


@lru_cache(maxsize=3)
def load_live_model(horizon_weeks: int) -> tuple[CatBoostRegressor, dict, Path]:
    if horizon_weeks not in SUPPORTED_HORIZONS:
        raise LiveModelUnavailableError(
            f"Unsupported horizon; choose one of {SUPPORTED_HORIZONS} weeks"
        )

    configured = Path(
        __import__("os").getenv("REFERENCE_CATBOOST_RUN", "").strip()
    ) if __import__("os").getenv("REFERENCE_CATBOOST_RUN", "").strip() else None

    if configured is not None:
        run = configured if configured.is_absolute() else (ROOT / configured).resolve()
    else:
        runs = sorted(
            (p for p in RUNS_DIR.glob("reference_catboost_*") if p.is_dir()),
            key=lambda p: p.name,
        )
        if not runs:
            raise LiveModelUnavailableError("No reference CatBoost run is available")
        run = runs[-1]

    try:
        metadata = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
        info = metadata["horizons"][str(horizon_weeks)]
        if metadata.get("feature_names") != FEATURE_COLUMNS:
            raise ValueError("Run feature metadata does not match the live feature contract")
        if metadata.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
            raise ValueError("Run feature schema version does not match the live feature contract")
        if metadata.get("schema_version") != FEATURE_SCHEMA_VERSION:
            raise ValueError("Run metadata schema version does not match the live feature contract")
        if info.get("feature_names") != FEATURE_COLUMNS:
            raise ValueError("Horizon feature metadata does not match the live feature contract")

        artifact = (run / info["model_artifact"]).resolve()
        if run.resolve() not in artifact.parents or not artifact.is_file():
            raise ValueError("Model artifact is missing or outside the selected run")

        model = CatBoostRegressor()
        model.load_model(str(artifact))
        if model.tree_count_ != int(info.get("tree_count", 50)):
            raise ValueError("Unexpected CatBoost tree count")
        if list(model.feature_names_) != list(FEATURE_COLUMNS):
            raise ValueError("CatBoost feature order does not match the live feature contract")
        return model, info, run
    except Exception as exc:
        raise LiveModelUnavailableError(
            f"CatBoost model for {horizon_weeks} weeks is unavailable or incompatible"
        ) from exc


def get_live_areas() -> dict[str, list[str]]:
    data = load_historical_source()
    return {
        str(city): sorted(group.ward_id.astype(str).unique().tolist())
        for city, group in data.groupby("city", sort=True, observed=True)
    }


def get_live_defaults(city: str, ward_id: str) -> dict:
    """Return static ward context and the next expected weekly input date."""
    data = load_historical_source()
    ward = data.loc[
        data.city.astype(str).eq(str(city))
        & data.ward_id.astype(str).eq(str(ward_id))
    ].sort_values("date")

    if ward.empty:
        raise LiveInputError("Unknown city/ward combination")
    if len(ward) < MIN_HISTORY_WEEKS:
        raise LiveInputError("This ward does not have enough weekly history for live inference")

    latest = ward.iloc[-1]
    next_date = pd.Timestamp(latest["date"]) + pd.Timedelta(weeks=1)

    return {
        "city": str(latest["city"]),
        "wardId": str(latest["ward_id"]),
        "latestHistoricalDate": pd.Timestamp(latest["date"]).date().isoformat(),
        "nextExpectedWeeklyDate": next_date.date().isoformat(),
        "populationDensity": float(latest["population_density_sqkm"]),
        "waterLoggingRisk": float(latest["water_logging_risk_index"]),
        "sanitationScore": float(latest["sanitation_score"]),
        "previousCases": float(latest["dengue_cases"]),
        "previousRainfall": float(latest["rainfall_mm"]),
        "previousTemperature": float(latest["avg_temp_C"]),
        "previousHumidity": float(latest["relative_humidity_pct"]),
    }


def _validate_number(name: str, value: float, minimum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LiveInputError(f"{name} must be numeric") from exc
    if not np.isfinite(number):
        raise LiveInputError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise LiveInputError(f"{name} must be at least {minimum}")
    return number


def predict_live(
    *,
    city: str,
    ward_id: str,
    observation_date: date,
    horizon_weeks: int,
    avg_temp_c: float,
    max_temp_c: float,
    min_temp_c: float,
    rainfall_mm: float,
    relative_humidity_pct: float,
    additional_observations: pd.DataFrame | None = None,
    static_context: dict | None = None,
) -> dict:
    """Build one live feature row and run the selected CatBoost horizon model."""
    if horizon_weeks not in SUPPORTED_HORIZONS:
        raise LiveInputError(f"Unsupported horizon; choose one of {SUPPORTED_HORIZONS} weeks")

    data = load_historical_source()
    if additional_observations is not None and not additional_observations.empty:
        extra = additional_observations.loc[
            additional_observations.city.astype(str).eq(str(city))
            & additional_observations.ward_id.astype(str).eq(str(ward_id))
        ].copy()
        if not extra.empty:
            data = pd.concat(
                [
                    data.loc[:, list(RAW_COLUMNS)].copy(),
                    extra.loc[:, list(RAW_COLUMNS)].copy(),
                ],
                ignore_index=True,
            )
            data = engineer_features(data)
            data = data.sort_values(["ward_id", "date"], kind="stable").reset_index(drop=True)
    ward = data.loc[
        data.city.astype(str).eq(str(city))
        & data.ward_id.astype(str).eq(str(ward_id))
    ].sort_values("date")

    if ward.empty:
        raise LiveInputError("Unknown city/ward combination")
    if len(ward) < MIN_HISTORY_WEEKS:
        raise LiveInputError("This ward does not have enough weekly history for live inference")

    latest_date = pd.Timestamp(ward.date.iloc[-1])
    live_date = pd.Timestamp(observation_date)
    expected_date = latest_date + pd.Timedelta(weeks=1)

    if live_date != expected_date:
        raise LiveInputError(
            "Live observation date must be exactly one week after the latest stored observation "
            f"({expected_date.date().isoformat()}). Update the historical dataset first if a week is missing."
        )

    # Static ward-level values are taken from the latest stored row. They do not
    # need to be re-entered by the user every week.
    latest = ward.iloc[-1]
    if static_context is not None:
        latest_static = {
            "population_density_sqkm": float(static_context["populationDensity"]),
            "water_logging_risk_index": float(static_context["waterLoggingRisk"]),
            "sanitation_score": float(static_context["sanitationScore"]),
        }
    else:
        latest_static = {
            "population_density_sqkm": float(latest["population_density_sqkm"]),
            "water_logging_risk_index": float(latest["water_logging_risk_index"]),
            "sanitation_score": float(latest["sanitation_score"]),
        }
    live_row = {
        "date": live_date,
        "city": str(city),
        "ward_id": str(ward_id),
        "population_density_sqkm": latest_static["population_density_sqkm"],
        "water_logging_risk_index": latest_static["water_logging_risk_index"],
        "sanitation_score": latest_static["sanitation_score"],
        "avg_temp_C": _validate_number("Average temperature", avg_temp_c),
        "max_temp_c": _validate_number("Maximum temperature", max_temp_c),
        "min_temp_c": _validate_number("Minimum temperature", min_temp_c),
        "rainfall_mm": _validate_number("Rainfall", rainfall_mm, 0.0),
        "relative_humidity_pct": _validate_number("Relative humidity", relative_humidity_pct, 0.0),
        # Current-week dengue cases are not a live input or a feature. The
        # model uses only case counts observed before this forecast origin.
        "dengue_cases": np.nan,
    }

    # engineer_features() is deliberately reused here. This guarantees the
    # live row gets exactly the same lag, rolling and calendar transformations
    # as the rows used to train the reference CatBoost models.
    source = pd.concat(
        [ward.loc[:, list(RAW_COLUMNS)].copy(), pd.DataFrame([live_row])],
        ignore_index=True,
    )
    engineered = engineer_features(source)
    live_features = engineered.loc[engineered.date.eq(live_date)].copy()

    if len(live_features) != 1:
        raise LiveInputError("The live observation could not be converted into exactly one feature row")
    if not bool(usable_feature_mask(live_features).iloc[0]):
        missing_case_history = any(
            name.startswith("dengue_") and pd.isna(live_features.iloc[0][name])
            for name in FEATURE_COLUMNS
        )
        if missing_case_history:
            raise LiveInputError(
                "Confirmed dengue history is incomplete for the recent weeks. "
                "Update the historical dataset when those case reports are available, then retry."
            )
        raise LiveInputError(
            "The live observation does not have enough complete historical information to build all model features"
        )

    model, info, run = load_live_model(horizon_weeks)
    matrix = live_features.loc[:, list(FEATURE_COLUMNS)].copy()
    for column in CATEGORICAL_FEATURES:
        matrix[column] = matrix[column].astype(str)
    numeric = [c for c in FEATURE_COLUMNS if c not in CATEGORICAL_FEATURES]
    matrix[numeric] = matrix[numeric].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(matrix[numeric].to_numpy(dtype=float)).all():
        raise LiveInputError("Live model features contain missing or non-finite values")

    raw_prediction = float(np.asarray(model.predict(matrix), dtype=float).reshape(-1)[0])
    if not np.isfinite(raw_prediction):
        raise LiveModelUnavailableError("CatBoost produced a non-finite prediction")

    nonnegative = max(0.0, raw_prediction)
    predicted_cases = display_case_count(nonnegative)

    # Risk bands are calculated from the historical observations only. The
    # current live case count must not change the threshold used for its own alert.
    risk = classify_risk(predicted_cases, ward.dengue_cases)
    history = ward.tail(16)

    target_date = live_date + pd.Timedelta(weeks=horizon_weeks)
    return {
        "city": str(city),
        "wardId": str(ward_id),
        "horizonWeeks": int(horizon_weeks),
        "observationDate": live_date.date().isoformat(),
        "targetDate": target_date.date().isoformat(),
        "predictedCases": predicted_cases,
        "rawPredictedCases": raw_prediction,
        "expectedPredictedCases": nonnegative,
        "riskCategory": risk["level"],
        "riskMethod": risk["method"],
        "riskThresholds": risk["thresholds"],
        "riskWarning": risk["warning"],
        "earlyWarning": risk["level"] in {"HIGH", "CRITICAL"},
        "warningMessage": (
            "Early warning triggered: predicted dengue activity is in the HIGH/CRITICAL range."
            if risk["level"] in {"HIGH", "CRITICAL"}
            else "No HIGH/CRITICAL early warning was triggered by the current model prediction."
        ),
        "model": "CatBoost",
        "predictionBackend": "reference_catboost_live_input",
        "experimentId": run.name,
        "modelArtifact": info["model_artifact"],
        "modelTreeCount": info["tree_count"],
        "modelTrainingDataThrough": info["training_cutoff"],
        "modelInputFeatureCount": len(FEATURE_COLUMNS),
        "expectedFeatureCount": len(FEATURE_COLUMNS),
        "featureContract": list(FEATURE_COLUMNS),
        "staticContext": {
            "populationDensity": latest_static["population_density_sqkm"],
            "waterLoggingRisk": latest_static["water_logging_risk_index"],
            "sanitationScore": latest_static["sanitation_score"],
        },
        "liveInput": {
            "avgTempC": live_row["avg_temp_C"],
            "maxTempC": live_row["max_temp_c"],
            "minTempC": live_row["min_temp_c"],
            "rainfallMm": live_row["rainfall_mm"],
            "humidityPct": live_row["relative_humidity_pct"],
            "caseCountsProvided": False,
        },
        "drivers": describe_drivers(ward, pd.Series(live_row)),
        "history": [
            {
                "date": pd.Timestamp(r.date).date().isoformat(),
                "cases": float(r.dengue_cases) if pd.notna(r.dengue_cases) else None,
            }
            for r in history.itertuples(index=False)
        ],
        "forecast": [{"date": target_date.date().isoformat(), "cases": predicted_cases}],
        "observedFeatures": {
            name: float(live_features.iloc[0][name])
            for name in (
                "avg_temp_C", "relative_humidity_pct", "rainfall_mm",
                "rainfall_lag_1w", "rainfall_lag_2w", "rainfall_lag_3w", "rainfall_lag_4w",
                "rainfall_mean_prev_2w", "rainfall_mean_prev_4w", "rainfall_mean_prev_8w",
                "population_density_sqkm", "water_logging_risk_index", "sanitation_score",
                "dengue_cases_lag_1w", "dengue_cases_lag_2w", "dengue_cases_lag_3w", "dengue_cases_lag_4w",
            )
            if name in live_features.columns and pd.notna(live_features.iloc[0][name])
        },
    }
