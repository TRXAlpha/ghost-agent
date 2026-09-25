"""Dependency-free local dashboard for the loopback Ghost control plane."""
from __future__ import annotations


def render_dashboard() -> str:
    """Return static HTML that reads only the local `/v1/snapshot` endpoint."""
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Ghost Control Plane</title>
<style>
body{font:15px system-ui;background:#10131a;color:#e8edf5;margin:0;padding:24px}h1{margin-top:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.card{background:#1a2030;padding:14px;border-radius:9px}
pre{white-space:pre-wrap;overflow:auto;max-height:280px}.ok{color:#75dd9b}.warn{color:#ffc766}
</style></head><body><h1>Ghost Control Plane</h1><p id="status">Loading local state…</p>
<div class="grid"><section class="card"><h2>Outcomes & Goals</h2><pre id="goals"></pre></section>
<section class="card"><h2>Approvals</h2><pre id="approvals"></pre></section>
<section class="card"><h2>Health</h2><pre id="health"></pre></section>
<section class="card"><h2>Recent Provenance</h2><pre id="provenance"></pre></section></div>
<section class="card"><h2>Security Events</h2><pre id="security"></pre></section>
<script>
async function refresh(){try{const r=await fetch('/v1/snapshot');if(!r.ok)throw Error(r.status);const d=await r.json();
document.querySelector('#goals').textContent=JSON.stringify(d.goals,null,2);document.querySelector('#approvals').textContent=JSON.stringify(d.pending_approvals,null,2);
document.querySelector('#health').textContent=JSON.stringify(d.health,null,2);document.querySelector('#provenance').textContent=JSON.stringify(d.recent_provenance,null,2);
document.querySelector('#security').textContent=JSON.stringify(d.security_events,null,2);
document.querySelector('#status').textContent='Local state refreshed '+new Date().toLocaleTimeString();}catch(e){document.querySelector('#status').textContent='Unable to reach local API: '+e;}}
refresh();setInterval(refresh,5000);
</script></body></html>"""
