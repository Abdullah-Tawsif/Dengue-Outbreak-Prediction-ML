# AEDISIGHT - Dengue Early Warning and Resource Command

Ward-level dengue forecasting and emergency resource planning for 60 urban wards in five city corporations: Dhaka North, Dhaka South, Chattogram, Gazipur and Narayanganj.

Phase 1 forecasts weekly dengue cases 2, 3 and 4 weeks ahead. Phase 2 (the onsite final of the EDU AI Engineering Hackathon 2026) builds on that forecast to plan saline bags, diagnostic kits, hospital beds, spraying crews and same-city bed overflow routing for ward commissioners and health-center managers.

AEDISIGHT — Application Screenshots

Here is a preview of the AEDISIGHT dengue outbreak prediction platform.

### 1. Main Dashboard
![Main Dashboard](assets/images/UI_PIC_1.png)

### 2. Hospital Dashboard
![Hospital Dashboard](assets/images/UI_PIC_1%28user_1st_page%29.png)

### 3. Dengue Prediction
![Dengue Prediction](assets/images/UI_PIC_1%28test_forecast_zoomed_out%29.png)

### 4. Analytics and Visualization
![Analytics](assets/images/UI_PIC_1%28test_forecast_zoomed_out_2%29.png)

### 5. Admin Dashboard
![Admin Dashboard](assets/images/UI_PIC_1%28admin_UI.png)

| | |
|---|---|
| Live URL | TODO: paste the deployed Vercel URL |
| Walkthrough video | TODO: paste the video link |
| Source | https://github.com/Abdullah-Tawsif/Dengue-Outbreak-Prediction-ML |
| Architecture and AI disclosure | [ARCHITECTURE.md](ARCHITECTURE.md) |

## Phase 2 module status

| # | Module | Status | Where |
|---|---|---|---|
| 1 | Constrained resource allocation (stock slider 30-100%, spraying crews, deficit alerts) | Implemented | `src/operations.py`, `GET /api/operations/status`, controls on `/live-input` |
| 2 | Hospital surge and same-city overflow routing | Implemented (needs ward bed capacities set by an admin) | `route_ward_overflow` in `src/operations.py` |
| 3 | What-if vector-control simulator | Partial: API-only scenario calculator using operator-entered assumptions; no UI, no per-ward ROI ranking, not ML-driven | `POST /api/operations/counterfactual` |
| 4 | SMS / WhatsApp / printable dispatch notices | Not built yet | - |
| 5 | Offline resilience | Core math runs locally; requires a local SQLite database (see [Offline mode](#offline-mode)) | - |

Update this table as modules are completed.

## Quick start (local)

Requires Python 3.12.

```bash
git clone https://github.com/Abdullah-Tawsif/Dengue-Outbreak-Prediction-ML.git
cd Dengue-Outbreak-Prediction-ML

python3.12 -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
test -f .env || cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"  # Set this as SESSION_SECRET in .env

python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Open http://127.0.0.1:8000. On Windows, if you get `WinError 10013`, port 8000 is blocked or reserved: use `--port 8080` instead.

`.env` for local development must contain `APP_ENV=development`, `DATABASE_URL=sqlite:///./dengue.db`, `COOKIE_SECURE=false` and a `SESSION_SECRET` of at least 32 characters. The app refuses to start otherwise. Never commit `.env`.

Trained models and the processed dataset are checked in, so no training is needed to start the server.

## Using the app

There are two roles, and they are kept apart: an administrator is redirected away from hospital pages, and a hospital user cannot open admin pages. For a full demo, create two accounts.

1. Register two users at `/register`.
2. Promote one to administrator:
   ```bash
   python -m src.backend.admin_cli --email admin@example.com
   ```
   The command asks you to type `PROMOTE` to confirm.
3. Administrator: sign in, open `/admin_dashboard`, and set total and occupied beds for wards (also available as `POST /api/admin/ward-bed-capacity`). Routing needs these values.
4. Hospital user: sign in, open `/live-input`, choose a city and ward, enter the weekly weather, and run a 2-, 3- or 4-week forecast. Repeat for more wards. The observation date must continue the ward's weekly series; the page pre-fills the next expected date.
5. On the same page, the operations panel shows per-ward saline bags, diagnostic kits and beds, allocation under the **Available regional stock** slider (30-100%), the **Total spraying crews** assignment, critical-deficit warnings, same-city overflow routes and city-wide bed utilization.

`/user_dashboard` shows historical replay forecasts from the stored dataset.

Wards appear in the operations panel once they have a stored live forecast or a configured bed capacity. All 60 wards appear only after each has been forecast.

## API summary

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/`, `/login`, `/register` | public | Pages |
| POST | `/register`, `/login`, `/logout` | public / session | Authentication |
| GET | `/user_dashboard` | hospital | Historical replay dashboard |
| POST | `/api/predict` | hospital + CSRF | Forecast from stored history (2-4 weeks) |
| GET | `/live-input` | hospital | Weekly input and operations page |
| GET | `/api/live-input/defaults?city=&ward=` | hospital | Ward context and next expected date |
| POST | `/api/live-predict` | hospital + CSRF | Forecast from entered weather; saves the observation and prediction |
| GET | `/api/operations/status?city=&availableStockPercent=&totalSprayingCrews=` | hospital | Allocation, deficits, crews, overflow routes, city bed utilization |
| POST | `/api/operations/counterfactual` | hospital + CSRF | Scenario estimate from operator assumptions |
| GET | `/admin_dashboard` | admin | Admin console |
| GET | `/api/admin/dashboard/summary`, `/users`, `/activity`, `/system-status` | admin | Admin data |
| POST | `/api/admin/ward-bed-capacity`, `/hospital-capacity`, `/crew-availability` | admin + CSRF | Capacity configuration |

State-changing API calls need the `X-CSRF-Token` header, which the pages supply automatically.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `development`, `test` or `production` |
| `DATABASE_URL` | none (required) | SQLite locally, PostgreSQL (Neon) in production. SQLite is rejected when `APP_ENV=production` |
| `SESSION_SECRET` | none (required) | At least 32 characters |
| `SESSION_MAX_AGE` | `3600` | Session lifetime in seconds |
| `COOKIE_SECURE` | `true` in production, else `false` | Must be `true` in production |
| `PREDICTION_BACKEND` | `reference_catboost` | `phase2` selects the earlier rolling-origin backend in `src/predict.py` |
| `REFERENCE_CATBOOST_RUN` | newest `runs/reference_catboost_*` | Pin a specific model run directory |
| `AEDISIGHT_MODEL_RUN` | auto-discovered | Model run for the `phase2` backend |
| `SALINE_BAGS_PER_CASE` | `2.0` | Planning ratio |
| `DIAGNOSTIC_KITS_PER_CASE` | `0.25` | Planning ratio |
| `BEDS_PER_CASE` | `0.10` | Planning ratio |

The three ratios are configurable planning assumptions, not validated clinical coefficients. See [ARCHITECTURE.md](ARCHITECTURE.md#assumptions-and-calibration) for how they compare with the supply columns in the provided dataset.

## Offline mode

Forecasting, allocation, overflow routing and crew planning run entirely on the local machine. A code search found no LLM SDK or outbound HTTP client in the runtime code, so nothing in the core math calls an external API.

To run with no internet:

- Use SQLite: `DATABASE_URL=sqlite:///./dengue.db`.
- Do **not** point `DATABASE_URL` at Neon. The app creates and syncs tables at startup, so with an unreachable Postgres server it fails to start (confirmed by test).
- Browser-side only: pages request Google Fonts (fallback fonts are used offline), and the landing page `/` loads Tailwind from a CDN, so its styling can degrade offline. Login, dashboards and `/live-input` are otherwise self-contained.

Rehearse the offline run with Wi-Fi switched off before judging.

## Data and model

- Data: `data/processed/processed_historical_data.csv`, 31,320 weekly rows for 60 wards (Dhaka North 20, Dhaka South 20, Chattogram 10, Gazipur 5, Narayanganj 5), 2016-01-04 to 2025-12-29. Source: the onsite dataset in `data/raw/`.
- Model: one CatBoost regressor per horizon (2, 3, 4 weeks), 50 iterations, depth 7, learning rate 0.05, seed 42. Trained on target dates through 2024, tested on 2025.
- Test metrics (single chronological split, run `reference_catboost_20261127T095240_866358Z`):

| Horizon | MAE | RMSE | R2 |
|---|---|---|---|
| 2 weeks | 1.22 | 2.11 | 0.713 |
| 3 weeks | 1.24 | 2.11 | 0.713 |
| 4 weeks | 1.40 | 2.33 | 0.651 |

- Risk levels (LOW, MEDIUM, HIGH, CRITICAL) are ward-relative percentile bands over each ward's historical active weeks. They are not clinical thresholds.
- The dataset ends on 2025-12-29, so the dashboard's "current" forecasting is disabled by a 14-day freshness policy. The `/live-input` workflow continues each ward's weekly series from the dataset end.

Details: [ARCHITECTURE.md](ARCHITECTURE.md), [REFERENCE_CATBOOST.md](REFERENCE_CATBOOST.md).

## Deploying to Vercel with Neon

1. Create a Neon PostgreSQL database and copy its connection string.
2. Write the connection string with the `postgresql+psycopg2://` scheme and `?sslmode=require`:
   `postgresql+psycopg2://USER:PASSWORD@HOST/DATABASE?sslmode=require`.
   A plain `postgresql://` URL selects the `psycopg` (v3) driver in current SQLAlchemy (2.1), which is not in `requirements.txt`; a fresh install then fails at startup.
3. In Vercel, set `APP_ENV=production`, `DATABASE_URL`, `SESSION_SECRET`, `COOKIE_SECURE=true` and `SESSION_MAX_AGE=3600`.
4. Import the repository root as the project. Vercel detects the FastAPI app from `main.py`; `vercel.json` only holds the schema reference.
5. Create the production admin: register, then promote with the admin CLI against the production `DATABASE_URL`.

Full checklist: [VERCEL_DEPLOYMENT.md](VERCEL_DEPLOYMENT.md). Pinning versions in `requirements.txt` is recommended so that deployments are reproducible.

## Tests

```bash
python -m pytest -q
```

## Documentation

| File | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, operations math, offline design, limitations, AI disclosure |
| [REFERENCE_CATBOOST.md](REFERENCE_CATBOOST.md) | Reference CatBoost feature contract and evaluation |
| [VERCEL_DEPLOYMENT.md](VERCEL_DEPLOYMENT.md) | Deployment checklist |
| [RUNNING.md](RUNNING.md) | Extra run instructions |
| [MODEL_INTEGRATION.md](MODEL_INTEGRATION.md) | How the model was first wired into the dashboard |

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Set the local values and a fresh session secret in `.env`, then start the server:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

If PowerShell blocks virtual-environment activation, allow it for this terminal only and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Windows Command Prompt (cmd.exe)

From the project root:

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
if not exist .env copy .env.example .env
```

Set the local values and a fresh session secret in `.env`, then start the server:

```bat
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

## Open and stop the application

Visit <http://127.0.0.1:8000>. Leave the terminal running while you use the app. Press `Ctrl+C` in that terminal to stop it. If port 8000 is already in use, change the command to `--port 8001` and visit <http://127.0.0.1:8001>.

The project includes a local SQLite database file where supplied and will not reset it during these steps. For a fresh checkout, the SQLite database is created as needed. The processed dataset and trained horizon artifacts are included, so model training is not required to start the server.

## Forecast data status

The bundled dengue dataset ends on 2025-12-29. With the current freshness policy, live/current forecasting is disabled until recent weekly data is supplied. The dashboard can still demonstrate held-out historical replay; label it as historical replay, not live surveillance.

## Reference CatBoost backend

This branch uses the independent reference-style CatBoost backend by default. The three 50-iteration model artifacts and their ordered feature metadata are stored in `runs/reference_catboost_20261127T095240_866358Z/`. The API supports 2-, 3-, and 4-week forecasts. See [REFERENCE_CATBOOST.md](REFERENCE_CATBOOST.md) for the feature contract, chronological evaluation results, historical-date behavior, and known differences from the reference source.

Set `PREDICTION_BACKEND=phase2` in the local environment to select the preserved Phase2 prediction backend. The app does not silently fall back if the reference CatBoost artifacts are unavailable.

## Live-input and operational workflow

Hospital users can open `/live-input`, select a city and ward, enter current weekly weather, and run a 2-, 3-, or 4-week forecast. Dengue case counts are forecast outputs, not required live inputs: case trend features use confirmed observations from weeks before the forecast origin. When later case reports become available, add them to the historical dataset before forecasting from those weeks; incomplete case history is rejected rather than replaced with zeroes. Re-running the same city/ward/week updates that observation and its prediction instead of returning a duplicate error. Live observations and prediction traces are persisted in `live_observations` and `live_predictions`.

The BLUEPINE live workflow records whether a case count was actually reported or is only historical context; context-only values are excluded from model case history.

The operational status endpoint is `/api/operations/status?city=...`. It reports exact integer `saline_bags_needed`, `diagnostic_kits_needed`, and `hospital_beds_required` by ward. Its `availableStockPercent` control accepts 30–100% and allocates saline and diagnostic kits by descending risk and population density, with a critical-deficit flag whenever demand is not fully covered. `totalSprayingCrews` dynamically reallocates the entered crew count to active wards; HIGH/CRITICAL risk and above-median population density increase derived crew need and priority.

Administrators can set ward-level total and occupied beds in the admin console or through `/api/admin/ward-bed-capacity`. The operations response shows local/net ward deficits, projected city-wide bed utilization, and routes only to lower-burden wards within the same city. Ward bed capacities and occupancy must be configured for meaningful surge routing. The endpoint `/api/admin/hospital-capacity` remains available for hospital-level capacity records. `/api/operations/counterfactual` accepts operator-defined scenario assumptions and returns labelled scenario estimates and benefit/cost information; it does not claim causal intervention effects.

Hospital users can open `/exports` for a city-filtered operations export. The printable commissioner brief is at `/exports/commissioner-brief`; `/exports/operations.csv` and `/exports/operations.json` download the ward snapshot in CSV and JSON. These authenticated exports use saved live forecasts and configured capacity, and retain the operational scenario limitations. The live-input operations panel also provides SMS and WhatsApp message drafts. Selecting a channel opens a prefilled message in the user's messaging app; AEDISIGHT does not send notifications or maintain a delivery record. Review each message and verify local conditions before sending.

Supply ratios can be configured with `SALINE_BAGS_PER_CASE`, `DIAGNOSTIC_KITS_PER_CASE`, and `BEDS_PER_CASE`. They are planning assumptions, not validated clinical coefficients. The SQLAlchemy metadata creates these tables automatically: `ward_contexts`, `live_observations`, `live_predictions`, `hospital_capacity`, `ward_bed_capacities`, and `crew_availability`. For production, use the project's migration process or create the corresponding PostgreSQL schema before enabling live writes. The live workflow must not be interpreted as confirmed bed assignment, ambulance dispatch, or a proven case reduction.

## Disclaimer

Forecasts, supply quantities, bed routes and crew assignments are planning aids built on configurable assumptions and historical data. They are not clinically validated, confirmed bed admissions, ambulance dispatch orders, or proof that an intervention reduces cases.
