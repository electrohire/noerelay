"""Self-contained HTML dashboard for NoeRelay analytics.

Renders a single-page dashboard that fetches data from the analytics API
endpoints and displays it using vanilla HTML/CSS/JS (no dependencies).

Sections:
- Overview: System health, key metrics, recent activity
- Models: Local/cloud model management, ranking, recommendations
- Benchmarks: Run benchmarks, view results, compare models
- Governance: Routing policy, risk classes, verification DAG
- Routing: Portfolio management, add/remove candidates
- Analytics: Cost, performance, usage, escalation analytics
- Ledger: Epistemic ledger viewer, decision traces, chain verification
- Tenants: Multi-tenant management, budgets
- API Keys: Key management, create/revoke/rotate
- Alerts: Active alerts, rules, history
- Webhooks: Webhook management
- Config: System configuration
- Audit Log: Audit trail, anomaly detection
- Settings: System settings, backup/restore
"""

from __future__ import annotations


def render_dashboard() -> str:
    """Render the dashboard HTML.

    A single-page dashboard that fetches data from the analytics API
    endpoints and displays it using vanilla HTML/CSS/JS (no dependencies).

    Sections:
    - Overview cards: total runs, accuracy, cost today, HIR, RR, active models
    - Cost trend chart (last 30 days)
    - Usage trend chart (last 30 days)
    - Model ranking table
    - Recent runs table
    - Recent alerts
    - Escalation summary
    - Models management (local, cloud, ranking, recommendations)
    - Benchmarks (run, results, comparison)
    - Governance (policy, risk classes)
    - Routing (portfolio, candidates)
    - Analytics (cost, performance, usage, escalations)
    - Ledger (events, traces, verification)
    - Tenants (list, create, budgets)
    - API Keys (list, create, revoke, rotate)
    - Alerts (list, acknowledge, rules)
    - Webhooks (list, register, delete)
    - Config (view, edit)
    - Audit Log (entries, filter)
    - Settings (mode, cache, TLS, DB, backup/restore)
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NoeRelay Dashboard</title>
<style>
:root {
  --bg: #0f1117;
  --bg2: #161822;
  --bg3: #1c1f2e;
  --bg4: #252838;
  --border: #2a2d3a;
  --text: #e1e4ed;
  --text2: #9ca3b8;
  --text3: #6b7280;
  --accent: #6366f1;
  --accent2: #818cf8;
  --green: #22c55e;
  --yellow: #eab308;
  --red: #ef4444;
  --blue: #3b82f6;
  --cyan: #06b6d4;
  --sidebar-w: 220px;
  --topbar-h: 48px;
  --radius: 8px;
  --shadow: 0 1px 3px rgba(0,0,0,0.3);
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  display: flex;
}
a { color: var(--accent2); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Sidebar */
#sidebar {
  width: var(--sidebar-w);
  min-width: var(--sidebar-w);
  background: var(--bg2);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: fixed;
  top: 0; left: 0; bottom: 0;
  z-index: 100;
  overflow-y: auto;
}
#sidebar .logo {
  padding: 14px 16px;
  font-size: 1.1em;
  font-weight: 700;
  color: var(--accent2);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 8px;
}
#sidebar nav { flex: 1; padding: 8px 0; }
#sidebar nav a {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 16px;
  color: var(--text2);
  font-size: 0.88em;
  cursor: pointer;
  transition: all 0.15s;
  border-left: 3px solid transparent;
  text-decoration: none;
}
#sidebar nav a:hover { background: var(--bg3); color: var(--text); text-decoration: none; }
#sidebar nav a.active { background: var(--bg3); color: var(--accent2); border-left-color: var(--accent2); }
#sidebar nav a .icon { font-size: 1.1em; width: 20px; text-align: center; }
#sidebar .sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  font-size: 0.75em;
  color: var(--text3);
}

/* Main area */
#main-wrap {
  margin-left: var(--sidebar-w);
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

/* Top bar */
#topbar {
  height: var(--topbar-h);
  background: var(--bg2);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  position: sticky;
  top: 0;
  z-index: 50;
}
#topbar .tb-left { display: flex; align-items: center; gap: 16px; }
#topbar .tb-right { display: flex; align-items: center; gap: 12px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
.status-dot.green { background: var(--green); }
.status-dot.red { background: var(--red); }
.status-dot.yellow { background: var(--yellow); }
.tb-badge {
  font-size: 0.75em;
  padding: 2px 10px;
  border-radius: 12px;
  background: var(--bg3);
  border: 1px solid var(--border);
  color: var(--text2);
}
.tb-badge.live { border-color: var(--green); color: var(--green); }
.tb-badge.stub { border-color: var(--yellow); color: var(--yellow); }
#refresh-btn {
  background: var(--accent);
  color: white;
  border: none;
  padding: 5px 14px;
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 0.8em;
  font-weight: 600;
  transition: background 0.15s;
}
#refresh-btn:hover { background: var(--accent2); }
#auto-refresh-toggle {
  font-size: 0.75em;
  color: var(--text2);
  cursor: pointer;
  user-select: none;
  display: flex; align-items: center; gap: 4px;
}

/* Content */
#content {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
}
.section { display: none; }
.section.active { display: block; }
.section h2 {
  font-size: 1.3em;
  margin-bottom: 20px;
  color: var(--text);
  display: flex; align-items: center; gap: 8px;
}

/* Cards */
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}
.card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px;
  box-shadow: var(--shadow);
}
.card h3 { font-size: 0.75em; text-transform: uppercase; color: var(--text3); margin-bottom: 8px; letter-spacing: 0.5px; }
.card .value { font-size: 1.8em; font-weight: 700; color: var(--text); }
.card .sub { font-size: 0.78em; color: var(--text3); margin-top: 4px; }

/* Grid layouts */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 20px; }
@media (max-width: 1024px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }

/* Panels */
.panel {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px;
  margin-bottom: 16px;
  box-shadow: var(--shadow);
}
.panel h3 { font-size: 0.95em; margin-bottom: 14px; color: var(--text); }

/* Charts */
.chart { width: 100%; height: 220px; position: relative; overflow: hidden; }
.chart canvas { width: 100%; height: 100%; }

/* Tables */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--border); }
th { background: var(--bg3); font-weight: 600; color: var(--text2); font-size: 0.78em; text-transform: uppercase; letter-spacing: 0.3px; position: sticky; top: 0; }
tr:hover { background: var(--bg3); }
.sortable { cursor: pointer; user-select: none; }
.sortable::after { content: ' \\2195'; font-size: 0.7em; color: var(--text3); }
.sortable.asc::after { content: ' \\2191'; color: var(--accent2); }
.sortable.desc::after { content: ' \\2193'; color: var(--accent2); }

/* Badges */
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75em; font-weight: 600; }
.badge-success { background: #064e3b20; color: var(--green); border: 1px solid #064e3b40; }
.badge-warning { background: #713f1220; color: var(--yellow); border: 1px solid #713f1240; }
.badge-danger { background: #7f1d1d20; color: var(--red); border: 1px solid #7f1d1d40; }
.badge-info { background: #1e3a5f20; color: var(--blue); border: 1px solid #1e3a5f40; }
.badge-cyan { background: #164e6320; color: var(--cyan); border: 1px solid #164e6340; }

/* Rank numbers */
.rank-num { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 50%; background: var(--bg4); color: var(--text2); font-size: 0.75em; font-weight: 700; }
.rank-1 { background: #FFD700; color: #1a1a2e; }
.rank-2 { background: #C0C0C0; color: #1a1a2e; }
.rank-3 { background: #CD7F32; color: white; }

/* Forms */
.form-group { margin-bottom: 14px; }
.form-group label { display: block; font-size: 0.82em; color: var(--text2); margin-bottom: 4px; font-weight: 500; }
.form-group input, .form-group select, .form-group textarea {
  width: 100%;
  padding: 8px 12px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-size: 0.88em;
  font-family: inherit;
}
.form-group input:focus, .form-group select:focus, .form-group textarea:focus {
  outline: none;
  border-color: var(--accent);
}
.form-group textarea { min-height: 80px; resize: vertical; }
.form-row { display: flex; gap: 12px; }
.form-row .form-group { flex: 1; }

/* Buttons */
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 16px;
  border-radius: var(--radius);
  font-size: 0.85em;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.15s;
  font-family: inherit;
}
.btn-primary { background: var(--accent); color: white; border-color: var(--accent); }
.btn-primary:hover { background: var(--accent2); }
.btn-danger { background: var(--red); color: white; border-color: var(--red); }
.btn-danger:hover { background: #dc2626; }
.btn-outline { background: transparent; color: var(--text2); border-color: var(--border); }
.btn-outline:hover { background: var(--bg3); color: var(--text); }
.btn-sm { padding: 4px 10px; font-size: 0.78em; }
.btn-xs { padding: 2px 8px; font-size: 0.72em; }
.btn-group { display: flex; gap: 8px; flex-wrap: wrap; }

/* Alerts */
.alert-item { padding: 10px 14px; border-radius: var(--radius); margin-bottom: 8px; font-size: 0.85em; border-left: 3px solid; }
.alert-warning { background: #713f1220; border-left-color: var(--yellow); }
.alert-critical { background: #7f1d1d20; border-left-color: var(--red); }
.alert-info { background: #1e3a5f20; border-left-color: var(--blue); }

/* Empty / Loading */
.empty { text-align: center; color: var(--text3); padding: 40px; font-style: italic; }
.loading { text-align: center; color: var(--text3); padding: 40px; }
.spinner {
  display: inline-block; width: 20px; height: 20px;
  border: 2px solid var(--border); border-top-color: var(--accent);
  border-radius: 50%; animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Toast */
#toast-container {
  position: fixed; top: 16px; right: 16px; z-index: 9999;
  display: flex; flex-direction: column; gap: 8px;
}
.toast {
  padding: 12px 20px;
  border-radius: var(--radius);
  font-size: 0.85em;
  font-weight: 500;
  box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  animation: slideIn 0.3s ease;
  max-width: 380px;
}
.toast-success { background: #064e3b; color: var(--green); border: 1px solid #065f46; }
.toast-error { background: #7f1d1d; color: #fca5a5; border: 1px solid #991b1b; }
.toast-info { background: #1e3a5f; color: #93c5fd; border: 1px solid #1e40af; }
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

/* Modal */
.modal-overlay {
  display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.6); z-index: 1000;
  align-items: center; justify-content: center;
}
.modal-overlay.show { display: flex; }
.modal {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
  max-width: 480px;
  width: 90%;
  box-shadow: 0 8px 32px rgba(0,0,0,0.5);
}
.modal h3 { margin-bottom: 16px; font-size: 1.1em; }
.modal .modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 20px; }

/* Pagination */
.pagination { display: flex; gap: 4px; justify-content: center; margin-top: 12px; }
.pagination button {
  padding: 4px 10px; background: var(--bg3); border: 1px solid var(--border);
  color: var(--text2); border-radius: 4px; cursor: pointer; font-size: 0.8em;
}
.pagination button:hover { background: var(--bg4); color: var(--text); }
.pagination button.active { background: var(--accent); color: white; border-color: var(--accent); }

/* Time range selector */
.time-range { display: flex; gap: 4px; margin-bottom: 16px; }
.time-range button {
  padding: 4px 12px; background: var(--bg3); border: 1px solid var(--border);
  color: var(--text2); border-radius: 4px; cursor: pointer; font-size: 0.8em;
}
.time-range button:hover { background: var(--bg4); color: var(--text); }
.time-range button.active { background: var(--accent); color: white; border-color: var(--accent); }

/* Key reveal */
.key-reveal {
  background: var(--bg3); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 12px; margin: 12px 0;
  font-family: monospace; font-size: 0.85em; word-break: break-all;
  color: var(--accent2);
}

/* Responsive sidebar */
@media (max-width: 768px) {
  #sidebar { width: 60px; min-width: 60px; }
  #sidebar .logo span { display: none; }
  #sidebar nav a span { display: none; }
  #sidebar nav a { justify-content: center; padding: 12px; }
  #sidebar .sidebar-footer { display: none; }
  #main-wrap { margin-left: 60px; }
  :root { --sidebar-w: 60px; }
  .cards { grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }
}
</style>
</head>
<body>

<!-- Sidebar -->
<div id="sidebar">
  <div class="logo"><span>&#9889;</span> <span>NoeRelay</span></div>
  <nav>
    <a data-section="overview" class="active" onclick="switchSection('overview')"><span class="icon">&#9632;</span> <span>Overview</span></a>
    <a data-section="models" onclick="switchSection('models')"><span class="icon">&#9881;</span> <span>Models</span></a>
    <a data-section="benchmarks" onclick="switchSection('benchmarks')"><span class="icon">&#9733;</span> <span>Benchmarks</span></a>
    <a data-section="governance" onclick="switchSection('governance')"><span class="icon">&#9878;</span> <span>Governance</span></a>
    <a data-section="routing" onclick="switchSection('routing')"><span class="icon">&#8661;</span> <span>Routing</span></a>
    <a data-section="analytics" onclick="switchSection('analytics')"><span class="icon">&#128200;</span> <span>Analytics</span></a>
    <a data-section="ledger" onclick="switchSection('ledger')"><span class="icon">&#128214;</span> <span>Ledger</span></a>
    <a data-section="tenants" onclick="switchSection('tenants')"><span class="icon">&#128101;</span> <span>Tenants</span></a>
    <a data-section="apikeys" onclick="switchSection('apikeys')"><span class="icon">&#128273;</span> <span>API Keys</span></a>
    <a data-section="alerts" onclick="switchSection('alerts')"><span class="icon">&#128276;</span> <span>Alerts</span></a>
    <a data-section="webhooks" onclick="switchSection('webhooks')"><span class="icon">&#128279;</span> <span>Webhooks</span></a>
    <a data-section="config" onclick="switchSection('config')"><span class="icon">&#9881;</span> <span>Config</span></a>
    <a data-section="audit" onclick="switchSection('audit')"><span class="icon">&#128269;</span> <span>Audit Log</span></a>
    <a data-section="settings" onclick="switchSection('settings')"><span class="icon">&#128295;</span> <span>Settings</span></a>
  </nav>
  <div class="sidebar-footer">NoeRelay v0.1.0</div>
</div>

<!-- Main -->
<div id="main-wrap">
  <!-- Top bar -->
  <div id="topbar">
    <div class="tb-left">
      <span id="connection-status"><span class="status-dot green"></span>Connected</span>
      <span class="tb-badge stub" id="gateway-mode">stub</span>
      <span class="tb-badge" id="cache-status">cache: --</span>
    </div>
    <div class="tb-right">
      <span id="auto-refresh-toggle" onclick="toggleAutoRefresh()" title="Toggle auto-refresh">&#8635; Auto: ON</span>
      <button id="refresh-btn" onclick="refreshCurrent()">&#8635; Refresh</button>
    </div>
  </div>

  <!-- Content -->
  <div id="content">

    <!-- ========== OVERVIEW ========== -->
    <div class="section active" id="section-overview">
      <h2>&#9632; Overview</h2>
      <div class="cards" id="overview-cards">
        <div class="card"><h3>Total Runs</h3><div class="value" id="v-total-runs">-</div><div class="sub">All time</div></div>
        <div class="card"><h3>Accuracy</h3><div class="value" id="v-accuracy">-</div><div class="sub">Accepted / Total</div></div>
        <div class="card"><h3>Cost Today</h3><div class="value" id="v-cost-today">-</div><div class="sub">USD</div></div>
        <div class="card"><h3>HIR</h3><div class="value" id="v-hir">-</div><div class="sub">Human Intervention Rate</div></div>
        <div class="card"><h3>RR</h3><div class="value" id="v-rr">-</div><div class="sub">Rework Rate</div></div>
        <div class="card"><h3>Active Models</h3><div class="value" id="v-active-models">-</div><div class="sub">In portfolio</div></div>
      </div>

      <div class="grid-2">
        <div class="panel">
          <h3>&#128200; Cost Trend (30 days)</h3>
          <div class="chart"><canvas id="cost-chart"></canvas></div>
        </div>
        <div class="panel">
          <h3>&#128202; Usage Trend (30 days)</h3>
          <div class="chart"><canvas id="usage-chart"></canvas></div>
        </div>
      </div>

      <div class="panel" id="ranking-section">
        <h3>&#127942; Model Ranking</h3>
        <div class="table-wrap">
          <table id="model-ranking-table">
            <thead><tr><th>Rank</th><th>Model</th><th>Accuracy</th><th>Avg Tokens</th><th>True Cost/Correct</th><th>Latency</th><th>HIR</th><th>RR</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>

      <div class="panel" id="runs-section">
        <h3>&#128337; Recent Runs</h3>
        <div class="table-wrap">
          <table id="recent-runs-table">
            <thead><tr><th>Run ID</th><th>Status</th><th>Model</th><th>Risk</th><th>Tokens</th><th>Cost</th><th>Latency</th><th>HIR</th><th>RR</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>

      <div class="panel" id="alerts-section">
        <h3>&#128276; Recent Alerts</h3>
        <div id="alerts-container"><div class="empty">Loading alerts...</div></div>
      </div>
    </div>

    <!-- ========== MODELS ========== -->
    <div class="section" id="section-models">
      <h2>&#9881; Models</h2>
      <div class="panel">
        <h3>Local Models</h3>
        <div class="table-wrap">
          <table id="local-models-table">
            <thead><tr><th>Name</th><th>Size</th><th>Specialization</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h3>Cloud Models</h3>
        <div class="table-wrap">
          <table id="cloud-models-table">
            <thead><tr><th>Name</th><th>Pricing</th><th>Context Length</th><th>Provider</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h3>Model Ranking (True Cost)</h3>
        <div class="table-wrap">
          <table id="models-ranking-table">
            <thead><tr><th>Rank</th><th>Model</th><th>Accuracy</th><th>True Cost/Correct</th><th>Latency</th><th>HIR</th><th>RR</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="grid-2">
        <div class="panel">
          <h3>Download Recommendations</h3>
          <div id="download-recs"><div class="empty">Loading...</div></div>
        </div>
        <div class="panel">
          <h3>Removal Recommendations</h3>
          <div id="removal-recs"><div class="empty">Loading...</div></div>
        </div>
      </div>
      <div class="panel">
        <h3>Discover on HuggingFace</h3>
        <div class="form-row">
          <div class="form-group" style="flex:1"><input type="text" id="hf-search" placeholder="Search models..."></div>
          <button class="btn btn-primary" onclick="searchHF()">Search</button>
        </div>
        <div id="hf-results" style="margin-top:12px;"></div>
      </div>
    </div>

    <!-- ========== BENCHMARKS ========== -->
    <div class="section" id="section-benchmarks">
      <h2>&#9733; Benchmarks</h2>
      <div class="panel">
        <h3>Run Benchmark</h3>
        <div class="form-row">
          <div class="form-group"><label>Dataset</label><select id="bm-dataset"><option value="">Loading...</option></select></div>
          <div class="form-group"><label>Cohort Name</label><input type="text" id="bm-cohort" placeholder="my-cohort"></div>
          <div class="form-group"><label>Evaluator</label><select id="bm-evaluator"><option value="exact_match">Exact Match</option><option value="semantic">Semantic</option><option value="custom">Custom</option></select></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label><input type="checkbox" id="bm-prefer-local"> Prefer Local Models</label></div>
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="runBenchmark()">&#9654; Run Benchmark</button>
          <button class="btn btn-outline" onclick="runInlineBenchmark()">&#9889; Run Inline Benchmark</button>
        </div>
      </div>
      <div class="panel">
        <h3>Benchmark Results</h3>
        <div class="table-wrap">
          <table id="benchmark-results-table">
            <thead><tr><th>Cohort</th><th>Model</th><th>Accuracy</th><th>Tokens/Correct</th><th>Cost</th><th>Latency</th><th>Timestamp</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h3>Model Comparison</h3>
        <div id="model-comparison"><div class="empty">Select models to compare</div></div>
      </div>
    </div>

    <!-- ========== GOVERNANCE ========== -->
    <div class="section" id="section-governance">
      <h2>&#9878; Governance</h2>
      <div class="panel">
        <h3>Routing Policy</h3>
        <div id="policy-display"><div class="empty">Loading...</div></div>
      </div>
      <div class="panel">
        <h3>Risk Classes</h3>
        <div class="table-wrap">
          <table id="risk-classes-table">
            <thead><tr><th>Class</th><th>LCB Threshold</th><th>Verification Steps</th><th>Max Cost</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h3>Verification DAG</h3>
        <div id="verification-dag"><div class="empty">Select a risk class to view DAG</div></div>
      </div>
      <div class="panel">
        <h3>Policy Version</h3>
        <div id="policy-version"><div class="empty">Loading...</div></div>
      </div>
    </div>

    <!-- ========== ROUTING ========== -->
    <div class="section" id="section-routing">
      <h2>&#8661; Routing Portfolio</h2>
      <div class="panel">
        <h3>Portfolio</h3>
        <div class="table-wrap">
          <table id="portfolio-table">
            <thead><tr><th>Candidate ID</th><th>Model</th><th>Provider</th><th>Gateway</th><th>Cost</th><th>LCB</th><th>Capabilities</th><th>Actions</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h3>Add Candidate</h3>
        <div class="form-row">
          <div class="form-group"><label>Model ID</label><input type="text" id="cand-model" placeholder="model-id"></div>
          <div class="form-group"><label>Provider</label><input type="text" id="cand-provider" placeholder="openrouter"></div>
          <div class="form-group"><label>Gateway</label><select id="cand-gateway"><option value="openrouter">OpenRouter</option><option value="local">Local</option></select></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label>Cost per Token</label><input type="text" id="cand-cost" placeholder="0.000001"></div>
          <div class="form-group"><label>LCB</label><input type="text" id="cand-lcb" placeholder="0.95"></div>
          <div class="form-group"><label>Capabilities (comma-sep)</label><input type="text" id="cand-caps" placeholder="code,reason"></div>
        </div>
        <button class="btn btn-primary" onclick="addCandidate()">+ Add Candidate</button>
      </div>
    </div>

    <!-- ========== ANALYTICS ========== -->
    <div class="section" id="section-analytics">
      <h2>&#128200; Analytics</h2>
      <div class="time-range" id="analytics-timerange">
        <button data-days="7" onclick="setAnalyticsRange(7, this)">7d</button>
        <button data-days="30" class="active" onclick="setAnalyticsRange(30, this)">30d</button>
        <button data-days="90" onclick="setAnalyticsRange(90, this)">90d</button>
      </div>
      <div class="grid-2">
        <div class="panel"><h3>Cost by Model</h3><div id="cost-breakdown"><div class="empty">Loading...</div></div></div>
        <div class="panel"><h3>Cost Trend</h3><div class="chart"><canvas id="analytics-cost-chart"></canvas></div></div>
      </div>
      <div class="grid-2">
        <div class="panel"><h3>Performance: Accuracy/Latency</h3><div class="chart"><canvas id="analytics-perf-chart"></canvas></div></div>
        <div class="panel"><h3>Usage: Request Volume</h3><div class="chart"><canvas id="analytics-usage-chart"></canvas></div></div>
      </div>
      <div class="grid-2">
        <div class="panel"><h3>Escalation: HIR/RR Trends</h3><div class="chart"><canvas id="analytics-esc-chart"></canvas></div></div>
        <div class="panel"><h3>Escalation by Model</h3><div id="esc-by-model"><div class="empty">Loading...</div></div></div>
      </div>
      <div class="panel"><h3>Cost Anomalies</h3><div id="cost-anomalies"><div class="empty">Loading...</div></div></div>
    </div>

    <!-- ========== LEDGER ========== -->
    <div class="section" id="section-ledger">
      <h2>&#128214; Epistemic Ledger</h2>
      <div class="panel">
        <h3>Run Selector</h3>
        <div class="form-row">
          <div class="form-group" style="flex:1"><input type="text" id="ledger-run-id" placeholder="Run ID or search..."></div>
          <button class="btn btn-primary" onclick="loadLedgerEvents()">Load</button>
        </div>
      </div>
      <div class="panel">
        <h3>Ledger Events</h3>
        <div class="table-wrap">
          <table id="ledger-events-table">
            <thead><tr><th>Timestamp</th><th>Event Type</th><th>Actor</th><th>Subject</th><th>Hash</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h3>Decision Trace</h3>
        <div id="decision-trace"><div class="empty">Select a run to view trace</div></div>
      </div>
      <div class="btn-group">
        <button class="btn btn-primary" onclick="verifyChain()">&#10003; Verify Chain</button>
        <button class="btn btn-outline" onclick="exportChain()">&#128229; Export</button>
      </div>
      <div id="chain-verify-result" style="margin-top:12px;"></div>
    </div>

    <!-- ========== TENANTS ========== -->
    <div class="section" id="section-tenants">
      <h2>&#128101; Tenants</h2>
      <div class="panel">
        <h3>Tenant List</h3>
        <div class="table-wrap">
          <table id="tenants-table">
            <thead><tr><th>Name</th><th>Budget</th><th>Spend</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h3>Create Tenant</h3>
        <div class="form-row">
          <div class="form-group"><label>Name</label><input type="text" id="tenant-name" placeholder="tenant-name"></div>
          <div class="form-group"><label>Budget (USD)</label><input type="text" id="tenant-budget" placeholder="100.00"></div>
        </div>
        <button class="btn btn-primary" onclick="createTenant()">+ Create Tenant</button>
      </div>
    </div>

    <!-- ========== API KEYS ========== -->
    <div class="section" id="section-apikeys">
      <h2>&#128273; API Keys</h2>
      <div class="panel">
        <h3>Keys</h3>
        <div class="table-wrap">
          <table id="apikeys-table">
            <thead><tr><th>Name</th><th>Role</th><th>Created</th><th>Last Used</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h3>Create Key</h3>
        <div class="form-row">
          <div class="form-group"><label>Name</label><input type="text" id="key-name" placeholder="key-name"></div>
          <div class="form-group"><label>Role</label><select id="key-role"><option value="admin">Admin</option><option value="operator">Operator</option><option value="readonly">Read Only</option></select></div>
          <div class="form-group"><label>Rate Limit (req/min)</label><input type="text" id="key-rate" placeholder="60"></div>
        </div>
        <button class="btn btn-primary" onclick="createApiKey()">+ Create Key</button>
        <div id="key-reveal-area" style="display:none; margin-top:12px;">
          <div class="key-reveal" id="key-reveal-value"></div>
          <button class="btn btn-sm btn-outline" onclick="copyKey()">&#128203; Copy</button>
        </div>
      </div>
    </div>

    <!-- ========== ALERTS ========== -->
    <div class="section" id="section-alerts">
      <h2>&#128276; Alerts</h2>
      <div class="panel">
        <h3>Active Alerts</h3>
        <div id="active-alerts-list"><div class="empty">Loading...</div></div>
      </div>
      <div class="panel">
        <h3>Alert Rules</h3>
        <div id="alert-rules"><div class="empty">Loading...</div></div>
      </div>
      <div class="panel">
        <h3>Alert History</h3>
        <div id="alert-history"><div class="empty">Loading...</div></div>
      </div>
    </div>

    <!-- ========== WEBHOOKS ========== -->
    <div class="section" id="section-webhooks">
      <h2>&#128279; Webhooks</h2>
      <div class="panel">
        <h3>Registered Webhooks</h3>
        <div class="table-wrap">
          <table id="webhooks-table">
            <thead><tr><th>URL</th><th>Events</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h3>Register Webhook</h3>
        <div class="form-row">
          <div class="form-group"><label>URL</label><input type="text" id="webhook-url" placeholder="https://example.com/hook"></div>
          <div class="form-group"><label>Events (comma-sep)</label><input type="text" id="webhook-events" placeholder="run.completed,alert.created"></div>
        </div>
        <button class="btn btn-primary" onclick="registerWebhook()">+ Register</button>
      </div>
    </div>

    <!-- ========== CONFIG ========== -->
    <div class="section" id="section-config">
      <h2>&#9881; Configuration</h2>
      <div class="panel">
        <h3>Config Values</h3>
        <div class="table-wrap">
          <table id="config-table">
            <thead><tr><th>Key</th><th>Value</th><th>Updated</th><th>Actions</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h3>Environment Variables</h3>
        <div id="env-vars"><div class="empty">Loading...</div></div>
      </div>
    </div>

    <!-- ========== AUDIT LOG ========== -->
    <div class="section" id="section-audit">
      <h2>&#128269; Audit Log</h2>
      <div class="panel">
        <h3>Filters</h3>
        <div class="form-row">
          <div class="form-group"><label>Actor</label><input type="text" id="audit-actor" placeholder="admin"></div>
          <div class="form-group"><label>Action</label><input type="text" id="audit-action" placeholder="api_call"></div>
          <div class="form-group"><label>Time Range</label><select id="audit-range"><option value="24h">Last 24h</option><option value="7d">Last 7d</option><option value="30d">Last 30d</option></select></div>
        </div>
        <button class="btn btn-primary" onclick="loadAuditLog()">Apply Filters</button>
      </div>
      <div class="panel">
        <h3>Audit Entries</h3>
        <div class="table-wrap">
          <table id="audit-table">
            <thead><tr><th>Timestamp</th><th>Actor</th><th>Action</th><th>Resource</th><th>IP</th><th>Success</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h3>Anomaly Detection</h3>
        <div id="audit-anomalies"><div class="empty">Loading...</div></div>
      </div>
    </div>

    <!-- ========== SETTINGS ========== -->
    <div class="section" id="section-settings">
      <h2>&#128295; Settings</h2>
      <div class="grid-2">
        <div class="panel">
          <h3>Gateway Mode</h3>
          <div id="settings-mode"><div class="empty">Loading...</div></div>
          <div class="btn-group" style="margin-top:12px;">
            <button class="btn btn-outline" onclick="setGatewayMode('live')">Switch to Live</button>
            <button class="btn btn-outline" onclick="setGatewayMode('stub')">Switch to Stub</button>
          </div>
        </div>
        <div class="panel">
          <h3>Cache</h3>
          <div id="settings-cache"><div class="empty">Loading...</div></div>
          <div class="btn-group" style="margin-top:12px;">
            <button class="btn btn-outline" onclick="toggleCache()">Toggle Cache</button>
          </div>
        </div>
      </div>
      <div class="grid-2">
        <div class="panel">
          <h3>TLS Status</h3>
          <div id="settings-tls"><div class="empty">Loading...</div></div>
        </div>
        <div class="panel">
          <h3>Database Status</h3>
          <div id="settings-db"><div class="empty">Loading...</div></div>
        </div>
      </div>
      <div class="panel">
        <h3>Backup & Restore</h3>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="runBackup()">&#128190; Backup</button>
          <button class="btn btn-outline" onclick="runRestore()">&#128194; Restore</button>
          <button class="btn btn-outline" onclick="runExport()">&#128229; Export</button>
          <button class="btn btn-outline" onclick="runImport()">&#128228; Import</button>
        </div>
        <div id="backup-result" style="margin-top:12px;"></div>
      </div>
    </div>

  </div><!-- /content -->
</div><!-- /main-wrap -->

<!-- Toast container -->
<div id="toast-container"></div>

<!-- Modal -->
<div class="modal-overlay" id="modal-overlay">
  <div class="modal" id="modal-box">
    <h3 id="modal-title">Confirm</h3>
    <p id="modal-body">Are you sure?</p>
    <div class="modal-actions">
      <button class="btn btn-outline" onclick="closeModal()">Cancel</button>
      <button class="btn btn-danger" id="modal-confirm-btn" onclick="confirmModal()">Confirm</button>
    </div>
  </div>
</div>

<script>
// =========================================================================
// State
// =========================================================================
var API_BASE = '/v1/analytics';
var currentSection = 'overview';
var autoRefresh = true;
var autoRefreshInterval = null;
var analyticsDays = 30;
var modalCallback = null;
var currentSortCol = null;
var currentSortDir = 'asc';
var copiedKeyValue = null;

// =========================================================================
// Utilities
// =========================================================================
function fmt(n, d) { d = d || 2; return Number(n || 0).toFixed(d); }
function fmtPct(n) { return fmt(n, 1) + '%'; }
function fmtUsd(n) { return '$' + fmt(n, 6); }
function fmtMs(n) { return fmt(n, 1) + 'ms'; }
function fmtShort(s, n) { n = n || 12; return s ? (s.length > n ? s.substring(0, n) + '...' : s) : 'N/A'; }
function fmtTs(ts) { if (!ts) return 'N/A'; return ts.replace('T', ' ').substring(0, 19); }
function statusBadge(s) {
  var cls = s === 'accepted' ? 'badge-success' : s === 'escalated' ? 'badge-danger' : s === 'failed' ? 'badge-danger' : 'badge-warning';
  return '<span class="badge ' + cls + '">' + s + '</span>';
}
function rankBadge(n) {
  var cls = n === 1 ? 'rank-1' : n === 2 ? 'rank-2' : n === 3 ? 'rank-3' : '';
  return '<span class="rank-num ' + cls + '">' + n + '</span>';
}
function boolBadge(v) {
  return v ? '<span class="badge badge-success">Yes</span>' : '<span class="badge badge-danger">No</span>';
}

// =========================================================================
// Toast
// =========================================================================
function showToast(msg, type) {
  type = type || 'info';
  var container = document.getElementById('toast-container');
  var el = document.createElement('div');
  el.className = 'toast toast-' + type;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(function() { el.remove(); }, 4000);
}

// =========================================================================
// Modal
// =========================================================================
function showModal(title, body, cb) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').textContent = body;
  document.getElementById('modal-overlay').classList.add('show');
  modalCallback = cb;
}
function closeModal() {
  document.getElementById('modal-overlay').classList.remove('show');
  modalCallback = null;
}
function confirmModal() {
  if (modalCallback) modalCallback();
  closeModal();
}

// =========================================================================
// Navigation
// =========================================================================
function switchSection(name) {
  currentSection = name;
  document.querySelectorAll('#sidebar nav a').forEach(function(a) { a.classList.remove('active'); });
  var link = document.querySelector('#sidebar nav a[data-section="' + name + '"]');
  if (link) link.classList.add('active');
  document.querySelectorAll('.section').forEach(function(s) { s.classList.remove('active'); });
  var sec = document.getElementById('section-' + name);
  if (sec) sec.classList.add('active');
  localStorage.setItem('noerelay_section', name);
  loadSection(name);
}

function loadSection(name) {
  switch (name) {
    case 'overview': loadOverview(); break;
    case 'models': loadModels(); break;
    case 'benchmarks': loadBenchmarks(); break;
    case 'governance': loadGovernance(); break;
    case 'routing': loadRouting(); break;
    case 'analytics': loadAnalytics(); break;
    case 'ledger': loadLedger(); break;
    case 'tenants': loadTenants(); break;
    case 'apikeys': loadApiKeys(); break;
    case 'alerts': loadAlertsPage(); break;
    case 'webhooks': loadWebhooks(); break;
    case 'config': loadConfig(); break;
    case 'audit': loadAuditLog(); break;
    case 'settings': loadSettings(); break;
  }
}

function refreshCurrent() {
  loadSection(currentSection);
  showToast('Refreshed', 'info');
}

// =========================================================================
// Auto-refresh
// =========================================================================
function toggleAutoRefresh() {
  autoRefresh = !autoRefresh;
  var el = document.getElementById('auto-refresh-toggle');
  el.innerHTML = autoRefresh ? '&#8635; Auto: ON' : '&#8635; Auto: OFF';
  localStorage.setItem('noerelay_autorefresh', autoRefresh ? '1' : '0');
  if (autoRefresh) startAutoRefresh(); else stopAutoRefresh();
}

function startAutoRefresh() {
  stopAutoRefresh();
  autoRefreshInterval = setInterval(function() { loadSection(currentSection); }, 30000);
}

function stopAutoRefresh() {
  if (autoRefreshInterval) { clearInterval(autoRefreshInterval); autoRefreshInterval = null; }
}

// =========================================================================
// Fetch
// =========================================================================
async function fetchJSON(url, opts) {
  var resp = await fetch(url, opts);
  if (!resp.ok) {
    var txt = '';
    try { txt = await resp.text(); } catch(e) {}
    throw new Error('HTTP ' + resp.status + (txt ? ': ' + txt.substring(0, 200) : ''));
  }
  return resp.json();
}

// =========================================================================
// Charts
// =========================================================================
function drawBarChart(canvasId, data, labelKey, valueKey, color) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var dpr = window.devicePixelRatio || 1;
  var rect = canvas.parentElement.getBoundingClientRect();
  if (rect.width === 0) return;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  ctx.scale(dpr, dpr);
  var w = rect.width - 10, h = rect.height - 30;
  if (!data || !data.length) { ctx.fillStyle = '#6b7280'; ctx.font = '14px system-ui'; ctx.fillText('No data', w/2-30, h/2); return; }
  var vals = data.map(function(d) { return d[valueKey] || 0; });
  var max = Math.max.apply(null, vals) || 1;
  var barW = Math.max(2, (w / data.length) - 2);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.strokeStyle = '#2a2d3a'; ctx.lineWidth = 0.5;
  for (var i = 0; i <= 4; i++) { var y = 10 + (h * i / 4); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  for (var i = 0; i < data.length; i++) {
    var bh = (vals[i] / max) * h;
    ctx.fillStyle = color || '#22c55e';
    ctx.fillRect(i * (barW + 2), h - bh + 10, barW, bh);
  }
  ctx.fillStyle = '#9ca3b8'; ctx.font = '9px system-ui';
  var step = Math.max(1, Math.floor(data.length / 7));
  for (var i = 0; i < data.length; i += step) {
    var label = (data[i][labelKey] || '').substring(5);
    ctx.fillText(label, i * (barW + 2), h + 25);
  }
  ctx.fillText(fmt(max, 4), w - 40, 18);
}

function drawLineChart(canvasId, data, valueKey, color) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var dpr = window.devicePixelRatio || 1;
  var rect = canvas.parentElement.getBoundingClientRect();
  if (rect.width === 0) return;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  ctx.scale(dpr, dpr);
  var w = rect.width - 10, h = rect.height - 30;
  if (!data || !data.length) { ctx.fillStyle = '#6b7280'; ctx.font = '14px system-ui'; ctx.fillText('No data', w/2-30, h/2); return; }
  var vals = data.map(function(d) { return d[valueKey] || 0; });
  var max = Math.max.apply(null, vals) || 1;
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.strokeStyle = '#2a2d3a'; ctx.lineWidth = 0.5;
  for (var i = 0; i <= 4; i++) { var y = 10 + (h * i / 4); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  ctx.strokeStyle = color || '#3b82f6'; ctx.lineWidth = 2; ctx.beginPath();
  var xStep = data.length > 1 ? w / (data.length - 1) : w;
  for (var i = 0; i < data.length; i++) {
    var x = i * xStep, y = h - (vals[i] / max * h) + 10;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.lineTo((data.length - 1) * xStep, h + 10); ctx.lineTo(0, h + 10); ctx.closePath();
  ctx.fillStyle = (color || '#3b82f6') + '20'; ctx.fill();
  ctx.fillStyle = '#9ca3b8'; ctx.font = '9px system-ui';
  var step = Math.max(1, Math.floor(data.length / 7));
  for (var i = 0; i < data.length; i += step) {
    var label = (data[i].day || data[i].period || '').substring(5);
    ctx.fillText(label, i * xStep, h + 25);
  }
  ctx.fillText(fmt(max, 4), w - 40, 18);
}

// =========================================================================
// Table sorting
// =========================================================================
function sortTable(tableId, colIdx) {
  var table = document.getElementById(tableId);
  if (!table) return;
  var tbody = table.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));
  var ths = table.querySelectorAll('th');
  var dir = 'asc';
  if (currentSortCol === tableId + '-' + colIdx) {
    dir = currentSortDir === 'asc' ? 'desc' : 'asc';
  }
  currentSortCol = tableId + '-' + colIdx;
  currentSortDir = dir;
  ths.forEach(function(th, i) { th.classList.remove('asc', 'desc'); });
  if (ths[colIdx]) ths[colIdx].classList.add(dir);
  rows.sort(function(a, b) {
    var va = (a.cells[colIdx] ? a.cells[colIdx].textContent : '').trim();
    var vb = (b.cells[colIdx] ? b.cells[colIdx].textContent : '').trim();
    var na = parseFloat(va.replace(/[$,]/g, ''));
    var nb = parseFloat(vb.replace(/[$,]/g, ''));
    if (!isNaN(na) && !isNaN(nb)) return dir === 'asc' ? na - nb : nb - na;
    return dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
  });
  rows.forEach(function(r) { tbody.appendChild(r); });
}

// =========================================================================
// OVERVIEW
// =========================================================================
async function loadOverview() {
  try {
    var data = await fetchJSON(API_BASE + '/dashboard');
    var s = data.summary || {};
    document.getElementById('v-total-runs').textContent = s.total_runs || 0;
    document.getElementById('v-accuracy').textContent = fmtPct(s.accuracy);
    document.getElementById('v-cost-today').textContent = fmtUsd(s.cost_today_usd);
    document.getElementById('v-hir').textContent = fmtPct(s.hir);
    document.getElementById('v-rr').textContent = fmtPct(s.rr);
    document.getElementById('v-active-models').textContent = s.active_models || '-';

    drawBarChart('cost-chart', data.cost_trend || [], 'day', 'total_cost', '#22c55e');
    drawLineChart('usage-chart', data.usage_trend || [], 'total_tokens', '#3b82f6');

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

    // Alerts
    loadAlertsWidget();

    document.getElementById('connection-status').innerHTML = '<span class="status-dot green"></span>Connected';
  } catch(e) {
    document.getElementById('connection-status').innerHTML = '<span class="status-dot red"></span>Disconnected';
    console.error('Dashboard load error:', e);
  }
}

async function loadAlertsWidget() {
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

// =========================================================================
// MODELS
// =========================================================================
async function loadModels() {
  try {
    // Local models
    var localData = await fetchJSON('/models/local');
    var localTbody = document.querySelector('#local-models-table tbody');
    localTbody.innerHTML = '';
    (localData.models || []).forEach(function(m) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td><strong>' + (m.name || m.id || 'N/A') + '</strong></td>' +
        '<td>' + (m.size || '-') + '</td>' +
        '<td>' + (m.specialization || '-') + '</td>' +
        '<td>' + (m.status || '-') + '</td>' +
        '<td><button class="btn btn-xs btn-danger" onclick="removeModel(\'' + (m.name || m.id || '') + '\')">Remove</button></td>';
      localTbody.appendChild(tr);
    });

    // Cloud models
    var cloudData = await fetchJSON('/models/cloud');
    var cloudTbody = document.querySelector('#cloud-models-table tbody');
    cloudTbody.innerHTML = '';
    (cloudData.models || []).forEach(function(m) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td><strong>' + (m.name || m.id || 'N/A') + '</strong></td>' +
        '<td>' + (m.pricing || '-') + '</td>' +
        '<td>' + (m.context_length || '-') + '</td>' +
        '<td>' + (m.provider || '-') + '</td>';
      cloudTbody.appendChild(tr);
    });

    // Model ranking
    var rankData = await fetchJSON('/models/ranking');
    var rankTbody = document.querySelector('#models-ranking-table tbody');
    rankTbody.innerHTML = '';
    (rankData.ranking || rankData.models || []).forEach(function(m, i) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + rankBadge(m.rank || (i + 1)) + '</td>' +
        '<td><strong>' + (m.model_id || 'N/A') + '</strong></td>' +
        '<td>' + fmtPct(m.accuracy) + '</td>' +
        '<td>' + fmtUsd(m.true_cost_per_correct) + '</td>' +
        '<td>' + fmtMs(m.avg_latency_ms) + '</td>' +
        '<td>' + fmtPct(m.hir_rate) + '</td>' +
        '<td>' + fmtPct(m.rework_rate) + '</td>';
      rankTbody.appendChild(tr);
    });

    // Recommendations
    var recsData = await fetchJSON('/models/recommendations');
    var dlDiv = document.getElementById('download-recs');
    var rmDiv = document.getElementById('removal-recs');
    dlDiv.innerHTML = '';
    rmDiv.innerHTML = '';
    var downloads = recsData.download || recsData.recommendations || [];
    var removals = recsData.remove || recsData.removals || [];
    if (!downloads.length) dlDiv.innerHTML = '<div class="empty">No recommendations</div>';
    downloads.forEach(function(m) {
      var div = document.createElement('div');
      div.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);';
      div.innerHTML = '<span>' + (m.name || m.model_id || 'N/A') + ' <small style="color:var(--text3)">' + (m.reason || '') + '</small></span>' +
        '<button class="btn btn-xs btn-primary" onclick="pullModel(\'' + (m.name || m.model_id || '') + '\')">Pull</button>';
      dlDiv.appendChild(div);
    });
    if (!removals.length) rmDiv.innerHTML = '<div class="empty">No recommendations</div>';
    removals.forEach(function(m) {
      var div = document.createElement('div');
      div.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);';
      div.innerHTML = '<span>' + (m.name || m.model_id || 'N/A') + ' <small style="color:var(--text3)">' + (m.reason || '') + '</small></span>' +
        '<button class="btn btn-xs btn-danger" onclick="removeModel(\'' + (m.name || m.model_id || '') + '\')">Remove</button>';
      rmDiv.appendChild(div);
    });
  } catch(e) {
    console.error('Models load error:', e);
  }
}

async function pullModel(name) {
  try {
    await fetchJSON('/v1/models/pull', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({model: name}) });
    showToast('Pull initiated for ' + name, 'success');
  } catch(e) { showToast('Pull failed: ' + e.message, 'error'); }
}

async function removeModel(name) {
  showModal('Remove Model', 'Remove ' + name + '? This cannot be undone.', async function() {
    try {
      await fetchJSON('/v1/models/' + encodeURIComponent(name), { method: 'DELETE' });
      showToast('Model removed: ' + name, 'success');
      loadModels();
    } catch(e) { showToast('Remove failed: ' + e.message, 'error'); }
  });
}

function searchHF() {
  var q = document.getElementById('hf-search').value.trim();
  if (!q) return;
  var url = 'https://huggingface.co/models?search=' + encodeURIComponent(q);
  window.open(url, '_blank');
}

// =========================================================================
// BENCHMARKS
// =========================================================================
async function loadBenchmarks() {
  try {
    // Load datasets
    var dsData = await fetchJSON('/v1/benchmarks/datasets');
    var dsSelect = document.getElementById('bm-dataset');
    dsSelect.innerHTML = '';
    (dsData.datasets || []).forEach(function(d) {
      dsSelect.innerHTML += '<option value="' + d + '">' + d + '</option>';
    });
    if (!dsSelect.innerHTML) dsSelect.innerHTML = '<option value="">No datasets found</option>';

    // Load results
    var resData = await fetchJSON('/v1/benchmarks/results?limit=50');
    var tbody = document.querySelector('#benchmark-results-table tbody');
    tbody.innerHTML = '';
    (resData.results || []).forEach(function(r) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + (r.cohort_name || '-') + '</td>' +
        '<td><strong>' + (r.model_id || '-') + '</strong></td>' +
        '<td>' + fmtPct(r.accuracy) + '</td>' +
        '<td>' + fmt(r.total_tokens, 0) + '</td>' +
        '<td>' + fmtUsd(r.total_cost_usd) + '</td>' +
        '<td>' + fmtMs(r.mean_latency_ms) + '</td>' +
        '<td>' + fmtTs(r.created_at || r.timestamp) + '</td>';
      tbody.appendChild(tr);
    });

    // Model comparison
    var cmpData = await fetchJSON('/v1/benchmarks/compare');
    var cmpDiv = document.getElementById('model-comparison');
    if (cmpData.comparison && cmpData.comparison.length) {
      var html = '<div class="table-wrap"><table><thead><tr><th>Model</th><th>Accuracy</th><th>Cost</th><th>Latency</th><th>HIR</th><th>RR</th></tr></thead><tbody>';
      cmpData.comparison.forEach(function(m) {
        html += '<tr><td><strong>' + (m.model_id || '-') + '</strong></td>' +
          '<td>' + fmtPct(m.accuracy) + '</td>' +
          '<td>' + fmtUsd(m.total_cost_usd) + '</td>' +
          '<td>' + fmtMs(m.mean_latency_ms) + '</td>' +
          '<td>' + fmtPct(m.hir) + '</td>' +
          '<td>' + fmtPct(m.rr) + '</td></tr>';
      });
      html += '</tbody></table></div>';
      cmpDiv.innerHTML = html;
    } else {
      cmpDiv.innerHTML = '<div class="empty">No comparison data</div>';
    }
  } catch(e) {
    console.error('Benchmarks load error:', e);
  }
}

async function runBenchmark() {
  var dataset = document.getElementById('bm-dataset').value;
  var cohort = document.getElementById('bm-cohort').value.trim();
  var evaluator = document.getElementById('bm-evaluator').value;
  var preferLocal = document.getElementById('bm-prefer-local').checked;
  if (!dataset || !cohort) { showToast('Dataset and cohort name required', 'error'); return; }
  try {
    var resp = await fetchJSON('/v1/benchmarks/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({dataset: dataset, cohort_name: cohort, evaluator: evaluator, prefer_local: preferLocal})
    });
    showToast('Benchmark started: ' + (resp.run_id || 'ok'), 'success');
    setTimeout(loadBenchmarks, 2000);
  } catch(e) { showToast('Benchmark failed: ' + e.message, 'error'); }
}

async function runInlineBenchmark() {
  try {
    var resp = await fetchJSON('/v1/benchmarks/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({dataset: 'quick-test.jsonl', cohort_name: 'inline-' + Date.now(), evaluator: 'exact_match', prefer_local: true})
    });
    showToast('Inline benchmark started', 'success');
    setTimeout(loadBenchmarks, 2000);
  } catch(e) { showToast('Inline benchmark failed: ' + e.message, 'error'); }
}

// =========================================================================
// GOVERNANCE
// =========================================================================
async function loadGovernance() {
  try {
    // Policy
    var polData = await fetchJSON('/v1/governance/policy');
    var polDiv = document.getElementById('policy-display');
    var pol = polData.policy || polData;
    polDiv.innerHTML = '<div style="font-size:0.85em;">' +
      '<p><strong>Forbidden Models:</strong> ' + (pol.forbidden_models || []).join(', ') || 'None' + '</p>' +
      '<p><strong>Allowed Gateways:</strong> ' + (pol.allowed_gateways || []).join(', ') || 'All' + '</p>' +
      '<p><strong>Risk Thresholds:</strong> ' + JSON.stringify(pol.risk_thresholds || {}) + '</p>' +
      '</div>';

    // Risk classes
    var rcData = await fetchJSON('/v1/governance/risk-classes');
    var rcTbody = document.querySelector('#risk-classes-table tbody');
    rcTbody.innerHTML = '';
    (rcData.risk_classes || rcData.classes || []).forEach(function(rc) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td><strong>' + (rc.name || rc.class || '-') + '</strong></td>' +
        '<td>' + (rc.lcb_threshold || '-') + '</td>' +
        '<td>' + (rc.verification_steps || '-') + '</td>' +
        '<td>' + (rc.max_cost || '-') + '</td>';
      tr.style.cursor = 'pointer';
      tr.onclick = function() { showVerificationDAG(rc); };
      rcTbody.appendChild(tr);
    });

    // Policy version
    document.getElementById('policy-version').innerHTML = '<p style="font-size:0.85em;">Version: ' + (polData.version || 'N/A') + ' | Updated: ' + fmtTs(polData.updated_at) + '</p>';
  } catch(e) {
    console.error('Governance load error:', e);
  }
}

function showVerificationDAG(rc) {
  var dagDiv = document.getElementById('verification-dag');
  var steps = rc.verification_steps || rc.steps || [];
  if (!steps.length) { dagDiv.innerHTML = '<div class="empty">No verification steps defined</div>'; return; }
  var html = '<div style="font-size:0.85em;"><strong>' + (rc.name || rc.class || 'Risk Class') + ' DAG:</strong><br>';
  steps.forEach(function(s, i) {
    html += '<span style="display:inline-block;padding:4px 10px;margin:4px;background:var(--bg3);border-radius:4px;">' + s + '</span>';
    if (i < steps.length - 1) html += ' &#8594; ';
  });
  html += '</div>';
  dagDiv.innerHTML = html;
}

// =========================================================================
// ROUTING
// =========================================================================
async function loadRouting() {
  try {
    var data = await fetchJSON('/v1/routing/portfolio');
    var tbody = document.querySelector('#portfolio-table tbody');
    tbody.innerHTML = '';
    (data.portfolio || data.candidates || []).forEach(function(c) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + fmtShort(c.candidate_id || c.id, 16) + '</td>' +
        '<td><strong>' + (c.model_id || '-') + '</strong></td>' +
        '<td>' + (c.provider || '-') + '</td>' +
        '<td>' + (c.gateway || '-') + '</td>' +
        '<td>' + fmtUsd(c.cost_per_token) + '</td>' +
        '<td>' + fmt(c.lcb || c.lower_confidence_bound, 4) + '</td>' +
        '<td>' + (c.capabilities || []).join(', ') + '</td>' +
        '<td><button class="btn btn-xs btn-danger" onclick="removeCandidate(\'' + (c.candidate_id || c.id || '') + '\')">Remove</button></td>';
      tbody.appendChild(tr);
    });
  } catch(e) {
    console.error('Routing load error:', e);
  }
}

async function addCandidate() {
  var model = document.getElementById('cand-model').value.trim();
  var provider = document.getElementById('cand-provider').value.trim();
  var gateway = document.getElementById('cand-gateway').value;
  var cost = document.getElementById('cand-cost').value.trim();
  var lcb = document.getElementById('cand-lcb').value.trim();
  var caps = document.getElementById('cand-caps').value.trim();
  if (!model) { showToast('Model ID required', 'error'); return; }
  try {
    await fetchJSON('/v1/routing/candidates', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        model_id: model, provider: provider || 'openrouter', gateway: gateway,
        cost_per_token: parseFloat(cost) || 0, lcb: parseFloat(lcb) || 0.95,
        capabilities: caps ? caps.split(',').map(function(s) { return s.trim(); }) : []
      })
    });
    showToast('Candidate added', 'success');
    loadRouting();
    document.getElementById('cand-model').value = '';
    document.getElementById('cand-provider').value = '';
    document.getElementById('cand-cost').value = '';
    document.getElementById('cand-lcb').value = '';
    document.getElementById('cand-caps').value = '';
  } catch(e) { showToast('Add failed: ' + e.message, 'error'); }
}

async function removeCandidate(id) {
  showModal('Remove Candidate', 'Remove candidate ' + id + '?', async function() {
    try {
      await fetchJSON('/v1/routing/candidates/' + encodeURIComponent(id), { method: 'DELETE' });
      showToast('Candidate removed', 'success');
      loadRouting();
    } catch(e) { showToast('Remove failed: ' + e.message, 'error'); }
  });
}

// =========================================================================
// ANALYTICS
// =========================================================================
function setAnalyticsRange(days, btn) {
  analyticsDays = days;
  document.querySelectorAll('#analytics-timerange button').forEach(function(b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  loadAnalytics();
}

async function loadAnalytics() {
  var d = analyticsDays;
  try {
    // Cost breakdown
    var costData = await fetchJSON('/v1/analytics/cost?days=' + d);
    var cbDiv = document.getElementById('cost-breakdown');
    if (costData.groups && costData.groups.length) {
      var html = '<div class="table-wrap"><table><thead><tr><th>Model</th><th>Cost</th><th>Runs</th></tr></thead><tbody>';
      costData.groups.forEach(function(g) {
        html += '<tr><td><strong>' + (g.model_id || g.group || '-') + '</strong></td><td>' + fmtUsd(g.total_cost_usd) + '</td><td>' + (g.total_runs || 0) + '</td></tr>';
      });
      html += '</tbody></table></div>';
      cbDiv.innerHTML = html;
    } else { cbDiv.innerHTML = '<div class="empty">No cost data</div>'; }

    // Cost trend
    drawBarChart('analytics-cost-chart', costData.trend || costData.daily || [], 'day', 'total_cost', '#22c55e');

    // Performance
    var perfData = await fetchJSON('/v1/analytics/performance?days=' + d);
    drawLineChart('analytics-perf-chart', perfData.trend || perfData.daily || [], 'accuracy', '#818cf8');

    // Usage
    var usageData = await fetchJSON('/v1/analytics/usage?days=' + d);
    drawLineChart('analytics-usage-chart', usageData.trend || usageData.daily || [], 'total_tokens', '#06b6d4');

    // Escalations
    var escData = await fetchJSON('/v1/analytics/escalations?days=' + d);
    drawLineChart('analytics-esc-chart', escData.trend || escData.daily || [], 'hir_rate', '#eab308');

    // Escalation by model
    var escByDiv = document.getElementById('esc-by-model');
    if (escData.by_model && escData.by_model.length) {
      var ehtml = '<div class="table-wrap"><table><thead><tr><th>Model</th><th>HIR</th><th>RR</th><th>Escalations</th></tr></thead><tbody>';
      escData.by_model.forEach(function(m) {
        ehtml += '<tr><td><strong>' + (m.model_id || '-') + '</strong></td><td>' + fmtPct(m.hir_rate) + '</td><td>' + fmtPct(m.rework_rate) + '</td><td>' + (m.escalation_count || 0) + '</td></tr>';
      });
      ehtml += '</tbody></table></div>';
      escByDiv.innerHTML = ehtml;
    } else { escByDiv.innerHTML = '<div class="empty">No escalation data</div>'; }

    // Cost anomalies
    var anomDiv = document.getElementById('cost-anomalies');
    if (costData.anomalies && costData.anomalies.length) {
      var ahtml = '';
      costData.anomalies.forEach(function(a) {
        ahtml += '<div class="alert-item alert-warning"><strong>Anomaly:</strong> ' + (a.description || a.model_id || 'Unknown') + ' - ' + fmtUsd(a.cost) + ' <small>' + fmtTs(a.timestamp) + '</small></div>';
      });
      anomDiv.innerHTML = ahtml || '<div class="empty">No anomalies</div>';
    } else { anomDiv.innerHTML = '<div class="empty">No anomalies detected</div>'; }
  } catch(e) {
    console.error('Analytics load error:', e);
  }
}

// =========================================================================
// LEDGER
// =========================================================================
async function loadLedger(runId) {
  try {
    if (!runId) {
      // Try to load events without a run filter
      var eventsData = await fetchJSON('/v1/epr/ledger/events?limit=50');
      renderLedgerEvents(eventsData);
      return;
    }
    var eventsData = await fetchJSON('/v1/epr/ledger/events?run_id=' + encodeURIComponent(runId) + '&limit=100');
    renderLedgerEvents(eventsData);

    // Decision trace
    var traceData = await fetchJSON('/v1/epr/runs/' + encodeURIComponent(runId) + '/trace');
    var traceDiv = document.getElementById('decision-trace');
    if (traceData.events && traceData.events.length) {
      var html = '<div style="font-size:0.85em;">';
      traceData.events.forEach(function(ev) {
        html += '<div style="padding:6px 0;border-bottom:1px solid var(--border);">' +
          '<strong>' + (ev.event_type || 'Event') + '</strong> ' +
          '<small style="color:var(--text3)">' + fmtTs(ev.timestamp) + '</small><br>' +
          (ev.reasoning || ev.details || JSON.stringify(ev.data || {}).substring(0, 200)) +
          '</div>';
      });
      html += '</div>';
      traceDiv.innerHTML = html;
    } else {
      traceDiv.innerHTML = '<div class="empty">No trace data for this run</div>';
    }
  } catch(e) {
    console.error('Ledger load error:', e);
  }
}

function renderLedgerEvents(data) {
  var tbody = document.querySelector('#ledger-events-table tbody');
  tbody.innerHTML = '';
  (data.events || []).forEach(function(ev) {
    var tr = document.createElement('tr');
    tr.innerHTML = '<td>' + fmtTs(ev.timestamp) + '</td>' +
      '<td><span class="badge badge-info">' + (ev.event_type || '-') + '</span></td>' +
      '<td>' + (ev.actor || '-') + '</td>' +
      '<td>' + fmtShort(ev.subject || ev.run_id || '-', 20) + '</td>' +
      '<td><code style="font-size:0.75em;">' + fmtShort(ev.hash || ev.event_hash || '-', 12) + '</code></td>';
    tbody.appendChild(tr);
  });
}

function loadLedgerEvents() {
  var runId = document.getElementById('ledger-run-id').value.trim();
  loadLedger(runId || null);
}

async function verifyChain() {
  var runId = document.getElementById('ledger-run-id').value.trim();
  if (!runId) { showToast('Enter a run ID first', 'error'); return; }
  try {
    var data = await fetchJSON('/v1/epr/ledger/verify/' + encodeURIComponent(runId), { method: 'POST' });
    var div = document.getElementById('chain-verify-result');
    if (data.valid) {
      div.innerHTML = '<div class="alert-item alert-info"><strong>&#10003; Chain Verified</strong> - Hash: ' + fmtShort(data.chain_hash || '', 16) + '</div>';
    } else {
      div.innerHTML = '<div class="alert-item alert-critical"><strong>&#10007; Verification Failed</strong> - ' + (data.reason || 'Unknown error') + '</div>';
    }
  } catch(e) { showToast('Verification failed: ' + e.message, 'error'); }
}

async function exportChain() {
  var runId = document.getElementById('ledger-run-id').value.trim();
  if (!runId) { showToast('Enter a run ID first', 'error'); return; }
  try {
    var data = await fetchJSON('/v1/epr/ledger/export/' + encodeURIComponent(runId));
    var blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = 'ledger-' + runId + '.json'; a.click();
    URL.revokeObjectURL(url);
    showToast('Exported', 'success');
  } catch(e) { showToast('Export failed: ' + e.message, 'error'); }
}

// =========================================================================
// TENANTS
// =========================================================================
async function loadTenants() {
  try {
    var data = await fetchJSON('/v1/tenants');
    var tbody = document.querySelector('#tenants-table tbody');
    tbody.innerHTML = '';
    (data.tenants || []).forEach(function(t) {
      var tr = document.createElement('tr');
      var budgetPct = t.budget ? ((t.spend || 0) / t.budget * 100) : 0;
      var statusCls = budgetPct > 90 ? 'badge-danger' : budgetPct > 70 ? 'badge-warning' : 'badge-success';
      tr.innerHTML = '<td><strong>' + (t.name || t.id || '-') + '</strong></td>' +
        '<td>' + fmtUsd(t.budget) + '</td>' +
        '<td>' + fmtUsd(t.spend || t.current_spend) + '</td>' +
        '<td><span class="badge ' + statusCls + '">' + (t.status || 'active') + '</span></td>' +
        '<td><button class="btn btn-xs btn-danger" onclick="deleteTenant(\'' + (t.id || t.name || '') + '\')">Delete</button></td>';
      tbody.appendChild(tr);
    });
  } catch(e) {
    console.error('Tenants load error:', e);
  }
}

async function createTenant() {
  var name = document.getElementById('tenant-name').value.trim();
  var budget = document.getElementById('tenant-budget').value.trim();
  if (!name) { showToast('Tenant name required', 'error'); return; }
  try {
    await fetchJSON('/v1/tenants', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, budget: parseFloat(budget) || 0})
    });
    showToast('Tenant created', 'success');
    loadTenants();
    document.getElementById('tenant-name').value = '';
    document.getElementById('tenant-budget').value = '';
  } catch(e) { showToast('Create failed: ' + e.message, 'error'); }
}

async function deleteTenant(id) {
  showModal('Delete Tenant', 'Delete tenant ' + id + '?', async function() {
    try {
      await fetchJSON('/v1/tenants/' + encodeURIComponent(id), { method: 'DELETE' });
      showToast('Tenant deleted', 'success');
      loadTenants();
    } catch(e) { showToast('Delete failed: ' + e.message, 'error'); }
  });
}

// =========================================================================
// API KEYS
// =========================================================================
async function loadApiKeys() {
  try {
    var data = await fetchJSON('/v1/api-keys');
    var tbody = document.querySelector('#apikeys-table tbody');
    tbody.innerHTML = '';
    (data.api_keys || data.keys || []).forEach(function(k) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td><strong>' + (k.name || k.id || '-') + '</strong></td>' +
        '<td>' + (k.role || '-') + '</td>' +
        '<td>' + fmtTs(k.created_at) + '</td>' +
        '<td>' + fmtTs(k.last_used_at) + '</td>' +
        '<td><span class="badge ' + (k.revoked ? 'badge-danger' : 'badge-success') + '">' + (k.revoked ? 'Revoked' : 'Active') + '</span></td>' +
        '<td><button class="btn btn-xs btn-outline" onclick="rotateKey(\'' + (k.id || k.name || '') + '\')">Rotate</button> ' +
        '<button class="btn btn-xs btn-danger" onclick="revokeKey(\'' + (k.id || k.name || '') + '\')">Revoke</button></td>';
      tbody.appendChild(tr);
    });
  } catch(e) {
    console.error('API Keys load error:', e);
  }
}

async function createApiKey() {
  var name = document.getElementById('key-name').value.trim();
  var role = document.getElementById('key-role').value;
  var rate = document.getElementById('key-rate').value.trim();
  if (!name) { showToast('Key name required', 'error'); return; }
  try {
    var data = await fetchJSON('/v1/api-keys', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, role: role, rate_limit: parseInt(rate) || 60})
    });
    var keyValue = data.key || data.api_key || data.secret || '';
    copiedKeyValue = keyValue;
    document.getElementById('key-reveal-value').textContent = keyValue;
    document.getElementById('key-reveal-area').style.display = 'block';
    showToast('Key created! Copy it now - it won\'t be shown again.', 'success');
    loadApiKeys();
    document.getElementById('key-name').value = '';
  } catch(e) { showToast('Create failed: ' + e.message, 'error'); }
}

function copyKey() {
  if (!copiedKeyValue) return;
  navigator.clipboard.writeText(copiedKeyValue).then(function() {
    showToast('Key copied to clipboard', 'success');
  }).catch(function() {
    showToast('Copy failed', 'error');
  });
}

async function revokeKey(id) {
  showModal('Revoke Key', 'Revoke key ' + id + '?', async function() {
    try {
      await fetchJSON('/v1/api-keys/' + encodeURIComponent(id), { method: 'DELETE' });
      showToast('Key revoked', 'success');
      loadApiKeys();
    } catch(e) { showToast('Revoke failed: ' + e.message, 'error'); }
  });
}

async function rotateKey(id) {
  showModal('Rotate Key', 'Rotate key ' + id + '? A new key will be generated.', async function() {
    try {
      var data = await fetchJSON('/v1/api-keys/' + encodeURIComponent(id) + '/rotate', { method: 'POST' });
      var keyValue = data.key || data.api_key || data.secret || '';
      copiedKeyValue = keyValue;
      document.getElementById('key-reveal-value').textContent = keyValue;
      document.getElementById('key-reveal-area').style.display = 'block';
      showToast('Key rotated! Copy it now.', 'success');
      loadApiKeys();
    } catch(e) { showToast('Rotate failed: ' + e.message, 'error'); }
  });
}

// =========================================================================
// ALERTS PAGE
// =========================================================================
async function loadAlertsPage() {
  try {
    var data = await fetchJSON('/v1/alerts?limit=50');
    var container = document.getElementById('active-alerts-list');
    var alerts = data.alerts || [];
    if (!alerts.length) { container.innerHTML = '<div class="empty">No alerts</div>'; }
    else {
      container.innerHTML = '';
      alerts.forEach(function(a) {
        var cls = a.severity === 'critical' ? 'alert-critical' : a.severity === 'warning' ? 'alert-warning' : 'alert-info';
        var div = document.createElement('div');
        div.className = 'alert-item ' + cls;
        div.innerHTML = '<strong>' + (a.alert_type || 'Alert') + '</strong> &mdash; ' + (a.message || '') +
          '<br><small>' + fmtTs(a.timestamp) + ' | ' + (a.acknowledged ? 'Acknowledged' : 'Pending') + '</small> ' +
          (!a.acknowledged ? '<button class="btn btn-xs btn-outline" onclick="ackAlert(\'' + (a.id || '') + '\')">Acknowledge</button>' : '');
        container.appendChild(div);
      });
    }

    // Alert rules
    var rulesDiv = document.getElementById('alert-rules');
    if (data.rules && data.rules.length) {
      var rhtml = '<div class="table-wrap"><table><thead><tr><th>Rule</th><th>Condition</th><th>Severity</th></tr></thead><tbody>';
      data.rules.forEach(function(r) {
        rhtml += '<tr><td>' + (r.name || '-') + '</td><td>' + (r.condition || '-') + '</td><td>' + (r.severity || '-') + '</td></tr>';
      });
      rhtml += '</tbody></table></div>';
      rulesDiv.innerHTML = rhtml;
    } else { rulesDiv.innerHTML = '<div class="empty">No alert rules configured</div>'; }

    // Alert history
    var histDiv = document.getElementById('alert-history');
    if (data.history && data.history.length) {
      var hhtml = '<div style="font-size:0.85em;">';
      data.history.slice(0, 20).forEach(function(h) {
        hhtml += '<div style="padding:4px 0;border-bottom:1px solid var(--border);">' +
          '<strong>' + (h.alert_type || 'Alert') + '</strong> - ' + (h.message || '') +
          ' <small style="color:var(--text3)">' + fmtTs(h.timestamp) + '</small></div>';
      });
      hhtml += '</div>';
      histDiv.innerHTML = hhtml;
    } else { histDiv.innerHTML = '<div class="empty">No alert history</div>'; }
  } catch(e) {
    console.error('Alerts load error:', e);
  }
}

async function ackAlert(id) {
  try {
    await fetchJSON('/v1/alerts/' + encodeURIComponent(id) + '/acknowledge', { method: 'POST' });
    showToast('Alert acknowledged', 'success');
    loadAlertsPage();
  } catch(e) { showToast('Acknowledge failed: ' + e.message, 'error'); }
}

// =========================================================================
// WEBHOOKS
// =========================================================================
async function loadWebhooks() {
  try {
    var data = await fetchJSON('/v1/webhooks');
    var tbody = document.querySelector('#webhooks-table tbody');
    tbody.innerHTML = '';
    (data.webhooks || []).forEach(function(w) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + (w.url || '-') + '</td>' +
        '<td>' + (w.events || []).join(', ') + '</td>' +
        '<td><span class="badge ' + (w.active !== false ? 'badge-success' : 'badge-danger') + '">' + (w.active !== false ? 'Active' : 'Inactive') + '</span></td>' +
        '<td><button class="btn btn-xs btn-danger" onclick="deleteWebhook(\'' + (w.id || '') + '\')">Delete</button></td>';
      tbody.appendChild(tr);
    });
  } catch(e) {
    console.error('Webhooks load error:', e);
  }
}

async function registerWebhook() {
  var url = document.getElementById('webhook-url').value.trim();
  var events = document.getElementById('webhook-events').value.trim();
  if (!url) { showToast('URL required', 'error'); return; }
  try {
    await fetchJSON('/v1/webhooks', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: url, events: events ? events.split(',').map(function(s) { return s.trim(); }) : []})
    });
    showToast('Webhook registered', 'success');
    loadWebhooks();
    document.getElementById('webhook-url').value = '';
    document.getElementById('webhook-events').value = '';
  } catch(e) { showToast('Register failed: ' + e.message, 'error'); }
}

async function deleteWebhook(id) {
  showModal('Delete Webhook', 'Delete webhook ' + id + '?', async function() {
    try {
      await fetchJSON('/v1/webhooks/' + encodeURIComponent(id), { method: 'DELETE' });
      showToast('Webhook deleted', 'success');
      loadWebhooks();
    } catch(e) { showToast('Delete failed: ' + e.message, 'error'); }
  });
}

// =========================================================================
// CONFIG
// =========================================================================
async function loadConfig() {
  try {
    var data = await fetchJSON('/v1/config');
    var tbody = document.querySelector('#config-table tbody');
    tbody.innerHTML = '';
    var configs = data.config || data.configs || data;
    var keys = Object.keys(configs);
    if (!keys.length) { tbody.innerHTML = '<tr><td colspan="4" class="empty">No config values</td></tr>'; }
    keys.forEach(function(k) {
      var v = configs[k];
      var tr = document.createElement('tr');
      tr.innerHTML = '<td><strong>' + k + '</strong></td>' +
        '<td><code style="font-size:0.82em;">' + (typeof v === 'object' ? JSON.stringify(v).substring(0, 80) : String(v).substring(0, 80)) + '</code></td>' +
        '<td>' + fmtTs(data.updated_at || '') + '</td>' +
        '<td><button class="btn btn-xs btn-outline" onclick="editConfig(\'' + k + '\', \'' + String(v).replace(/'/g, "\\'").substring(0, 100) + '\')">Edit</button></td>';
      tbody.appendChild(tr);
    });

    // Env vars
    var envDiv = document.getElementById('env-vars');
    if (data.env && Object.keys(data.env).length) {
      var ehtml = '<div style="font-size:0.82em;">';
      Object.keys(data.env).forEach(function(k) {
        if (k.toUpperCase().includes('SECRET') || k.toUpperCase().includes('KEY') || k.toUpperCase().includes('TOKEN')) return;
        ehtml += '<div style="padding:3px 0;"><strong>' + k + ':</strong> ' + data.env[k] + '</div>';
      });
      ehtml += '</div>';
      envDiv.innerHTML = ehtml;
    } else { envDiv.innerHTML = '<div class="empty">No environment variables</div>'; }
  } catch(e) {
    console.error('Config load error:', e);
  }
}

function editConfig(key, currentValue) {
  var newVal = prompt('Edit ' + key + ':', currentValue);
  if (newVal === null) return;
  fetchJSON('/v1/config/' + encodeURIComponent(key), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({value: newVal})
  }).then(function() {
    showToast('Config updated', 'success');
    loadConfig();
  }).catch(function(e) {
    showToast('Update failed: ' + e.message, 'error');
  });
}

// =========================================================================
// AUDIT LOG
// =========================================================================
async function loadAuditLog() {
  var actor = document.getElementById('audit-actor').value.trim();
  var action = document.getElementById('audit-action').value.trim();
  var range = document.getElementById('audit-range').value;
  var params = '?limit=100';
  if (actor) params += '&actor=' + encodeURIComponent(actor);
  if (action) params += '&action=' + encodeURIComponent(action);
  if (range) params += '&range=' + encodeURIComponent(range);
  try {
    var data = await fetchJSON('/v1/analytics/audit' + params);
    var tbody = document.querySelector('#audit-table tbody');
    tbody.innerHTML = '';
    (data.entries || data.audit_logs || []).forEach(function(e) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + fmtTs(e.timestamp || e.created_at) + '</td>' +
        '<td>' + (e.actor_id || e.actor || '-') + '</td>' +
        '<td>' + (e.action || '-') + '</td>' +
        '<td>' + fmtShort(e.resource_id || e.resource || '-', 30) + '</td>' +
        '<td>' + (e.ip_address || e.ip || '-') + '</td>' +
        '<td>' + boolBadge(e.success !== false) + '</td>';
      tbody.appendChild(tr);
    });

    // Anomalies
    var anomDiv = document.getElementById('audit-anomalies');
    if (data.anomalies && data.anomalies.length) {
      var ahtml = '';
      data.anomalies.forEach(function(a) {
        ahtml += '<div class="alert-item alert-warning"><strong>Anomaly:</strong> ' + (a.description || a.type || 'Unknown') + ' <small>' + fmtTs(a.timestamp) + '</small></div>';
      });
      anomDiv.innerHTML = ahtml || '<div class="empty">No anomalies</div>';
    } else { anomDiv.innerHTML = '<div class="empty">No anomalies detected</div>'; }
  } catch(e) {
    console.error('Audit log load error:', e);
  }
}

// =========================================================================
// SETTINGS
// =========================================================================
async function loadSettings() {
  try {
    // Health
    var healthData = await fetchJSON('/health');
    document.getElementById('settings-mode').innerHTML = '<p>Mode: <span class="badge ' + (healthData.mode === 'live' ? 'badge-success' : 'badge-warning') + '">' + (healthData.mode || 'unknown') + '</span></p>' +
      '<p>Status: <span class="badge badge-success">' + (healthData.status || 'ok') + '</span></p>';
    document.getElementById('gateway-mode').textContent = healthData.mode || 'stub';
    document.getElementById('gateway-mode').className = 'tb-badge ' + (healthData.mode === 'live' ? 'live' : 'stub');

    // Cache
    try {
      var cacheData = await fetchJSON('/cache/stats');
      document.getElementById('settings-cache').innerHTML = '<p>Enabled: ' + boolBadge(cacheData.enabled) + '</p>' +
        '<p>Hits: ' + (cacheData.hits || 0) + ' | Misses: ' + (cacheData.misses || 0) + '</p>' +
        '<p>Hit Rate: ' + fmtPct(cacheData.hit_rate || 0) + '</p>' +
        '<p>Size: ' + (cacheData.size || 0) + ' entries</p>';
      document.getElementById('cache-status').textContent = 'cache: ' + fmtPct(cacheData.hit_rate || 0);
    } catch(e) { document.getElementById('settings-cache').innerHTML = '<div class="empty">Cache unavailable</div>'; }

    // TLS
    document.getElementById('settings-tls').innerHTML = '<p>TLS: ' + boolBadge(healthData.tls || false) + '</p>';

    // DB
    document.getElementById('settings-db').innerHTML = '<p>Database: <span class="badge badge-success">Connected</span></p>' +
      '<p>Uptime: ' + (healthData.uptime || 'N/A') + '</p>';
  } catch(e) {
    console.error('Settings load error:', e);
  }
}

async function setGatewayMode(mode) {
  try {
    await fetchJSON('/v1/config/gateway_mode', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({value: mode})
    });
    showToast('Gateway mode set to ' + mode, 'success');
    loadSettings();
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

async function toggleCache() {
  try {
    await fetchJSON('/v1/config/cache_enabled', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({value: 'toggle'})
    });
    showToast('Cache toggled', 'success');
    loadSettings();
  } catch(e) { showToast('Failed: ' + e.message, 'error'); }
}

async function runBackup() {
  try {
    var data = await fetchJSON('/v1/admin/backup', { method: 'POST' });
    showToast('Backup completed: ' + (data.file || 'ok'), 'success');
    document.getElementById('backup-result').innerHTML = '<div class="alert-item alert-info">Backup: ' + (data.file || 'Success') + '</div>';
  } catch(e) { showToast('Backup failed: ' + e.message, 'error'); }
}

async function runRestore() {
  showModal('Restore', 'Restore from backup? This will overwrite current data.', async function() {
    try {
      var data = await fetchJSON('/v1/admin/restore', { method: 'POST' });
      showToast('Restore completed', 'success');
    } catch(e) { showToast('Restore failed: ' + e.message, 'error'); }
  });
}

async function runExport() {
  try {
    var data = await fetchJSON('/v1/export');
    var blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = 'noerelay-export-' + new Date().toISOString().substring(0,10) + '.json'; a.click();
    URL.revokeObjectURL(url);
    showToast('Exported', 'success');
  } catch(e) { showToast('Export failed: ' + e.message, 'error'); }
}

async function runImport() {
  var input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json';
  input.onchange = async function() {
    var file = input.files[0];
    if (!file) return;
    try {
      var text = await file.text();
      var data = JSON.parse(text);
      await fetchJSON('/v1/import', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
      });
      showToast('Imported successfully', 'success');
    } catch(e) { showToast('Import failed: ' + e.message, 'error'); }
  };
  input.click();
}

// =========================================================================
// Init
// =========================================================================
function loadAll() {
  loadSection(currentSection);
}

// Restore preferences
(function() {
  var savedSection = localStorage.getItem('noerelay_section');
  if (savedSection) {
    currentSection = savedSection;
    document.querySelectorAll('#sidebar nav a').forEach(function(a) { a.classList.remove('active'); });
    var link = document.querySelector('#sidebar nav a[data-section="' + savedSection + '"]');
    if (link) link.classList.add('active');
    document.querySelectorAll('.section').forEach(function(s) { s.classList.remove('active'); });
    var sec = document.getElementById('section-' + savedSection);
    if (sec) sec.classList.add('active');
  }
  var savedAuto = localStorage.getItem('noerelay_autorefresh');
  if (savedAuto === '0') {
    autoRefresh = false;
    document.getElementById('auto-refresh-toggle').innerHTML = '&#8635; Auto: OFF';
  }
})();

loadAll();
if (autoRefresh) startAutoRefresh();

window.addEventListener('resize', function() {
  if (currentSection === 'overview') loadOverview();
  if (currentSection === 'analytics') loadAnalytics();
});
</script>
</body>
</html>"""