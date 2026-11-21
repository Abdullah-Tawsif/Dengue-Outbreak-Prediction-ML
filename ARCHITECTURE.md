# AEDISIGHT Architecture and Validation Notes

## 1. Scope and implementation boundary

The repository currently contains two related but separate application tracks:

1. **Forecasting/data track:** validate and transform ward-level CSV data, train two- and four-week regressors, and save serialized scikit-learn pipelines.
2. **Web/authentication track:** serve FastAPI/Jinja pages and provide hospital-user and administrator registration, login, logout, and access control backed by SQLAlchemy.

The current code does **not** connect the saved forecasting models to the FastAPI application. There is no implemented inference endpoint or dynamic ward-risk prediction dashboard in the reviewed implementation. The dashboards contain presentation/sample content; they must not be interpreted as live model output. Low/Medium/High/Critical risk-tier generation and alert delivery are not implemented in `src/train.py`.

## 2. Forecasting data flow

```text
 data/raw/hackathon_preli_dataset - bangladesh_dengue_large_dataset.csv
                         |
                         v
                 src/clean_data.py
                         |
        +----------------+--------------------+
        |                                     |
        v                                     v
 validate source records              reject invalid records
 remove source rows with              (bad schema, dates,
 missing values                       numeric ranges, duplicates,
        |                              ward labels, weekly gaps)
        v
 sort/group observations by ward
        |
        v
 construct historical lag and rolling features
        |
        v
 remove rows still missing engineered history
        |
        v
 data/processed/dengue_dataset.csv
        |
        v
                  src/train.py
        |
        +--------------------------+
        |                          |
        v                          v
  target_cases(t+2w)        target_cases(t+4w)
        |                          |
        v                          v
 chronological model selection, refit, held-out test
        |                          |
        v                          v
 models/model_2w.joblib    models/model_4w.joblib
```

### 2.1 Source validation and cleaning

`src/clean_data.py` reads the raw CSV and checks the expected source fields, including date, city, ward, population density, water-logging risk, sanitation, temperature, rainfall, humidity, and dengue cases. It validates ISO-compatible dates, numeric parsing and finite values, non-negative case/rainfall/population values (with population density required to be positive), bounded risk/sanitation/humidity values, plausible temperature bounds, and the ordering `min_temp_c <= avg_temp_c <= max_temp_c`.

It also rejects exact duplicate rows and duplicate `(date, ward_id)` keys, checks consistent city/ward labels, and enforces seven-day spacing between consecutive observations within each ward. The continuity check is intentional: lag features are defined in weekly steps, so silently accepting gaps could make a nominal one-week lag represent a longer interval.

Source rows with missing fields are removed before feature engineering. Feature engineering then creates missing values at the beginning of each ward's history because there are not yet enough previous observations. The cleaner therefore performs a second missing-value removal after feature engineering and before writing the processed CSV.

Observed output for the processed dataset used in the current development run:

| Measure | Observed value |
|---|---:|
| Source records | 31,320 |
| Source records removed for missing input values | 0 |
| Engineered records removed for incomplete lag/rolling history | 240 |
| Final processed rows | 31,080 |
| Ward count | 60 |
| Final feature columns | 34 |
| Final date span | 2016-02-01 through 2025-12-29 |

The 240 engineered rows correspond to four initial weekly positions per ward in this dataset (60 wards × 4 positions). This is an observed property of this dataset and current feature recipe, not a hard-coded expected row count. The cleaner preserves `dengue_cases` exactly as supplied because the source does not identify whether it represents weekly incident cases or cumulative values.

### 2.2 Engineered features

Feature construction is performed independently within each `ward_id`, in chronological order. The current training feature list includes:

- Categorical identifiers: `city`, `ward_id`.
- Vulnerability/context: `population_density_sqkm`, `water_logging_risk_index`, `sanitation_score`.
- Current observations: temperature, rainfall, humidity, and `dengue_cases`.
- Rainfall lags: `rain_lag_1w` through `rain_lag_4w`.
- Previous-window rainfall totals: `rain_sum_2w` and `rain_sum_4w`.
- Dengue case lags: `dengue_cases_lag_1w` through `dengue_cases_lag_4w`.
- Temperature and humidity lags for the previous one to four weeks.
- Previous-four-week temperature and humidity means.
- Calendar features: ISO calendar week and calendar month.

The cleaner's rolling windows use only observations before the current row. For example, `rain_sum_4w` is the sum of the four preceding weekly rainfall values, not a window that includes the current week's rainfall.

### 2.3 Target construction

After sorting by ward and date, `src/train.py` uses grouped shifts of the `dengue_cases` series:

- `target_2w`: the value two weekly records ahead for the same ward.
- `target_4w`: the value four weekly records ahead for the same ward.

It also records `target_date_2w` and `target_date_4w`. Rows without a future target/date or required input feature are excluded from training/evaluation for that horizon.

## 3. Model training and chronological validation

For each horizon (two and four weeks), `src/train.py` considers two candidate regressors:

- `RandomForestRegressor` with 300 trees, `max_depth=18`, `min_samples_leaf=2`, `random_state=42`, and parallel jobs enabled.
- `HistGradientBoostingRegressor` with Poisson loss, 200 iterations, learning rate 0.08, at most 31 leaf nodes, L2 regularization 1.0, and `random_state=42`.

The preprocessing pipeline one-hot encodes `city` and `ward_id` with `handle_unknown="ignore"`; other columns pass through. Predictions are clipped at zero because negative case counts are not meaningful.

### 3.1 Date split

The split is based on the **date of the future target**, not a random row split and not merely the feature row's date:

| Partition | Future target date |
|---|---|
| Training | Before 2024-01-01 |
| Validation | 2024-01-01 through 2024-12-31 |
| Test | 2025-01-01 through 2025-12-31 |

The script compares candidate models using validation MAE. It selects the lowest-validation-MAE candidate, retrains that model on the union of training and validation rows, and evaluates once on the held-out 2025 target period. Reported metrics include MAE, RMSE, R², and a baseline MAE. The baseline prediction is the latest observed `dengue_cases` value carried forward to the target horizon.

### 3.2 Why chronological validation is used

A random split would mix earlier and later observations in each partition and would not represent the operational task of forecasting future weeks from past information. A chronological split makes the evaluation more representative of future deployment and keeps the 2025 target period unseen during candidate selection and final fitting.

Using target dates for the split matters because the feature row and its future target belong to different dates. Assigning a row by its future target date keeps evaluation periods aligned with the date the model is supposed to forecast.

This design reduces temporal leakage risk but does not prove that every upstream input is free from leakage. Before operational use, verify that each source feature would actually have been available at the forecast issuance date and that any retrospective data corrections do not reveal future information.

## 4. Web application and authentication flow

```text
Browser
  |
  v
FastAPI application (main.py)
  |
  +--> Jinja2 templates and static CSS/JavaScript
  |
  +--> Authentication router (src/backend/auth.py)
  |       |
  |       +--> CSRF validation
  |       +--> Argon2 password hash verification
  |       +--> SQLAlchemy User records
  |       +--> session user_id after successful login
  |
  +--> Protected dashboard routes
          |
          +--> get_current_user: session ID, database lookup, active check
          +--> get_current_hospital_user: reject admin role
          +--> get_current_admin_user: require admin role
```

### 4.1 Account model and access boundaries

The SQLAlchemy `User` model includes identity/contact fields, a password hash, `is_admin`, `is_active`, account creation time, and terms acceptance. Public registration always sets `is_admin=False` and `is_active=True`; additional submitted fields cannot grant administrator access.

Passwords are hashed and verified with `pwdlib`'s recommended Argon2 configuration. Registration, login, and logout validate session-backed CSRF tokens using constant-time comparison. Login clears prior session state, stores the user ID, and rotates the CSRF token. Logout validates CSRF before clearing the session.

`get_current_user` checks that the session user ID is an integer, retrieves the account from the database, and invalidates the session if the user no longer exists or is inactive. Role dependencies prevent hospital users from accessing administrator-only pages and prevent administrators from using hospital-only pages.

Administrator provisioning is intentionally separate from public registration. `src/backend/admin_cli.py` promotes an existing active account after email validation and an explicit `PROMOTE` confirmation. It does not create a user or set a hard-coded password.

### 4.2 Session configuration

`main.py` reads configuration from environment variables after loading the project `.env` file. The session middleware uses:

- Cookie name: `dengue_session`.
- SameSite: `lax`.
- Maximum age: `SESSION_MAX_AGE` (default 3600 seconds).
- HTTPS-only behavior: controlled by `COOKIE_SECURE` and intended to be mandatory in production.
- Signing secret: `SESSION_SECRET`, which must be present and at least 32 characters.

The session should contain only the minimal session identifiers/tokens needed by the application. Do not store passwords, database credentials, or other secrets in the client-side session.

## 5. Database and deployment configuration

`src/backend/database.py` creates a SQLAlchemy engine from `DATABASE_URL`.

- SQLite is supported for local development. Relative database paths are resolved from the repository root, and in-memory SQLite uses `StaticPool` for shared test connections.
- PostgreSQL is supported through the PostgreSQL SQLAlchemy driver. The engine enables `pool_pre_ping`; PostgreSQL connections also use a 300-second pool recycle setting.
- A hosted PostgreSQL connection should use the TLS/SSL settings supplied by the provider (the example uses `sslmode=require`).

The FastAPI startup module calls `Base.metadata.create_all(bind=engine)`. This creates missing tables but does not apply schema migrations to existing databases. Although Alembic is listed as a dependency, a migration workflow is not currently established in this repository; production schema evolution needs a migration plan before changing live tables.

The repository includes a Vercel configuration and exposes a Mangum handler for ASGI hosting. Its current `includeFiles` list includes the templates and static directories. Model files and training datasets are not configured as deployment assets. Since the prediction model is not yet loaded by the web app, the current web deployment path is authentication/page serving only.

## 6. Security and operational controls

- Keep `.env` out of source control; commit only placeholders in `.env.example`.
- Generate a unique, high-entropy `SESSION_SECRET` for each environment.
- Set `APP_ENV=production` and `COOKIE_SECURE=true` behind HTTPS.
- Keep the PostgreSQL URL and password in hosting-provider secret settings.
- Rotate any credential that is accidentally committed; deleting the latest copy does not invalidate a leaked credential.
- Do not expose the administrator CLI to an unauthenticated web endpoint.
- Avoid treating static dashboard figures as model predictions.
- Keep the source-data semantics and provenance explicit; the current code does not determine whether dengue case values are weekly incident counts or cumulative totals.

## 7. Tests and current verification boundary

The currently implemented authentication test module has 18 integration tests. They cover successful and rejected registration, password hashing, duplicate/invalid email, terms and password mismatch, CSRF rejection, login/logout session behavior, inactive accounts, admin redirection, and role separation.

Run:

```bash
python -m py_compile main.py src/clean_data.py
python -m compileall -q .
python -m pytest -v
```

Passing these checks verifies syntax and the test cases that exist. It does not substitute for a fresh model-training run, an independent temporal backtest, a production database migration rehearsal, deployment smoke tests, or clinical/public-health validation.

## 8. Known gaps before operational use

1. **Model serving:** `models/model_2w.joblib` and `models/model_4w.joblib` are training outputs; the web app does not load them or expose prediction endpoints.
2. **Risk tiers:** the current training script predicts numeric case values. It does not define/calibrate Low, Medium, High, or Critical thresholds.
3. **Dashboard integration:** current dashboard content includes static/sample statistics and is not a live ward-level forecasting display.
4. **Data semantics:** the input's `dengue_cases` field is preserved as provided; its incident-versus-cumulative meaning needs authoritative confirmation.
5. **Production migrations:** SQLAlchemy table creation exists, but a versioned Alembic migration workflow is not yet configured.
6. **Production validation:** verify deployment settings, secure cookies, Neon connectivity, model artifact handling, monitoring, and recovery procedures before operational use.

## 9. Model-use and public-health disclaimer

The models are statistical forecasting components for research and decision support. They can be affected by missing or biased surveillance, changes in reporting practices, changes in population or intervention patterns, and differences between training data and current conditions. They must not be used as a standalone diagnosis or as the sole basis for public-health resource allocation. Outputs require review by qualified public-health personnel and validation against trusted surveillance information.
