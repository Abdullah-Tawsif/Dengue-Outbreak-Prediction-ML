import os
import joblib
import numpy as np
import pandas as pd

from catboost import CatBoostRegressor


# =========================
# CONFIGURATION
# =========================

DATA_PATH = "data/processed/dengue_dataset.csv"
MODEL_DIR = "models"

RANDOM_STATE = 42

TARGETS = {
    "2w": 2,
    "3w": 3,
    "4w": 4
}

RAW_COLUMNS = [
    "date",
    "city",
    "ward_id",
    "population_density_sqkm",
    "water_logging_risk_index",
    "sanitation_score",
    "avg_temp_C",
    "max_temp_c",
    "min_temp_c",
    "rainfall_mm",
    "relative_humidity_pct",
    "dengue_cases"
]


# =========================
# CREATE DIRECTORIES
# =========================

os.makedirs(MODEL_DIR, exist_ok=True)


# =========================
# LOAD DATA
# =========================

print("Loading data...")

df = pd.read_csv(DATA_PATH)

print(f"Original rows: {len(df)}")


# =========================
# KEEP ONLY RAW COLUMNS
# =========================

df = df[RAW_COLUMNS].copy()

df["date"] = pd.to_datetime(df["date"])

df = df.sort_values(
    ["ward_id", "date"]
).reset_index(drop=True)


# =========================
# RAW DATA CHECK
# =========================

missing = df[RAW_COLUMNS].isna().sum()

if missing.sum() > 0:
    print("\nMissing values found in raw data:")
    print(missing[missing > 0])

    raise ValueError(
        "Raw dataset contains missing values. "
        "Fix the raw data before training."
    )

print("Raw data check passed.")


# =========================
# FEATURE ENGINEERING
# =========================

print("Creating features...")

grouped = df.groupby("ward_id", group_keys=False)


# ----- Lag features -----

for lag in range(1, 5):

    df[f"rainfall_lag_{lag}w"] = (
        grouped["rainfall_mm"].shift(lag)
    )

    df[f"avg_temp_lag_{lag}w"] = (
        grouped["avg_temp_C"].shift(lag)
    )

    df[f"humidity_lag_{lag}w"] = (
        grouped["relative_humidity_pct"].shift(lag)
    )

    df[f"dengue_cases_lag_{lag}w"] = (
        grouped["dengue_cases"].shift(lag)
    )


# ----- Rolling rainfall features -----

for window in [2, 4, 8]:

    df[f"rainfall_mean_prev_{window}w"] = (
        grouped["rainfall_mm"]
        .transform(
            lambda x: x.shift(1).rolling(window).mean()
        )
    )

    df[f"rainfall_sum_prev_{window}w"] = (
        grouped["rainfall_mm"]
        .transform(
            lambda x: x.shift(1).rolling(window).sum()
        )
    )


# ----- Rolling temperature features -----

for window in [2, 4, 8]:

    df[f"avg_temp_mean_prev_{window}w"] = (
        grouped["avg_temp_C"]
        .transform(
            lambda x: x.shift(1).rolling(window).mean()
        )
    )


# ----- Rolling humidity features -----

for window in [2, 4, 8]:

    df[f"humidity_mean_prev_{window}w"] = (
        grouped["relative_humidity_pct"]
        .transform(
            lambda x: x.shift(1).rolling(window).mean()
        )
    )


# ----- Rolling dengue features -----

for window in [2, 4, 8]:

    df[f"dengue_mean_prev_{window}w"] = (
        grouped["dengue_cases"]
        .transform(
            lambda x: x.shift(1).rolling(window).mean()
        )
    )


# ----- Dengue trend features -----

df["dengue_change_1w"] = (
    df["dengue_cases"]
    - df["dengue_cases_lag_1w"]
)

df["dengue_change_2w"] = (
    df["dengue_cases_lag_1w"]
    - df["dengue_cases_lag_2w"]
)

df["dengue_change_4w"] = (
    df["dengue_cases_lag_1w"]
    - df["dengue_cases_lag_4w"]
)


# ----- Dengue growth features -----

df["dengue_growth_ratio_1w"] = (
    df["dengue_cases"]
    / (df["dengue_cases_lag_1w"] + 1)
)

df["dengue_growth_ratio_2w"] = (
    df["dengue_cases"]
    / (df["dengue_cases_lag_2w"] + 1)
)


# ----- Calendar features -----

df["calendar_week"] = df["date"].dt.isocalendar().week.astype(int)

df["calendar_month"] = df["date"].dt.month


# ----- Cyclical calendar features -----

df["week_sin"] = np.sin(
    2 * np.pi * df["calendar_week"] / 52
)

df["week_cos"] = np.cos(
    2 * np.pi * df["calendar_week"] / 52
)

df["month_sin"] = np.sin(
    2 * np.pi * df["calendar_month"] / 12
)

df["month_cos"] = np.cos(
    2 * np.pi * df["calendar_month"] / 12
)


# =========================
# CREATE FUTURE TARGETS
# =========================

for name, weeks in TARGETS.items():

    df[f"target_{name}"] = (
        grouped["dengue_cases"].shift(-weeks)
    )

    df[f"target_date_{name}"] = (
        grouped["date"].shift(-weeks)
    )


# =========================
# DEFINE FEATURES
# =========================

DROP_COLUMNS = [
    "date",
    "dengue_cases",

    "target_2w",
    "target_3w",
    "target_4w",

    "target_date_2w",
    "target_date_3w",
    "target_date_4w"
]

FEATURE_COLUMNS = [
    column
    for column in df.columns
    if column not in DROP_COLUMNS
]

CATEGORICAL_FEATURES = [
    "city",
    "ward_id"
]

CATEGORICAL_INDICES = [
    FEATURE_COLUMNS.index(column)
    for column in CATEGORICAL_FEATURES
]


# =========================
# CATBOOST CONFIGURATION
# =========================

catboost_params = {
    "loss_function": "RMSE",
    "iterations": 700,
    "learning_rate": 0.05,
    "depth": 7,
    "l2_leaf_reg": 3,
    "random_seed": RANDOM_STATE,
    "verbose": 100,
    "allow_writing_files": False
}


# =========================
# TRAIN MODELS
# =========================

for horizon_name, weeks in TARGETS.items():

    print(f"\nTraining CatBoost for {weeks}-week forecast...")

    target_column = f"target_{horizon_name}"
    target_date_column = f"target_date_{horizon_name}"

    # Remove rows where future target does not exist
    model_df = df.dropna(
        subset=[target_column]
    ).copy()

    # Remove rows where engineered features are unavailable
    model_df = model_df.dropna(
        subset=FEATURE_COLUMNS
    ).copy()

    # Target date
    model_df[target_date_column] = pd.to_datetime(
        model_df[target_date_column]
    )

    # Chronological training data
    train_df = model_df[
        model_df[target_date_column].dt.year <= 2024
    ].copy()

    test_df = model_df[
        model_df[target_date_column].dt.year == 2025
    ].copy()

    print(f"Usable rows: {len(model_df)}")
    print(f"Training rows: {len(train_df)}")
    print(f"2025 test rows: {len(test_df)}")

    if len(train_df) == 0:
        raise ValueError(
            f"No training data available for {horizon_name}."
        )

    # Features
    X_train = train_df[FEATURE_COLUMNS].copy()
    y_train = train_df[target_column].copy()

    # CatBoost categorical columns
    for column in CATEGORICAL_FEATURES:
        X_train[column] = X_train[column].astype(str)

    # Target
    y_train = y_train.astype(float)

    # Create model
    model = CatBoostRegressor(
        **catboost_params
    )

    # Train
    model.fit(
        X_train,
        y_train,
        cat_features=CATEGORICAL_INDICES
    )

    # Save model
    model_path = os.path.join(
        MODEL_DIR,
        f"catboost_model_{horizon_name}.joblib"
    )

    joblib.dump(
        {
            "model": model,
            "feature_columns": FEATURE_COLUMNS,
            "categorical_features": CATEGORICAL_FEATURES,
            "horizon_weeks": weeks
        },
        model_path
    )

    print(f"Model saved: {model_path}")


print("\nTraining completed successfully.")