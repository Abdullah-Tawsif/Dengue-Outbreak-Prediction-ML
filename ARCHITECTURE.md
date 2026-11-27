# AEDISIGHT - Architecture

This document describes the system as implemented in the repository: the Phase 1 forecasting pipeline that is retained, and the Phase 2 operational layer built on top of it for the onsite final. It states what each part does, how the numbers are computed, what runs offline, and what is not done.

## Scope and terminology

- **Phase 1** = the forecasting codebase: data cleaning, CatBoost models, risk bands, authenticated dashboard.
- **Phase 2 / onsite** = resource allocation, overflow routing, what-if simulation, dispatch notices, offline resilience.
- `PREDICTION_BACKEND=phase2` is a code-level switch for an older rolling-origin forecasting backend (`src/predict.py`). It is unrelated to the hackathon's Phase 2. The default backend is `reference_catboost`.
- Everything here is a planning aid. Quantities are scenario estimates built on configurable assumptions, not clinical orders.

## System overview

```text
Browser (Jinja2 pages + vanilla JS)
   |  session cookie + X-CSRF-Token
   v
FastAPI app (main.py)
   |-- auth router ............. register / login / logout, roles: hospital | admin
   |-- forecast routes ......... /api/predict (history), /api/live-predict (weekly input)
   |-- operations routes ....... /api/operations/status, /api/operations/counterfactual
   |-- admin routes ............ users, activity, system status, capacity configuration
   |
   |-- src/reference_catboost_predict.py, src/live_input_predict.py   -> CatBoost models in runs/
   |-- src/risk_engine.py ...................................... -> LOW/MEDIUM/HIGH/CRITICAL
   |-- src/operations.py ....... supply, stock allocation, crews, overflow routing (pure Python)
   |
   v
SQLAlchemy -> SQLite (local / offline)  or  PostgreSQL on Neon (production)
```

Startup (at import of `main.py`): validate environment, create missing tables (`Base.metadata.create_all`), add provenance columns to `live_observations` if absent, and copy static ward attributes from the processed CSV into `ward_contexts`. The database must therefore be reachable when the app starts.

## Repository map

| Path | Role |
|---|---|
| `main.py` | App entry point, configuration checks, all non-auth routes, startup database setup |
| `src/operations.py` | Phase 2 math: supply quantities, priority score, stock allocation, crew plan, overflow routing |
| `src/reference_catboost_features.py`, `reference_catboost_predict.py`, `train_reference_catboost.py` | Default forecasting backend: feature engineering, inference, training |
| `src/live_input_predict.py` | Forecast from weekly weather entered on `/live-input` |
| `src/risk_engine.py` | Ward-relative risk bands and driver text |
| `src/clean_data.py`, `validation.py`, `feature_engineering.py`, `preprocessing.py`, `train.py`, `predict.py`, `compare_splits.py` | Phase 1 cleaning, validation, multi-model training and the preserved `phase2` backend |
| `src/backend/` | `auth.py`, `database.py`, `models.py`, `security.py` (CSRF), `dependencies.py` (role checks), `audit.py`, `admin_cli.py` |
| `templates/`, `static/` | Pages and assets for home, login, register, user dashboard, live input, admin dashboard |
| `data/raw/`, `data/processed/` | Source CSV; `processed_historical_data.csv` (model input) and `operational_capacity.csv` (see Data layer) |
| `runs/` | Trained model run directories with metadata and metrics |
| `models/` | Older Phase 1 `.joblib` artifacts |
| `tests/` | pytest suite |

`src/explain.py` and `src/file_handler.py` are empty placeholders.

## Request flows

**Authentication.** `POST /register` creates a hospital user. An administrator is created by promoting an existing user with `python -m src.backend.admin_cli --email ...`. `POST /login` sets a signed session cookie (`dengue_session`). Hospital users reach `/user_dashboard` and `/live-input`; administrators reach `/admin_dashboard`. Each role is redirected away from the other's pages.

**Historical forecast.** `POST /api/predict` (hospital + CSRF) validates city, ward and horizon (2-4), loads the selected model for that horizon, builds features from the stored ward history, and returns the forecast with a risk band.

**Live forecast.** `POST /api/live-predict` (hospital + CSRF):

1. Load the ward's static context (`ward_contexts`) and any earlier saved live observations.
2. Append this week's weather observation to the ward's historical rows and rebuild all lag and rolling features with the same function used in training.
3. Run the CatBoost model for the requested horizon; classify risk.
4. Upsert the `live_observations` row and its `live_predictions` row (one per observation and horizon). Re-running the same ward and date updates instead of duplicating.

Dengue case counts are optional input. If omitted, the observation is saved as `context_only` and excluded from case history. Incomplete case history is rejected, not zero-filled.

**Operations status.** `GET /api/operations/status` (hospital):

1. Take the latest stored prediction for each ward (optionally filtered by `city`), plus any ward that has a configured bed capacity.
2. Convert predicted cases to supply quantities and a priority score (section "Operations engine").
3. Allocate saline and kits per city under `availableStockPercent` (30-100).
4. Compute each ward's crew need and assign `totalSprayingCrews`.
5. Route bed deficits to lower-burden wards in the same city; compute city bed utilization.
6. Return everything in one JSON document, including a `limitations` list.

**Capacity configuration.** `POST /api/admin/ward-bed-capacity` (admin + CSRF) upserts total and occupied beds per ward and rejects occupied > total. `/api/admin/hospital-capacity` and `/api/admin/crew-availability` do the same for hospital-level beds and crew records.

## Data layer

**Datasets.**

- `data/raw/bangladesh_dengue_large_dataset_onsite - ...csv`: 31,320 rows, 15 columns, including `dengue_cases`, `saline_bags_needed`, `diagnostic_kits_needed`, `hospital_beds_required`, population density, water-logging risk, sanitation score and weather.
- `data/processed/processed_historical_data.csv`: 31,320 weekly rows, 48 columns (raw columns plus lags, rolling sums and means, calendar features), 60 wards, 2016-01-04 to 2025-12-29.
- `data/processed/operational_capacity.csv`: 60 rows with an **estimated** bed capacity per ward (75th percentile of weekly `hospital_beds_required` in June-October 2016-2023, rounded up, minimum 1). Actual bed, stock and crew columns are empty. The application does not load this file; bed capacities come only from administrator input.

**Database tables** (created automatically; there is no migration step, and `create_all` does not alter existing tables):

| Table | Purpose |
|---|---|
| `users`, `audit_logs` | Accounts, roles, activity feed |
| `ward_contexts` | Static ward attributes (density, water-logging risk, sanitation), synced from the processed CSV at startup |
| `live_observations` | Weekly weather and case input, with `dengue_case_source` (`reported` or `context_only`) |
| `live_predictions` | Forecast, risk band, model run, one row per observation and horizon |
| `ward_bed_capacities` | Admin-entered total and occupied beds per ward |
| `hospital_capacity` | Hospital-level beds per city |
| `crew_availability` | Crew records per city |

## Forecasting pipeline

**Cleaning and validation (`src/clean_data.py`, `src/validation.py`).** Checks required columns, ISO dates, finite non-negative values, humidity in [0, 100], risk and sanitation scores in [0, 1], ordered min/avg/max temperature, integer case counts, duplicate rows and `(date, ward_id)` keys, one city per ward, and exact seven-day spacing within each ward. Rows with missing values or insufficient history are counted and dropped, not imputed.

**Features (default backend, `src/reference_catboost_features.py`).** 53 ordered features: `city` and `ward_id` (categorical); static ward attributes (population density, water-logging risk, sanitation score); current-week weather (average, minimum and maximum temperature, rainfall, humidity); weekly lags 1-4 of rainfall, temperature, humidity and dengue cases, plus a 5-week dengue lag; rolling means over the previous 2, 4 and 8 weeks for rainfall, temperature, humidity and dengue cases (and rainfall sums over the same windows); dengue change over 1, 2 and 4 weeks and growth ratio over 1 and 2 weeks; calendar week and month with sine/cosine encodings. The current week's dengue count is not a feature. Lags and rolling values are computed per ward, so no feature crosses a ward boundary. The ordered feature list is stored with each run (`feature_names.json`).

**Targets and split.** For horizon h in {2, 3, 4}, the target is the same ward's `dengue_cases` h weekly rows ahead. Training uses target dates through 2024; testing uses 2025. The tested model is the saved model (no refit). Random splitting is not used.

**Model.** One CatBoostRegressor per horizon: RMSE loss, learning rate 0.05, depth 7, L2 leaf regularization 3, seed 42, 50 iterations, `city` and `ward_id` as categorical features. Negative predictions are clipped to zero. `src/reference_catboost_predict.py` loads the newest `runs/reference_catboost_*` directory unless `REFERENCE_CATBOOST_RUN` is set, and returns an explicit error if no model is available (no silent fallback).

**Test metrics, run `reference_catboost_20261127T095240_866358Z`:**

| Horizon | MAE | RMSE | R2 | Test rows |
|---|---|---|---|---|
| 2 weeks | 1.221 | 2.114 | 0.713 | 3,120 |
| 3 weeks | 1.244 | 2.114 | 0.713 | 3,120 |
| 4 weeks | 1.401 | 2.331 | 0.651 | 3,120 |

This is a single chronological split. The metrics file does not include a persistence baseline. See `REFERENCE_CATBOOST.md` for the feature contract and differences from the reference source.

**Risk bands (`src/risk_engine.py`).** Using only history up to the forecast origin, take the ward's weeks with cases > 0. If there are at least 8, the cutoffs are their 50th, 75th and 90th percentiles; otherwise fixed cutoffs 3, 6, 10. A forecast below 1 is LOW; at or below the first cutoff LOW; then MEDIUM, HIGH; above the last, CRITICAL. These are ward-relative display bands, not clinical thresholds or probabilities.

**Preserved multi-model pipeline.** `src/train.py` trains CatBoost, GradientBoosting and HistGradientBoosting plus a persistence baseline, selects per horizon on validation MAE (validation = 2024, test = 2025), and writes `runs/aedisight_*`. It is selectable with `PREDICTION_BACKEND=phase2` and is not the default.

## Operations engine

All of this is deterministic Python in `src/operations.py`; there are no network calls and no randomness.

**Supply quantities (`calculate_supply`).** For predicted cases `c` (negative values treated as 0):

```text
saline_bags_needed     = ceil(c x SALINE_BAGS_PER_CASE)        default 2.0
diagnostic_kits_needed = ceil(c x DIAGNOSTIC_KITS_PER_CASE)    default 0.25
hospital_beds_required = ceil(c x BEDS_PER_CASE)               default 0.10
```

**Priority.** Wards are ordered by the tuple (risk rank LOW=0 .. CRITICAL=3, population density, priority score, ward id), highest first. The priority score (`rank_priority`) is:

```text
0.50 x predicted cases + 10 x growth + 0.15 x (density / 1000) + 0.20 x (water-logging risk + (1 - sanitation score))
```

In `/api/operations/status` the `growth` term is currently fixed at 0.

**Stock allocation (`allocate_regional_stock`).** For each city, separately for saline and kits:

```text
available = floor(total city demand x availableStockPercent / 100)      (percent must be 30-100)
```

Wards are served in priority order: each gets `min(need, remaining)`, which never overspends. Every ward gets `*_allocated` and `*_deficit` values. A ward whose need is not fully met gets `criticalSupplyDeficit = true`, and the city response lists warnings such as "saline bags demand is not fully covered". The stock percentage is a scenario share of calculated demand, not a measured inventory.

**Crew plan (`plan_crews` and the rule in `main.py`).** A ward is active if its predicted cases are above 0. Crew need per ward = 1 (active) + 1 (risk HIGH or CRITICAL) + 1 (density at or above the median density of active wards in its city), so 0 to 3. The `totalSprayingCrews` crews (each one unit) are handed out in priority order; a crew serves at most one ward. The response lists assigned crews and any `unmetDeploymentNeed`.

**Overflow routing (`route_ward_overflow`).** Run per city; wards are never routed across cities.

```text
available beds   = total beds - occupied beds           (admin-entered)
local deficit    = max(0, hospital_beds_required - available beds)
spare capacity   = max(0, available beds - hospital_beds_required)
```

Source wards (those with a deficit) are taken in priority order. Destinations are wards in the same city with spare capacity and a strictly lower priority tuple, tried lowest-burden first. Each route moves `min(remaining deficit, destination spare capacity)` beds. Per ward the result reports `localBedDeficit`, `overflowBedsIn`, `overflowBedsOut` and `netBedDeficit`. Per city it reports total beds, occupied beds, projected demand, and:

```text
bedUtilizationPercent = (occupied + projected bed demand) / total beds x 100
capacityCoveragePercent = wards with configured capacity / wards in the response x 100
```

Wards without configured capacity are skipped in routing and reported as unknown. The overall status is `unknown`, `surge` (unrouted deficit remains), `partial` (some wards unconfigured) or `covered`.

**Worked example (verified by running the functions).** Dhaka North, three wards:

| Ward | Risk | Density | Predicted cases | Saline | Kits | Beds |
|---|---|---|---|---|---|---|
| A | CRITICAL | 80,000 | 20 | 40 | 5 | 2 |
| B | HIGH | 40,000 | 10 | 20 | 3 | 1 |
| C | LOW | 60,000 | 5 | 10 | 2 | 1 |

At 60% stock, available saline = floor(70 x 0.6) = 42 and kits = floor(10 x 0.6) = 6. In priority order A receives 40 saline and 5 kits (fully covered); B receives 2 of 20 saline and 1 of 3 kits; C receives 0 of 10 and 0 of 2. B and C are flagged with critical supply deficits; the city shows deficits of 28 saline bags and 4 kits.

Bed routing, same city (a separate illustration in which Ward A's requirement is 3 beds): Ward A has 2 total beds and 1 occupied, so its local deficit is 2. Ward B (8 total, 4 occupied, needs 1) and Ward C (5 total, 2 occupied, needs 1) have spare capacity of 3 and 2. C is the lowest-burden ward with spare beds, so 2 beds are routed A to C and A's net deficit is 0. City utilization = (7 occupied + 5 projected demand) / 15 total = 80%.

**What-if scenario endpoint (`POST /api/operations/counterfactual`).** Takes `predictedCases`, `additionalBeds`, `additionalCrews`, `additionalDiagnosticKits`, `estimatedCost` and `assumedBenefitPoints`. It returns baseline unmet beds (`ceil(cases x BEDS_PER_CASE)`), scenario unmet beds (`max(0, baseline - additionalBeds)`) and `benefitPerCost = assumedBenefitPoints / estimatedCost`, labelled "scenario estimate only". It does not use the forecasting model, does not model a 2-4 week reduction in cases, and does not rank wards by cases prevented per crew. There is no UI for it.

## Offline resilience

| Component | Offline behavior |
|---|---|
| Forecasting | Local. CatBoost models are loaded from `runs/`; data from `data/processed/`. |
| Allocation, crews, routing | Local pure Python. |
| Database | Local with SQLite. With a Neon URL and no internet, startup fails (verified: importing `main.py` against an unreachable Postgres exits with a SQLAlchemy connection error). |
| External APIs / LLMs | None used. A code search of the runtime code found no LLM SDK or outbound HTTP client. |
| Front-end assets | Google Fonts on all pages (fallback fonts offline); landing page `/` loads Tailwind from a CDN, so its styling can degrade. JavaScript and CSS under `static/` are local. |

Verified in a test sandbox: with `DATABASE_URL=sqlite:///...`, the app starts and serves `/`, `/login`, `/register` and static files (HTTP 200), and protected pages redirect to login (HTTP 303). A full rehearsal with Wi-Fi disabled on the demo machine is still recommended.

Dispatch notices, when implemented, are meant to be built from fixed templates filled with computed values so they also work offline.

## Security model

- Signed session cookie (`SessionMiddleware`), `SameSite=Lax`, `Secure` in production, configurable lifetime.
- `SESSION_SECRET` must be at least 32 characters or the app refuses to start; production requires `COOKIE_SECURE=true` and rejects SQLite.
- CSRF: every state-changing API call must carry a valid `X-CSRF-Token`.
- Role separation through FastAPI dependencies (`get_current_hospital_user`, `get_current_admin_user`).
- Audit log table and an admin activity feed.
- Secrets live in `.env` (git-ignored) or the hosting provider's environment settings.

## Deployment

Target: Vercel (FastAPI app detected from `main.py`; `vercel.json` holds only the schema reference) with a Neon PostgreSQL database. Required variables: `APP_ENV=production`, `DATABASE_URL`, `SESSION_SECRET`, `COOKIE_SECURE=true`. See `VERCEL_DEPLOYMENT.md`.

- Use `postgresql+psycopg2://...?sslmode=require`. With SQLAlchemy 2.1, a plain `postgresql://` URL selects the `psycopg` v3 driver, which `requirements.txt` does not install.
- Dependencies are unpinned in `requirements.txt`; pin them for reproducible builds.
- The size of the deployed bundle (CatBoost, pandas, scikit-learn, model runs) has not been measured against the serverless function limit.
- The offline fallback is a local run with SQLite, not the hosted deployment.

## Testing

```bash
python -m pytest -q
```

Test files: `test_auth.py` (registration, login, roles, CSRF), `test_app.py` (forecast API and pages), `test_admin_api.py` and `test_admin_dashboard.py` (admin console), `test_operations.py` (allocation, routing, crews), `test_operations_api.py` (operations endpoints), `test_live_input_workflow.py` (live forecast persistence), `test_ml_pipeline.py` (cleaning, features, targets, splits, training), `test_reference_catboost.py` (default backend). Tests verify code behavior on fixtures; they are not model-performance claims.

## Phase 2 requirement coverage

| # | Requirement | Status | Notes |
|---|---|---|---|
| 1 | Constrained allocation, stock 30-100%, crews, deficit alerts | Implemented | Covers wards with a stored forecast or configured capacity |
| 2 | Same-city overflow routing, net deficits, utilization | Implemented | Needs administrator-entered ward bed capacity |
| 3 | What-if vector-control simulator, 2-4 week effect, ROI ranking | Partial | Generic scenario calculator only; see above |
| 4 | SMS / WhatsApp / printable dispatch notices | Not implemented | No templates, routes or `/exports` samples yet |
| 5 | Offline resilience | Partial | Core math is local; requires SQLite; CDN assets affect styling |
| - | Tagged repo, live URL, `/exports` samples, ARCHITECTURE.md, AI disclosure | In progress | `/exports` does not exist; live URL to be added |

## Assumptions and calibration

- **Supply ratios are planning assumptions.** Defaults are 2.0 saline bags, 0.25 diagnostic kits and 0.10 beds per predicted case. The onsite dataset has its own supply columns; for rows with at least one case, their ratios to `dengue_cases` are: saline 2.00-2.50 (median 2.375), kits 1.00-1.25 (median 1.13), beds 0.00-0.50 (median 0.29). The defaults do not reproduce those columns, most visibly for kits. Set the three environment variables, or change the formulas, to align with the dataset's definitions. Remove or update this note after recalibrating.
- **Stock percentage** is a scenario share of calculated demand, not an inventory count.
- **Beds** treat `hospital_beds_required` as additional beds needed on top of occupied beds.
- **Risk bands** are ward-relative and not clinical.
- **Forecast freshness.** The dataset ends on 2025-12-29. The dashboard's current-date forecasting is disabled by a 14-day freshness policy; the live-input workflow continues each ward's weekly series from the dataset end.

## Known limitations

- The operations view contains only wards with a stored live forecast or a configured bed capacity. Reaching all 60 wards requires forecasting each ward and entering its capacity; `operational_capacity.csv` is not used to seed capacities.
- The priority score's `growth` term is fixed at 0 in the operations endpoint.
- The what-if endpoint is not model-driven and not ward-ranked.
- Dispatch notices and exports are not implemented.
- The application cannot start if the configured database is unreachable.
- No migration tooling is wired up; schema changes need manual handling.
- Evaluation is a single train/test split (2025); no persistence baseline in the default backend's metrics file; results should not be generalized beyond this backtest.
- Dengue case semantics in the source data (incidence versus cumulative) are not documented; values are used as given.

## AI disclosure

- **Runtime:** no LLM or external AI service is called by the application. Forecasts come from locally trained CatBoost models; allocation, routing and crew planning are deterministic code.
- **Development:** AI coding assistance was used on this project. The Git history contains a commit titled "update from bolt agent". This document and the README were drafted with Claude (Anthropic) from a read of the repository on 2026-11-27.
- TODO (team): list every AI tool used, what each was used for (code, tests, documentation, data analysis), and confirm that the team reviewed and can explain the submitted code.
