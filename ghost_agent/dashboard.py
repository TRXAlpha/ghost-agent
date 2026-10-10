"""Dependency-free local dashboard for the loopback Ghost control plane."""
from __future__ import annotations


def render_dashboard() -> str:
    """Return static HTML that drives the local `/v1/...` owner-control endpoints.

    The page is self-contained (vanilla JS, no external assets). Every API
    value is inserted with textContent, never innerHTML, so stored data can
    not inject markup. The owner token lives only in sessionStorage and is
    sent as a Bearer header on every request when present.
    """
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Ghost Control Plane</title>
<style>
body{font:15px system-ui;background:#10131a;color:#e8edf5;margin:0;padding:24px}h1{margin-top:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.card{background:#1a2030;padding:14px;border-radius:9px}
pre{white-space:pre-wrap;overflow:auto;max-height:280px}.ok{color:#75dd9b}.warn{color:#ffc766}
button{background:#2b3650;color:#e8edf5;border:1px solid #3d4a6b;border-radius:6px;padding:4px 10px;margin:2px;cursor:pointer}
button:hover{background:#3d4a6b}input{background:#0d1017;color:#e8edf5;border:1px solid #3d4a6b;border-radius:6px;padding:4px 8px;margin:2px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:3px 8px;border-bottom:1px solid #26304a}
.status{margin:2px 0;color:#9fb3d9}
</style></head><body><h1>Ghost Control Plane</h1><p id="status">Loading local state…</p>
<div class="card"><h2>Token</h2>
<input id="token-input" type="password" placeholder="API token (optional)">
<button id="token-save">Save token</button><button id="token-clear">Clear token</button>
<span class="status" id="token-state"></span></div>
<div class="grid"><section class="card"><h2>Emergency Stop</h2>
<p class="status" id="estop-status">unknown</p>
<button id="estop-engage">Engage</button><button id="estop-disengage">Disengage</button></section>
<section class="card"><h2>Policy</h2><div id="policy-modes"></div></section>
<section class="card"><h2>Notifications</h2>
<div id="notifications-list"></div><p class="status" id="held-count"></p></section>
<section class="card"><h2>Focus</h2><div id="focus-current"></div>
<div id="focus-form"><input id="focus-purpose" placeholder="Purpose">
<input id="focus-minutes" type="number" value="25" min="1" style="width:5em">
<label><input id="focus-hold" type="checkbox"> Hold notifications</label>
<button id="focus-start">Start focus</button><button id="focus-stop">Stop</button></div></section>
<section class="card"><h2>Jobs</h2>
<table id="jobs-table"><thead><tr><th>id</th><th>status</th><th>goal</th><th></th></tr></thead>
<tbody id="jobs-body"></tbody></table></section>
<section class="card"><h2>Why</h2><input id="why-id" placeholder="job or opportunity id">
<button id="why-show">Show</button><pre id="why-output"></pre></section>
<section class="card"><h2>Outcomes & Goals</h2><pre id="goals"></pre></section>
<section class="card"><h2>Approvals</h2><pre id="approvals"></pre></section>
<section class="card"><h2>Health</h2><pre id="health"></pre></section>
<section class="card"><h2>Recent Provenance</h2><pre id="provenance"></pre></section></div>
<section class="card"><h2>Security Events</h2><pre id="security"></pre></section>
<script>
function token(){return sessionStorage.getItem('ghost_api_token')||'';}
function authHeaders(){const t=token();return t?{'Authorization':'Bearer '+t}:{};}
function showTokenState(){const el=document.getElementById('token-state');
el.textContent=token()?'token stored in this tab (sessionStorage)':'no token stored';}
document.getElementById('token-save').addEventListener('click',()=>{
sessionStorage.setItem('ghost_api_token',document.getElementById('token-input').value);
document.getElementById('token-input').value='';showTokenState();refresh();});
document.getElementById('token-clear').addEventListener('click',()=>{
sessionStorage.removeItem('ghost_api_token');showTokenState();refresh();});
async function api(method,path,body){const opts={method,headers:{...authHeaders()}};
if(body!==undefined){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body);}
const r=await fetch(path,opts);const d=await r.json().catch(()=>({}));
if(r.status===401||r.status===403)setStatus('Request refused ('+r.status+'): '+(d.error||'unauthorized')+
(token()?'':' — save the API token above if one is configured'));
return {ok:r.ok,status:r.status,data:d};}
function setStatus(msg){document.getElementById('status').textContent=msg;}
function put(id,value){document.getElementById(id).textContent=value;}
document.getElementById('estop-engage').addEventListener('click',async()=>{
const reason=prompt('Emergency stop reason:');if(!reason)return;
await api('POST','/v1/emergency-stop/engage',{reason});refresh();});
document.getElementById('estop-disengage').addEventListener('click',async()=>{
await api('POST','/v1/emergency-stop/disengage',{});refresh();});
document.getElementById('focus-start').addEventListener('click',async()=>{
const body={purpose:document.getElementById('focus-purpose').value,
minutes:parseInt(document.getElementById('focus-minutes').value,10)||0,
hold_notifications:document.getElementById('focus-hold').checked};
await api('POST','/v1/focus/start',body);refresh();});
document.getElementById('focus-stop').addEventListener('click',async()=>{
await api('POST','/v1/focus/stop',{});refresh();});
document.getElementById('why-show').addEventListener('click',async()=>{
const id=document.getElementById('why-id').value.trim();
const r=await api('GET','/v1/why/'+encodeURIComponent(id));
put('why-output',r.ok?JSON.stringify(r.data,null,2):(r.data&&r.data.error)?r.data.error:'not found');});
function button(label,onclick){const b=document.createElement('button');
b.textContent=label;b.addEventListener('click',onclick);return b;}
async function renderPolicy(){const r=await api('GET','/v1/policy');const box=document.getElementById('policy-modes');
box.textContent='';if(!r.ok)return;const modes=r.data&&r.data.modes?r.data.modes:r.data;for(const mode of Object.keys(modes)){const row=document.createElement('div');
const label=document.createElement('span');label.textContent=mode+': '+JSON.stringify(modes[mode])+' ';
row.appendChild(label);const on=modes[mode]===true;
row.appendChild(button(on?'on':'turn on',async()=>{await api('POST','/v1/policy',{mode,enabled:true});refresh();}));
row.appendChild(button(!on?'off':'turn off',async()=>{await api('POST','/v1/policy',{mode,enabled:false});refresh();}));
box.appendChild(row);}}
async function renderNotifications(){const r=await api('GET','/v1/notifications');const box=document.getElementById('notifications-list');
box.textContent='';if(!r.ok)return;
for(const note of r.data.deliverable||[]){const row=document.createElement('div');
row.textContent=note.notification_id+' ('+note.priority+'): '+(note.message||'')+' ';
row.appendChild(button('Ack',async()=>{await api('POST','/v1/notifications/'+note.notification_id+'/ack',{});refresh();}));
box.appendChild(row);}
document.getElementById('held-count').textContent=(r.data.held||[]).length+' held';}
async function renderJobs(){const jobs=d=>d.scheduler_jobs||d.jobs||[];
const r=await api('GET','/v1/snapshot');const body=document.getElementById('jobs-body');
body.textContent='';if(!r.ok)return;
for(const job of jobs(r.data)){const tr=document.createElement('tr');
const td=id=>{const c=document.createElement('td');c.textContent=id;return c;};
tr.appendChild(td(job.job_id));tr.appendChild(td(job.status));tr.appendChild(td(job.goal||''));
const actions=document.createElement('td');
actions.appendChild(button('Pause',async()=>{await api('POST','/v1/jobs/'+job.job_id+'/pause',{});refresh();}));
actions.appendChild(button('Resume',async()=>{await api('POST','/v1/jobs/'+job.job_id+'/resume',{});refresh();}));
actions.appendChild(button('Cancel',async()=>{await api('POST','/v1/jobs/'+job.job_id+'/cancel',{});refresh();}));
tr.appendChild(actions);body.appendChild(tr);}}
async function refresh(){try{
const snap=await api('GET','/v1/snapshot');
if(!snap.ok){if(snap.status!==401&&snap.status!==403)setStatus('Unable to reach local API: '+snap.status);return;}
const d=snap.data;
put('goals',JSON.stringify(d.goals,null,2));put('approvals',JSON.stringify(d.pending_approvals,null,2));
put('health',JSON.stringify(d.health,null,2));put('provenance',JSON.stringify(d.recent_provenance,null,2));
put('security',JSON.stringify(d.security_events,null,2));
const estop=d.health&&d.health.emergency_stop;
const estopEl=document.getElementById('estop-status');estopEl.textContent='emergency_stop: '+estop;
estopEl.className='status '+(estop==='engaged'?'warn':'ok');
await renderPolicy();await renderNotifications();
const focus=await api('GET','/v1/focus');
put('focus-current',focus.data?('active: '+JSON.stringify(focus.data)):'no active focus session');
await renderJobs();
setStatus('Local state refreshed '+new Date().toLocaleTimeString());
}catch(e){setStatus('Unable to reach local API: '+e);}}
showTokenState();refresh();setInterval(refresh,10000);
</script></body></html>"""
