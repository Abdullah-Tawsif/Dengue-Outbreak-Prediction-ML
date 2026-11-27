# AEDISIGHT - Dengue Early Warning and Resource Command

Ward-level dengue forecasting and emergency resource planning for 60 urban wards in five city corporations: Dhaka North, Dhaka South, Chattogram, Gazipur and Narayanganj.

Phase 1 forecasts weekly dengue cases 2, 3 and 4 weeks ahead. Phase 2 (the onsite final of the EDU AI Engineering Hackathon 2026) builds on that forecast to plan saline bags, diagnostic kits, hospital beds, spraying crews and same-city bed overflow routing for ward commissioners and health-center managers.

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

cp .env.example .env                 # Windows PowerShell: Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste the output as SESSION_SECRET in .env

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

## Disclaimer

Forecasts, supply quantities, bed routes and crew assignments are planning aids built on configurable assumptions and historical data. They are not clinically validated, confirmed bed admissions, ambulance dispatch orders, or proof that an intervention reduces cases.
