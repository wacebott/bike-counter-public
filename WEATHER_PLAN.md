# Weather Overlay for Public Heatmap — Build Plan

## Goal
Add a weather toggle to the "Hourly Heatmap — all dates × all hours" table on the
public Railway dashboard. When ON, each date expands with one extra row showing
per-hour temperature (°C, color-scaled blue→red) and a precip emoji (🌧 rain /
❄️ snow; snow wins if both; nothing if dry).

## Data source
Open-Meteo (free, no key). Pinned to Hollis St: lat 44.648, lon -63.575, tz America/Halifax.
- Forecast endpoint (`/v1/forecast?...&past_days=7&forecast_days=1`): covers today incl.
  elapsed hours + trailing 7 days. Used for recent/current data.
- Archive endpoint (`/v1/archive?...&start_date&end_date`): accurate reanalysis, ~5-day lag.
  Used to overwrite forecast data once a date is ≥5 days old, then locked.
- Verified delta: forecast vs archive on same day = up to 2.8°C temp, 25% rain yes/no
  disagreement → reconciliation is necessary for analytical accuracy.

## Architecture (decision (b): Railway owns weather, decoupled from counter)
- Railway fetches Open-Meteo directly, caches in its own Postgres.
- iMac/counter NOT involved.
- Daily refresh triggered by a Hermes cron curl to a protected endpoint (avoids the
  gunicorn 2-worker double-scheduler problem; survives Railway idling).

## Postgres table: weather_hourly
- date TEXT, hour TEXT ('HH:00'), temp_c REAL, rain_mm REAL, snow_cm REAL,
  source TEXT ('forecast'|'archive'), is_final BOOL, PRIMARY KEY(date,hour).

## Backend (app.py)
1. Add weather_hourly to INIT_SQL (both Postgres + sqlite).
2. fetch_forecast() / fetch_archive(start,end) — urllib with browser UA, parse hourly.
3. upsert_weather(rows, source, is_final) — ON CONFLICT update unless existing is_final.
4. refresh_weather():
   - forecast refresh: last 7 days (source=forecast, is_final=False).
   - reconcile: for each distinct count-date in crossings that is ≥5 days old and not
     is_final, fetch archive, overwrite, set is_final=True.
   - backfill: any count-date with no weather rows → fetch (archive if ≥5d old else forecast).
5. Routes:
   - GET  /api/weather → {date: {hour: {t: temp, p: 'rain'|'snow'|null}}}
   - POST /api/weather/refresh (X-API-Key guard, reuse push key) → runs refresh_weather().

## Frontend (heatmap section)
1. Checkbox "🌦 Weather" in the .filter-row.
2. On toggle: lazy-fetch /api/weather once, cache in JS, re-render.
3. In renderHeatmap(): if weather on, after each date's count row, insert a weather row:
   - row label "  weather" (indented, muted).
   - per hour cell: temp int + emoji; bg color scaled by temp (blue cold → red hot).
4. Σ column on weather row: daily avg temp.

## Daily cron (Hermes)
- curl -X POST https://<railway>/api/weather/refresh -H 'X-API-Key: <key>'
- Schedule: daily ~6 AM. no_agent script.

## Verify
- Deploy, POST refresh (backfill), GET /api/weather shows real data, toggle renders
  on live site (browser screenshot), emoji + temp colors correct, weekend rows intact.
