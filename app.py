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

CREATE TABLE IF NOT EXISTS review_queue (
    id SERIAL PRIMARY KEY,
    local_id INTEGER NOT NULL UNIQUE,
    ts TEXT NOT NULL,
    class_id INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue (status);
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

CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_id INTEGER NOT NULL UNIQUE,
    ts TEXT NOT NULL,
    class_id INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue (status);
"""

CLASS_LABELS = {1: "🚲 Bicycle", 2: "🚗 Car", 3: "🛵 Moped", 5: "🚌 Bus", 7: "🚛 Truck", 100: "🛴 Scooter"}


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
  Data refreshes automatically &bull; Powered by YOLO detection
</footer>

<script>
const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: true, labels: { color: '#aaa', boxWidth: 12, font: { size: 10 } } } },
  scales: {
    x: {
      stacked: true,
      ticks: { color: '#888', maxRotation: 45 },
      grid: { color: '#2a2a2a' }
    },
    y: {
      stacked: true,
      ticks: { color: '#888' },
      grid: { color: '#2a2a2a' },
      beginAtZero: true
    }
  }
};

// Canonical class_id → display name
const CLASS_NAMES = { 1: 'Bicycle', 2: 'Car', 3: 'Moped', 5: 'Bus', 7: 'Truck', 100: 'Scooter' };
// Per category + direction palette (12 series)
const SERIES_PALETTE = {
  'Bicycle →in':  '#4caf50', 'Bicycle out→': '#81c784',
  'Car →in':      '#2196f3', 'Car out→':     '#64b5f6',
  'Moped →in':    '#8bc34a', 'Moped out→':   '#aed581',
  'Bus →in':      '#ff9800', 'Bus out→':     '#ffb74d',
  'Truck →in':    '#f44336', 'Truck out→':   '#e57373',
  'Scooter →in':  '#9c27b0', 'Scooter out→': '#ce93d8',
};
const FALLBACK = ['#4caf50','#2196f3','#8bc34a','#03a9f4','#ffb300','#ff7043'];

let hourlyChart, dailyChart;

// Build stacked datasets keyed by category+direction. Returns {labels, datasets, total}.
function buildStacked(rows) {
  const labels = [...new Set(rows.map(r => r.label))].sort();
  const series = {};
  let total = 0;
  rows.forEach(r => {
    const cname = CLASS_NAMES[r.class_id] || ('Class ' + r.class_id);
    const key = r.direction === 'in' ? (cname + ' →in') : (cname + ' out→');
    if (!series[key]) series[key] = new Array(labels.length).fill(0);
    const idx = labels.indexOf(r.label);
    if (idx >= 0) series[key][idx] += r.count;
    total += r.count;
  });
  const datasets = Object.entries(series).map(([label, data], i) => {
    const color = SERIES_PALETTE[label] || FALLBACK[i % FALLBACK.length];
    return {
      label, data,
      backgroundColor: color + 'cc',
      borderColor: color,
      borderWidth: 1,
      borderRadius: 3,
    };
  });
  return { labels, datasets, total };
}

// Sum totals per label across all series (for stat cards).
function perLabelTotals(rows) {
  const m = {};
  rows.forEach(r => { m[r.label] = (m[r.label] || 0) + r.count; });
  return m;
}

function makeStacked(ctx, labels, datasets) {
  return new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets },
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

    // Hourly chart (stacked per category+direction)
    const h = buildStacked(hourly);
    if (hourlyChart) {
      hourlyChart.data.labels = h.labels;
      hourlyChart.data.datasets = h.datasets;
      hourlyChart.update();
    } else {
      hourlyChart = makeStacked(
        document.getElementById('hourlyChart').getContext('2d'),
        h.labels, h.datasets
      );
    }

    // Daily chart (stacked per category+direction)
    const d = buildStacked(daily);
    if (dailyChart) {
      dailyChart.data.labels = d.labels;
      dailyChart.data.datasets = d.datasets;
      dailyChart.update();
    } else {
      dailyChart = makeStacked(
        document.getElementById('dailyChart').getContext('2d'),
        d.labels, d.datasets
      );
    }

    // Stats — computed from the summed daily series
    const dayTotals = perLabelTotals(daily);
    const dayKeys = Object.keys(dayTotals).sort();
    const dayValues = dayKeys.map(k => dayTotals[k]);
    const today = dayValues.length ? dayValues[dayValues.length - 1] : 0;
    const week  = dayValues.slice(-7).reduce((a, b) => a + b, 0);
    const total = dayValues.reduce((a, b) => a + b, 0);
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
    """Return counts grouped by hour, direction, and class_id for the last 24 hours."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""
                SELECT
                    to_char(ts::timestamp, 'HH24:00') AS label,
                    direction,
                    class_id,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE ts::timestamp >= NOW() - INTERVAL '24 hours'
                GROUP BY label, direction, class_id
                ORDER BY label
            """)
        else:
            cur.execute("""
                SELECT
                    strftime('%H:00', ts) AS label,
                    direction,
                    class_id,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE ts >= datetime('now', '-24 hours')
                GROUP BY label, direction, class_id
                ORDER BY label
            """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([
            {"label": r[0], "direction": r[1], "class_id": r[2], "count": r[3]}
            for r in rows
        ])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/daily")
def api_daily():
    """Return counts grouped by date, direction, and class_id for the last 30 days."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""
                SELECT
                    to_char(ts::timestamp, 'YYYY-MM-DD') AS label,
                    direction,
                    class_id,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE ts::timestamp >= NOW() - INTERVAL '30 days'
                GROUP BY label, direction, class_id
                ORDER BY label
            """)
        else:
            cur.execute("""
                SELECT
                    strftime('%Y-%m-%d', ts) AS label,
                    direction,
                    class_id,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE ts >= datetime('now', '-30 days')
                GROUP BY label, direction, class_id
                ORDER BY label
            """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([
            {"label": r[0], "direction": r[1], "class_id": r[2], "count": r[3]}
            for r in rows
        ])
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


# ── Auth helpers ──────────────────────────────────────────────────────────────

def check_push_key():
    api_key = os.environ.get("PUSH_API_KEY", "")
    provided = request.headers.get("X-API-Key", "")
    return bool(provided and provided == api_key)


def check_review_key():
    review_key = os.environ.get("REVIEW_API_KEY", "")
    if not review_key:
        return False
    provided = (request.headers.get("X-Review-Key", "")
                or request.args.get("key", "")
                or (request.get_json(silent=True, force=True) or {}).get("key", ""))
    return bool(provided and provided == review_key)


# ── Review page HTML ──────────────────────────────────────────────────────────

REVIEW_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>🔍 Review Queue</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #111; color: #e0e0e0;
      font-family: 'Segoe UI', system-ui, sans-serif;
      min-height: 100vh; padding: 24px 16px 48px;
      max-width: 700px; margin: 0 auto;
    }
    h1 { font-size: 1.6rem; margin-bottom: 6px; }
    .subtitle { color: #888; font-size: 0.85rem; margin-bottom: 28px; }
    .empty { color: #555; text-align: center; padding: 60px 0; font-size: 1.1rem; }
    .item {
      background: #1e1e1e; border-radius: 12px;
      padding: 18px 20px; margin-bottom: 16px;
      display: flex; flex-direction: column; gap: 10px;
    }
    .item-meta { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .badge { background: #2a2a2a; border-radius: 6px; padding: 4px 10px; font-size: 0.82rem; color: #ccc; }
    .conf { font-size: 0.82rem; }
    .conf.low  { color: #ef9a9a; }
    .conf.mid  { color: #ffcc80; }
    .conf.high { color: #a5d6a7; }
    .ts { color: #666; font-size: 0.78rem; }
    .btns { display: flex; gap: 10px; }
    button {
      flex: 1; padding: 10px 0; border: none; border-radius: 8px;
      font-size: 0.95rem; font-weight: 600; cursor: pointer; transition: opacity 0.15s;
    }
    button:active { opacity: 0.7; }
    .btn-approve { background: #2e7d32; color: #fff; }
    .btn-reject  { background: #c62828; color: #fff; }
    .decided { opacity: 0.4; pointer-events: none; }
    .decided-label {
      font-size: 0.82rem; font-weight: 600; text-align: center;
      padding: 8px; border-radius: 6px;
    }
    .decided-label.approved { background: #1b5e20; color: #a5d6a7; }
    .decided-label.rejected { background: #4e0000; color: #ef9a9a; }
    #status-bar {
      position: fixed; top: 0; left: 0; right: 0;
      background: #333; color: #fff; font-size: 0.85rem;
      text-align: center; padding: 8px; display: none; z-index: 999;
    }
    #status-bar.error { background: #b71c1c; }
    #status-bar.ok    { background: #1b5e20; }
    .pending-count {
      display: inline-block; background: #ef9a9a; color: #111;
      border-radius: 999px; padding: 1px 9px;
      font-size: 0.78rem; font-weight: 700; margin-left: 8px;
    }
  </style>
</head>
<body>
<div id="status-bar"></div>
<h1>🔍 Review Queue <span id="count-badge" class="pending-count" style="display:none"></span></h1>
<p class="subtitle">Low-confidence detections — approve to count them, reject to discard.</p>
<div id="items-container"><div class="empty">Loading…</div></div>

<script>
const KEY = new URLSearchParams(location.search).get('key') || '';
const CLASS_LABELS = {1:'🚲 Bicycle',2:'🚗 Car',3:'🛵 Moped',5:'🚌 Bus',7:'🚛 Truck',100:'🛴 Scooter'};

function showStatus(msg, type='ok', ms=2500) {
  const bar = document.getElementById('status-bar');
  bar.textContent = msg; bar.className = type; bar.style.display = 'block';
  setTimeout(() => { bar.style.display = 'none'; }, ms);
}
function confClass(c) { return c < 0.45 ? 'low' : c < 0.60 ? 'mid' : 'high'; }
function formatTs(ts) {
  try { return new Date(ts.endsWith('Z') ? ts : ts + 'Z').toLocaleString(); } catch { return ts; }
}

function renderItems(items) {
  const container = document.getElementById('items-container');
  const badge = document.getElementById('count-badge');
  const pending = items.filter(i => i.status === 'pending');
  badge.textContent = pending.length;
  badge.style.display = pending.length ? 'inline-block' : 'none';
  if (!items.length) { container.innerHTML = '<div class="empty">✅ No items to review</div>'; return; }
  container.innerHTML = items.map(item => {
    const label = CLASS_LABELS[item.class_id] || ('class ' + item.class_id);
    const confPct = Math.round(item.confidence * 100);
    const decided = item.status !== 'pending';
    return `<div class="item ${decided ? 'decided' : ''}" id="item-${item.local_id}">
      <div class="item-meta">
        <span class="badge">${label}</span>
        <span class="badge">${item.direction === 'in' ? '⬆ In' : '⬇ Out'}</span>
        <span class="conf ${confClass(item.confidence)}">${confPct}% confidence</span>
        <span class="ts">${formatTs(item.ts)}</span>
      </div>
      ${decided
        ? `<div class="decided-label ${item.status}">${item.status === 'approved' ? '✅ Approved' : '❌ Rejected'}</div>`
        : `<div class="btns">
             <button class="btn-approve" onclick="decide(${item.local_id},'approved')">✅ Approve</button>
             <button class="btn-reject"  onclick="decide(${item.local_id},'rejected')">❌ Reject</button>
           </div>`
      }
    </div>`;
  }).join('');
}

async function loadItems() {
  try {
    const res = await fetch('/api/review/pending?key=' + encodeURIComponent(KEY));
    if (res.status === 401) {
      document.getElementById('items-container').innerHTML = '<div class="empty">🔒 Invalid or missing key</div>';
      return;
    }
    renderItems(await res.json());
  } catch(e) {
    document.getElementById('items-container').innerHTML = '<div class="empty">⚠️ Failed to load</div>';
  }
}

async function decide(localId, decision) {
  const el = document.getElementById('item-' + localId);
  if (el) el.style.opacity = '0.5';
  try {
    const res = await fetch('/api/review/decide', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: KEY, local_id: localId, decision})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.status);
    showStatus(decision === 'approved' ? '✅ Approved' : '❌ Rejected', 'ok');
    if (el) {
      el.classList.add('decided');
      const btns = el.querySelector('.btns');
      if (btns) {
        const lbl = document.createElement('div');
        lbl.className = 'decided-label ' + decision;
        lbl.textContent = decision === 'approved' ? '✅ Approved' : '❌ Rejected';
        btns.replaceWith(lbl);
      }
    }
    loadItems();
  } catch(e) {
    if (el) el.style.opacity = '1';
    showStatus('Error: ' + e.message, 'error', 4000);
  }
}

loadItems();
setInterval(loadItems, 60 * 1000);
</script>
</body>
</html>
"""


# ── Review routes ─────────────────────────────────────────────────────────────

@app.route("/review")
def review_page():
    """Human review UI — protected by REVIEW_API_KEY via ?key= query param."""
    if not check_review_key():
        return "Unauthorized — add ?key=YOUR_REVIEW_KEY to the URL", 401
    return render_template_string(REVIEW_HTML)


@app.route("/api/review/push", methods=["POST"])
def api_review_push():
    """Receive review_queue items from the local counter (metadata only, no images).
    Auth: X-API-Key (PUSH_API_KEY). Body: {events: [{local_id, ts, class_id, ...}]}
    """
    if not check_push_key():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        events = request.get_json(force=True)["events"]
        if not isinstance(events, list):
            raise ValueError
    except Exception:
        return jsonify({"error": "Malformed request body"}), 400

    required = {"local_id", "ts", "class_id", "track_id", "direction", "confidence"}
    conn = get_conn(); cur = conn.cursor(); upserted = 0
    try:
        for ev in events:
            if not required.issubset(ev.keys()):
                continue
            try:
                if USE_POSTGRES:
                    cur.execute(
                        "INSERT INTO review_queue (local_id,ts,class_id,track_id,direction,confidence,status) "
                        "VALUES (%s,%s,%s,%s,%s,%s,'pending') ON CONFLICT (local_id) DO NOTHING",
                        (int(ev["local_id"]), ev["ts"], int(ev["class_id"]), int(ev["track_id"]),
                         str(ev["direction"]), float(ev["confidence"]))
                    )
                else:
                    cur.execute(
                        "INSERT OR IGNORE INTO review_queue (local_id,ts,class_id,track_id,direction,confidence,status) "
                        "VALUES (?,?,?,?,?,?,'pending')",
                        (int(ev["local_id"]), ev["ts"], int(ev["class_id"]), int(ev["track_id"]),
                         str(ev["direction"]), float(ev["confidence"]))
                    )
                if cur.rowcount > 0:
                    upserted += 1
            except Exception:
                pass
        conn.commit()
    finally:
        cur.close(); conn.close()
    return jsonify({"upserted": upserted}), 200


@app.route("/api/review/pending")
def api_review_pending():
    """Return review items for the UI. Auth: ?key=REVIEW_API_KEY."""
    if not check_review_key():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute(
            "SELECT local_id, ts, class_id, track_id, direction, confidence, status "
            "FROM review_queue ORDER BY local_id DESC LIMIT 200"
        )
        rows = cur.fetchall(); cur.close(); conn.close()
        return jsonify([
            {"local_id": r[0], "ts": r[1], "class_id": r[2], "track_id": r[3],
             "direction": r[4], "confidence": r[5], "status": r[6]}
            for r in rows
        ])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/review/decide", methods=["POST"])
def api_review_decide():
    """Record a human decision. Body: {key, local_id, decision}. Auth: key in body or header."""
    if not check_review_key():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        body = request.get_json(force=True)
        local_id = int(body["local_id"])
        decision = str(body["decision"])
        if decision not in ("approved", "rejected"):
            raise ValueError("bad decision")
    except Exception as e:
        return jsonify({"error": f"Bad request: {e}"}), 400

    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = get_conn(); cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("UPDATE review_queue SET status=%s, decided_at=%s WHERE local_id=%s",
                        (decision, now, local_id))
        else:
            cur.execute("UPDATE review_queue SET status=?, decided_at=? WHERE local_id=?",
                        (decision, now, local_id))
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"error": "Item not found"}), 404
        conn.commit(); cur.close(); conn.close()
        return jsonify({"ok": True, "local_id": local_id, "decision": decision}), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/review/decisions")
def api_review_decisions():
    """Asus polls this for decisions to apply locally. Auth: X-API-Key (PUSH_API_KEY)."""
    if not check_push_key():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute(
            "SELECT local_id, status, decided_at FROM review_queue "
            "WHERE status != 'pending' ORDER BY local_id"
        )
        rows = cur.fetchall(); cur.close(); conn.close()
        return jsonify([{"local_id": r[0], "status": r[1], "decided_at": r[2]} for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
