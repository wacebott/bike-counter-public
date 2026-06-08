import os
import json
from datetime import datetime, timezone
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

# ── Database helpers ──────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

SQLITE_PATH = "/tmp/counts.db"

INIT_SQL_PG = """
CREATE TABLE IF NOT EXISTS crossings (
    id SERIAL PRIMARY KEY,
    ts TEXT NOT NULL,
    class_id INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    UNIQUE (ts, track_id, direction)
);
CREATE INDEX IF NOT EXISTS idx_crossings_ts ON crossings (ts);
"""

INIT_SQL_SQLITE = """
CREATE TABLE IF NOT EXISTS crossings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    class_id INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    UNIQUE (ts, track_id, direction)
);
CREATE INDEX IF NOT EXISTS idx_crossings_ts ON crossings (ts);
"""


def get_conn():
    if USE_POSTGRES:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    else:
        import sqlite3
        return sqlite3.connect(SQLITE_PATH)


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    if USE_POSTGRES:
        for stmt in INIT_SQL_PG.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
    else:
        for stmt in INIT_SQL_SQLITE.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
    conn.commit()
    cur.close()
    conn.close()


# Initialise on startup
try:
    init_db()
except Exception as exc:
    print(f"[WARN] DB init failed: {exc}")


# ── Dashboard HTML ────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>🚲 Bike Lane Counter — Public View</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #111;
      color: #e0e0e0;
      font-family: 'Segoe UI', system-ui, sans-serif;
      min-height: 100vh;
      padding: 24px 16px 48px;
    }
    header {
      text-align: center;
      margin-bottom: 32px;
    }
    header h1 {
      font-size: 2rem;
      font-weight: 700;
      letter-spacing: 0.01em;
    }
    header p.subtitle {
      margin-top: 6px;
      color: #888;
      font-size: 0.9rem;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(480px, 1fr));
      gap: 24px;
      max-width: 1200px;
      margin: 0 auto;
    }
    .card {
      background: #1e1e1e;
      border-radius: 12px;
      padding: 20px 24px;
      box-shadow: 0 2px 12px rgba(0,0,0,0.4);
    }
    .card h2 {
      font-size: 1rem;
      font-weight: 600;
      color: #aaa;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 16px;
    }
    .chart-wrap {
      position: relative;
      height: 260px;
    }
    footer {
      text-align: center;
      margin-top: 40px;
      color: #555;
      font-size: 0.8rem;
    }
    .loading {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 260px;
      color: #555;
      font-size: 0.9rem;
    }
    .stat-row {
      display: flex;
      gap: 16px;
      justify-content: center;
      margin-bottom: 24px;
      flex-wrap: wrap;
    }
    .stat {
      background: #1e1e1e;
      border-radius: 10px;
      padding: 14px 28px;
      text-align: center;
      min-width: 140px;
    }
    .stat .value {
      font-size: 2.2rem;
      font-weight: 700;
      color: #4fc3f7;
    }
    .stat .label {
      font-size: 0.78rem;
      color: #888;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin-top: 4px;
    }
  </style>
</head>
<body>

<header>
  <h1>🚲 Bike Lane Counter — Public View</h1>
  <p class="subtitle">Updated every 15 minutes from the local counter.</p>
</header>

<div class="stat-row" id="stats">
  <div class="stat"><div class="value" id="stat-today">—</div><div class="label">Today</div></div>
  <div class="stat"><div class="value" id="stat-week">—</div><div class="label">This Week</div></div>
  <div class="stat"><div class="value" id="stat-total">—</div><div class="label">All Time</div></div>
</div>

<div class="grid">
  <div class="card">
    <h2>Hourly Activity (last 24 h)</h2>
    <div class="chart-wrap">
      <canvas id="hourlyChart"></canvas>
    </div>
  </div>
  <div class="card">
    <h2>Daily Totals (last 30 days)</h2>
    <div class="chart-wrap">
      <canvas id="dailyChart"></canvas>
    </div>
  </div>
</div>

<footer>
  Data refreshes automatically &bull; Powered by a Raspberry Pi &amp; YOLO detection
</footer>

<script>
const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
  scales: {
    x: {
      ticks: { color: '#888', maxRotation: 45 },
      grid: { color: '#2a2a2a' }
    },
    y: {
      ticks: { color: '#888' },
      grid: { color: '#2a2a2a' },
      beginAtZero: true
    }
  }
};

let hourlyChart, dailyChart;

function makeBar(ctx, labels, data, color) {
  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: color + 'cc',
        borderColor: color,
        borderWidth: 1,
        borderRadius: 4
      }]
    },
    options: { ...CHART_DEFAULTS }
  });
}

async function fetchAndRender() {
  try {
    const [hRes, dRes] = await Promise.all([
      fetch('/api/hourly'),
      fetch('/api/daily')
    ]);
    const hourly = await hRes.json();
    const daily  = await dRes.json();

    // Hourly chart
    const hLabels = hourly.map(r => r.hour);
    const hData   = hourly.map(r => r.count);
    if (hourlyChart) {
      hourlyChart.data.labels = hLabels;
      hourlyChart.data.datasets[0].data = hData;
      hourlyChart.update();
    } else {
      hourlyChart = makeBar(
        document.getElementById('hourlyChart').getContext('2d'),
        hLabels, hData, '#4fc3f7'
      );
    }

    // Daily chart
    const dLabels = daily.map(r => r.date);
    const dData   = daily.map(r => r.count);
    if (dailyChart) {
      dailyChart.data.labels = dLabels;
      dailyChart.data.datasets[0].data = dData;
      dailyChart.update();
    } else {
      dailyChart = makeBar(
        document.getElementById('dailyChart').getContext('2d'),
        dLabels, dData, '#81c784'
      );
    }

    // Stats
    const today = dData.length ? dData[dData.length - 1] : 0;
    const week  = dData.slice(-7).reduce((a, b) => a + b, 0);
    const total = dData.reduce((a, b) => a + b, 0);
    document.getElementById('stat-today').textContent = today.toLocaleString();
    document.getElementById('stat-week').textContent  = week.toLocaleString();
    document.getElementById('stat-total').textContent = total.toLocaleString();

  } catch(e) {
    console.error('Fetch error', e);
  }
}

fetchAndRender();
setInterval(fetchAndRender, 5 * 60 * 1000); // re-fetch every 5 min
</script>
</body>
</html>
"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/hourly")
def api_hourly():
    """Return counts grouped by hour for the last 24 hours."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""
                SELECT
                    to_char(ts::timestamp, 'HH24:00') AS hour,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE ts::timestamp >= NOW() - INTERVAL '24 hours'
                GROUP BY hour
                ORDER BY hour
            """)
        else:
            cur.execute("""
                SELECT
                    strftime('%H:00', ts) AS hour,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE ts >= datetime('now', '-24 hours')
                GROUP BY hour
                ORDER BY hour
            """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([{"hour": r[0], "count": r[1]} for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/daily")
def api_daily():
    """Return counts grouped by date for the last 30 days."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""
                SELECT
                    to_char(ts::timestamp, 'YYYY-MM-DD') AS date,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE ts::timestamp >= NOW() - INTERVAL '30 days'
                GROUP BY date
                ORDER BY date
            """)
        else:
            cur.execute("""
                SELECT
                    strftime('%Y-%m-%d', ts) AS date,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE ts >= datetime('now', '-30 days')
                GROUP BY date
                ORDER BY date
            """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([{"date": r[0], "count": r[1]} for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/push", methods=["POST"])
def api_push():
    """Receive a batch of crossing events from the local counter."""
    # Auth
    api_key = os.environ.get("PUSH_API_KEY", "")
    provided = request.headers.get("X-API-Key", "")
    if not provided or provided != api_key:
        return jsonify({"error": "Unauthorized"}), 401

    # Parse body
    try:
        body = request.get_json(force=True)
        events = body["events"]
        if not isinstance(events, list):
            raise ValueError("events must be a list")
    except Exception:
        return jsonify({"error": "Malformed request body"}), 400

    # Validate & insert
    required_keys = {"ts", "class_id", "track_id", "direction", "confidence"}
    try:
        conn = get_conn()
        cur = conn.cursor()
        inserted = 0
        for ev in events:
            if not required_keys.issubset(ev.keys()):
                continue  # skip malformed individual events
            try:
                if USE_POSTGRES:
                    cur.execute(
                        """
                        INSERT INTO crossings (ts, class_id, track_id, direction, confidence)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (ts, track_id, direction) DO NOTHING
                        """,
                        (ev["ts"], int(ev["class_id"]), int(ev["track_id"]),
                         str(ev["direction"]), float(ev["confidence"]))
                    )
                else:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO crossings
                            (ts, class_id, track_id, direction, confidence)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (ev["ts"], int(ev["class_id"]), int(ev["track_id"]),
                         str(ev["direction"]), float(ev["confidence"]))
                    )
                if cur.rowcount > 0:
                    inserted += 1
            except Exception:
                pass  # skip individual bad rows
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"inserted": inserted}), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
