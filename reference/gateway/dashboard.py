"""Self-contained HTML dashboard for NoeRelay analytics.

Renders a single-page dashboard that fetches data from the analytics API
endpoints and displays it using vanilla HTML/CSS/JS (no dependencies).
"""

from __future__ import annotations


def render_dashboard() -> str:
    """Render the dashboard HTML.

    A single-page dashboard that fetches data from the analytics API
    endpoints and displays it using vanilla HTML/CSS/JS (no dependencies).

    Sections:
    - Overview cards: total runs, accuracy, cost today, HIR, RR
    - Cost trend chart (last 30 days)
    - Usage trend chart (last 30 days)
    - Model ranking table
    - Recent runs table
    - Recent alerts
    - Escalation summary
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NoeRelay Dashboard</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; background: #f0f2f5; color: #1a1a2e; padding: 0; min-height: 100vh; }
header { background: #1a1a2e; color: white; padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; }
header h1 { font-size: 1.3em; font-weight: 600; }
header .refresh { cursor: pointer; font-size: 0.9em; color: #a0a0c0; user-select: none; }
header .refresh:hover { color: white; }
header .status { font-size: 0.8em; color: #4CAF50; margin-left: 12px; }
main { max-width: 1200px; margin: 0 auto; padding: 20px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 20px; }
.card { background: white; border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.card h3 { font-size: 0.8em; text-transform: uppercase; color: #888; margin-bottom: 8px; letter-spacing: 0.5px; }
.card .value { font-size: 2em; font-weight: 700; }
.card .sub { font-size: 0.8em; color: #999; margin-top: 4px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }
@media (max-width: 768px) { .grid-2 { grid-template-columns: 1fr; } }
.section { background: white; border-radius: 10px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.section h2 { font-size: 1.1em; margin-bottom: 16px; color: #333; }
.chart { width: 100%; height: 220px; position: relative; overflow: hidden; }
.chart canvas { width: 100%; height: 100%; }
table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #eee; }
th { background: #f8f9fa; font-weight: 600; color: #555; font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.3px; }
tr:hover { background: #f8f9ff; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75em; font-weight: 600; }
.badge-success { background: #d4edda; color: #155724; }
.badge-warning { background: #fff3cd; color: #856404; }
.badge-danger { background: #f8d7da; color: #721c24; }
.badge-info { background: #d1ecf1; color: #0c5460; }
.alert-item { padding: 10px 12px; border-radius: 6px; margin-bottom: 8px; font-size: 0.85em; }
.alert-warning { background: #fff3cd; border-left: 3px solid #ffc107; }
.alert-critical { background: #f8d7da; border-left: 3px solid #dc3545; }
.alert-info { background: #d1ecf1; border-left: 3px solid #17a2b8; }
.empty { text-align: center; color: #999; padding: 40px; font-style: italic; }
.loading { text-align: center; color: #999; padding: 40px; }
.loading::after { content: '...'; animation: dots 1.5s infinite; }
@keyframes dots { 0%,20% { content: '.'; } 40% { content: '..'; } 60%,100% { content: '...'; } }
.rank-num { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 50%; background: #1a1a2e; color: white; font-size: 0.75em; font-weight: 700; }
.rank-1 { background: #FFD700; color: #333; }
.rank-2 { background: #C0C0C0; color: #333; }
.rank-3 { background: #CD7F32; color: white; }
footer { text-align: center; padding: 20px; color: #999; font-size: 0.8em; }
</style>
</head>
<body>
<header>
  <div>
    <h1>&#9889; NoeRelay Dashboard</h1>
  </div>
  <div style="display:flex;align-items:center;">
    <span class="status" id="connection-status">&#9679; Connected</span>
    <span class="refresh" onclick="loadAll()" title="Refresh data">&#8635; Refresh</span>
  </div>
</header>
<main>
  <div class="cards" id="overview-cards">
    <div class="card"><h3>Total Runs</h3><div class="value" id="v-total-runs">-</div><div class="sub">All time</div></div>
    <div class="card"><h3>Accuracy</h3><div class="value" id="v-accuracy">-</div><div class="sub">Accepted / Total</div></div>
    <div class="card"><h3>Cost Today</h3><div class="value" id="v-cost-today">-</div><div class="sub">USD</div></div>
    <div class="card"><h3>HIR</h3><div class="value" id="v-hir">-</div><div class="sub">Human Intervention Rate</div></div>
    <div class="card"><h3>RR</h3><div class="value" id="v-rr">-</div><div class="sub">Rework Rate</div></div>
  </div>

  <div class="grid-2">
    <div class="section">
      <h2>&#128200; Cost Trend (30 days)</h2>
      <div class="chart"><canvas id="cost-chart"></canvas></div>
    </div>
    <div class="section">
      <h2>&#128202; Usage Trend (30 days)</h2>
      <div class="chart"><canvas id="usage-chart"></canvas></div>
    </div>
  </div>

  <div class="section" id="ranking-section">
    <h2>&#127942; Model Ranking</h2>
    <div style="overflow-x:auto;">
      <table id="model-ranking-table">
        <thead><tr><th>Rank</th><th>Model</th><th>Accuracy</th><th>Avg Tokens</th><th>True Cost/Correct</th><th>Latency</th><th>HIR</th><th>RR</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div class="section" id="runs-section">
    <h2>&#128337; Recent Runs</h2>
    <div style="overflow-x:auto;">
      <table id="recent-runs-table">
        <thead><tr><th>Run ID</th><th>Status</th><th>Model</th><th>Risk</th><th>Tokens</th><th>Cost</th><th>Latency</th><th>HIR</th><th>RR</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div class="section" id="alerts-section">
    <h2>&#128276; Recent Alerts</h2>
    <div id="alerts-container"><div class="empty">Loading alerts...</div></div>
  </div>
</main>
<footer>NoeRelay Analytics Dashboard &mdash; Auto-refreshes every 30s</footer>

<script>
var API_BASE = '/v1/analytics';

function fmt(n, d) { d = d || 2; return Number(n || 0).toFixed(d); }
function fmtPct(n) { return fmt(n, 1) + '%'; }
function fmtUsd(n) { return '$' + fmt(n, 6); }
function fmtMs(n) { return fmt(n, 1) + 'ms'; }
function fmtShort(s, n) { n = n || 8; return s ? (s.length > n ? s.substring(0, n) + '...' : s) : 'N/A'; }
function statusBadge(s) {
  var cls = s === 'accepted' ? 'badge-success' : s === 'escalated' ? 'badge-danger' : s === 'failed' ? 'badge-danger' : 'badge-warning';
  return '<span class="badge ' + cls + '">' + s + '</span>';
}
function rankBadge(n) {
  var cls = n === 1 ? 'rank-1' : n === 2 ? 'rank-2' : n === 3 ? 'rank-3' : '';
  return '<span class="rank-num ' + cls + '">' + n + '</span>';
}

function drawBarChart(canvasId, data, labelKey, valueKey, color) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var dpr = window.devicePixelRatio || 1;
  var rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  ctx.scale(dpr, dpr);
  var w = rect.width - 10, h = rect.height - 30;
  if (!data || !data.length) { ctx.fillStyle = '#999'; ctx.font = '14px system-ui'; ctx.fillText('No data', w/2-30, h/2); return; }
  var vals = data.map(function(d) { return d[valueKey] || 0; });
  var max = Math.max.apply(null, vals) || 1;
  var barW = Math.max(2, (w / data.length) - 2);
  ctx.clearRect(0, 0, rect.width, rect.height);
  // Grid lines
  ctx.strokeStyle = '#eee'; ctx.lineWidth = 0.5;
  for (var i = 0; i <= 4; i++) { var y = 10 + (h * i / 4); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  // Bars
  for (var i = 0; i < data.length; i++) {
    var bh = (vals[i] / max) * h;
    ctx.fillStyle = color || '#4CAF50';
    ctx.fillRect(i * (barW + 2), h - bh + 10, barW, bh);
  }
  // Labels
  ctx.fillStyle = '#888'; ctx.font = '9px system-ui';
  var step = Math.max(1, Math.floor(data.length / 7));
  for (var i = 0; i < data.length; i += step) {
    var label = (data[i][labelKey] || '').substring(5);
    ctx.fillText(label, i * (barW + 2), h + 25);
  }
  // Max value
  ctx.fillStyle = '#888'; ctx.font = '9px system-ui';
  ctx.fillText(fmt(max, 4), w - 40, 18);
}

function drawLineChart(canvasId, data, valueKey, color) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var dpr = window.devicePixelRatio || 1;
  var rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  ctx.scale(dpr, dpr);
  var w = rect.width - 10, h = rect.height - 30;
  if (!data || !data.length) { ctx.fillStyle = '#999'; ctx.font = '14px system-ui'; ctx.fillText('No data', w/2-30, h/2); return; }
  var vals = data.map(function(d) { return d[valueKey] || 0; });
  var max = Math.max.apply(null, vals) || 1;
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.strokeStyle = '#eee'; ctx.lineWidth = 0.5;
  for (var i = 0; i <= 4; i++) { var y = 10 + (h * i / 4); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  ctx.strokeStyle = color || '#2196F3'; ctx.lineWidth = 2; ctx.beginPath();
  var xStep = data.length > 1 ? w / (data.length - 1) : w;
  for (var i = 0; i < data.length; i++) {
    var x = i * xStep, y = h - (vals[i] / max * h) + 10;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();
  // Fill
  ctx.lineTo((data.length - 1) * xStep, h + 10); ctx.lineTo(0, h + 10); ctx.closePath();
  ctx.fillStyle = (color || '#2196F3') + '20'; ctx.fill();
  ctx.fillStyle = '#888'; ctx.font = '9px system-ui';
  var step = Math.max(1, Math.floor(data.length / 7));
  for (var i = 0; i < data.length; i += step) {
    var label = (data[i].day || data[i].period || '').substring(5);
    ctx.fillText(label, i * xStep, h + 25);
  }
  ctx.fillText(fmt(max, 4), w - 40, 18);
}

async function fetchJSON(url) {
  var resp = await fetch(url);
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  return resp.json();
}

async function loadDashboard() {
  try {
    var data = await fetchJSON(API_BASE + '/dashboard');
    // Overview cards
    var s = data.summary || {};
    document.getElementById('v-total-runs').textContent = s.total_runs || 0;
    document.getElementById('v-accuracy').textContent = fmtPct(s.accuracy);
    document.getElementById('v-cost-today').textContent = fmtUsd(s.cost_today_usd);
    document.getElementById('v-hir').textContent = fmtPct(s.hir);
    document.getElementById('v-rr').textContent = fmtPct(s.rr);

    // Charts
    drawBarChart('cost-chart', data.cost_trend || [], 'day', 'total_cost', '#4CAF50');
    drawLineChart('usage-chart', data.usage_trend || [], 'total_tokens', '#2196F3');

    // Model ranking
    var rankingTbody = document.querySelector('#model-ranking-table tbody');
    rankingTbody.innerHTML = '';
    (data.model_ranking || []).forEach(function(m) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + rankBadge(m.rank) + '</td>' +
        '<td><strong>' + (m.model_id || 'N/A') + '</strong></td>' +
        '<td>' + fmtPct(m.accuracy) + '</td>' +
        '<td>' + fmt(m.avg_tokens, 0) + '</td>' +
        '<td>' + fmtUsd(m.true_cost_per_correct) + '</td>' +
        '<td>' + fmtMs(m.avg_latency_ms) + '</td>' +
        '<td>' + fmtPct(m.hir_rate) + '</td>' +
        '<td>' + fmtPct(m.rework_rate) + '</td>';
      rankingTbody.appendChild(tr);
    });

    // Recent runs
    var runsTbody = document.querySelector('#recent-runs-table tbody');
    runsTbody.innerHTML = '';
    (data.recent_runs || []).forEach(function(r) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + fmtShort(r.run_id, 12) + '</td>' +
        '<td>' + statusBadge(r.status) + '</td>' +
        '<td>' + (r.model_id || 'N/A') + '</td>' +
        '<td>' + (r.risk_class || 'low') + '</td>' +
        '<td>' + fmt(r.total_tokens, 0) + '</td>' +
        '<td>' + fmtUsd(r.actual_cost_usd) + '</td>' +
        '<td>' + fmtMs(r.latency_ms) + '</td>' +
        '<td>' + (r.required_human_intervention ? '<span class="badge badge-warning">Yes</span>' : '<span class="badge badge-success">No</span>') + '</td>' +
        '<td>' + (r.required_rework ? '<span class="badge badge-warning">Yes</span>' : '<span class="badge badge-success">No</span>') + '</td>';
      runsTbody.appendChild(tr);
    });

    document.getElementById('connection-status').textContent = '\u25CF Connected';
    document.getElementById('connection-status').style.color = '#4CAF50';
  } catch(e) {
    document.getElementById('connection-status').textContent = '\u25CF Disconnected';
    document.getElementById('connection-status').style.color = '#dc3545';
    console.error('Dashboard load error:', e);
  }
}

async function loadAlerts() {
  try {
    var data = await fetchJSON('/v1/alerts?limit=10');
    var container = document.getElementById('alerts-container');
    var alerts = data.alerts || [];
    if (!alerts.length) { container.innerHTML = '<div class="empty">No alerts</div>'; return; }
    container.innerHTML = '';
    alerts.forEach(function(a) {
      var cls = a.severity === 'critical' ? 'alert-critical' : a.severity === 'warning' ? 'alert-warning' : 'alert-info';
      var div = document.createElement('div');
      div.className = 'alert-item ' + cls;
      div.innerHTML = '<strong>' + (a.alert_type || 'Alert') + '</strong> &mdash; ' + (a.message || '') +
        '<br><small>' + (a.timestamp || '') + ' | ' + (a.acknowledged ? 'Acknowledged' : 'Pending') + '</small>';
      container.appendChild(div);
    });
  } catch(e) {
    document.getElementById('alerts-container').innerHTML = '<div class="empty">Alerts unavailable</div>';
  }
}

function loadAll() {
  loadDashboard();
  loadAlerts();
}

loadAll();
setInterval(loadAll, 30000);

window.addEventListener('resize', function() {
  loadDashboard();
});
</script>
</body>
</html>"""