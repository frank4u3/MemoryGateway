"""Enhanced dashboard HTML with per-agent comparison charts and rankings table."""

TELEMETRY_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Memory Gateway — Telemetry</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}
h1{color:#58a6ff;margin-bottom:8px}
h2{color:#8b949e;font-size:14px;font-weight:400;margin-bottom:24px}
h3{font-size:14px;color:#c9d1d9;margin-bottom:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.card h4{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.card .value{font-size:24px;font-weight:600;color:#f0f6fc}
.card .sub{font-size:11px;color:#8b949e;margin-top:4px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:24px}
.chart-box{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.chart-box canvas{max-height:300px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 12px;border-bottom:2px solid #30363d;color:#8b949e}
td{padding:8px 12px;border-bottom:1px solid #21262d}
.rate{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:500}
.rate-good{background:#1b4b2b;color:#3fb950}
.rate-ok{background:#4d3500;color:#d29922}
.rate-bad{background:#4b1b1b;color:#f85149}
.rank-1{color:#ffd700;font-weight:700}
.rank-2{color:#c0c0c0;font-weight:700}
.rank-3{color:#cd7f32;font-weight:700}
.tab-bar{display:flex;gap:4px;margin-bottom:20px}
.tab{padding:8px 16px;background:#161b22;border:1px solid #30363d;border-radius:6px 6px 0 0;color:#8b949e;cursor:pointer;font-size:13px}
.tab.active{background:#1c2128;color:#58a6ff;border-bottom-color:#1c2128}
.tab-content{display:none}
.tab-content.active{display:block}
</style>
</head>
<body>
<h1>Memory Gateway — Telemetry</h1>
<h2 id="subtitle">Loading...</h2>

<div class="tab-bar">
  <div class="tab active" onclick="switchTab('overview',this)">Overview</div>
  <div class="tab" onclick="switchTab('agents',this)">Agents</div>
  <div class="tab" onclick="switchTab('rankings',this)">Rankings</div>
</div>

<!-- Overview Tab -->
<div id="tab-overview" class="tab-content active">
  <div class="cards">
    <div class="card"><h4>Total Requests</h4><div class="value" id="total_requests">-</div></div>
    <div class="card"><h4>Cache Hit Rate</h4><div class="value"><span id="hit_rate">-</span>%</div><div class="sub" id="hit_sub">-</div></div>
    <div class="card"><h4>Tokens Saved</h4><div class="value" id="tokens_saved">-</div><div class="sub">by cache</div></div>
    <div class="card"><h4>Cost Saved</h4><div class="value" id="cost_saved">-</div><div class="sub" id="cost_sub">-</div></div>
    <div class="card"><h4>Avg Latency</h4><div class="value"><span id="avg_latency">-</span> <span style="font-size:14px;color:#8b949e">ms</span></div></div>
    <div class="card"><h4>Net Cost</h4><div class="value" id="net_cost">-</div><div class="sub">saved - spent</div></div>
  </div>

  <div class="row">
    <div class="chart-box"><h3>Daily Cache Hit Rate</h3><canvas id="hitRateChart"></canvas></div>
    <div class="chart-box"><h3>Daily Savings (USD)</h3><canvas id="savingsChart"></canvas></div>
  </div>

  <div class="row">
    <div class="chart-box"><h3>Agent Request Distribution</h3><canvas id="agentPieChart"></canvas></div>
    <div class="chart-box"><h3>Agent Hit Rate Comparison</h3><canvas id="agentBarChart"></canvas></div>
  </div>
</div>

<!-- Agents Tab -->
<div id="tab-agents" class="tab-content">
  <div class="row3" id="agentCards"></div>
  <div class="row">
    <div class="chart-box"><h3>Per-Agent Requests</h3><canvas id="agentRequestsChart"></canvas></div>
    <div class="chart-box"><h3>Per-Agent Cost (USD)</h3><canvas id="agentCostChart"></canvas></div>
  </div>
  <div class="row">
    <div class="chart-box"><h3>Per-Agent Tokens Saved</h3><canvas id="agentTokensChart"></canvas></div>
    <div class="chart-box"><h3>Per-Agent Hit Rate %</h3><canvas id="agentHitRateChart"></canvas></div>
  </div>
</div>

<!-- Rankings Tab -->
<div id="tab-rankings" class="tab-content">
  <div class="chart-box" style="margin-bottom:24px">
    <h3>Agent Rankings</h3>
    <table>
      <thead><tr><th>Rank</th><th>Agent</th><th>Score</th><th>Requests</th><th>Hit Rate</th><th>Tokens Saved</th><th>Cost Saved</th><th>Avg Latency</th></tr></thead>
      <tbody id="rankingsBody"></tbody>
    </table>
  </div>
  <div class="chart-box">
    <h3>Ranking Scores</h3><canvas id="rankingChart"></canvas>
  </div>
</div>

<script>
let charts = {};

function switchTab(name, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function getRateClass(pct) {
  if (pct >= 60) return 'rate-good';
  if (pct >= 30) return 'rate-ok';
  return 'rate-bad';
}

function getRankClass(rank) {
  if (rank === 1) return 'rank-1';
  if (rank === 2) return 'rank-2';
  if (rank === 3) return 'rank-3';
  return '';
}

const AGENT_COLORS = {
  hermes: '#58a6ff',
  opencode: '#3fb950',
  qoder: '#d29922',
  vscode: '#f85149'
};

async function loadTelemetry() {
  try {
    const [overview, cache, cost, rankings] = await Promise.all([
      fetch('/v1/telemetry/overview').then(r=>r.json()),
      fetch('/v1/metrics/cache').then(r=>r.json()),
      fetch('/v1/metrics/cost').then(r=>r.json()),
      fetch('/v1/telemetry/rankings').then(r=>r.json()),
    ]);

    document.getElementById('subtitle').textContent = 'Last updated: ' + new Date().toLocaleString();

    // Overview cards
    document.getElementById('total_requests').textContent = (overview.total_requests || 0).toLocaleString();
    const hr = (overview.hit_rate_pct || 0).toFixed(1);
    document.getElementById('hit_rate').textContent = hr;
    document.getElementById('hit_sub').textContent = (overview.total_hits || 0) + ' hits / ' + (overview.total_misses || 0) + ' misses';
    document.getElementById('tokens_saved').textContent = (overview.total_tokens_saved || 0).toLocaleString();
    document.getElementById('cost_saved').textContent = '$' + (overview.total_cost_saved_usd || 0).toFixed(4);
    document.getElementById('cost_sub').textContent = 'spent: $' + (overview.total_cost_spent_usd || 0).toFixed(4);
    document.getElementById('avg_latency').textContent = (overview.avg_latency_ms || 0).toFixed(1);
    document.getElementById('net_cost').textContent = '$' + (overview.total_net_cost_usd || 0).toFixed(4);

    // Hit rate chart
    destroyChart('hitRate');
    if (cache.daily_hit_rate && cache.daily_hit_rate.length) {
      charts.hitRate = new Chart(document.getElementById('hitRateChart'), {
        type: 'line',
        data: {
          labels: cache.daily_hit_rate.map(d=>d.date),
          datasets: [{
            label: 'Hit Rate %',
            data: cache.daily_hit_rate.map(d=>d.rate),
            borderColor: '#58a6ff',
            backgroundColor: 'rgba(88,166,255,0.1)',
            fill: true,
            tension: 0.3
          }]
        },
        options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { min: 0, max: 100 } } }
      });
    }

    // Savings chart
    destroyChart('savings');
    if (cost.daily_savings && cost.daily_savings.length) {
      charts.savings = new Chart(document.getElementById('savingsChart'), {
        type: 'bar',
        data: {
          labels: cost.daily_savings.map(d=>d.date),
          datasets: [{
            label: 'Savings ($)',
            data: cost.daily_savings.map(d=>d.savings),
            backgroundColor: '#3fb950'
          }]
        },
        options: { responsive: true, plugins: { legend: { display: false } } }
      });
    }

    // Agent data
    const agents = overview.agents || [];
    const agentIds = agents.map(a => a.agent_id);
    const agentColors = agentIds.map(id => AGENT_COLORS[id] || '#bc8cff');

    // Agent pie chart
    destroyChart('agentPie');
    charts.agentPie = new Chart(document.getElementById('agentPieChart'), {
      type: 'doughnut',
      data: {
        labels: agentIds,
        datasets: [{
          data: agents.map(a => a.requests || 0),
          backgroundColor: agentColors
        }]
      },
      options: { responsive: true, plugins: { legend: { position: 'bottom' } } }
    });

    // Agent bar chart (hit rate)
    destroyChart('agentBar');
    charts.agentBar = new Chart(document.getElementById('agentBarChart'), {
      type: 'bar',
      data: {
        labels: agentIds,
        datasets: [{
          label: 'Hit Rate %',
          data: agents.map(a => a.hit_rate_pct || 0),
          backgroundColor: agentColors
        }]
      },
      options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { min: 0, max: 100 } } }
    });

    // Agent cards
    const cardsDiv = document.getElementById('agentCards');
    cardsDiv.innerHTML = agents.map(a => {
      const rc = getRateClass(a.hit_rate_pct);
      return `<div class="card">
        <h4>${a.agent_id}</h4>
        <div class="value">${(a.requests||0).toLocaleString()}</div>
        <div class="sub">requests</div>
        <div style="margin-top:8px"><span class="rate ${rc}">${a.hit_rate_pct.toFixed(1)}%</span> hit rate</div>
        <div class="sub" style="margin-top:4px">saved: $${(a.cost_saved_usd||0).toFixed(4)} | latency: ${(a.avg_latency_ms||0).toFixed(0)}ms</div>
      </div>`;
    }).join('');

    // Per-agent requests chart
    destroyChart('agentReq');
    charts.agentReq = new Chart(document.getElementById('agentRequestsChart'), {
      type: 'bar',
      data: {
        labels: agentIds,
        datasets: [{
          label: 'Requests',
          data: agents.map(a => a.requests || 0),
          backgroundColor: agentColors
        }]
      },
      options: { responsive: true, plugins: { legend: { display: false } } }
    });

    // Per-agent cost chart
    destroyChart('agentCost');
    charts.agentCost = new Chart(document.getElementById('agentCostChart'), {
      type: 'bar',
      data: {
        labels: agentIds,
        datasets: [
          { label: 'Spent', data: agents.map(a => a.cost_spent_usd || 0), backgroundColor: '#f85149' },
          { label: 'Saved', data: agents.map(a => a.cost_saved_usd || 0), backgroundColor: '#3fb950' }
        ]
      },
      options: { responsive: true }
    });

    // Per-agent tokens saved chart
    destroyChart('agentTokens');
    charts.agentTokens = new Chart(document.getElementById('agentTokensChart'), {
      type: 'bar',
      data: {
        labels: agentIds,
        datasets: [{
          label: 'Tokens Saved',
          data: agents.map(a => a.tokens_saved || 0),
          backgroundColor: agentColors
        }]
      },
      options: { responsive: true, plugins: { legend: { display: false } } }
    });

    // Per-agent hit rate chart
    destroyChart('agentHR');
    charts.agentHR = new Chart(document.getElementById('agentHitRateChart'), {
      type: 'bar',
      data: {
        labels: agentIds,
        datasets: [{
          label: 'Hit Rate %',
          data: agents.map(a => a.hit_rate_pct || 0),
          backgroundColor: agentColors
        }]
      },
      options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { min: 0, max: 100 } } }
    });

    // Rankings table
    const rankingsBody = document.getElementById('rankingsBody');
    if (rankings && rankings.length) {
      rankingsBody.innerHTML = rankings.map(r => {
        const rc = getRateClass(r.hit_rate_pct);
        const rankC = getRankClass(r.rank);
        return `<tr>
          <td class="${rankC}">#${r.rank}</td>
          <td><strong>${r.agent_id}</strong></td>
          <td>${r.score.toFixed(2)}</td>
          <td>${(r.requests||0).toLocaleString()}</td>
          <td><span class="rate ${rc}">${r.hit_rate_pct.toFixed(1)}%</span></td>
          <td>${(r.tokens_saved||0).toLocaleString()}</td>
          <td>$${(r.cost_saved_usd||0).toFixed(4)}</td>
          <td>${(r.avg_latency_ms||0).toFixed(0)}ms</td>
        </tr>`;
      }).join('');
    }

    // Ranking scores chart
    destroyChart('ranking');
    if (rankings && rankings.length) {
      charts.ranking = new Chart(document.getElementById('rankingChart'), {
        type: 'bar',
        data: {
          labels: rankings.map(r => '#' + r.rank + ' ' + r.agent_id),
          datasets: [{
            label: 'Score',
            data: rankings.map(r => r.score),
            backgroundColor: rankings.map(r => AGENT_COLORS[r.agent_id] || '#bc8cff')
          }]
        },
        options: { responsive: true, plugins: { legend: { display: false } } }
      });
    }

  } catch(e) {
    document.getElementById('subtitle').textContent = 'Error loading telemetry: ' + e.message;
  }
}
loadTelemetry();
setInterval(loadTelemetry, 15000);
</script>
</body>
</html>"""
