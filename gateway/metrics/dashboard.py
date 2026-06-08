"""Embedded HTML dashboard for the Memory Gateway metrics."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Memory Gateway Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}
h1{color:#58a6ff;margin-bottom:8px}
h2{color:#8b949e;font-size:14px;font-weight:400;margin-bottom:24px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.card h3{font-size:12px;color:#8b949e;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px}
.card .value{font-size:28px;font-weight:600;color:#f0f6fc}
.card .sub{font-size:12px;color:#8b949e;margin-top:4px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
.chart-box{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.chart-box h3{font-size:14px;color:#c9d1d9;margin-bottom:12px}
.chart-box canvas{max-height:300px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 12px;border-bottom:2px solid #30363d;color:#8b949e}
td{padding:8px 12px;border-bottom:1px solid #21262d}
.rate{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:500}
.rate-good{background:#1b4b2b;color:#3fb950}
.rate-ok{background:#4d3500;color:#d29922}
.rate-bad{background:#4b1b1b;color:#f85149}
</style>
</head>
<body>
<h1>Memory Gateway Dashboard</h1>
<h2 id="subtitle">Loading...</h2>

<div class="cards">
  <div class="card"><h3>Total Requests</h3><div class="value" id="total_requests">-</div></div>
  <div class="card"><h3>Cache Hit Rate</h3><div class="value"><span id="hit_rate">-</span>%</div><div class="sub" id="hit_sub">-</div></div>
  <div class="card"><h3>Tokens Saved</h3><div class="value" id="tokens_saved">-</div><div class="sub">by cache</div></div>
  <div class="card"><h3>Cost Saved</h3><div class="value" id="cost_saved">-</div><div class="sub" id="cost_sub">vs estimated cost</div></div>
</div>

<div class="row">
  <div class="chart-box"><h3>Daily Cache Hit Rate</h3><canvas id="hitRateChart"></canvas></div>
  <div class="chart-box"><h3>Daily Savings (USD)</h3><canvas id="savingsChart"></canvas></div>
</div>

<div class="row">
  <div class="chart-box"><h3>Top Cache Keys</h3><table><thead><tr><th>Cache Key</th><th>Hits</th><th>Actions</th></tr></thead><tbody id="topKeysBody"></tbody></table></div>
  <div class="chart-box"><h3>Agent Breakdown</h3><canvas id="agentChart"></canvas></div>
</div>

<script>
async function loadMetrics() {
  try {
    const [overview, cache, cost] = await Promise.all([
      fetch('/v1/metrics/overview').then(r=>r.json()),
      fetch('/v1/metrics/cache').then(r=>r.json()),
      fetch('/v1/metrics/cost').then(r=>r.json()),
    ]);
    document.getElementById('subtitle').textContent = 'Last updated: ' + new Date().toLocaleString();

    // Overview cards
    document.getElementById('total_requests').textContent = overview.total_requests || 0;
    const hr = (overview.hit_rate_pct || 0).toFixed(1);
    document.getElementById('hit_rate').textContent = hr;
    document.getElementById('hit_sub').textContent = (overview.total_hits || 0) + ' hits / ' + (overview.total_misses || 0) + ' misses';
    document.getElementById('tokens_saved').textContent = (overview.tokens_saved || 0).toLocaleString();
    document.getElementById('cost_saved').textContent = '$' + (overview.cost_saved_usd || 0).toFixed(4);
    document.getElementById('cost_sub').textContent = 'spent: $' + (overview.cost_spent_usd || 0).toFixed(4);

    // Hit rate chart
    if (cache.daily_hit_rate && cache.daily_hit_rate.length) {
      new Chart(document.getElementById('hitRateChart'), {
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
    if (cost.daily_savings && cost.daily_savings.length) {
      new Chart(document.getElementById('savingsChart'), {
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

    // Top cache keys
    const keysBody = document.getElementById('topKeysBody');
    if (cache.top_keys && cache.top_keys.length) {
      keysBody.innerHTML = cache.top_keys.slice(0, 20).map(k =>
        '<tr><td style="font-family:monospace;font-size:12px">' + k.key + '</td><td>' + k.hits + '</td><td>' + (k.action||'-') + '</td></tr>'
      ).join('');
    }

    // Agent chart
    if (overview.agent_breakdown) {
      const agents = Object.entries(overview.agent_breakdown);
      new Chart(document.getElementById('agentChart'), {
        type: 'doughnut',
        data: {
          labels: agents.map(([name])=>name),
          datasets: [{
            data: agents.map(([,v])=>v.requests || 0),
            backgroundColor: ['#58a6ff','#3fb950','#d29922','#f85149','#bc8cff']
          }]
        },
        options: { responsive: true, plugins: { legend: { position: 'bottom' } } }
      });
    }
  } catch(e) {
    document.getElementById('subtitle').textContent = 'Error loading metrics: ' + e.message;
  }
}
loadMetrics();
setInterval(loadMetrics, 15000);
</script>
</body>
</html>"""
