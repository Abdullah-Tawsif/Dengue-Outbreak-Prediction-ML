import os
import warnings

import numpy as np
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    GradientBoostingRegressor,
    HistGradientBoostingRegressor
)
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    r2_score
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from catboost import CatBoostRegressor


warnings.filterwarnings("ignore")


# ============================================================
# 1. CONFIGURATION
# ============================================================

DATA_PATH = "data/processed/dengue_dataset.csv"

RANDOM_STATE = 42


# ============================================================
# 2. LOAD RAW DATA
# ============================================================

print("\n" + "=" * 70)
print("LOADING DATA")
print("=" * 70)

df = pd.read_csv(DATA_PATH)

print("Original rows:", len(df))

df["date"] = pd.to_datetime(df["date"])

df = df.sort_values(
    ["ward_id", "date"]
).reset_index(drop=True)


# ============================================================
# 3. CHECK ONLY RAW DATA FOR MISSING VALUES
# ============================================================

# These are the actual source columns.
# Engineered lag/rolling columns are intentionally NOT included.

raw_columns = [
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


print("\n" + "=" * 70)
print("RAW DATA MISSING-VALUE CHECK")
print("=" * 70)

raw_missing = df[raw_columns].isna().sum()

raw_missing = (
    raw_missing[
        raw_missing > 0
    ]
    .sort_values(ascending=False)
)

if len(raw_missing) == 0:

    print("No missing values found in raw source columns.")

else:

    print("Missing values in raw source columns:")

    print(raw_missing)

    print(
        "\nTotal missing raw cells:",
        int(
            df[raw_columns]
            .isna()
            .sum()
            .sum()
        )
    )


# ============================================================
# 4. CREATE TEMPORAL FEATURES
# ============================================================

print("\n" + "=" * 70)
print("CREATING TEMPORAL FEATURES")
print("=" * 70)

grouped = df.groupby(
    "ward_id",
    sort=False
)


# ============================================================
# 4.1 RAINFALL LAGS
# ============================================================

for lag in range(1, 5):

    df[f"rain_lag_{lag}w"] = (
        grouped["rainfall_mm"]
        .shift(lag)
    )


# ============================================================
# 4.2 TEMPERATURE LAGS
# ============================================================

for lag in range(1, 5):

    df[f"avg_temp_C_lag_{lag}w"] = (
        grouped["avg_temp_C"]
        .shift(lag)
    )


# ============================================================
# 4.3 HUMIDITY LAGS
# ============================================================

for lag in range(1, 5):

    df[
        f"relative_humidity_pct_lag_{lag}w"
    ] = (
        grouped[
            "relative_humidity_pct"
        ]
        .shift(lag)
    )


# ============================================================
# 4.4 DENGUE CASE LAGS
# ============================================================

for lag in range(1, 5):

    df[
        f"dengue_cases_lag_{lag}w"
    ] = (
        grouped["dengue_cases"]
        .shift(lag)
    )


# ============================================================
# 4.5 RAINFALL ROLLING FEATURES
# ============================================================

for window in [2, 4, 8]:

    df[
        f"rain_mean_prev_{window}w"
    ] = (
        grouped["rainfall_mm"]
        .transform(
            lambda x:
            x.shift(1)
            .rolling(window)
            .mean()
        )
    )

    df[
        f"rain_sum_prev_{window}w"
    ] = (
        grouped["rainfall_mm"]
        .transform(
            lambda x:
            x.shift(1)
            .rolling(window)
            .sum()
        )
    )


# ============================================================
# 4.6 TEMPERATURE ROLLING FEATURES
# ============================================================

for window in [2, 4, 8]:

    df[
        f"temp_mean_prev_{window}w"
    ] = (
        grouped["avg_temp_C"]
        .transform(
            lambda x:
            x.shift(1)
            .rolling(window)
            .mean()
        )
    )


# ============================================================
# 4.7 HUMIDITY ROLLING FEATURES
# ============================================================

for window in [2, 4, 8]:

    df[
        f"humidity_mean_prev_{window}w"
    ] = (
        grouped["relative_humidity_pct"]
        .transform(
            lambda x:
            x.shift(1)
            .rolling(window)
            .mean()
        )
    )


# ============================================================
# 4.8 DENGUE ROLLING FEATURES
# ============================================================

for window in [2, 4, 8]:

    df[
        f"dengue_mean_prev_{window}w"
    ] = (
        grouped["dengue_cases"]
        .transform(
            lambda x:
            x.shift(1)
            .rolling(window)
            .mean()
        )
    )


# ============================================================
# 4.9 DENGUE TREND FEATURES
# ============================================================

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


# Growth ratios
df["dengue_growth_ratio_1w"] = (
    df["dengue_cases"]
    /
    (
        df["dengue_cases_lag_1w"]
        + 1
    )
)

df["dengue_growth_ratio_2w"] = (
    df["dengue_cases"]
    /
    (
        df["dengue_cases_lag_2w"]
        + 1
    )
)


# ============================================================
# 4.10 CYCLICAL SEASONAL FEATURES
# ============================================================

df["calendar_week"] = (
    df["date"]
    .dt.isocalendar()
    .week
    .astype(int)
)

df["calendar_month"] = (
    df["date"]
    .dt.month
)


# Week-of-year cyclic encoding
df["week_sin"] = np.sin(
    2 * np.pi
    * df["calendar_week"]
    / 52
)

df["week_cos"] = np.cos(
    2 * np.pi
    * df["calendar_week"]
    / 52
)


# Month cyclic encoding
df["month_sin"] = np.sin(
    2 * np.pi
    * df["calendar_month"]
    / 12
)

df["month_cos"] = np.cos(
    2 * np.pi
    * df["calendar_month"]
    / 12
)


# ============================================================
# 5. CREATE FUTURE TARGETS
# ============================================================

print("\n" + "=" * 70)
print("CREATING FORECAST TARGETS")
print("=" * 70)

grouped = df.groupby(
    "ward_id",
    sort=False
)


# 2-week target
df["target_2w"] = (
    grouped["dengue_cases"]
    .shift(-2)
)

df["target_date_2w"] = (
    grouped["date"]
    .shift(-2)
)


# 3-week target
df["target_3w"] = (
    grouped["dengue_cases"]
    .shift(-3)
)

df["target_date_3w"] = (
    grouped["date"]
    .shift(-3)
)


# 4-week target
df["target_4w"] = (
    grouped["dengue_cases"]
    .shift(-4)
)

df["target_date_4w"] = (
    grouped["date"]
    .shift(-4)
)


# ============================================================
# 6. TARGET CHECK
# ============================================================

print("\n" + "=" * 70)
print("FORECAST TARGET CHECK")
print("=" * 70)

print(
    "Missing target_2w:",
    df["target_2w"].isna().sum()
)

print(
    "Missing target_3w:",
    df["target_3w"].isna().sum()
)

print(
    "Missing target_4w:",
    df["target_4w"].isna().sum()
)

print(
    "\nExpected 2-week target boundary:",
    df["ward_id"].nunique() * 2
)

print(
    "Expected 3-week target boundary:",
    df["ward_id"].nunique() * 3
)

print(
    "Expected 4-week target boundary:",
    df["ward_id"].nunique() * 4
)


# ============================================================
# 7. FEATURES
# ============================================================

categorical_features = [
    "city",
    "ward_id"
]


numerical_features = [

    # Static / environmental
    "population_density_sqkm",
    "water_logging_risk_index",
    "sanitation_score",

    # Current weather
    "avg_temp_C",
    "max_temp_c",
    "min_temp_c",
    "rainfall_mm",
    "relative_humidity_pct",

    # Current dengue
    "dengue_cases",

    # Rainfall lags
    "rain_lag_1w",
    "rain_lag_2w",
    "rain_lag_3w",
    "rain_lag_4w",

    # Temperature lags
    "avg_temp_C_lag_1w",
    "avg_temp_C_lag_2w",
    "avg_temp_C_lag_3w",
    "avg_temp_C_lag_4w",

    # Humidity lags
    "relative_humidity_pct_lag_1w",
    "relative_humidity_pct_lag_2w",
    "relative_humidity_pct_lag_3w",
    "relative_humidity_pct_lag_4w",

    # Dengue lags
    "dengue_cases_lag_1w",
    "dengue_cases_lag_2w",
    "dengue_cases_lag_3w",
    "dengue_cases_lag_4w",

    # Rainfall rolling
    "rain_mean_prev_2w",
    "rain_mean_prev_4w",
    "rain_mean_prev_8w",

    "rain_sum_prev_2w",
    "rain_sum_prev_4w",
    "rain_sum_prev_8w",

    # Temperature rolling
    "temp_mean_prev_2w",
    "temp_mean_prev_4w",
    "temp_mean_prev_8w",

    # Humidity rolling
    "humidity_mean_prev_2w",
    "humidity_mean_prev_4w",
    "humidity_mean_prev_8w",

    # Dengue rolling
    "dengue_mean_prev_2w",
    "dengue_mean_prev_4w",
    "dengue_mean_prev_8w",

    # Dengue trends
    "dengue_change_1w",
    "dengue_change_2w",
    "dengue_change_4w",

    "dengue_growth_ratio_1w",
    "dengue_growth_ratio_2w",

    # Calendar
    "calendar_week",
    "calendar_month",

    # Cyclical seasonality
    "week_sin",
    "week_cos",
    "month_sin",
    "month_cos"
]


feature_columns = (
    categorical_features
    +
    numerical_features
)


# ============================================================
# 8. PREPROCESSOR
# ============================================================

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


# ============================================================
# 9. MODELS
# ============================================================

models = {

    # --------------------------------------------------------
    # HISTOGRAM GRADIENT BOOSTING
    # --------------------------------------------------------

    "HGB":

        HistGradientBoostingRegressor(

            loss="poisson",

            max_iter=300,

            learning_rate=0.05,

            max_leaf_nodes=31,

            min_samples_leaf=20,

            l2_regularization=1.0,

            random_state=RANDOM_STATE
        ),


    # --------------------------------------------------------
    # GRADIENT BOOSTING
    # --------------------------------------------------------

    "GB":

        GradientBoostingRegressor(

            loss="squared_error",

            n_estimators=300,

            learning_rate=0.05,

            max_depth=3,

            min_samples_leaf=5,

            subsample=0.9,

            random_state=RANDOM_STATE
        ),


    # --------------------------------------------------------
    # CATBOOST
    # --------------------------------------------------------

    "CatBoost":

        CatBoostRegressor(

            loss_function="RMSE",

            iterations=700,

            learning_rate=0.05,

            depth=7,

            l2_leaf_reg=3,

            random_seed=RANDOM_STATE,

            verbose=False,

            allow_writing_files=False
        )
}


# ============================================================
# 10. METRICS
# ============================================================

def calculate_metrics(
    y_true,
    y_pred
):

    mae = mean_absolute_error(
        y_true,
        y_pred
    )

    rmse = np.sqrt(
        mean_squared_error(
            y_true,
            y_pred
        )
    )

    r2 = r2_score(
        y_true,
        y_pred
    )

    return mae, rmse, r2


# ============================================================
# 11. FORECAST HORIZONS
# ============================================================

targets = {

    "2 weeks": (
        "target_2w",
        "target_date_2w"
    ),

    "3 weeks": (
        "target_3w",
        "target_date_3w"
    ),

    "4 weeks": (
        "target_4w",
        "target_date_4w"
    )
}


# ============================================================
# 12. RESULTS
# ============================================================

all_results = []


# ============================================================
# 13. TRAINING
# ============================================================

for horizon_name, (
    target_column,
    target_date_column
) in targets.items():

    print("\n\n")

    print("#" * 70)

    print(
        f"FORECAST HORIZON: {horizon_name}"
    )

    print("#" * 70)


    # --------------------------------------------------------
    # Remove rows where required features/target unavailable
    # --------------------------------------------------------

    horizon_df = df.dropna(
        subset=(
            feature_columns
            +
            [
                target_column,
                target_date_column
            ]
        )
    ).copy()


    print(
        "\nUsable rows:",
        len(horizon_df)
    )


    # ========================================================
    # CHRONOLOGICAL SPLIT
    # ========================================================

    train_df = horizon_df[
        horizon_df[
            target_date_column
        ].dt.year <= 2023
    ].copy()


    validation_df = horizon_df[
        horizon_df[
            target_date_column
        ].dt.year == 2024
    ].copy()


    test_df = horizon_df[
        horizon_df[
            target_date_column
        ].dt.year == 2025
    ].copy()


    print("\n" + "=" * 70)
    print("CHRONOLOGICAL SPLIT")
    print("=" * 70)

    print(
        "Train      :",
        len(train_df),
        "(2016-2023)"
    )

    print(
        "Validation :",
        len(validation_df),
        "(2024)"
    )

    print(
        "Test       :",
        len(test_df),
        "(2025)"
    )


    # ========================================================
    # X / Y
    # ========================================================

    X_train = train_df[
        feature_columns
    ]

    y_train = train_df[
        target_column
    ]


    X_val = validation_df[
        feature_columns
    ]

    y_val = validation_df[
        target_column
    ]


    X_test = test_df[
        feature_columns
    ]

    y_test = test_df[
        target_column
    ]


    # ========================================================
    # TRAIN MODELS
    # ========================================================

    for model_name, model in models.items():

        print("\n" + "-" * 70)

        print(
            f"TRAINING: {model_name}"
        )

        print("-" * 70)


        # ====================================================
        # CATBOOST
        # ====================================================

        if model_name == "CatBoost":

            X_train_cb = X_train.copy()
            X_val_cb = X_val.copy()
            X_test_cb = X_test.copy()


            for column in categorical_features:

                X_train_cb[column] = (
                    X_train_cb[column]
                    .astype(str)
                )

                X_val_cb[column] = (
                    X_val_cb[column]
                    .astype(str)
                )

                X_test_cb[column] = (
                    X_test_cb[column]
                    .astype(str)
                )


            model.fit(
                X_train_cb,
                y_train,
                cat_features=categorical_features
            )


            val_pred = model.predict(
                X_val_cb
            )

            test_pred = model.predict(
                X_test_cb
            )


        # ====================================================
        # HGB / GB
        # ====================================================

        else:

            pipeline = Pipeline(

                steps=[

                    (
                        "preprocessor",
                        preprocessor
                    ),

                    (
                        "model",
                        model
                    )
                ]
            )


            pipeline.fit(
                X_train,
                y_train
            )


            val_pred = pipeline.predict(
                X_val
            )

            test_pred = pipeline.predict(
                X_test
            )


        # ====================================================
        # VALIDATION
        # ====================================================

        val_mae, val_rmse, val_r2 = (
            calculate_metrics(
                y_val,
                val_pred
            )
        )


        print("\nValidation Results:")

        print(
            f"MAE  : {val_mae:.3f}"
        )

        print(
            f"RMSE : {val_rmse:.3f}"
        )

        print(
            f"R²   : {val_r2:.3f}"
        )


        all_results.append({

            "Horizon":
                horizon_name,

            "Model":
                model_name,

            "Split":
                "Validation",

            "MAE":
                val_mae,

            "RMSE":
                val_rmse,

            "R2":
                val_r2
        })


        # ====================================================
        # 2025 TEST
        # ====================================================

        test_mae, test_rmse, test_r2 = (
            calculate_metrics(
                y_test,
                test_pred
            )
        )


        print("\n2025 Test Results:")

        print(
            f"MAE  : {test_mae:.3f}"
        )

        print(
            f"RMSE : {test_rmse:.3f}"
        )

        print(
            f"R²   : {test_r2:.3f}"
        )


        all_results.append({

            "Horizon":
                horizon_name,

            "Model":
                model_name,

            "Split":
                "Test",

            "MAE":
                test_mae,

            "RMSE":
                test_rmse,

            "R2":
                test_r2
        })


# ============================================================
# 14. FINAL COMPARISON
# ============================================================

results_df = pd.DataFrame(
    all_results
)


print("\n\n")

print("=" * 100)

print(
    "FINAL CHRONOLOGICAL MODEL COMPARISON"
)

print("=" * 100)


print(

    results_df[
        [
            "Horizon",
            "Model",
            "Split",
            "MAE",
            "RMSE",
            "R2"
        ]
    ].to_string(
        index=False
    )
)


# ============================================================
# 15. BEST MODEL FOR EACH HORIZON
# ============================================================

print("\n" + "=" * 100)

print(
    "BEST MODELS BASED ON 2025 TEST R²"
)

print("=" * 100)


for horizon in targets.keys():

    subset = results_df[
        (
            results_df["Horizon"]
            == horizon
        )
        &
        (
            results_df["Split"]
            == "Test"
        )
    ].sort_values(
        "R2",
        ascending=False
    )


    print(
        f"\n{horizon}:"
    )

    print(

        subset[
            [
                "Model",
                "MAE",
                "RMSE",
                "R2"
            ]
        ].to_string(
            index=False
        )
    )


    best = subset.iloc[0]


    print(
        "\nBEST MODEL:",
        best["Model"]
    )

    print(
        f"Test R² : {best['R2']:.3f}"
    )

    print(
        f"Test MAE: {best['MAE']:.3f}"
    )

    print(
        f"Test RMSE: {best['RMSE']:.3f}"
    )


# ============================================================
# 16. SAVE RESULTS
# ============================================================

os.makedirs(
    "results",
    exist_ok=True
)


output_path = (
    "results/"
    "chronological_model_comparison.csv"
)


results_df.to_csv(
    output_path,
    index=False
)


print("\n" + "=" * 70)

print("RESULTS SAVED")

print("=" * 70)

print(
    f"File: {output_path}"
)

print("\nDone.")