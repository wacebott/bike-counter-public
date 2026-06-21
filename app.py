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

CREATE TABLE IF NOT EXISTS recipients (
    id SERIAL PRIMARY KEY,
    name TEXT,
    phone TEXT,
    position INT
);
CREATE INDEX IF NOT EXISTS idx_recipients_position ON recipients (position);
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

CREATE TABLE IF NOT EXISTS sundowner_recipients (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    number TEXT NOT NULL
);
"""

INIT_SQL_SQLITE = INIT_SQL_SQLITE.rstrip() + """

CREATE TABLE IF NOT EXISTS sundowner_recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    number TEXT NOT NULL
);
"""

INIT_SQL_SQLITE = INIT_SQL_SQLITE.rstrip() + """

CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    phone TEXT,
    position INT
);
CREATE INDEX IF NOT EXISTS idx_recipients_position ON recipients (position);
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
  <title>🚲 Bike Lane Counter</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #111;
      color: #e0e0e0;
      font-family: 'Segoe UI', system-ui, sans-serif;
      min-height: 100vh;
      padding: 24px 16px 48px;
    }
    header { text-align: center; margin-bottom: 28px; }
    header h1 { font-size: 1.8rem; font-weight: 700; }
    header p.subtitle { margin-top: 6px; color: #888; font-size: 0.85rem; }

    /* ── Stat cards ── */
    .stat-row {
      display: flex; gap: 14px; justify-content: center;
      margin-bottom: 28px; flex-wrap: wrap;
    }
    .stat {
      background: #1e1e1e; border-radius: 10px;
      padding: 14px 24px; text-align: center; min-width: 130px;
    }
    .stat .value { font-size: 2rem; font-weight: 700; color: #4fc3f7; }
    .stat .value.bike-pct { color: #81c784; }
    .stat .label {
      font-size: 0.72rem; color: #888;
      text-transform: uppercase; letter-spacing: 0.06em; margin-top: 4px;
    }

    /* ── Section headings ── */
    .section-title {
      font-size: 0.78rem; font-weight: 600; color: #888;
      text-transform: uppercase; letter-spacing: 0.08em;
      margin: 0 0 12px 0;
    }

    /* ── Hourly grid ── */
    .grid-wrap {
      max-width: 1300px; margin: 0 auto 32px;
      background: #1e1e1e; border-radius: 12px;
      padding: 20px 18px; overflow-x: auto;
      box-shadow: 0 2px 12px rgba(0,0,0,0.4);
    }
    table.hgrid {
      border-collapse: collapse;
      width: 100%;
      font-size: 0.82rem;
      white-space: nowrap;
    }
    table.hgrid th, table.hgrid td {
      padding: 6px 8px;
      text-align: right;
      border-bottom: 1px solid #2a2a2a;
    }
    table.hgrid th { color: #888; font-weight: 500; }
    table.hgrid td.type-label {
      text-align: left; font-weight: 600; color: #ccc;
      padding-right: 16px; white-space: nowrap;
    }
    table.hgrid tr.total-row td {
      border-top: 2px solid #333; font-weight: 700; color: #e0e0e0;
      padding-top: 8px;
    }
    /* heat-map colouring applied via inline style */
    td.heat { border-radius: 4px; }

    /* ── Vehicle expand/collapse ── */
    tr.group-vehicle td.type-label { cursor: pointer; user-select: none; }
    tr.group-vehicle td.type-label:hover { color: #fff; }
    tr.group-vehicle .expand-icon { display: inline-block; margin-right: 5px; transition: transform 0.15s; font-style: normal; font-size: 0.65rem; vertical-align: middle; }
    tr.group-vehicle.expanded .expand-icon { transform: rotate(90deg); }
    tr.vehicle-sub { display: none; }
    tr.vehicle-sub td { border-bottom-color: #1a1a1a !important; }
    tr.vehicle-sub td.type-label { padding-left: 26px; font-weight: 400; color: #888; font-size: 0.78rem; }
    tr.vehicle-sub.visible { display: table-row; }

    /* ── Daily summary table ── */
    .daily-wrap {
      max-width: 1300px; margin: 0 auto 32px;
      background: #1e1e1e; border-radius: 12px;
      padding: 20px 18px; overflow-x: auto;
      box-shadow: 0 2px 12px rgba(0,0,0,0.4);
    }
    table.dtable {
      border-collapse: collapse; width: 100%;
      font-size: 0.82rem; white-space: nowrap;
    }
    table.dtable th, table.dtable td {
      padding: 6px 12px;
      text-align: right;
      border-bottom: 1px solid #2a2a2a;
    }
    table.dtable th { color: #888; font-weight: 500; }
    table.dtable td:first-child { text-align: left; color: #aaa; }
    table.dtable td.bike-pct-cell { color: #81c784; font-weight: 600; }

    footer { text-align: center; margin-top: 40px; color: #555; font-size: 0.8rem; }

    /* ── Light mode ── */
    body.light { background: #f5f5f5; color: #111; }
    body.light .stat { background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
    body.light .grid-wrap, body.light .daily-wrap { background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
    body.light table.hgrid th, body.light table.dtable th { color: #555; }
    body.light table.hgrid th, body.light table.hgrid td,
    body.light table.dtable th, body.light table.dtable td { border-bottom-color: #e0e0e0; }
    body.light table.hgrid tr.total-row td { border-top-color: #ccc; color: #111; }
    body.light table.hgrid td.type-label, body.light table.dtable td:first-child { color: #333; }
    body.light footer { color: #aaa; }
    body.light header p.subtitle { color: #666; }
    body.light .section-title { color: #666; }

    #theme-btn {
      position: fixed; top: 60px; right: 16px;
      background: #2a2a2a; border: none; border-radius: 20px;
      color: #e0e0e0; font-size: 0.82rem; padding: 6px 14px;
      cursor: pointer; z-index: 100; transition: background 0.2s;
    }
    body.light #theme-btn { background: #ddd; color: #333; }
  </style>
</head>
<body>

<button id="theme-btn" onclick="toggleTheme()">☀️ Light</button>

<header>
  <h1>🚲 Bike Lane Counter</h1>
  <p class="subtitle">Updated every 5 minutes</p>
</header>

<div class="stat-row">
  <div class="stat">
    <div class="value" id="stat-bikes-today">—</div>
    <div class="label">Bikes Today</div>
  </div>
  <div class="stat">
    <div class="value bike-pct" id="stat-bike-pct">—</div>
    <div class="label">Bike % of Traffic Today</div>
  </div>
  <div class="stat">
    <div class="value" id="stat-total-today">—</div>
    <div class="label">All Traffic Today</div>
  </div>
  <div class="stat">
    <div class="value" id="stat-bikes-week">—</div>
    <div class="label">Bikes This Week</div>
  </div>
</div>

<div class="grid-wrap">
  <p class="section-title">Hourly counts</p>
  <div id="hourly-grid">Loading…</div>
</div>

<div class="daily-wrap">
  <p class="section-title">Daily summary — last 30 days</p>
  <div id="daily-table">Loading…</div>
</div>

<footer>Data refreshes automatically &bull; Powered by YOLO detection</footer>

<script>
const CLASS_INFO = [
  { id: 1,   label: '🚲 Bicycle',    color: '#4caf50' },
  { id: 99,  label: '🚗 Vehicle',    color: '#2196f3' },
  { id: 0,   label: '🚶 Pedestrian', color: '#ff9800' },
];
const CLASS_IDS = CLASS_INFO.map(c => c.id);
const CLASS_MAP = Object.fromEntries(CLASS_INFO.map(c => [c.id, c]));

// Vehicle sub-class info for drill-down
const VEHICLE_SUB = {
  2:   { label: '🚗 Car',     color: '#2196f3' },
  3:   { label: '🛵 Moped',   color: '#00bcd4' },
  5:   { label: '🚌 Bus',     color: '#ff9800' },
  7:   { label: '🚛 Truck',   color: '#f44336' },
  100: { label: '🛴 Scooter', color: '#9c27b0' },
};

// Aggregate rows: {label→{class_id→count}} — API consolidates to 1=Bicycle, 0=Pedestrian, 99=Vehicle
function aggregate(rows) {
  const m = {};
  rows.forEach(r => {
    if (!m[r.label]) m[r.label] = {};
    const cid = r.class_id === 1 ? 1 : r.class_id === 0 ? 0 : 99;
    m[r.label][cid] = (m[r.label][cid] || 0) + r.count;
  });
  return m;
}

// Which class IDs actually appear in data?
function activeClasses(agg) {
  const seen = new Set();
  Object.values(agg).forEach(h => Object.keys(h).forEach(id => seen.add(Number(id))));
  return CLASS_IDS.filter(id => seen.has(id));
}

// Simple heat colour: 0 → transparent, max → full colour
function heatStyle(val, max, hexColor) {
  if (!val || !max) return '';
  const alpha = 0.15 + 0.65 * (val / max);
  const r = parseInt(hexColor.slice(1,3),16);
  const g = parseInt(hexColor.slice(3,5),16);
  const b = parseInt(hexColor.slice(5,7),16);
  return `background:rgba(${r},${g},${b},${alpha.toFixed(2)});color:#fff;`;
}

function buildHourlyGrid(rows) {
  const agg = aggregate(rows);
  const hours = Array.from({length: 24}, (_, i) => String(i).padStart(2,'0') + ':00');
  const hasAny = hours.some(h => agg[h] && Object.keys(agg[h]).length > 0);
  if (!hasAny) return '<p style="color:#555;padding:20px 0">No data yet</p>';

  // Row order: Bicycle, Vehicles, Pedestrian
  const ROWS = [
    { id: 1,  label: '🚲 Bicycle',    color: '#4caf50', cls: '' },
    { id: 99, label: '🚗 Vehicles',   color: '#2196f3', cls: 'group-vehicle' },
    { id: 0,  label: '🚶 Pedestrian', color: '#ff9800', cls: '' },
  ];
  const maxPerRow = {};
  ROWS.forEach(r => {
    maxPerRow[r.id] = Math.max(...hours.map(h => (agg[h] && agg[h][r.id]) || 0), 1);
  });

  let html = '<table class="hgrid"><thead><tr><th></th>';
  hours.forEach(h => { html += `<th>${h}</th>`; });
  html += '<th style="color:#aaa">Total</th></tr></thead><tbody>';

  ROWS.forEach(row => {
    const rowTotal = hours.reduce((s, h) => s + ((agg[h] && agg[h][row.id]) || 0), 0);
    const expandIcon = row.id === 99 ? '<i class="expand-icon">▶</i>' : '';
    const onclick    = row.id === 99 ? 'onclick="toggleVehicles(this)"' : '';
    html += `<tr class="${row.cls}" ${onclick}>`;
    html += `<td class="type-label">${expandIcon}${row.label}</td>`;
    hours.forEach(h => {
      const v = (agg[h] && agg[h][row.id]) || 0;
      const style = v ? heatStyle(v, maxPerRow[row.id], row.color) : '';
      html += `<td class="heat" style="${style}">${v || '·'}</td>`;
    });
    html += `<td style="color:#aaa">${rowTotal || '·'}</td></tr>`;

    // Placeholder sub-rows for vehicles (populated async on expand)
    if (row.id === 99) {
      Object.entries(VEHICLE_SUB).forEach(([id, info]) => {
        html += `<tr class="vehicle-sub" data-vid="${id}">`;
        html += `<td class="type-label">${info.label}</td>`;
        hours.forEach(() => { html += '<td class="heat">·</td>'; });
        html += '<td style="color:#666">·</td></tr>';
      });
    }
  });

  // Total row
  html += '<tr class="total-row"><td class="type-label">Total</td>';
  let grandTotal = 0;
  hours.forEach(h => {
    const colTotal = ROWS.reduce((s, r) => s + ((agg[h] && agg[h][r.id]) || 0), 0);
    grandTotal += colTotal;
    html += `<td>${colTotal || '·'}</td>`;
  });
  html += `<td>${grandTotal}</td></tr>`;
  html += '</tbody></table>';
  return html;
}

let _vehicleDetailCache = null;

async function toggleVehicles(labelCell) {
  const row = labelCell.closest('tr');
  const subRows = document.querySelectorAll('tr.vehicle-sub');
  const isOpen  = row.classList.contains('expanded');

  if (!isOpen) {
    // Fetch detail if not yet loaded
    if (!_vehicleDetailCache) {
      try {
        _vehicleDetailCache = await fetch('/api/hourly/vehicles').then(r => r.json());
      } catch(e) { _vehicleDetailCache = []; }
    }
    // Build per-class per-hour lookup
    const hours = Array.from({length: 24}, (_, i) => String(i).padStart(2,'0') + ':00');
    const detail = {};  // class_id → {hour → count}
    _vehicleDetailCache.forEach(r => {
      if (!detail[r.class_id]) detail[r.class_id] = {};
      detail[r.class_id][r.label] = (detail[r.class_id][r.label] || 0) + r.count;
    });
    // Compute max per sub-class for heat scaling
    const maxSub = {};
    Object.entries(detail).forEach(([id, hmap]) => {
      maxSub[id] = Math.max(...Object.values(hmap), 1);
    });
    // Populate each sub-row
    subRows.forEach(subRow => {
      const vid = parseInt(subRow.dataset.vid);
      const info = VEHICLE_SUB[vid] || { color: '#888' };
      const cells = subRow.querySelectorAll('td.heat');
      const totalCell = subRow.querySelector('td:last-child');
      let rowTotal = 0;
      cells.forEach((cell, i) => {
        const v = (detail[vid] && detail[vid][hours[i]]) || 0;
        rowTotal += v;
        cell.style.cssText = v ? heatStyle(v, maxSub[vid] || 1, info.color) : '';
        cell.textContent = v || '·';
      });
      // Only show sub-rows that have data
      subRow.style.display = rowTotal > 0 ? '' : 'none';
      if (totalCell) totalCell.textContent = rowTotal || '·';
      subRow.classList.toggle('visible', rowTotal > 0);
    });
  } else {
    subRows.forEach(r => { r.classList.remove('visible'); r.style.display = ''; });
  }
  row.classList.toggle('expanded', !isOpen);
}

function buildDailyTable(rows) {
  const agg = aggregate(rows);
  const days = Object.keys(agg).sort().reverse(); // newest first
  const classes = activeClasses(agg);
  if (!days.length) return '<p style="color:#555;padding:20px 0">No data yet</p>';

  let html = '<table class="dtable"><thead><tr><th>Date</th>';
  classes.forEach(id => {
    const info = CLASS_MAP[id] || { label: 'Class ' + id };
    html += `<th>${info.label}</th>`;
  });
  html += '<th>Total</th><th>🚲 Bike %</th></tr></thead><tbody>';

  days.forEach(day => {
    const rowTotal = classes.reduce((s, id) => s + (agg[day][id] || 0), 0);
    const bikes = agg[day][1] || 0;
    const pct = rowTotal ? ((bikes / rowTotal) * 100).toFixed(1) + '%' : '—';
    html += `<tr><td>${day}</td>`;
    classes.forEach(id => {
      html += `<td>${agg[day][id] || 0}</td>`;
    });
    html += `<td>${rowTotal}</td><td class="bike-pct-cell">${pct}</td></tr>`;
  });
  html += '</tbody></table>';
  return html;
}

async function fetchAndRender() {
  try {
    const [hRes, dRes] = await Promise.all([
      fetch('/api/hourly'),
      fetch('/api/daily'),
    ]);
    const hourly = await hRes.json();
    const daily  = await dRes.json();

    document.getElementById('hourly-grid').innerHTML = buildHourlyGrid(hourly);
    document.getElementById('daily-table').innerHTML = buildDailyTable(daily);

    // Stat cards — today = last date in daily
    const todayAgg = aggregate(daily);
    const todayKey = Object.keys(todayAgg).sort().pop();
    const todayCounts = todayAgg[todayKey] || {};
    const todayBikes = todayCounts[1] || 0;
    const todayTotal = Object.values(todayCounts).reduce((a,b)=>a+b, 0);
    const todayPct   = todayTotal ? ((todayBikes/todayTotal)*100).toFixed(1)+'%' : '—';

    // Week bikes
    const sortedDays = Object.keys(todayAgg).sort();
    const weekDays = sortedDays.slice(-7);
    const weekBikes = weekDays.reduce((s, d) => s + (todayAgg[d][1] || 0), 0);

    document.getElementById('stat-bikes-today').textContent = todayBikes.toLocaleString();
    document.getElementById('stat-bike-pct').textContent    = todayPct;
    document.getElementById('stat-total-today').textContent = todayTotal.toLocaleString();
    document.getElementById('stat-bikes-week').textContent  = weekBikes.toLocaleString();

  } catch(e) {
    console.error('Fetch error', e);
  }
}

fetchAndRender();
setInterval(fetchAndRender, 5 * 60 * 1000);

function toggleTheme() {
  const light = document.body.classList.toggle('light');
  document.getElementById('theme-btn').textContent = light ? '🌙 Dark' : '☀️ Light';
  localStorage.setItem('theme', light ? 'light' : 'dark');
}
// Restore saved preference
if (localStorage.getItem('theme') === 'light') {
  document.body.classList.add('light');
  document.getElementById('theme-btn').textContent = '🌙 Dark';
}
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
                    to_char(ts::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'America/Halifax', 'HH24:00') AS label,
                    direction,
                    class_id,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE (ts::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'America/Halifax')::date
                      = (NOW() AT TIME ZONE 'America/Halifax')::date
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
        return jsonify(_consolidate_rows(rows))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/hourly/vehicles")
def api_hourly_vehicles():
    """Return per-class vehicle breakdown for today — used by the expand drill-down."""
    VEHICLE_CLASS_IDS = (2, 3, 5, 7, 100)
    try:
        conn = get_conn()
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""
                SELECT
                    to_char(ts::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'America/Halifax', 'HH24:00') AS label,
                    direction,
                    class_id,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE (ts::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'America/Halifax')::date
                      = (NOW() AT TIME ZONE 'America/Halifax')::date
                  AND class_id = ANY(%s)
                GROUP BY label, direction, class_id
                ORDER BY label
            """, (list(VEHICLE_CLASS_IDS),))
        else:
            placeholders = ",".join("?" * len(VEHICLE_CLASS_IDS))
            cur.execute(f"""
                SELECT
                    strftime('%H:00', ts) AS label,
                    direction,
                    class_id,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE ts >= datetime('now', '-24 hours')
                  AND class_id IN ({placeholders})
                GROUP BY label, direction, class_id
                ORDER BY label
            """, VEHICLE_CLASS_IDS)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        merged = {}
        for label, direction, class_id, cnt in rows:
            key = (label, int(class_id))
            merged[key] = merged.get(key, 0) + cnt
        result = [
            {"label": lbl, "class_id": cid, "count": c}
            for (lbl, cid), c in sorted(merged.items())
        ]
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _consolidate_rows(rows):
    """Merge raw DB rows into Bicycle (1) + Pedestrian (0) + Vehicle (99).
    Each row is (label, direction, class_id, count).
    Returns list of dicts with keys: label, direction, class_id, count.
    """
    merged = {}  # (label, direction, consolidated_class_id) → count
    for label, direction, class_id, cnt in rows:
        if class_id == 1:
            cid = 1    # Bicycle
        elif class_id == 0:
            cid = 0    # Pedestrian
        else:
            cid = 99   # Vehicle (car, moped, bus, truck, scooter, etc.)
        key = (label, direction, cid)
        merged[key] = merged.get(key, 0) + cnt
    return [
        {"label": lbl, "direction": d, "class_id": cid, "count": c}
        for (lbl, d, cid), c in sorted(merged.items())
    ]


@app.route("/api/daily")
def api_daily():
    """Return counts grouped by date, direction, and class_id for the last 30 days."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""
                SELECT
                    to_char(ts::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'America/Halifax', 'YYYY-MM-DD') AS label,
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
        return jsonify(_consolidate_rows(rows))
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
  try {
    return new Date(ts.endsWith('Z') ? ts : ts + 'Z').toLocaleString('en-CA', {
      timeZone: 'America/Halifax', year: 'numeric', month: 'short',
      day: 'numeric', hour: '2-digit', minute: '2-digit'
    });
  } catch { return ts; }
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


# ── Recipients API (sundowner SMS list, Tailscale-trusted, no auth) ───────────

@app.route("/api/recipients", methods=["GET"])
def api_recipients_get():
    """Return recipients ordered by position. No auth — Tailscale-trusted only."""
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT name, phone, position FROM recipients ORDER BY position")
        rows = cur.fetchall(); cur.close(); conn.close()
        return jsonify([{"name": r[0], "phone": r[1], "position": r[2]} for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/recipients", methods=["POST"])
def api_recipients_post():
    """Replace all recipients. Accepts [{name, phone, position}]. No auth — Tailscale-trusted."""
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, list):
        return jsonify({"error": "Expected a JSON array"}), 400
    try:
        conn = get_conn(); cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("DELETE FROM recipients")
            for r in data:
                cur.execute(
                    "INSERT INTO recipients (name, phone, position) VALUES (%s, %s, %s)",
                    (r.get("name", ""), r.get("phone", ""), int(r.get("position", 0)))
                )
        else:
            cur.execute("DELETE FROM recipients")
            for r in data:
                cur.execute(
                    "INSERT INTO recipients (name, phone, position) VALUES (?, ?, ?)",
                    (r.get("name", ""), r.get("phone", ""), int(r.get("position", 0)))
                )
        conn.commit(); cur.close(); conn.close()
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


# ── Stats page ─────────────────────────────────────────────────────────────────

STATS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>📊 Traffic Stats — Bike Lane Counter</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #111; color: #e0e0e0;
      font-family: 'Segoe UI', system-ui, sans-serif;
      min-height: 100vh; padding: 24px 16px 48px;
    }
    header { text-align: center; margin-bottom: 28px; }
    header h1 { font-size: 1.8rem; font-weight: 700; }
    header p.subtitle { margin-top: 6px; color: #888; font-size: 0.85rem; }
    a.back-link {
      display: inline-block; margin-bottom: 20px;
      color: #4fc3f7; text-decoration: none; font-size: 0.85rem;
    }
    a.back-link:hover { text-decoration: underline; }

    .section-title {
      font-size: 0.78rem; font-weight: 600; color: #888;
      text-transform: uppercase; letter-spacing: 0.08em; margin: 0 0 14px 0;
    }
    .card {
      max-width: 1300px; margin: 0 auto 32px;
      background: #1e1e1e; border-radius: 12px;
      padding: 20px 18px;
      box-shadow: 0 2px 12px rgba(0,0,0,0.4);
    }
    .chart-wrap { position: relative; height: 280px; }

    /* daily table */
    table.dtable {
      border-collapse: collapse; width: 100%;
      font-size: 0.82rem; white-space: nowrap; margin-top: 20px;
    }
    table.dtable th, table.dtable td {
      padding: 7px 12px; text-align: right;
      border-bottom: 1px solid #2a2a2a;
    }
    table.dtable th { color: #888; font-weight: 500; }
    table.dtable td:first-child { text-align: left; }
    table.dtable tr.clickable { cursor: pointer; }
    table.dtable tr.clickable:hover td { background: #252525; }
    table.dtable tr.selected td { background: #1a2a3a !important; }
    td.bike-pct-cell { color: #81c784; font-weight: 600; }
    td.date-cell { color: #4fc3f7; }

    /* filter buttons */
    .filter-row {
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 16px;
    }
    .filter-row label { font-size: 0.82rem; color: #888; margin-right: 4px; }
    .filter-btn {
      background: #2a2a2a; border: 1px solid #444; border-radius: 20px;
      color: #aaa; font-size: 0.8rem; padding: 5px 14px; cursor: pointer;
      transition: all 0.15s;
    }
    .filter-btn.active { border-color: currentColor; color: #fff; }
    .filter-btn.bike.active  { background: #1b3a1f; border-color: #4caf50; color: #4caf50; }
    .filter-btn.veh.active   { background: #0d1f3c; border-color: #2196f3; color: #2196f3; }
    .filter-btn.both.active  { background: #2a2a2a; border-color: #aaa;    color: #eee; }
    .filter-btn.avg.active   { background: #3a2a1a; border-color: #ff9800; color: #ff9800; }
    .filter-btn.ped.active   { background: #2a1f3a; border-color: #ff9800; color: #ff9800; }
    /* date checkboxes */
    .date-checks {
      display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; max-height: 110px;
      overflow-y: auto; padding: 4px 0;
    }
    .date-check-label {
      display: flex; align-items: center; gap: 5px;
      font-size: 0.78rem; background: #2a2a2a; border: 1px solid #444;
      border-radius: 16px; padding: 3px 10px; cursor: pointer; user-select: none;
      transition: border-color 0.15s;
    }
    .date-check-label input { display: none; }
    .date-check-label.checked { border-color: #4fc3f7; color: #4fc3f7; background: #0d2030; }
    .date-check-label .dot {
      width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
    }
    .bulk-btns { display: flex; gap: 8px; margin-bottom: 10px; }
    .bulk-btn {
      background: none; border: 1px solid #444; border-radius: 14px;
      color: #888; font-size: 0.75rem; padding: 3px 10px; cursor: pointer;
    }
    .bulk-btn:hover { border-color: #888; color: #ccc; }
    /* heatmap */
    table.hmap {
      border-collapse: collapse; width: 100%; font-size: 0.73rem; white-space: nowrap;
    }
    table.hmap th, table.hmap td {
      padding: 4px 5px; text-align: center; border-bottom: 1px solid #1a1a1a;
    }
    table.hmap th { color: #666; font-weight: 400; }
    table.hmap td.row-label {
      text-align: left; color: #aaa; padding-right: 10px; font-size: 0.72rem;
    }
    td.hcell { border-radius: 3px; min-width: 28px; }
    .legend { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 10px; }
    .legend-item { display: flex; align-items: center; gap: 6px; font-size: 0.82rem; }
    .legend-dot { width: 12px; height: 12px; border-radius: 50%; }

    /* hourly data grid */
    table.hgrid {
      border-collapse: collapse; width: 100%;
      font-size: 0.8rem; white-space: nowrap; margin-top: 18px; overflow-x: auto;
    }
    table.hgrid th, table.hgrid td {
      padding: 5px 8px; text-align: right;
      border-bottom: 1px solid #2a2a2a;
    }
    table.hgrid th { color: #888; font-weight: 500; }
    table.hgrid td.type-label { text-align: left; font-weight: 600; color: #ccc; padding-right: 14px; }
    table.hgrid tr.total-row td { border-top: 2px solid #333; font-weight: 700; padding-top: 8px; }
    td.heat { border-radius: 4px; }

    /* theme btn */
    #theme-btn {
      position: fixed; top: 60px; right: 16px;
      background: #2a2a2a; border: none; border-radius: 20px;
      color: #e0e0e0; font-size: 0.82rem; padding: 6px 14px;
      cursor: pointer; z-index: 100;
    }
    body.light { background: #f5f5f5; color: #111; }
    body.light .card { background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
    body.light table.dtable th, body.light table.hgrid th { color: #555; }
    body.light table.dtable th, body.light table.dtable td,
    body.light table.hgrid th, body.light table.hgrid td { border-bottom-color: #e0e0e0; }
    body.light table.hgrid tr.total-row td { border-top-color: #ccc; }
    body.light #theme-btn { background: #ddd; color: #333; }
    body.light .section-title { color: #666; }
    body.light .filter-row label { color: #555; }
    body.light .filter-btn { background: #f0f0f0; border-color: #ccc; color: #555; }
    body.light .date-check-label { background: #f0f0f0; border-color: #ccc; color: #444; }
    body.light .date-check-label.checked { background: #e3f2fd; border-color: #2196f3; color: #1565c0; }
    body.light .bulk-btn { border-color: #ccc; color: #666; }
    body.light table.hmap th { color: #888; }
    body.light table.hmap td.row-label { color: #555; }
    body.light table.hmap { border-color: #eee; }
  </style>
</head>
<body>

<button id="theme-btn" onclick="toggleTheme()">☀️ Light</button>

<header>
  <h1>📊 Traffic Stats</h1>
  <p class="subtitle">Bicycle vs Vehicle — by date and hour</p>
</header>

<div style="max-width:1300px;margin:0 auto 8px;">
  <a class="back-link" href="/">← Back to live counter</a>
</div>

<!-- Section 1: Daily overview chart -->
<div class="card">
  <p class="section-title">Daily overview — all dates</p>
  <div class="legend">
    <div class="legend-item"><div class="legend-dot" style="background:#4caf50"></div>🚲 Bicycle</div>
    <div class="legend-item"><div class="legend-dot" style="background:#2196f3"></div>🚗 Vehicle</div>
  </div>
  <div class="chart-wrap"><canvas id="daily-chart"></canvas></div>
</div>

<!-- Section 2: Daily data table (click to drill down) -->
<div class="card">
  <p class="section-title">Daily summary — click a row to see hourly breakdown</p>
  <div id="daily-table-wrap" style="overflow-x:auto">Loading…</div>
</div>

<!-- Section 3: Heatmap -->
<div class="card">
  <p class="section-title">Hourly heatmap — all dates × all hours</p>
  <div class="filter-row">
    <label>Show:</label>
    <button class="filter-btn both active" id="hm-both" onclick="setHmFilter('both')">All</button>
    <button class="filter-btn bike" id="hm-bike" onclick="setHmFilter('bike')">🚲 Bicycles only</button>
    <button class="filter-btn veh"  id="hm-veh"  onclick="setHmFilter('veh')">🚗 Vehicles only</button>
    <button class="filter-btn ped"  id="hm-ped"  onclick="setHmFilter('ped')">🚶 Pedestrian only</button>
  </div>
  <div id="heatmap-wrap" style="overflow-x:auto">Loading…</div>
</div>

<!-- Section 4: Multi-date line chart -->
<div class="card" id="hourly-card">
  <p class="section-title">Hourly trends — compare dates</p>
  <div class="filter-row">
    <label>Show:</label>
    <button class="filter-btn both active" id="lc-both" onclick="setLcFilter('both')">Both</button>
    <button class="filter-btn bike" id="lc-bike" onclick="setLcFilter('bike')">🚲 Bicycles only</button>
    <button class="filter-btn veh"  id="lc-veh"  onclick="setLcFilter('veh')">🚗 Vehicles only</button>
    <button class="filter-btn avg"  id="lc-avg"  onclick="toggleAvg()">📈 Show average</button>
  </div>
  <div class="bulk-btns">
    <button class="bulk-btn" onclick="selectAllDates()">Select all</button>
    <button class="bulk-btn" onclick="clearAllDates()">Clear all</button>
    <button class="bulk-btn" onclick="selectRecentDates(7)">Last 7 days</button>
  </div>
  <div class="date-checks" id="date-checks"></div>
  <div class="chart-wrap" style="height:320px"><canvas id="hourly-chart"></canvas></div>
</div>

<footer style="text-align:center;margin-top:40px;color:#555;font-size:0.8rem">
  Powered by YOLO detection &bull; <a href="/" style="color:#4fc3f7">Live Counter</a>
</footer>

<script>
const BIKE_COLOR    = '#4caf50';
const VEHICLE_COLOR = '#2196f3';
const HOURS = Array.from({length:24}, (_,i) => String(i).padStart(2,'0')+':00');

let dailyChart  = null;
let hourlyChart = null;
let allDailyData   = {};   // date → {1: count, 99: count}
let allHourlyData  = {};   // date → hour → {1: count, 99: count}
let hmFilter = 'both';     // heatmap filter
let lcFilter = 'both';     // line chart filter
let showAvg  = false;

// palette for multi-date lines (bike / vehicle per date)
const PALETTE_BIKE = ['#4caf50','#80cbc4','#aed581','#26c6da','#9ccc65','#00bcd4','#c5e1a5','#b2dfdb'];
const PALETTE_VEH  = ['#2196f3','#7c4dff','#ff5722','#607d8b','#e91e63','#009688','#ff9800','#795548'];

// ── helpers ──────────────────────────────────────────────────────────────────
function aggregate(rows) {
  const m = {};
  rows.forEach(r => {
    if (!m[r.label]) m[r.label] = {};
    const cid = r.class_id === 1 ? 1 : 99;
    m[r.label][cid] = (m[r.label][cid] || 0) + r.count;
  });
  return m;
}

function heatStyle(val, max, hexColor) {
  if (!val || !max) return '';
  const alpha = 0.15 + 0.65 * (val / max);
  const r = parseInt(hexColor.slice(1,3),16);
  const g = parseInt(hexColor.slice(3,5),16);
  const b = parseInt(hexColor.slice(5,7),16);
  return `background:rgba(${r},${g},${b},${alpha.toFixed(2)});color:#fff;`;
}

// ── daily chart ───────────────────────────────────────────────────────────────
function renderDailyChart(agg) {
  const days = Object.keys(agg).sort();
  const bikes    = days.map(d => agg[d][1]  || 0);
  const vehicles = days.map(d => agg[d][99] || 0);

  const ctx = document.getElementById('daily-chart').getContext('2d');
  if (dailyChart) dailyChart.destroy();
  dailyChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: days,
      datasets: [
        { label: '🚲 Bicycle', data: bikes,    backgroundColor: BIKE_COLOR + 'cc',    borderRadius: 3, yAxisID: 'yBike' },
        { label: '🚗 Vehicle', data: vehicles, backgroundColor: VEHICLE_COLOR + 'cc', borderRadius: 3, yAxisID: 'yVeh' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#aaa' } } },
      scales: {
        x: { ticks: { color: '#888' }, grid: { color: '#222' } },
        yBike: {
          type: 'linear', position: 'right', beginAtZero: true,
          ticks: { color: BIKE_COLOR },
          grid: { color: '#222' },
          title: { display: true, text: '🚲 Bicycles', color: BIKE_COLOR, font: { size: 11 } },
        },
        yVeh: {
          type: 'linear', position: 'left', beginAtZero: true,
          ticks: { color: VEHICLE_COLOR },
          grid: { drawOnChartArea: false },
          title: { display: true, text: '🚗 Vehicles', color: VEHICLE_COLOR, font: { size: 11 } },
        },
      }
    }
  });
}

// ── daily table ───────────────────────────────────────────────────────────────
function renderDailyTable(agg) {
  const days = Object.keys(agg).sort().reverse();
  let html = `<table class="dtable"><thead><tr>
    <th>Date</th><th>🚲 Bicycle</th><th>🚗 Vehicle</th><th>Total</th><th>🚲 Bike %</th>
  </tr></thead><tbody>`;
  days.forEach(day => {
    const bikes = agg[day][1] || 0;
    const vehs  = agg[day][99] || 0;
    const total = bikes + vehs;
    const pct   = total ? ((bikes/total)*100).toFixed(1)+'%' : '—';
    html += `<tr class="clickable" onclick="selectDate('${day}')">
      <td class="date-cell">${day}</td>
      <td>${bikes.toLocaleString()}</td>
      <td>${vehs.toLocaleString()}</td>
      <td>${total.toLocaleString()}</td>
      <td class="bike-pct-cell">${pct}</td>
    </tr>`;
  });
  html += '</tbody></table>';
  document.getElementById('daily-table-wrap').innerHTML = html;
}

// ── heatmap filter ────────────────────────────────────────────────────────────
function setHmFilter(f) {
  hmFilter = f;
  ['both','bike','veh','ped'].forEach(x => document.getElementById('hm-'+x).classList.toggle('active', x===f));
  renderHeatmap();
}

// ── heatmap render ────────────────────────────────────────────────────────────
const PED_COLOR = '#ff9800';
function renderHeatmap() {
  const days = Object.keys(allHourlyData).sort();
  if (!days.length) { document.getElementById('heatmap-wrap').innerHTML = '<p style="color:#555;padding:20px">No data</p>'; return; }

  // compute values per cell and global max
  const cellVal = (date, hour) => {
    const d = allHourlyData[date][hour] || {};
    if (hmFilter === 'bike') return d[1]  || 0;
    if (hmFilter === 'veh')  return d[99] || 0;
    if (hmFilter === 'ped')  return d[0]  || 0;
    return (d[0] || 0) + (d[1] || 0) + (d[99] || 0);
  };
  const color = hmFilter === 'bike' ? BIKE_COLOR : hmFilter === 'veh' ? VEHICLE_COLOR : hmFilter === 'ped' ? PED_COLOR : '#90a4ae';
  const globalMax = Math.max(...days.flatMap(d => HOURS.map(h => cellVal(d, h))));

  let html = '<table class="hmap"><thead><tr><th></th>';
  HOURS.forEach(h => { html += `<th>${h.slice(0,2)}</th>`; });
  html += '<th style="color:#555">Σ</th></tr></thead><tbody>';

  days.slice().reverse().forEach(date => {
    const rowSum = HOURS.reduce((s,h) => s + cellVal(date,h), 0);
    const dow = new Date(date + 'T12:00:00').getDay(); // 0=Sun, 6=Sat
    const isWeekend = dow === 0 || dow === 6;
    const DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    const dayTag = ` <span style="font-size:0.7rem;color:#ce93d8;opacity:0.9">${DAYS[dow]}</span>`;
    const labelStyle = isWeekend ? 'color:#ce93d8;font-weight:600;' : '';
    html += `<tr><td class="row-label" style="${labelStyle}">${date}${dayTag}</td>`;
    HOURS.forEach(h => {
      const v = cellVal(date, h);
      const style = v ? heatStyle(v, globalMax, color) : '';
      const title = `${date} ${h}: ${v}`;
      html += `<td class="hcell" style="${style}" title="${title}">${v || ''}</td>`;
    });
    html += `<td style="color:#666;font-size:0.7rem">${rowSum}</td></tr>`;
  });
  html += '</tbody></table>';
  document.getElementById('heatmap-wrap').innerHTML = html;
}

// ── line chart filters ────────────────────────────────────────────────────────
function setLcFilter(f) {
  lcFilter = f;
  ['both','bike','veh'].forEach(x => document.getElementById('lc-'+x).classList.toggle('active', x===f));
  renderLineChart();
}

function toggleAvg() {
  showAvg = !showAvg;
  document.getElementById('lc-avg').classList.toggle('active', showAvg);
  renderLineChart();
}

// ── date checkboxes ───────────────────────────────────────────────────────────
function buildDateChecks(days) {
  const wrap = document.getElementById('date-checks');
  wrap.innerHTML = '';
  days.forEach((d, i) => {
    const lbl = document.createElement('label');
    lbl.className = 'date-check-label' + (i < 7 ? ' checked' : '');
    lbl.dataset.date = d;
    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.style.background = PALETTE_BIKE[i % PALETTE_BIKE.length];
    const inp = document.createElement('input');
    inp.type = 'checkbox';
    inp.checked = i < 7;
    inp.addEventListener('change', () => {
      lbl.classList.toggle('checked', inp.checked);
      renderLineChart();
    });
    lbl.appendChild(dot);
    lbl.appendChild(inp);
    lbl.appendChild(document.createTextNode(d));
    wrap.appendChild(lbl);
  });
}

function getCheckedDates() {
  return [...document.querySelectorAll('#date-checks .date-check-label.checked')].map(l => l.dataset.date);
}

function selectAllDates() {
  document.querySelectorAll('#date-checks .date-check-label').forEach(l => {
    l.classList.add('checked');
    l.querySelector('input').checked = true;
  });
  renderLineChart();
}

function clearAllDates() {
  document.querySelectorAll('#date-checks .date-check-label').forEach(l => {
    l.classList.remove('checked');
    l.querySelector('input').checked = false;
  });
  renderLineChart();
}

function selectRecentDates(n) {
  const labels = [...document.querySelectorAll('#date-checks .date-check-label')];
  labels.forEach((l, i) => {
    const on = i < n;
    l.classList.toggle('checked', on);
    l.querySelector('input').checked = on;
  });
  renderLineChart();
}

// ── line chart render ─────────────────────────────────────────────────────────
function renderLineChart() {
  const selected = getCheckedDates();
  const showBike = lcFilter !== 'veh';
  const showVeh  = lcFilter !== 'bike';
  const datasets = [];
  const allDays  = Object.keys(allHourlyData).sort().reverse();

  selected.forEach((date, idx) => {
    const agg = allHourlyData[date] || {};
    const ci  = allDays.indexOf(date);
    const bikeColor = PALETTE_BIKE[ci % PALETTE_BIKE.length];
    const vehColor  = PALETTE_VEH[ci % PALETTE_VEH.length];
    if (showBike) {
      datasets.push({
        label: `🚲 ${date}`,
        data: HOURS.map(h => (agg[h]&&agg[h][1])||0),
        borderColor: bikeColor, backgroundColor: bikeColor+'22',
        fill: false, tension: 0.3, borderWidth: 2, pointRadius: 2, yAxisID: 'yBike',
      });
    }
    if (showVeh) {
      datasets.push({
        label: `🚗 ${date}`,
        data: HOURS.map(h => (agg[h]&&agg[h][99])||0),
        borderColor: vehColor, backgroundColor: vehColor+'22',
        fill: false, tension: 0.3, borderWidth: 2, pointRadius: 2, yAxisID: 'yVeh',
      });
    }
  });

  // Average line
  if (showAvg && selected.length > 1) {
    const n = selected.length;
    if (showBike) {
      datasets.push({
        label: '📈 Avg Bike',
        data: HOURS.map(h => {
          const s = selected.reduce((sum, d) => sum + ((allHourlyData[d][h]&&allHourlyData[d][h][1])||0), 0);
          return Math.round(s / n);
        }),
        borderColor: '#ff9800', backgroundColor: '#ff980022',
        fill: false, tension: 0.4, borderWidth: 3, borderDash: [6,3],
        pointRadius: 0, yAxisID: 'yBike',
      });
    }
    if (showVeh) {
      datasets.push({
        label: '📈 Avg Vehicle',
        data: HOURS.map(h => {
          const s = selected.reduce((sum, d) => sum + ((allHourlyData[d][h]&&allHourlyData[d][h][99])||0), 0);
          return Math.round(s / n);
        }),
        borderColor: '#ffcc02', backgroundColor: '#ffcc0222',
        fill: false, tension: 0.4, borderWidth: 3, borderDash: [6,3],
        pointRadius: 0, yAxisID: 'yVeh',
      });
    }
  }

  const scales = {
    x: { ticks: { color: '#888' }, grid: { color: '#222' } },
  };
  if (showBike) scales.yBike = {
    type: 'linear', position: 'right', beginAtZero: true,
    ticks: { color: BIKE_COLOR }, grid: { color: '#222' },
    title: { display: true, text: '🚲 Bicycles', color: BIKE_COLOR, font: { size: 11 } },
  };
  if (showVeh) scales.yVeh = {
    type: 'linear', position: 'left', beginAtZero: true,
    ticks: { color: VEHICLE_COLOR }, grid: { drawOnChartArea: !showBike },
    title: { display: true, text: '🚗 Vehicles', color: VEHICLE_COLOR, font: { size: 11 } },
  };

  const ctx = document.getElementById('hourly-chart').getContext('2d');
  if (hourlyChart) hourlyChart.destroy();
  hourlyChart = new Chart(ctx, {
    type: 'line',
    data: { labels: HOURS, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#aaa', boxWidth: 12, font: { size: 11 } } }
      },
      scales,
      interaction: { mode: 'index', intersect: false },
    }
  });
}

function selectDate(date) {
  document.querySelectorAll('table.dtable tr.selected').forEach(r => r.classList.remove('selected'));
  document.querySelectorAll('table.dtable tr.clickable').forEach(r => {
    if (r.querySelector('td.date-cell')?.textContent === date) r.classList.add('selected');
  });
  // check only this date in the line chart
  document.querySelectorAll('#date-checks .date-check-label').forEach(l => {
    const on = l.dataset.date === date;
    l.classList.toggle('checked', on);
    l.querySelector('input').checked = on;
  });
  renderLineChart();
  document.getElementById('hourly-card').scrollIntoView({behavior:'smooth'});
}

// ── init ──────────────────────────────────────────────────────────────────────
async function init() {
  // Fetch daily summary + all hourly data in parallel
  const [dailyRes, hourlyRes] = await Promise.all([
    fetch('/api/daily').then(r=>r.json()),
    fetch('/api/hourly_all_dates').then(r=>r.json()),
  ]);

  // Daily
  const dailyAgg = aggregate(dailyRes);
  allDailyData = dailyAgg;
  renderDailyChart(dailyAgg);
  renderDailyTable(dailyAgg);

  // Hourly — build allHourlyData: date → hour → {0, 1, 99}
  hourlyRes.forEach(row => {
    const date = row.date;
    const hour = row.hour;
    const cid  = row.class_id === 1 ? 1 : row.class_id === 0 ? 0 : 99;
    if (!allHourlyData[date]) allHourlyData[date] = {};
    if (!allHourlyData[date][hour]) allHourlyData[date][hour] = {};
    allHourlyData[date][hour][cid] = (allHourlyData[date][hour][cid] || 0) + row.count;
  });

  const days = Object.keys(allHourlyData).sort().reverse();
  buildDateChecks(days);
  renderHeatmap();
  renderLineChart();
}

init();

function toggleTheme() {
  const light = document.body.classList.toggle('light');
  document.getElementById('theme-btn').textContent = light ? '🌙 Dark' : '☀️ Light';
  localStorage.setItem('theme', light ? 'light' : 'dark');
}
if (localStorage.getItem('theme') === 'light') {
  document.body.classList.add('light');
  document.getElementById('theme-btn').textContent = '🌙 Dark';
}
</script>
</body>
</html>
"""


@app.route("/api/hourly_all_dates")
def api_hourly_all_dates():
    """Return hourly counts for ALL dates, grouped by date + hour + class_id. Halifax time."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""
                SELECT
                    to_char(ts::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'America/Halifax', 'YYYY-MM-DD') AS date,
                    to_char(ts::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'America/Halifax', 'HH24:00') AS hour,
                    class_id,
                    COUNT(*) AS cnt
                FROM crossings
                GROUP BY date, hour, class_id
                ORDER BY date, hour
            """)
        else:
            cur.execute("""
                SELECT
                    strftime('%Y-%m-%d', ts) AS date,
                    strftime('%H:00', ts)    AS hour,
                    class_id,
                    COUNT(*) AS cnt
                FROM crossings
                GROUP BY date, hour, class_id
                ORDER BY date, hour
            """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = []
        for row in rows:
            date, hour, class_id, cnt = row
            if class_id == 1:
                cid = 1
            elif class_id == 0:
                cid = 0
            else:
                cid = 99
            result.append({"date": date, "hour": hour, "class_id": cid, "count": cnt})
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/hourly_by_date")
def api_hourly_by_date():
    """Return hourly counts for a specific date (YYYY-MM-DD). Halifax time."""
    date = request.args.get("date", "")
    if not date:
        return jsonify({"error": "date param required"}), 400
    try:
        conn = get_conn()
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""
                SELECT
                    to_char(ts::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'America/Halifax', 'HH24:00') AS label,
                    direction,
                    class_id,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE to_char(ts::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'America/Halifax', 'YYYY-MM-DD') = %s
                GROUP BY label, direction, class_id
                ORDER BY label
            """, (date,))
        else:
            cur.execute("""
                SELECT
                    strftime('%H:00', ts) AS label,
                    direction,
                    class_id,
                    COUNT(*) AS cnt
                FROM crossings
                WHERE strftime('%Y-%m-%d', ts) = ?
                GROUP BY label, direction, class_id
                ORDER BY label
            """, (date,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify(_consolidate_rows(rows))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
