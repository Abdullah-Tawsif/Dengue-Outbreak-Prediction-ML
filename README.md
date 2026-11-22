# AEDISIGHT — Dengue Outbreak Prediction (EDU AI Engineering Hackathon 2026)

Ward-level dengue early warning for Bangladesh. Uses weekly rainfall, temperature, humidity and case history, with multi-week lags, to forecast cases **2 or 4 weeks ahead** for 60 wards (Chattogram, Dhaka North, Dhaka South, Gazipur, Narayanganj) and classify them **LOW / MEDIUM / HIGH / CRITICAL**, so public-health officers can act before an outbreak peaks.

- **Model:** CatBoost regressor per horizon (2w, 4w), trained on 2016–2024, tested on 2025
- **Backend:** FastAPI (session auth + CSRF) · **Frontend:** Jinja2 dashboard with chart and driver breakdown
- Design details: see [ARCHITECTURE.md](ARCHITECTURE.md)

## Quick start (≈ 3 minutes)
```bash
git clone https://github.com/Abdullah-Tawsif/Dengue-Outbreak-Prediction-ML.git
cd Dengue-Outbreak-Prediction-ML

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                 # Windows: copy .env.example .env
# edit .env: set SESSION_SECRET to 32+ random characters, e.g.
# python -c "import secrets; print(secrets.token_urlsafe(48))"

python train.py                      # skip if models/*.joblib are already in the repo
uvicorn main:app --reload
```
Open http://127.0.0.1:8000 → **Register** a hospital account → sign in → `/user_dashboard`.

## Using the dashboard
1. Pick a **city**, **ward**, **forecast period** (2 or 4 weeks) and a **forecast origin week**.
2. Click **Generate forecast** to see predicted weekly cases, the risk tier, weather/case drivers, and a history-vs-forecast chart.

> **Tip:** dengue is seasonal (peak Jul–Sep, almost no cases Nov–Mar). Use an origin week in **May–Sep** (default `2025-08-04`) to see peak-season risk. The latest data point (2025-12-29) is dry season, so a near-zero forecast there is correct. When the target week exists in the dataset, the actual recorded cases are shown for comparison.

## Project structure
```
main.py                      FastAPI app and POST /api/predict
train.py                     trains 2w/3w/4w CatBoost models, writes models/metrics.json
src/feature_engineering.py   per-ward lag and rolling features (shared by train + serve)
src/predict.py               cached data/models, forecast from a chosen origin week
src/risk_engine.py           risk tiers and driver descriptions
src/backend/                 auth, database, models, security
templates/  static/          dashboard UI
data/processed/              dengue_dataset.csv
models/                      trained .joblib bundles + metrics.json
ARCHITECTURE.md              data flow, validation split, model rationale, cost controls
```

## Data
`data/processed/dengue_dataset.csv`: weekly records per ward (2016-02-01 → 2025-12-29) with `avg_temp_C`, `rainfall_mm`, `relative_humidity_pct`, `dengue_cases`, plus ward vulnerability features (`population_density_sqkm`, `water_logging_risk_index`, `sanitation_score`), supplied by the hackathon organizers.

## Model evaluation
`python train.py` prints and saves MAE/RMSE on the held-out 2025 test set to `models/metrics.json`, alongside simple baselines.

## Limitations
- Forecasts use the historical dataset (no live weather/hospital feed yet).
- Risk tiers are prototype ward-relative percentiles, not clinically validated thresholds.
- Driver text describes observed inputs, not SHAP attributions.

## AI assistants & external APIs disclosure
All team members used the **free tiers** of these assistants (no paid subscriptions and no paid APIs):

| AI assistant | Used for |
|---|---|
| **ChatGPT** (OpenAI, free) | Coding help: backend, ML pipeline, debugging |
| **Claude** (Anthropic, free) | Coding help: code review, debugging (zero-forecast / risk-band fix), documentation |
| **Gemini** (Google, free) | HTML, CSS and styling of the website |

- All AI-generated code was reviewed, integrated and tested by the team.
- **External APIs at runtime: none.** No weather API and no LLM calls; inference runs locally with CatBoost, so running the app costs nothing.
- Libraries: FastAPI, CatBoost, pandas, NumPy, scikit-learn, joblib, SQLAlchemy.


Built in 24 hours for the EDU AI Engineering Hackathon 2026, preliminary round (Nov 21–22, 2026).