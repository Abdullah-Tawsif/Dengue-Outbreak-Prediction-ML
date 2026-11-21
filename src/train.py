import os
import joblib
import numpy as np
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    RandomForestRegressor,
    HistGradientBoostingRegressor
)
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.base import clone


# 1. LOAD DATA

DATA_PATH = "data/processed/dengue_dataset.csv"

df = pd.read_csv(DATA_PATH)

df["date"] = pd.to_datetime(df["date"])

df = df.sort_values(["ward_id", "date"]).reset_index(drop=True)

print("Original rows:", len(df))
print("Unique wards:", df["ward_id"].nunique())
print("Cities:", df["city"].unique())


# 2. DEFINE FEATURES

categorical_features = [
    "city",
    "ward_id"
]

numerical_features = [
    "population_density_sqkm",
    "water_logging_risk_index",
    "sanitation_score",

    "avg_temp_C",
    "max_temp_c",
    "min_temp_c",
    "rainfall_mm",
    "relative_humidity_pct",
    "dengue_cases",

    "rain_lag_1w",
    "rain_lag_2w",
    "rain_lag_3w",
    "rain_lag_4w",
    "rain_sum_2w",
    "rain_sum_4w",

    "dengue_cases_lag_1w",
    "dengue_cases_lag_2w",
    "dengue_cases_lag_3w",
    "dengue_cases_lag_4w",

    "avg_temp_C_lag_1w",
    "avg_temp_C_lag_2w",
    "avg_temp_C_lag_3w",
    "avg_temp_C_lag_4w",

    "relative_humidity_pct_lag_1w",
    "relative_humidity_pct_lag_2w",
    "relative_humidity_pct_lag_3w",
    "relative_humidity_pct_lag_4w",

    "avg_temp_C_mean_prev_4w",
    "relative_humidity_pct_mean_prev_4w",

    "calendar_week",
    "calendar_month"
]

feature_columns = categorical_features + numerical_features

required_columns = feature_columns + ["date", "dengue_cases"]

missing_columns = [
    col for col in required_columns
    if col not in df.columns
]

if missing_columns:
    raise ValueError(f"Missing columns: {missing_columns}")


# 3. CREATE FUTURE TARGETS

grouped = df.groupby("ward_id", sort=False)

df["target_2w"] = grouped["dengue_cases"].shift(-2)
df["target_4w"] = grouped["dengue_cases"].shift(-4)

# Record the actual date of each future target.
df["target_date_2w"] = grouped["date"].shift(-2)
df["target_date_4w"] = grouped["date"].shift(-4)


# 4. PREPARE MODELS

preprocessor = ColumnTransformer(
    transformers=[
        (
            "categories",
            OneHotEncoder(
                handle_unknown="ignore",
                sparse_output=False
            ),
            categorical_features
        )
    ],
    remainder="passthrough"
)

models = {
    "Random Forest": RandomForestRegressor(
        n_estimators=300,
        max_depth=18,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1
    ),

    "Gradient Boosting": HistGradientBoostingRegressor(
        loss="poisson",
        max_iter=200,
        learning_rate=0.08,
        max_leaf_nodes=31,
        l2_regularization=1.0,
        random_state=42
    )
}


# 5. TRAIN AND EVALUATE

os.makedirs("models", exist_ok=True)

for horizon in [2, 4]:

    print("\n" + "=" * 45)
    print(f"FORECAST: {horizon} WEEKS")
    print("=" * 45)

    target = f"target_{horizon}w"
    target_date = f"target_date_{horizon}w"

    # Remove rows with incomplete input features or targets.
    clean_df = df.dropna(
        subset=feature_columns + [target, target_date]
    ).copy()

    # Chronological split based on the date being predicted.
    train_data = clean_df[
        clean_df[target_date] < "2024-01-01"
    ]

    validation_data = clean_df[
        (clean_df[target_date] >= "2024-01-01") &
        (clean_df[target_date] < "2025-01-01")
    ]

    test_data = clean_df[
        (clean_df[target_date] >= "2025-01-01") &
        (clean_df[target_date] < "2026-01-01")
    ]

    print("Usable rows:", len(clean_df))
    print("Training rows:", len(train_data))
    print("Validation rows:", len(validation_data))
    print("Testing rows:", len(test_data))

    if min(len(train_data), len(validation_data), len(test_data)) == 0:
        raise ValueError("One of the chronological data splits is empty.")

    X_train = train_data[feature_columns]
    y_train = train_data[target]

    X_val = validation_data[feature_columns]
    y_val = validation_data[target]

    X_test = test_data[feature_columns]
    y_test = test_data[target]

    # Historical baseline: predict future cases equal to
    # the latest currently observed dengue cases.
    baseline_predictions = np.maximum(
        validation_data["dengue_cases"].to_numpy(), 0
    )

    print("\nHistorical baseline validation MAE:",
          round(mean_absolute_error(y_val, baseline_predictions), 3))

    best_model_name = None
    best_mae = float("inf")

    # Compare candidate models on validation data.
    for name, estimator in models.items():

        pipeline = Pipeline([
            ("preprocessor", clone(preprocessor)),
            ("model", clone(estimator))
        ])

        pipeline.fit(X_train, y_train)

        predictions = np.maximum(pipeline.predict(X_val), 0)

        mae = mean_absolute_error(y_val, predictions)
        rmse = np.sqrt(mean_squared_error(y_val, predictions))

        print(f"\n{name}")
        print("Validation MAE:", round(mae, 3))
        print("Validation RMSE:", round(rmse, 3))

        if mae < best_mae:
            best_mae = mae
            best_model_name = name

    print("\nSelected model:", best_model_name)

    # Retrain selected model using training + validation data.
    final_train = pd.concat(
        [train_data, validation_data]
    )

    final_model = Pipeline([
        ("preprocessor", preprocessor),
        ("model", models[best_model_name])
    ])

    final_model.fit(
        final_train[feature_columns],
        final_train[target]
    )

    # Evaluate once on the untouched test period.
    test_predictions = np.maximum(
        final_model.predict(X_test), 0
    )

    mae = mean_absolute_error(y_test, test_predictions)
    rmse = np.sqrt(mean_squared_error(y_test, test_predictions))
    r2 = r2_score(y_test, test_predictions)

    baseline_test = np.maximum(
        test_data["dengue_cases"].to_numpy(), 0
    )

    baseline_mae = mean_absolute_error(y_test, baseline_test)

    print("\nFINAL TEST RESULTS")
    print("Model:", best_model_name)
    print("MAE:", round(mae, 3))
    print("RMSE:", round(rmse, 3))
    print("R2:", round(r2, 3))
    print("Baseline MAE:", round(baseline_mae, 3))

    # Save the trained pipeline.
    model_path = f"models/model_{horizon}w.joblib"

    joblib.dump(final_model, model_path)

    print("Saved:", model_path)


print("\nMODEL TRAINING COMPLETED!")