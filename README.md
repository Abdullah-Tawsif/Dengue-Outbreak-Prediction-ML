# Run AEDISIGHT locally

These steps use the existing FastAPI application and its checked-in processed dataset and trained model artifacts. Use Python 3.12. The commands work in a VS Code integrated terminal or a regular terminal.

## Open the project in VS Code

Open the project folder, then choose **Terminal → New Terminal**. In the terminal, confirm its current directory is the project root. After creating the virtual environment below, open the Command Palette (`Ctrl+Shift+P`), select **Python: Select Interpreter**, and choose the interpreter inside `.venv`.

Keep any existing `.env` file and database. If `.env` does not exist, copy `.env.example` using the platform-specific command below. Set `APP_ENV=development`, `DATABASE_URL=sqlite:///./dengue.db`, and `COOKIE_SECURE=false` for local development. Replace the example `SESSION_SECRET` with a fresh random value of at least 32 characters. Generate one with:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit `.env` locally to set the generated value. Never commit `.env` or use the example secret.

## Linux terminal or VS Code terminal (Bash)

From the project root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
```

Set the local values and a fresh session secret in `.env`, then start the server:

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

## Windows PowerShell or VS Code PowerShell terminal

From the project root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
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

This branch uses the independent reference-style CatBoost backend by default. The three 50-iteration model artifacts and their ordered feature metadata are stored in `runs/reference_catboost_20261126T215839_759578Z/`. The API supports 2-, 3-, and 4-week forecasts. See [REFERENCE_CATBOOST.md](REFERENCE_CATBOOST.md) for the feature contract, chronological evaluation results, historical-date behavior, and known differences from the reference source.

Set `PREDICTION_BACKEND=phase2` in the local environment to select the preserved Phase2 prediction backend. The app does not silently fall back if the reference CatBoost artifacts are unavailable.

## Live-input and operational workflow

Hospital users can open `/live-input`, select a city and ward, enter current weekly weather, and run a 2-, 3-, or 4-week forecast. Dengue case counts are forecast outputs, not required live inputs: case trend features use confirmed observations from weeks before the forecast origin. When later case reports become available, add them to the historical dataset before forecasting from those weeks; incomplete case history is rejected rather than replaced with zeroes. Re-running the same city/ward/week updates that observation and its prediction instead of returning a duplicate error. Live observations and prediction traces are persisted in `live_observations` and `live_predictions`.

The BLUEPINE live workflow records whether a case count was actually reported or is only historical context; context-only values are excluded from model case history.

The operational status endpoint is `/api/operations/status?city=...`. It reports exact integer `saline_bags_needed`, `diagnostic_kits_needed`, and `hospital_beds_required` by ward. Its `availableStockPercent` control accepts 30–100% and allocates saline and diagnostic kits by descending risk and population density, with a critical-deficit flag whenever demand is not fully covered. `totalSprayingCrews` dynamically reallocates the entered crew count to active wards; HIGH/CRITICAL risk and above-median population density increase derived crew need and priority.

Administrators can set ward-level total and occupied beds in the admin console or through `/api/admin/ward-bed-capacity`. The operations response shows local/net ward deficits, projected city-wide bed utilization, and routes only to lower-burden wards within the same city. Ward bed capacities and occupancy must be configured for meaningful surge routing. The endpoint `/api/admin/hospital-capacity` remains available for hospital-level capacity records. `/api/operations/counterfactual` accepts operator-defined scenario assumptions and returns labelled scenario estimates and benefit/cost information; it does not claim causal intervention effects.

Supply ratios can be configured with `SALINE_BAGS_PER_CASE`, `DIAGNOSTIC_KITS_PER_CASE`, and `BEDS_PER_CASE`. They are planning assumptions, not validated clinical coefficients. The SQLAlchemy metadata creates these tables automatically: `ward_contexts`, `live_observations`, `live_predictions`, `hospital_capacity`, `ward_bed_capacities`, and `crew_availability`. For production, use the project's migration process or create the corresponding PostgreSQL schema before enabling live writes. The live workflow must not be interpreted as confirmed bed assignment, ambulance dispatch, or a proven case reduction.
