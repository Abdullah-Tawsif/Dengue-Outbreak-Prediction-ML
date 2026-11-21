# AEDISIGHT — Urban Dengue Early-Warning Prototype

AEDISIGHT is a Bangladesh-focused prototype for exploring ward-level dengue forecasting from historical dengue, weather, and vulnerability data. The repository contains a data-cleaning and feature-engineering pipeline, two-week and four-week model training, and a FastAPI web application with hospital-user and administrator authentication.

> **Implementation status:** the data preparation, model-training script, and authentication workflow are present. The trained forecasting models are **not yet connected to the web dashboards**: there is no implemented prediction API or dynamic ward-risk dashboard in the current code. Dashboard statistics and visualizations should be treated as presentation placeholders, not live dengue predictions. Risk-tier generation (Low / Medium / High / Critical) is also not implemented by the current training script.

## What is implemented

- CSV validation and ward-specific historical feature engineering in `src/clean_data.py`.
- Two- and four-week dengue-case forecasting model training in `src/train.py`.
- Chronological train/validation/test evaluation using the date of the future target.
- FastAPI pages and authentication routes for public registration, login, logout, and role-protected dashboards.
- Argon2 password hashing, CSRF tokens, signed session cookies, and an administrator provisioning CLI.
- SQLite for local development and SQLAlchemy-compatible PostgreSQL configuration for deployment.

## Repository layout

```text
.
├── data/
│   ├── raw/                 # Source ward-level dengue CSV
│   └── processed/           # Cleaned, feature-engineered training CSV
├── models/                  # Locally generated model artifacts
├── src/
│   ├── clean_data.py        # Validation, missing-row removal, feature engineering
│   ├── train.py             # Two- and four-week model training/evaluation
│   └── backend/
│       ├── admin_cli.py     # Promote an existing active user to administrator
│       ├── auth.py          # Register, login, logout routes
│       ├── database.py      # SQLAlchemy engine and database sessions
│       ├── dependencies.py  # Active-user and role authorization
│       ├── models.py        # User ORM model
│       └── security.py      # Argon2 password and CSRF helpers
├── static/                  # CSS and JavaScript assets
├── templates/               # Jinja2 pages
├── tests/test_auth.py       # Authentication and access-control tests
├── main.py                  # FastAPI application entry point
├── requirements.txt
├── .env.example
├── README.md
└── ARCHITECTURE.md
```

## Requirements

- Python 3.10 or newer (the project has been exercised with Python 3.12).
- The raw CSV at `data/raw/hackathon_preli_dataset - bangladesh_dengue_large_dataset.csv` for data preparation.
- A local SQLite database for development, or a PostgreSQL database such as Neon for hosted use.

## Local setup

Run commands from the repository root.

```bash
python -m venv .venv
source .venv/bin/activate       # Linux/macOS
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set a unique session secret. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

A local development configuration can use:

```dotenv
APP_ENV=development
DATABASE_URL=sqlite:///./dengue.db
SESSION_SECRET=PASTE_A_NEW_RANDOM_SECRET_HERE
COOKIE_SECURE=false
SESSION_MAX_AGE=3600
```

Do not commit `.env`. The repository's `.gitignore` excludes local environment files and virtual environments. Do not put real database passwords, session secrets, or API credentials in `.env.example`, source files, screenshots, or Git commits.

## Prepare the training dataset

The cleaner resolves paths relative to its own location, so it does not depend on a developer-specific home directory.

```bash
python -m py_compile src/clean_data.py
python src/clean_data.py
```

Default input:

```text
data/raw/hackathon_preli_dataset - bangladesh_dengue_large_dataset.csv
```

Default output:

```text
data/processed/dengue_dataset.csv
```

The cleaner checks required columns, missing source values, data types and numeric ranges, duplicate records, consistent ward/city labels, and exactly weekly continuity for each ward. It then creates historical weather and dengue features. Rows that lack enough historical observations for the engineered lag/rolling features are removed **after** feature engineering so the final CSV has no missing feature cells.

For the dataset last processed during development, the cleaner reported:

| Measure | Result |
|---|---:|
| Source rows | 31,320 |
| Incomplete source rows removed | 0 |
| Rows lacking complete engineered history removed | 240 |
| Final rows | 31,080 |
| Wards | 60 |
| Final columns | 34 |
| Final date range | 2016-02-01 to 2025-12-29 |

These are the observed results for that dataset version, not guarantees for future input files. The source does not specify whether `dengue_cases` means weekly new cases or cumulative counts; the pipeline preserves the supplied values and does not infer their meaning.

You can independently inspect the output:

```bash
python -c "import pandas as pd; d=pd.read_csv('data/processed/dengue_dataset.csv'); print('Rows:', len(d)); print('Columns:', len(d.columns)); print('Wards:', d.ward_id.nunique()); print('Missing cells:', int(d.isna().sum().sum())); print('Date range:', d.date.min(), 'to', d.date.max())"
```

## Train and evaluate forecasting models

After preparing `data/processed/dengue_dataset.csv`, run:

```bash
python -u src/train.py
```

The script creates `models/` if needed and writes:

```text
models/model_2w.joblib
models/model_4w.joblib
```

For each horizon, the script compares a Random Forest regressor and a Poisson-loss `HistGradientBoostingRegressor`. Categorical city and ward identifiers are one-hot encoded. Model selection uses validation MAE; the selected pipeline is then retrained on the combined training and validation periods and evaluated on the untouched test period. See [ARCHITECTURE.md](ARCHITECTURE.md) for the exact chronological split and feature details.

Training can be CPU-intensive, particularly the Random Forest candidate. The training script is a batch/offline workflow; the current FastAPI application does not load these artifacts or expose an inference route.

## Run the web application

Ensure `.env` contains a valid `DATABASE_URL` and a random `SESSION_SECRET` of at least 32 characters, then run:

```bash
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

- Home: `http://127.0.0.1:8000/`
- Register: `http://127.0.0.1:8000/register`
- Login: `http://127.0.0.1:8000/login`

The application creates missing SQLAlchemy tables at startup with `Base.metadata.create_all`. This is not a database migration system; use a controlled migration process when changing an existing production schema.

## Authentication and administrator provisioning

- Public registration always creates a non-admin user.
- Passwords are hashed with Argon2 through `pwdlib`; plaintext passwords are not stored.
- Registration, login, and logout forms use session-backed CSRF tokens.
- The session stores the user ID and is signed by Starlette's `SessionMiddleware`.
- Active-user and role dependencies protect `/user_dashboard` and `/admin_dashboard`.
- Administrator status is represented by `User.is_admin`; there is no public admin-registration flow.

To promote an already registered, active user, run this from the repository root with the correct database configured in `.env`:

```bash
python -m src.backend.admin_cli --email registered-user@example.com
```

Review the target account and type `PROMOTE` when prompted. The command does not create an account and will refuse to promote a missing or inactive account. Do not publish real user email addresses or credentials in logs or documentation.

## Production configuration (PostgreSQL / Neon)

Set environment variables in the hosting provider's secret/environment settings. Do not upload a production `.env` file.

```dotenv
APP_ENV=production
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require
SESSION_SECRET=YOUR_UNIQUE_RANDOM_SECRET_AT_LEAST_32_CHARACTERS
COOKIE_SECURE=true
SESSION_MAX_AGE=3600
```

Use the connection string supplied by your PostgreSQL provider. Keep TLS enabled (`sslmode=require` for the documented Neon-style URL). If the password contains URL-reserved characters, URL-encode them before placing it in the connection URL. Never use the example placeholders as real credentials.

In production, `COOKIE_SECURE` must be `true`; the application configuration is designed to reject an explicitly insecure cookie setting when `APP_ENV=production`. Serve the app only over HTTPS. Keep `SESSION_SECRET` private and rotate it if it is exposed. Configure `DATABASE_URL` and `SESSION_SECRET` through the deployment platform, not source control.

The repository includes `vercel.json` and a Mangum ASGI handler for a Vercel-style deployment. The current Vercel configuration explicitly includes `templates/**` and `static/**`; it does not configure model artifacts or training datasets as deployment assets. The current web app does not need trained model artifacts because inference is not yet integrated. Validate the deployed app, database connectivity, and cookie behavior in the hosting environment before treating it as production-ready.

## Tests and checks

Activate the virtual environment and run:

```bash
python -m py_compile main.py src/clean_data.py
python -m compileall -q .
python -m pytest -v
```

The authentication integration suite currently contains 18 tests covering registration, password hashing, CSRF rejection, login/logout sessions, inactive accounts, public-registration privilege boundaries, and role-based access. A passing test run verifies those implemented tests; it does not establish model accuracy or validate a live production deployment.

## AI / model-use disclosure and limitations

The reviewed forecasting training code uses locally trained scikit-learn regressors (Random Forest and histogram gradient boosting) with engineered historical features. It does not call a hosted generative-AI API as part of this training workflow. The models forecast future values of the supplied `dengue_cases` series at two- and four-week horizons; they do not independently establish whether those values represent incident weekly cases or cumulative totals.

This is an early-warning research prototype, not a diagnostic tool, a substitute for public-health surveillance, or an automatic instruction to allocate clinical resources. Predictions require validation against trusted epidemiological data and review by qualified public-health personnel. The current repository still needs a model-serving/inference layer, calibrated and validated risk tiers, and integration of those outputs into the dashboards before it can be described as a live ward-level operational warning system.

## Further technical details

See [ARCHITECTURE.md](ARCHITECTURE.md) for data flow, feature construction, chronological evaluation rationale, security boundaries, deployment notes, and known integration gaps.
