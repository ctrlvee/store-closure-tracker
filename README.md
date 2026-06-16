# Store Closure Tracker (CVS + Walgreens)

Automated project that tracks reported CVS and Walgreens store closures, commits data updates to GitHub, and publishes an easy dashboard with maps/charts via GitHub Pages.

## What this repo does

- Collects closure-related news from:
  - GDELT article API
  - Bing News RSS
- Extracts likely closure mentions and location candidates from article text
- Geocodes location strings (OpenStreetMap Nominatim, cached locally)
- Saves records to `data/closures.csv`
- Builds dashboard data in `docs/data/closures.json`
- Publishes `docs/index.html` on GitHub Pages
- Runs automatically on a daily schedule with GitHub Actions

## Repository layout

- `.github/workflows/update-data.yml` - scheduled data update + auto-commit
- `.github/workflows/deploy-pages.yml` - deploy GitHub Pages from `docs/`
- `scripts/collect_closure_mentions.py` - ingestion + extraction + geocoding
- `scripts/build_dashboard.py` - summary JSON for front-end dashboard
- `docs/index.html` - static dashboard UI
- `data/closures.csv` - tracked closure records
- `data/geocode_cache.json` - geocode cache to limit repeated lookups

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts/collect_closure_mentions.py
python scripts/build_dashboard.py
```

Open `docs/index.html` locally to preview.

## GitHub setup

1. Create a new GitHub repo and push this folder.
2. In GitHub:
   - `Settings -> Actions -> General -> Workflow permissions`
   - Enable: `Read and write permissions` (needed for auto-commit).
3. In `Settings -> Pages`:
   - Source: `GitHub Actions`.
4. Run `update-data` workflow once manually (`Actions -> update-data -> Run workflow`).

After that, data updates run daily and the dashboard auto-deploys.

## Notes on data quality

- This tracks **reported** closures in news sources, not an official master list from each company.
- Location extraction is heuristic. Verify critical records before downstream use.
- Geocoding uses cached best-effort matches from location text.

## Useful commands

```bash
# Collect without geocoding (faster)
python scripts/collect_closure_mentions.py --skip-geocode

# Collect fewer articles per query
python scripts/collect_closure_mentions.py --max-per-query 40
```

## Next additions to consider

- Add Rite Aid / Dollar General / Walmart closures for market context
- Add SEC filing parser (10-K/8-K) for corporate closure announcements
- Add county-level demographics overlays (income, pharmacy deserts, seniors)
- Add temporal forecasting (closure risk by region)
- Add alerting (email/Slack) when a new closure appears in selected states
