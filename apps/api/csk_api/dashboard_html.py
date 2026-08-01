"""The single self-contained dashboard page served at the UI route.

One HTML string: inline CSS, vanilla JS, NO external resource URLs (no CDN, no
web fonts, no images fetched over the network). It works fully offline and talks
ONLY to this app's own ``/ui/*`` JSON endpoints via relative-path ``fetch``. A
test asserts both properties, so keep every URL in here either a relative
``/ui/...`` path or absent.
"""
from __future__ import annotations

# NOTE FOR MAINTAINERS: do not add <script src=...>, <link href=...>, @import,
# url(http...), or any absolute http(s) URL to this page. The dashboard must stay
# offline and self-contained. fetch() targets must be relative "/ui/..." paths.
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ronin ui</title>
<style>
:root{
  --bg:#0d1117; --panel:#161b22; --panel2:#1c2230; --border:#2a3140;
  --fg:#e6edf3; --muted:#8b949e; --dim:#6e7681;
  --accent:#7aa2f7; --accent2:#bb9af7;
  --green:#3fb950; --green-bg:#15281a; --red:#f85149; --red-bg:#2d1418;
  --amber:#d29922; --amber-bg:#2a2310;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;height:100%}
body{
  background:var(--bg);color:var(--fg);
  font-family:var(--mono);font-size:13.5px;line-height:1.5;
}
a{color:var(--accent);text-decoration:none}
header{
  display:flex;align-items:center;gap:12px;
  padding:14px 20px;border-bottom:1px solid var(--border);
  background:linear-gradient(180deg,#11161f,#0d1117);
}
header .logo{font-size:18px;font-weight:700;letter-spacing:.5px}
header .logo .k{color:var(--accent)}
header .tag{color:var(--muted);font-size:12px}
header .spacer{flex:1}
header button.refresh{
  background:var(--panel2);color:var(--fg);border:1px solid var(--border);
  border-radius:6px;padding:6px 12px;cursor:pointer;font-family:var(--mono);font-size:12px;
}
header button.refresh:hover{border-color:var(--accent)}
.layout{display:flex;height:calc(100% - 53px)}
.sidebar{
  width:340px;min-width:300px;border-right:1px solid var(--border);
  overflow-y:auto;background:var(--panel);
}
.main{flex:1;overflow-y:auto;padding:20px 24px}
.section-h{
  padding:12px 16px 6px;font-size:11px;text-transform:uppercase;
  letter-spacing:1px;color:var(--dim);font-weight:700;
}
.run{
  padding:11px 16px;border-bottom:1px solid #1b2129;cursor:pointer;
  display:flex;flex-direction:column;gap:3px;
}
.run:hover{background:var(--panel2)}
.run.active{background:var(--panel2);box-shadow:inset 3px 0 0 var(--accent)}
.run .row{display:flex;align-items:center;gap:8px}
.run .title{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.run .meta{color:var(--dim);font-size:11px;display:flex;gap:8px;align-items:center}
.pill{
  font-size:10px;padding:1px 7px;border-radius:10px;border:1px solid var(--border);
  color:var(--muted);white-space:nowrap;
}
.pill.orchestrate{color:var(--accent2);border-color:#3a3050}
.pill.session{color:var(--accent);border-color:#26384f}
.dot{width:8px;height:8px;border-radius:50%;flex:none}
.dot.ok{background:var(--green)} .dot.fail{background:var(--red)}
.empty{
  padding:28px 20px;color:var(--muted);text-align:center;font-size:12.5px;
  border:1px dashed var(--border);border-radius:8px;margin:14px;
}
.empty .big{font-size:26px;display:block;margin-bottom:8px;opacity:.6}
.detail-empty{
  display:flex;align-items:center;justify-content:center;height:100%;
  color:var(--dim);flex-direction:column;gap:10px;text-align:center;
}
.detail-empty .big{font-size:40px;opacity:.4}
h2.dh{font-size:16px;margin:0 0 4px;font-weight:700}
.sub{color:var(--muted);font-size:12px;margin-bottom:16px;word-break:break-word}
.badge{
  display:inline-flex;align-items:center;gap:5px;font-size:11px;
  padding:2px 9px;border-radius:12px;font-weight:600;
}
.badge.green{background:var(--green-bg);color:var(--green);border:1px solid #1f5026}
.badge.red{background:var(--red-bg);color:var(--red);border:1px solid #5a2226}
.badge.amber{background:var(--amber-bg);color:var(--amber);border:1px solid #5a4a18}
.badge.neutral{background:var(--panel2);color:var(--muted);border:1px solid var(--border)}
.card{
  background:var(--panel);border:1px solid var(--border);border-radius:8px;
  padding:14px 16px;margin-bottom:16px;
}
.card h3{margin:0 0 10px;font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--dim)}
.tree{list-style:none;margin:0;padding:0}
.tree .node{margin:0}
.planner-node{
  display:flex;align-items:center;gap:9px;padding:8px 10px;
  background:var(--panel2);border:1px solid var(--border);border-radius:7px;margin-bottom:6px;
}
.planner-node .role{font-weight:700;color:var(--accent2)}
.prov{color:var(--green);font-size:11px}
.branch{margin-left:14px;border-left:2px solid var(--border);padding-left:14px}
.subagent{margin:6px 0}
.sa-head{
  display:flex;align-items:center;gap:9px;padding:7px 10px;cursor:pointer;
  background:var(--panel);border:1px solid var(--border);border-radius:7px;
}
.sa-head:hover{border-color:var(--accent)}
.sa-head .role{font-weight:700;color:var(--accent)}
.sa-head .caret{color:var(--dim);transition:transform .12s;display:inline-block;width:12px}
.sa-head.open .caret{transform:rotate(90deg)}
.sa-head.unused{opacity:.55}
.sa-head .count{color:var(--dim);font-size:11px;margin-left:auto}
.sa-body{padding:8px 0 4px 18px;display:none}
.sa-body.open{display:block}
.subtask{
  border:1px solid var(--border);border-radius:6px;padding:9px 11px;margin:7px 0;
  background:var(--panel2);
}
.subtask .st-top{display:flex;align-items:center;gap:8px;margin-bottom:5px}
.subtask .st-id{font-weight:700;color:var(--fg)}
.subtask .desc{color:var(--muted);font-size:12px}
.subtask .deps{color:var(--dim);font-size:11px;margin-top:4px}
.subtask pre{
  margin:7px 0 0;background:#0b0f14;border:1px solid var(--border);border-radius:5px;
  padding:8px 10px;overflow-x:auto;font-size:11.5px;color:#c9d1d9;white-space:pre-wrap;word-break:break-word;
}
.answer{
  background:#0b0f14;border:1px solid var(--border);border-radius:6px;
  padding:12px 14px;white-space:pre-wrap;word-break:break-word;color:#c9d1d9;font-size:12.5px;
}
.mem-list,.skill-list{list-style:none;margin:0;padding:0}
.mem-item{padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:7px;background:var(--panel2)}
.mem-item .ts{color:var(--dim);font-size:10.5px;margin-top:3px}
.skill-item{border:1px solid var(--border);border-radius:6px;margin-bottom:8px;background:var(--panel2)}
.skill-head{padding:8px 11px;cursor:pointer;display:flex;align-items:center;gap:8px}
.skill-head:hover{background:var(--panel)}
.skill-head .name{font-weight:700;color:var(--accent2)}
.skill-body{display:none;padding:0 11px 10px}
.skill-body.open{display:block}
.skill-body pre{margin:0;background:#0b0f14;border:1px solid var(--border);border-radius:5px;padding:9px 11px;overflow-x:auto;font-size:11.5px;color:#c9d1d9;white-space:pre-wrap}
.tabs{display:flex;gap:4px;margin-bottom:14px;border-bottom:1px solid var(--border)}
.tab{padding:8px 14px;cursor:pointer;color:var(--muted);border-bottom:2px solid transparent;font-size:12px}
.tab:hover{color:var(--fg)}
.tab.active{color:var(--fg);border-bottom-color:var(--accent)}
.panel-pane{display:none}
.panel-pane.active{display:block}
.kv{color:var(--dim);font-size:11.5px}
.kv b{color:var(--muted);font-weight:600}
.loading{color:var(--dim);padding:20px;text-align:center}
.transcript .line{padding:4px 0;border-bottom:1px solid #1b2129;white-space:pre-wrap;word-break:break-word}
.transcript .line.user{color:var(--accent)}
.transcript .line.assistant{color:var(--fg)}
.ops-list{list-style:none;margin:0;padding:0}
.ops-item{padding:10px 0;border-bottom:1px solid #1b2129}
.ops-item:last-child{border-bottom:0}
.ops-title{font-weight:700;word-break:break-word}
.ops-meta{display:flex;gap:7px;flex-wrap:wrap;margin-top:5px;color:var(--dim);font-size:11px}
.wave-list{display:flex;gap:5px;flex-wrap:wrap;margin-top:8px}
.wave{font-size:10.5px;padding:2px 6px;border:1px solid var(--border);border-radius:4px;color:var(--muted);background:var(--panel2)}
</style>
</head>
<body>
<header>
  <span class="logo">ro<span class="k">n</span>in</span>
  <span class="tag">agent dashboard &middot; read-only</span>
  <span class="spacer"></span>
  <button class="refresh" id="refreshBtn">&#x21bb; refresh</button>
</header>
<div class="layout">
  <aside class="sidebar">
    <div class="section-h">Recent runs</div>
    <div id="runs"><div class="loading">loading...</div></div>
  </aside>
  <main class="main">
    <div class="tabs">
      <div class="tab active" data-pane="run">Run detail</div>
      <div class="tab" data-pane="operations">Operations</div>
      <div class="tab" data-pane="memory">Memory</div>
      <div class="tab" data-pane="skills">Skills</div>
    </div>
    <div class="panel-pane active" id="pane-run">
      <div class="detail-empty" id="runDetail">
        <span class="big">&#9776;</span>
        <div>Select a run on the left to see its sub-agent tree.</div>
      </div>
    </div>
    <div class="panel-pane" id="pane-operations"><div class="loading">loading...</div></div>
    <div class="panel-pane" id="pane-memory"><div class="loading">loading...</div></div>
    <div class="panel-pane" id="pane-skills"><div class="loading">loading...</div></div>
  </main>
</div>
<script>
"use strict";
// All data comes from THIS app's own endpoints (relative paths only).
var API = {
  runs: "/ui/runs",
  run: function(id){ return "/ui/runs/" + encodeURIComponent(id); },
  memory: "/ui/memory",
  skills: "/ui/skills",
  fleetPlans: "/ui/fleet-plans",
  proposals: "/ui/proposals"
};
var state = { runs: [], activeId: null };

function esc(s){
  return String(s == null ? "" : s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;");
}
function el(html){ var t=document.createElement("template"); t.innerHTML=html.trim(); return t.content.firstChild; }
async function getJSON(url){
  var r = await fetch(url, {headers:{"Accept":"application/json"}});
  if(!r.ok) throw new Error("HTTP "+r.status);
  return await r.json();
}
function fmtDate(s){
  if(!s) return "";
  return String(s).replace("T"," ").slice(0,19);
}

// ---- faithfulness badge ----
function faithBadge(f){
  if(!f) return '<span class="badge neutral" title="not scored">&#9675; unscored</span>';
  var score = (typeof f.score==="number") ? f.score.toFixed(2) : "?";
  if(f.abstain){
    return '<span class="badge amber" title="'+esc(f.reason||"abstained")+'">&#9888; abstain '+score+'</span>';
  }
  if(f.grounded){
    return '<span class="badge green" title="grounded in sources">&#10003; grounded '+score+'</span>';
  }
  return '<span class="badge red" title="ungrounded">&#10007; ungrounded '+score+'</span>';
}

// ---- recent runs list ----
function renderRuns(){
  var c = document.getElementById("runs");
  c.innerHTML = "";
  if(!state.runs.length){
    c.appendChild(el('<div class="empty"><span class="big">&#9737;</span>'+
      'No runs yet.<br>Run <b>ronin orchestrate "..."</b> or start a chat, then refresh.</div>'));
    return;
  }
  state.runs.forEach(function(r){
    var dotCls = r.success ? "ok" : "fail";
    var kindCls = r.kind === "session" ? "session" : "orchestrate";
    var sub = r.kind === "session"
      ? ((r.turns||0)+" turns")
      : ((r.subtasks||0)+" subtasks");
    var node = el(
      '<div class="run" data-id="'+esc(r.id)+'">'+
        '<div class="row">'+
          '<span class="dot '+dotCls+'"></span>'+
          '<span class="title">'+esc(r.title||"(untitled)")+'</span>'+
          '<span class="pill '+kindCls+'">'+esc(r.kind)+'</span>'+
        '</div>'+
        '<div class="meta"><span>'+esc(fmtDate(r.created))+'</span><span>&middot;</span><span>'+esc(sub)+'</span></div>'+
      '</div>');
    if(r.id === state.activeId) node.classList.add("active");
    node.addEventListener("click", function(){ selectRun(r.id); });
    c.appendChild(node);
  });
}

// ---- run detail ----
async function selectRun(id){
  state.activeId = id;
  renderRuns();
  switchTab("run");
  var pane = document.getElementById("runDetail");
  pane.className = "";
  pane.innerHTML = '<div class="loading">loading run...</div>';
  try{
    var d = await getJSON(API.run(id));
    renderRunDetail(d);
  }catch(e){
    pane.className = "detail-empty";
    pane.innerHTML = '<span class="big">&#9888;</span><div>Could not load run ('+esc(e.message)+').</div>';
  }
}

function subtaskHtml(st){
  var deps = (st.depends_on && st.depends_on.length)
    ? '<div class="deps">depends on: '+st.depends_on.map(esc).join(", ")+'</div>' : "";
  var out = st.output ? '<pre>'+esc(st.output)+'</pre>' : "";
  var err = st.error ? '<pre style="color:var(--red)">'+esc(st.error)+'</pre>' : "";
  return '<div class="subtask">'+
    '<div class="st-top">'+
      '<span class="dot '+(st.success?"ok":"fail")+'"></span>'+
      '<span class="st-id">'+esc(st.id)+'</span>'+
      faithBadge(st.faithfulness)+
      '<span class="kv" style="margin-left:auto">'+esc(st.iterations||0)+' iters</span>'+
    '</div>'+
    '<div class="desc">'+esc(st.description)+'</div>'+ deps + out + err +
  '</div>';
}

function renderRunDetail(d){
  var pane = document.getElementById("runDetail");
  pane.className = "";
  if(d.kind === "session"){ renderSessionDetail(pane, d); return; }

  var html = "";
  html += '<h2 class="dh">'+esc(d.title)+'</h2>';
  html += '<div class="sub">'+
    '<span class="badge '+(d.success?"green":"red")+'">'+(d.success?"success":"failed")+'</span> '+
    faithBadge(d.faithfulness)+
    ' <span class="kv">&middot; '+esc(fmtDate(d.created))+' &middot; id '+esc(d.id)+'</span></div>';

  // --- sub-agent tree ---
  html += '<div class="card"><h3>Orchestrator sub-agent tree</h3><ul class="tree">';
  html += '<li class="node"><div class="planner-node">'+
    '<span class="badge neutral">planner</span>'+
    '<span class="role">decompose &amp; synthesize</span>'+
    '<span class="prov">'+esc(d.planner||"?")+'</span></div>';
  html += '<div class="branch">';
  var sas = d.subagents || [];
  if(!sas.length){
    html += '<div class="empty">No sub-agents recorded for this run.</div>';
  }
  sas.forEach(function(sa, i){
    var n = (sa.subtasks||[]).length;
    var unused = sa.used ? "" : " unused";
    html += '<div class="subagent">'+
      '<div class="sa-head'+unused+'" data-sa="'+i+'">'+
        '<span class="caret">&#9656;</span>'+
        '<span class="role">'+esc(sa.role)+'</span>'+
        '<span class="prov">'+esc(sa.provider||"base")+'</span>'+
        '<span class="count">'+n+(n===1?" subtask":" subtasks")+(sa.used?"":" &middot; idle")+'</span>'+
      '</div>'+
      '<div class="sa-body" data-sabody="'+i+'">'+
        ((sa.subtasks||[]).map(subtaskHtml).join("") || '<div class="kv" style="padding:4px 0">no subtasks assigned</div>')+
      '</div></div>';
  });
  html += '</div></li></ul></div>';

  // --- synthesized answer ---
  html += '<div class="card"><h3>Synthesized answer '+faithBadge(d.faithfulness)+'</h3>'+
    '<div class="answer">'+(d.output ? esc(d.output) : '<span class="kv">(no output)</span>')+'</div></div>';

  if(d.error){
    html += '<div class="card"><h3>Error</h3><div class="answer" style="color:var(--red)">'+esc(d.error)+'</div></div>';
  }

  pane.innerHTML = html;
  // expand/collapse sub-agent branches
  pane.querySelectorAll(".sa-head").forEach(function(h){
    h.addEventListener("click", function(){
      var i = h.getAttribute("data-sa");
      var body = pane.querySelector('[data-sabody="'+i+'"]');
      h.classList.toggle("open"); body.classList.toggle("open");
    });
  });
  // open the first used branch by default
  var first = pane.querySelector('.sa-head:not(.unused)');
  if(first) first.click();
}

function renderSessionDetail(pane, d){
  var html = "";
  html += '<h2 class="dh">'+esc(d.title)+'</h2>';
  html += '<div class="sub"><span class="badge session" style="background:var(--panel2);border:1px solid var(--border);color:var(--accent)">chat session</span> '+
    '<span class="kv">&middot; '+esc(fmtDate(d.created))+' &middot; id '+esc(d.id)+'</span></div>';
  html += '<div class="card"><h3>Transcript</h3><div class="transcript">';
  var t = d.transcript || [];
  if(!t.length){ html += '<div class="kv">(empty transcript)</div>'; }
  t.forEach(function(line){
    var cls = /^USER:/.test(line) ? "user" : (/^ASSISTANT:/.test(line) ? "assistant" : "");
    html += '<div class="line '+cls+'">'+esc(line)+'</div>';
  });
  html += '</div></div>';
  pane.innerHTML = html;
}

// ---- agent operations ----
function operationsEmpty(message){
  return '<div class="empty"><span class="big">&#9783;</span>'+message+'</div>';
}

function fleetPlanHtml(plan){
  var mode = plan.write ? "write-enabled" : "read-only";
  var waves = (plan.waves || []).map(function(wave){
    var wait = (wave.waits_for || []).length ? ' after '+wave.waits_for.join(",") : "";
    return '<span class="wave">wave '+esc(wave.number)+' '+esc(wave.phase)+' &middot; '+esc(wave.parallelism)+' agents'+esc(wait)+'</span>';
  }).join("");
  return '<li class="ops-item">'+
    '<div class="ops-title">'+esc(plan.goal)+'</div>'+
    '<div class="ops-meta"><span class="badge neutral">'+mode+'</span><span>'+esc(plan.member_count)+' selected of '+esc(plan.catalog_size)+'</span><span>max '+esc(plan.max_parallel)+' parallel</span><span>'+esc(fmtDate(plan.created))+'</span></div>'+
    '<div class="wave-list">'+waves+'</div>'+
  '</li>';
}

function proposalHtml(proposal){
  var ready = proposal.status === "ready";
  var status = '<span class="badge '+(ready ? "green" : "amber")+'">'+esc(proposal.status)+'</span>';
  var failed = proposal.failed_subtasks ? '<span>'+esc(proposal.failed_subtasks)+' failed subtasks</span>' : "";
  return '<li class="ops-item">'+
    '<div class="ops-title">'+esc(proposal.run_id)+'</div>'+
    '<div class="ops-meta">'+status+'<span>'+esc((proposal.roles || []).join(", "))+'</span><span>'+esc(proposal.patch_count)+' patches</span>'+failed+'<span>base '+esc((proposal.base_revision || "").slice(0,12))+'</span><span>'+esc(fmtDate(proposal.created))+'</span></div>'+
  '</li>';
}

async function loadOperations(){
  var pane = document.getElementById("pane-operations");
  pane.innerHTML = '<div class="loading">loading...</div>';
  try{
    var data = await Promise.all([getJSON(API.fleetPlans), getJSON(API.proposals)]);
    var plans = data[0], proposals = data[1];
    var html = '<div class="card"><h3>Fleet plans ('+plans.length+')</h3>';
    html += plans.length ? '<ul class="ops-list">'+plans.map(fleetPlanHtml).join("")+'</ul>' : operationsEmpty('No fleet plans saved for this project.');
    html += '</div><div class="card"><h3>Retained patch proposals ('+proposals.length+')</h3>';
    html += proposals.length ? '<ul class="ops-list">'+proposals.map(proposalHtml).join("")+'</ul>' : operationsEmpty('No isolated-worktree proposals saved for this project.');
    pane.innerHTML = html;
  }catch(e){
    pane.innerHTML = '<div class="empty">Could not load operations ('+esc(e.message)+').</div>';
  }
}

// ---- memory ----
async function loadMemory(){
  var pane = document.getElementById("pane-memory");
  pane.innerHTML = '<div class="loading">loading...</div>';
  try{
    var items = await getJSON(API.memory);
    if(!items.length){
      pane.innerHTML = '<div class="empty"><span class="big">&#9788;</span>'+
        'Nothing remembered yet.<br>ronin saves durable facts about you as you work.</div>';
      return;
    }
    var html = '<div class="card"><h3>What ronin remembers ('+items.length+')</h3><ul class="mem-list">';
    items.forEach(function(m){
      var ts = m.ts ? '<div class="ts">'+esc(new Date(m.ts*1000).toISOString().slice(0,19).replace("T"," "))+'</div>' : "";
      html += '<li class="mem-item">'+esc(m.text)+ts+'</li>';
    });
    html += '</ul></div>';
    pane.innerHTML = html;
  }catch(e){
    pane.innerHTML = '<div class="empty">Could not load memory ('+esc(e.message)+').</div>';
  }
}

// ---- skills ----
async function loadSkills(){
  var pane = document.getElementById("pane-skills");
  pane.innerHTML = '<div class="loading">loading...</div>';
  try{
    var items = await getJSON(API.skills);
    if(!items.length){
      pane.innerHTML = '<div class="empty"><span class="big">&#9670;</span>'+
        'No crystallized skills.<br>ronin saves reusable repo-local skills to <b>.ronin/commands/</b>.</div>';
      return;
    }
    var html = '<div class="card"><h3>Crystallized skills ('+items.length+')</h3><ul class="skill-list">';
    items.forEach(function(s, i){
      html += '<li class="skill-item">'+
        '<div class="skill-head" data-skill="'+i+'"><span class="caret">&#9656;</span><span class="name">/'+esc(s.name)+'</span></div>'+
        '<div class="skill-body" data-skillbody="'+i+'"><pre>'+esc(s.body)+'</pre></div></li>';
    });
    html += '</ul></div>';
    pane.innerHTML = html;
    pane.querySelectorAll(".skill-head").forEach(function(h){
      h.addEventListener("click", function(){
        var i=h.getAttribute("data-skill");
        h.classList.toggle("open");
        pane.querySelector('[data-skillbody="'+i+'"]').classList.toggle("open");
      });
    });
  }catch(e){
    pane.innerHTML = '<div class="empty">Could not load skills ('+esc(e.message)+').</div>';
  }
}

// ---- tabs ----
function switchTab(name){
  document.querySelectorAll(".tab").forEach(function(t){
    t.classList.toggle("active", t.getAttribute("data-pane")===name);
  });
  document.querySelectorAll(".panel-pane").forEach(function(p){
    p.classList.toggle("active", p.id==="pane-"+name);
  });
  if(name==="operations") loadOperations();
  if(name==="memory") loadMemory();
  if(name==="skills") loadSkills();
}
document.querySelectorAll(".tab").forEach(function(t){
  t.addEventListener("click", function(){ switchTab(t.getAttribute("data-pane")); });
});

// ---- boot ----
async function loadRuns(){
  try{
    state.runs = await getJSON(API.runs);
  }catch(e){
    state.runs = [];
  }
  renderRuns();
}
document.getElementById("refreshBtn").addEventListener("click", function(){
  loadRuns();
  var active = document.querySelector(".tab.active");
  if(active){ switchTab(active.getAttribute("data-pane")); }
});
loadRuns();
</script>
</body>
</html>
"""
