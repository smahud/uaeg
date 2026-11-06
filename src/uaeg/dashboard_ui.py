"""Embedded dashboard UI template (single HTML page, no external assets)."""

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>UAEG · Dashboard</title>
<style>
:root{--bg:#0e141b;--bg-raised:#121923;--panel:#141c26;--panel-2:#1b2534;--border:rgba(255,255,255,.08);--border-strong:rgba(255,255,255,.16);--text:#d5dde8;--text-dim:#8d99ab;--text-faint:#647080;--accent:#4f9cf9;--accent-strong:#8abaff;--accent-dim:rgba(79,156,249,.15);--green:#3fb970;--green-dim:rgba(63,185,112,.13);--red:#f0635f;--red-dim:rgba(240,99,95,.13);--orange:#e0a83e;--orange-dim:rgba(224,168,62,.12);--gray:#93a0b0;--gray-dim:rgba(147,160,176,.14);--blue-dim:rgba(79,156,249,.14);--mono:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace;--sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif}
*{box-sizing:border-box}
html{color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--text);font:13px/1.5 var(--sans)}
.mono{font-family:var(--mono);font-size:12px}
.dim{color:var(--text-dim)}
.hidden{display:none!important}
#topbar{position:sticky;top:0;z-index:60;display:flex;align-items:center;justify-content:space-between;gap:12px;height:50px;padding:0 20px;background:rgba(14,20,27,.94);border-bottom:1px solid var(--border)}
.brand{display:flex;align-items:center;gap:8px;font-size:14px}
.brand-logo{width:10px;height:10px;border-radius:3px;background:var(--accent);box-shadow:0 0 10px rgba(79,156,249,.8)}
.brand-name{font-weight:700;letter-spacing:.04em}
.brand-sub{color:var(--text-dim)}
.chip{font-size:11px;color:var(--text-dim);border:1px solid var(--border);border-radius:999px;padding:1px 8px;margin-left:6px}
.top-meta{display:flex;align-items:center;gap:14px;color:var(--text-dim)}
.refresh-btn{appearance:none;display:inline-flex;align-items:center;gap:7px;background:none;border:1px solid transparent;color:var(--text-dim);font:11.5px var(--mono);padding:4px 9px;border-radius:6px;cursor:pointer}
.refresh-btn:hover{border-color:var(--border-strong);color:var(--text)}
.refresh-btn .dot{width:7px;height:7px;border-radius:50%;background:var(--green);animation:blink 2.4s ease-in-out infinite}
.refresh-btn.err .dot{background:var(--red);animation:none}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}
#conn-banner{display:flex;align-items:center;gap:10px;margin:0 0 12px;padding:8px 12px;border:1px solid rgba(240,99,95,.45);background:var(--red-dim);border-radius:8px;color:#f3a09d;font-size:12.5px}
#tabs{position:sticky;top:50px;z-index:50;display:flex;gap:2px;padding:0 12px;background:rgba(14,20,27,.94);border-bottom:1px solid var(--border);overflow-x:auto}
.tab-btn{appearance:none;background:none;border:none;border-bottom:2px solid transparent;color:var(--text-dim);font:600 12px/1 var(--sans);letter-spacing:.08em;text-transform:uppercase;padding:13px 14px 11px;cursor:pointer;white-space:nowrap}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--accent-strong);border-bottom-color:var(--accent)}
main{padding:16px 20px 48px;max-width:1440px;margin:0 auto}
.tab-panel{display:none}
.tab-panel.active{display:block}
.tab-head{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin:4px 0 12px;flex-wrap:wrap}
.tab-head h2{margin:0;font-size:16px;font-weight:600}
.tab-desc{margin:3px 0 0;color:var(--text-dim);font-size:12.5px}
h3.section{margin:20px 0 8px;font-size:13px;font-weight:600;display:flex;align-items:baseline;gap:8px}
h3.section .hint{font-weight:400;color:var(--text-faint);font-size:11.5px}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden}
.padded{padding:14px 16px}
.panel-title{margin:0 0 10px;font-size:13px;font-weight:600}
.hint-line{color:var(--text-faint);font-size:11.5px;margin:4px 0 0}
.table-wrap{overflow-x:auto}
table.data{width:100%;border-collapse:collapse;font-size:12.5px}
table.data th{text-align:left;padding:8px 12px;font:600 10.5px/1.3 var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--text-faint);border-bottom:1px solid var(--border);background:var(--bg-raised);white-space:nowrap}
table.data td{padding:7px 12px;border-bottom:1px solid var(--border);vertical-align:top}
table.data tbody tr:last-child td{border-bottom:none}
table.data tbody tr:hover td{background:rgba(255,255,255,.022)}
td.cell-actions{white-space:nowrap;text-align:right}
td.cell-cmd,td.cell-preview{max-width:320px}
.ts{color:var(--text-dim);font-size:11px}
.trunc{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 9px;border-radius:999px;font:600 11px/1.4 var(--sans);white-space:nowrap}
.badge.pulse::before{content:'';width:6px;height:6px;border-radius:50%;background:currentColor;animation:blink 1.4s ease-in-out infinite}
.badge-green{color:#52d68a;background:var(--green-dim)}
.badge-red{color:#f38b88;background:var(--red-dim)}
.badge-orange{color:#e6b566;background:var(--orange-dim)}
.badge-gray{color:var(--gray);background:var(--gray-dim)}
.badge-blue{color:#85baff;background:var(--blue-dim)}
.btn{appearance:none;display:inline-flex;align-items:center;justify-content:center;gap:6px;background:var(--panel-2);color:var(--text);border:1px solid var(--border-strong);border-radius:6px;padding:6px 12px;font:600 12px/1 var(--sans);cursor:pointer;transition:background .12s,border-color .12s,color .12s}
.btn:hover{background:#223048;border-color:rgba(255,255,255,.28)}
.btn:disabled{opacity:.4;cursor:default}
.btn-primary{background:var(--accent-dim);border-color:rgba(79,156,249,.45);color:var(--accent-strong)}
.btn-primary:hover{background:rgba(79,156,249,.26);border-color:rgba(79,156,249,.7)}
.btn-danger{background:var(--red-dim);border-color:rgba(240,99,95,.4);color:#f38b88}
.btn-danger:hover{background:rgba(240,99,95,.24);border-color:rgba(240,99,95,.65)}
.btn-sm{padding:4px 9px;font-size:11px}
.btn-icon{padding:5px 9px;font-size:14px;line-height:1}
input[type=text],select,textarea{background:#0b1017;color:var(--text);border:1px solid var(--border-strong);border-radius:6px;padding:6px 9px;font:12.5px var(--sans)}
textarea{font-family:var(--mono);font-size:12px;width:100%;resize:vertical}
input:focus,select:focus,textarea:focus,.btn:focus-visible{outline:1px solid var(--accent)}
input::placeholder,textarea::placeholder{color:var(--text-faint)}
#log-filter{width:280px}
.form-panel{padding:14px 16px;margin-bottom:12px}
.form-row{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}
.form-field{display:flex;flex-direction:column;gap:4px}
.form-field label{font-size:10.5px;color:var(--text-dim);text-transform:uppercase;font-weight:600}
.form-err{margin-top:8px;color:#f3a09d;font-size:12px}
.empty{padding:26px 16px;text-align:center;color:var(--text-dim);font-size:12.5px}
.empty.error{color:#f3a09d}
.warn-box{border:1px solid rgba(224,168,62,.45);background:var(--orange-dim);color:#e6b566;border-radius:8px;padding:10px 14px;font-size:12.5px;line-height:1.55;margin:0 0 12px}
.danger-box{border:1px solid rgba(240,99,95,.5);background:var(--red-dim);color:#f3a09d;border-radius:8px;padding:11px 14px;font-size:12.5px;font-weight:600;text-align:center;margin:12px 0}
.warn-inline{color:#e6b566;font-weight:600}
.note-box{border:1px solid var(--border);background:var(--bg-raised);color:var(--text-dim);border-radius:8px;padding:10px 14px;font-size:12.5px;margin-top:12px}
.modal-overlay{position:fixed;inset:0;z-index:100;display:none;align-items:flex-start;justify-content:center;padding:9vh 16px 20px;background:rgba(4,7,11,.7)}
.modal-overlay.open{display:flex}
.modal{width:100%;max-width:540px;background:var(--panel);border:1px solid var(--border-strong);border-radius:10px;box-shadow:0 24px 60px rgba(0,0,0,.55);animation:pop .14s ease-out;overflow:hidden;display:flex;flex-direction:column;max-height:82vh}
.modal-wide{max-width:880px}
@keyframes pop{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:none}}
.modal-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 16px;border-bottom:1px solid var(--border)}
.modal-head h3{margin:0;font-size:14px}
.modal-body{padding:16px;overflow-y:auto}
.modal-foot{display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border)}
.confirm-text{margin:0;font-size:13px;line-height:1.6}
.secret-sub{margin:0 0 4px;color:var(--text-dim);font-size:12.5px}
.secret-box{background:#0a0f15;border:1px dashed rgba(79,156,249,.55);border-radius:6px;padding:12px 14px;font:13px/1.6 var(--mono);color:#9ecbff;word-break:break-all;user-select:all;margin:10px 0}
.secret-hint{color:var(--text-dim);font-size:12px;margin:0;line-height:1.5}
.jobout-controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.jobout-controls label{font-size:12px;color:var(--text-dim);display:inline-flex;align-items:center;gap:6px}
#jobout-meta{display:inline-flex;align-items:center;gap:8px;color:var(--text-dim);font-size:12px;flex:1}
pre.output-pre{background:#0a0f15;border:1px solid var(--border);border-radius:6px;padding:12px;margin:0;font:12px/1.55 var(--mono);color:#b8c4d4;white-space:pre-wrap;word-break:break-all;max-height:50vh;overflow-y:auto}
tr.log-row{cursor:pointer}
tr.log-row.open td{background:rgba(79,156,249,.05)}
tr.detail-row td{background:var(--bg-raised);padding:10px 16px 14px}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px}
.detail-title{font:600 10.5px var(--sans);text-transform:uppercase;letter-spacing:.08em;color:var(--text-faint);margin-bottom:6px}
pre.detail-pre{background:#0a0f15;border:1px solid var(--border);border-radius:6px;padding:10px 12px;margin:0;font:11.5px/1.5 var(--mono);color:#b8c4d4;white-space:pre-wrap;word-break:break-all;max-height:260px;overflow:auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.card h3{margin:0 0 10px;font-size:13px;font-weight:600}
.card-wide{grid-column:1/-1}
dl.kv{display:grid;grid-template-columns:130px 1fr;gap:7px 12px;margin:0;font-size:12.5px}
dl.kv dt{color:var(--text-dim)}
dl.kv dd{margin:0;word-break:break-all}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px}
.stat{background:var(--bg-raised);border:1px solid var(--border);border-radius:8px;padding:10px 12px}
.stat .num{font:700 20px/1.2 var(--mono)}
.stat .lbl{color:var(--text-dim);font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-top:2px}
#toasts{position:fixed;top:60px;right:16px;z-index:200;display:flex;flex-direction:column;gap:8px;width:360px;max-width:calc(100vw - 32px)}
.toast{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;background:#1a2432;border:1px solid var(--border-strong);border-left:3px solid var(--accent);border-radius:8px;padding:10px 12px;font-size:12.5px;box-shadow:0 10px 30px rgba(0,0,0,.4);animation:pop .15s ease-out;word-break:break-word}
.toast.error{border-left-color:var(--red)}
.toast.ok{border-left-color:var(--green)}
.toast-close{appearance:none;background:none;border:none;color:var(--text-faint);font-size:15px;line-height:1;cursor:pointer;padding:0}
.toast-close:hover{color:var(--text)}
</style>
</head>
<body>
<header id="topbar">
  <div class="brand">
    <span class="brand-logo"></span>
    <span class="brand-name">UAEG</span>
    <span class="dim">·</span>
    <span class="brand-sub">Dashboard</span>
    <span class="chip mono" id="hd-version">v—</span>
  </div>
  <div class="top-meta">
    <span class="mono" id="hd-uptime">up —</span>
    <button class="refresh-btn" id="refresh-ind">
      <span class="dot"></span><span id="refresh-text">connecting…</span>
    </button>
  </div>
</header>

<nav id="tabs">
  <button class="tab-btn active" data-tab="keys">Keys</button>
  <button class="tab-btn" data-tab="permissions">Permissions</button>
  <button class="tab-btn" data-tab="sessions">Sessions</button>
  <button class="tab-btn" data-tab="logs">Logs</button>
  <button class="tab-btn" data-tab="server">Server</button>
</nav>

<main>
<div id="conn-banner" class="hidden"><span>⚠</span><span id="conn-banner-msg"></span></div>

<section id="tab-keys" class="tab-panel active">
  <div class="tab-head">
    <div>
      <h2>Agent keys</h2>
      <p class="tab-desc">Bearer keys authenticate agents on the MCP endpoint; secrets are shown only once.</p>
    </div>
    <button class="btn btn-primary" id="btn-new-key">+ Generate new key</button>
  </div>
  <div class="panel form-panel hidden" id="key-gen-form">
    <div class="form-row">
      <div class="form-field">
        <label for="key-name">Name</label>
        <input type="text" id="key-name" placeholder="e.g. Claude">
      </div>
      <div class="form-field">
        <label for="key-mode">Permission mode</label>
        <select id="key-mode">
          <option value="all">all — unrestricted</option>
          <option value="restricted">restricted — advisory</option>
        </select>
      </div>
      <button class="btn btn-primary" id="key-gen-submit">Generate</button>
      <button class="btn" id="key-gen-cancel">Cancel</button>
    </div>
    <div class="form-err" id="key-gen-err"></div>
  </div>
  <div class="panel">
    <div class="table-wrap">
      <table class="data" id="keys-table">
        <thead><tr><th>Name</th><th>Key ID</th><th>Prefix</th><th>Status</th><th>Mode</th><th>Created</th><th>Last used</th><th style="text-align:right">Actions</th></tr></thead>
        <tbody id="keys-tbody"></tbody>
      </table>
    </div>
    <div class="empty hidden" id="keys-empty" data-default="No keys yet. Generate one to connect an agent."></div>
  </div>
</section>

<section id="tab-permissions" class="tab-panel">
  <div class="tab-head">
    <div>
      <h2>Permissions</h2>
      <p class="tab-desc">Access defaults and advisory path restrictions for agents.</p>
    </div>
  </div>
  <div class="empty error hidden" id="perm-empty"></div>
  <div class="panel padded">
    <h3 class="panel-title">Default access mode</h3>
    <p style="margin:0 0 6px;font-size:13px"><span id="perm-default-mode"></span> <span id="perm-default-desc" class="dim"></span></p>
    <p class="hint-line">Set in the UAEG configuration file; read-only here.</p>
  </div>
  <h3 class="section">Advisory restricted paths</h3>
  <div class="warn-box"><strong>Advisory restricted paths are usage rules communicated to agents. They are NOT a security boundary — a root shell can bypass them (PRD FR-014).</strong></div>
  <div class="panel padded">
    <p class="hint-line" style="margin:0 0 8px">One absolute path per line. Restricted-mode keys are asked, not forced, to avoid them.</p>
    <textarea id="restricted-paths" rows="8"></textarea>
    <div class="form-row" style="margin-top:10px">
      <button class="btn btn-primary" id="btn-save-paths">Save paths</button>
      <span class="hint-line" id="paths-status" style="margin-left:auto"></span>
    </div>
  </div>
</section>

<section id="tab-sessions" class="tab-panel">
  <div class="tab-head">
    <div>
      <h2>Sessions &amp; jobs</h2>
      <p class="tab-desc">Live PTY sessions, background jobs and session history.</p>
    </div>
  </div>
  <div class="empty error hidden" id="sess-empty"></div>
  <h3 class="section">Live PTY sessions <span class="hint">auto-refresh every 5 s</span></h3>
  <div class="panel">
    <div class="table-wrap">
      <table class="data" id="live-table">
        <thead><tr><th>Session</th><th>Command</th><th>PID</th><th>Created</th><th>Last activity</th><th>Buffered</th><th>Output preview</th><th style="text-align:right">Action</th></tr></thead>
        <tbody id="live-tbody"></tbody>
      </table>
    </div>
    <div class="empty hidden" id="live-empty" data-default="No live PTY sessions. Agents open one with shell_open."></div>
  </div>
  <h3 class="section">Background jobs</h3>
  <div class="panel">
    <div class="table-wrap">
      <table class="data" id="jobs-table">
        <thead><tr><th>Job</th><th>Command</th><th>Key</th><th>Status</th><th>Started</th><th>Finished</th><th>Exit</th><th style="text-align:right">Actions</th></tr></thead>
        <tbody id="jobs-tbody"></tbody>
      </table>
    </div>
    <div class="empty hidden" id="jobs-empty" data-default="No background jobs. Agents create them with background=true."></div>
  </div>
  <h3 class="section">Recent sessions <span class="hint">history</span></h3>
  <div class="panel">
    <div class="table-wrap">
      <table class="data" id="hist-table">
        <thead><tr><th>Session</th><th>Key</th><th>Command</th><th>Status</th><th>Created</th><th>Closed</th></tr></thead>
        <tbody id="hist-tbody"></tbody>
      </table>
    </div>
    <div class="empty hidden" id="hist-empty" data-default="No session history yet."></div>
  </div>
</section>

<section id="tab-logs" class="tab-panel">
  <div class="tab-head">
    <div>
      <h2>Execution audit log</h2>
      <p class="tab-desc">Newest first · auto-refresh 5 s · click a row for details.</p>
    </div>
    <input type="text" id="log-filter" placeholder="Filter logs…">
  </div>
  <div class="panel">
    <div class="table-wrap">
      <table class="data" id="logs-table">
        <thead><tr><th>Time</th><th>Key</th><th>Tool</th><th>Status</th><th>Exit</th><th>Duration</th><th>Command</th><th></th></tr></thead>
        <tbody id="logs-tbody"></tbody>
      </table>
    </div>
    <div class="empty hidden" id="logs-empty" data-default="No execution log entries yet."></div>
  </div>
</section>

<section id="tab-server" class="tab-panel">
  <div class="tab-head">
    <div>
      <h2>Server</h2>
      <p class="tab-desc">Runtime, endpoints, TLS and storage summary.</p>
    </div>
  </div>
  <div class="empty error hidden" id="srv-empty"></div>
  <div class="cards">
    <div class="card"><h3>Service</h3><dl class="kv" id="srv-service"></dl></div>
    <div class="card"><h3>MCP endpoint</h3><dl class="kv" id="srv-mcp"></dl></div>
    <div class="card"><h3>Dashboard listener</h3><dl class="kv" id="srv-dash"></dl></div>
    <div class="card card-wide"><h3>Summary</h3><div class="stats" id="srv-summary"></div></div>
  </div>
  <div class="note-box" id="srv-bind-note"></div>
</section>
</main>

<div id="toasts"></div>

<div class="modal-overlay" id="modal-confirm">
  <div class="modal">
    <div class="modal-head"><h3 id="confirm-title">Confirm</h3></div>
    <div class="modal-body"><p id="confirm-body" class="confirm-text"></p></div>
    <div class="modal-foot">
      <button class="btn" id="confirm-cancel">Cancel</button>
      <button class="btn btn-danger" id="confirm-ok">Confirm</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="modal-secret">
  <div class="modal">
    <div class="modal-head"><h3>One-time secret</h3></div>
    <div class="modal-body">
      <p class="secret-sub" id="secret-sub"></p>
      <div class="secret-box" id="secret-value"></div>
      <div class="danger-box">⚠ This secret is shown only once. Store it now.</div>
      <p class="secret-hint">Use it as the bearer token for the MCP endpoint — UAEG stores only a verifier and cannot show it again.</p>
    </div>
    <div class="modal-foot">
      <button class="btn" id="secret-copy">Copy</button>
      <button class="btn btn-primary" id="secret-saved">I have saved the secret</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="modal-job-output">
  <div class="modal modal-wide">
    <div class="modal-head">
      <h3 id="jobout-title">Job output</h3>
      <button class="btn btn-icon" id="jobout-close">×</button>
    </div>
    <div class="modal-body">
      <div class="jobout-controls">
        <label>Stream
          <select id="jobout-stream"><option value="stdout">stdout</option><option value="stderr">stderr</option></select>
        </label>
        <span id="jobout-meta"></span>
        <button class="btn" id="jobout-more">Load more</button>
      </div>
      <pre class="output-pre" id="jobout-pre"></pre>
    </div>
  </div>
</div>

<script>
(function(){
'use strict';
var NL=String.fromCharCode(10);
function el(id){return document.getElementById(id);}
function $all(s){return Array.prototype.slice.call(document.querySelectorAll(s));}
function esc(v){
 if(v===null||v===undefined)return '';
 return String(v).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});
}
function shortId(id,n){var s=String(id||'');n=n||12;return s.length>n?s.slice(0,n)+'…':s;}
function trunc(s,n){var t=(s==null)?'':String(s);return t.length>n?t.slice(0,n)+'…':t;}
function fmtBytes(n){
 if(n==null||isNaN(n))return '—';
 n=Number(n);
 if(n<1024)return n+' B';
 if(n<1048576)return (n/1024).toFixed(1)+' KB';
 if(n<1073741824)return (n/1048576).toFixed(1)+' MB';
 return (n/1073741824).toFixed(2)+' GB';
}
function fmtDur(ms){
 if(ms==null)return '—';
 var v=Number(ms);if(isNaN(v))return '—';
 if(v<1000)return Math.round(v)+' ms';
 if(v<60000)return (v/1000).toFixed(2)+' s';
 if(v<3600000)return Math.floor(v/60000)+'m '+String(Math.floor(v%60000/1000)).padStart(2,'0')+'s';
 return Math.floor(v/3600000)+'h '+String(Math.floor(v%3600000/60000)).padStart(2,'0')+'m';
}
function fmtUptime(s){
 s=Math.max(0,Math.floor(Number(s)||0));
 var d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60),p=function(x){return String(x).padStart(2,'0');};
 return (d>0?d+'d ':'')+p(h)+':'+p(m)+':'+p(s%60);
}
function utcClock(){return new Date().toISOString().slice(11,19);}
var BADGE={active:'green',revoked:'gray',open:'green',closed:'gray',interrupted:'gray',running:'blue pulse',completed:'green',failed:'red',cancelled:'orange',ok:'green',error:'red',timeout:'orange',denied:'gray'};
function badge(s){return '<span class="badge badge-'+(BADGE[s]||'gray')+'">'+esc((s==null||s==='')?'—':s)+'</span>';}
function td(cls,html){return '<td'+(cls?' class="'+cls+'"':'')+'>'+html+'</td>';}
function tdT(cls,title,html){return '<td class="'+cls+'" title="'+esc(title)+'">'+html+'</td>';}
function ttxt(v){return td('ts mono',esc(v||'—'));}
function cmdCell(v,n){return tdT('mono cell-cmd',v||'','<span class="trunc">'+esc(trunc(v||'—',n))+'</span>');}

// API client — every failure surfaces via toast and/or banner
async function api(method,path,body){
 var opts={method:method,headers:{}}; if(body!==undefined){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body);}
 var res;
 try{res=await fetch(path,opts);}
 catch(e){throw new Error('network error ('+((e&&e.message)?e.message:'fetch failed')+')');}
 var data=null;
 try{data=await res.json();}catch(e2){}
 if(!res.ok)throw new Error((data&&data.error)?data.error:('HTTP '+res.status+(res.statusText?' '+res.statusText:'')));
 return data;
}

var toastsEl=null;
function toast(msg,kind,ms){
 var t=document.createElement('div');t.className='toast '+(kind||'info');
 var s=document.createElement('span');s.textContent=msg;
 var x=document.createElement('button');x.className='toast-close';x.textContent='×';
 t.appendChild(s);t.appendChild(x);toastsEl.appendChild(t);
 var tm=setTimeout(rm,ms||(kind==='error'?9000:5000));
 function rm(){clearTimeout(tm);if(t.parentNode)t.parentNode.removeChild(t);}
 x.addEventListener('click',rm);
}

function openModal(id){el(id).classList.add('open');}
function closeModal(id){el(id).classList.remove('open');}
function modalOpen(){return document.querySelector('.modal-overlay.open')!==null;}
var confirmResolve=null;
function confirmDialog(o){
 return new Promise(function(resolve){
  confirmResolve=resolve;
  el('confirm-title').textContent=o.title||'Confirm action';
  if(o.html){el('confirm-body').innerHTML=o.html;}else{el('confirm-body').textContent=o.body||'Are you sure?';}
  var ok=el('confirm-ok');
  ok.textContent=o.confirmText||'Confirm';
  ok.className='btn '+(o.danger?'btn-danger':'btn-primary');
  openModal('modal-confirm');ok.focus();
 });
}
function settleConfirm(v){var r=confirmResolve;if(!r)return;confirmResolve=null;closeModal('modal-confirm');r(v);}
function confirmRun(o,btn,fn){confirmDialog(o).then(function(yes){if(yes)withBusy(btn,fn);});}
function copyText(text){
 // plain-HTTP LAN origins may block the async clipboard API — keep a fallback
 return new Promise(function(resolve){
  function fallback(){
   var ta=document.createElement('textarea');
   ta.value=text;ta.setAttribute('readonly','');
   ta.style.position='fixed';ta.style.top='0';ta.style.left='0';ta.style.opacity='0';
   document.body.appendChild(ta);ta.focus();ta.select();
   var ok=false;try{ok=document.execCommand('copy');}catch(e){}
   document.body.removeChild(ta);resolve(ok);
  }
  if(navigator.clipboard&&navigator.clipboard.writeText&&window.isSecureContext){
   navigator.clipboard.writeText(text).then(function(){resolve(true);},fallback);
  }else{fallback();}
 });
}

function setConn(good,err){
 el('refresh-ind').classList.toggle('err',!good);
 el('refresh-text').textContent=good?('updated '+utcClock()+' UTC'):'connection error';
 el('conn-banner').classList.toggle('hidden',good);
 if(!good)el('conn-banner-msg').textContent=err||'The UAEG dashboard API is unreachable.';
}
function showEmpty(e,msg,isErr){
 if(!e)return;
 e.classList.remove('hidden');e.classList.toggle('error',!!isErr);
 e.textContent=msg||e.getAttribute('data-default');
}
function setTableState(tid,eid,empty,msg,isErr){
 var t=el(tid),e=el(eid);
 if(empty){t.classList.add('hidden');showEmpty(e,msg,isErr);}
 else{t.classList.remove('hidden');e.classList.add('hidden');}
}
function loadFail(err,silent,tid,eid,what){
 var msg='Failed to load '+what+': '+err.message;
 setConn(false,msg+' — retrying every 5 s.');
 if(!silent)toast(msg,'error');
 if(eid){if(tid)el(tid).classList.add('hidden');showEmpty(el(eid),msg,true);}
}

// tabs & refresh loop: only the ACTIVE tab refreshes; paused while the
// document is hidden or any modal is open (protects the one-time secret)
var TABS=['keys','permissions','sessions','logs','server'];
var activeTab=null;
function setTab(name){
 if(TABS.indexOf(name)===-1)name='keys';
 activeTab=name;
 $all('.tab-btn').forEach(function(b){b.classList.toggle('active',b.dataset.tab===name);});
 TABS.forEach(function(t){el('tab-'+t).classList.toggle('active',t===name);});
 loadTab(name,false);
}
function loadTab(name,silent){
 var fn=LOADERS[name];
 if(!fn)return;
 Promise.resolve(fn(silent)).catch(function(e){setConn(false,'Unexpected error: '+((e&&e.message)?e.message:e));});
}
function periodicTick(){
 if(document.hidden||!activeTab)return;
 if(activeTab==='permissions'){loadOverview();return;} // never clobber the path editor
 if(modalOpen())return;
 loadTab(activeTab,true);loadOverview();
}

async function loadOverview(){
 try{
  var d=await api('GET','/api/overview');
  el('hd-version').textContent='v'+(d.version||'?');
  el('hd-uptime').textContent='up '+fmtUptime(d.uptime_seconds);
  setConn(true);
 }catch(e){setConn(false,'Lost connection to the UAEG API: '+e.message+' — retrying every 5 s.');}
}

var keysSig='';
function renderKeys(keys){
 setTableState('keys-table','keys-empty',keys.length===0);
 el('keys-tbody').innerHTML=keys.map(function(k){
  var revoked=k.status==='revoked';
  var mode=k.permission_mode==='restricted'?'restricted':'all';
  var prefix=String(k.prefix||'');if(prefix&&prefix.slice(-1)!=='…')prefix+='…';
  return '<tr>'+td('',esc(k.name))+td('mono dim',esc(k.key_id))+td('mono',esc(prefix||'—'))+td('',badge(k.status))+
   '<td><button class="btn btn-sm" data-act="mode" data-id="'+esc(k.key_id)+'" data-mode="'+mode+'"'+(revoked?' disabled':'')+'>'+mode+'</button></td>'+
   ttxt(k.created_at)+td('ts mono',esc(k.last_used_at||'never'))+
   '<td class="cell-actions">'+
   '<button class="btn btn-sm btn-danger" data-act="revoke" data-id="'+esc(k.key_id)+'" data-name="'+esc(k.name)+'"'+(revoked?' disabled':'')+'>Revoke</button> '+
   '<button class="btn btn-sm" data-act="rotate" data-id="'+esc(k.key_id)+'" data-name="'+esc(k.name)+'"'+(revoked?' disabled':'')+'>Rotate</button>'+
   '</td></tr>';
 }).join('');
}
async function loadKeys(silent){
 try{
  var d=await api('GET','/api/keys');
  var keys=(d&&d.keys)||[];var sig=JSON.stringify(keys);
  if(sig!==keysSig){keysSig=sig;renderKeys(keys);}
  setConn(true);
 }catch(e){loadFail(e,silent,keysSig?null:'keys-table',keysSig?null:'keys-empty','keys');}
}
function onKeysClick(ev){
 var btn=ev.target.closest('button[data-act]');
 if(!btn||btn.disabled)return;
 var act=btn.dataset.act,id=btn.dataset.id,name=btn.dataset.name||'';
 if(act==='revoke'){
  confirmRun({title:'Revoke key',body:'Revoke key "'+name+'"? The agent using it immediately loses access. This cannot be undone.',confirmText:'Revoke key',danger:true},btn,async function(){
   try{await api('POST','/api/keys/'+encodeURIComponent(id)+'/revoke');toast('Key "'+name+'" revoked.','ok');keysSig='';loadTab('keys',true);}
   catch(e){toast('Revoke failed: '+e.message,'error');}
  });
 }else if(act==='rotate'){
  confirmRun({title:'Rotate key',html:'Generate a new secret for key <b>'+esc(name)+'</b>?<br><span class="warn-inline">The old key stops working immediately</span> — the connected agent must be updated with the new secret.',confirmText:'Rotate key'},btn,async function(){
   try{
    var d=await api('POST','/api/keys/'+encodeURIComponent(id)+'/rotate',{});
    if(!d||!d.secret)throw new Error('server returned no secret');
    showSecret(d.secret,'New secret for key "'+((d.key&&d.key.name)||name)+'"');
    keysSig='';loadTab('keys',true);
   }catch(e){toast('Rotate failed: '+e.message,'error');}
  });
 }else if(act==='mode'){
  var next=(btn.dataset.mode==='restricted')?'all':'restricted';
  withBusy(btn,async function(){
   try{
    await api('POST','/api/keys/'+encodeURIComponent(id)+'/permissions',{permission_mode:next});
    btn.dataset.mode=next;btn.textContent=next;
    toast('Permission mode set to "'+next+'".','ok');
   }catch(e){toast('Could not change permission mode: '+e.message,'error');}
  });
 }
}
function onKeyGenSubmit(){
 var name=el('key-name').value.trim();
 var mode=el('key-mode').value==='restricted'?'restricted':'all';
 if(!name){el('key-gen-err').textContent='Enter a display name for the key (e.g. "Claude").';el('key-name').focus();return;}
 el('key-gen-err').textContent='';
 withBusy(el('key-gen-submit'),async function(){
  try{
   var d=await api('POST','/api/keys',{name:name,permission_mode:mode});
   if(!d||!d.secret)throw new Error('server returned no secret');
   el('key-gen-form').classList.add('hidden');el('key-name').value='';
   showSecret(d.secret,'Secret for new key "'+((d.key&&d.key.name)||name)+'" — permission mode: '+mode);
   keysSig='';loadTab('keys',true);
  }catch(e){el('key-gen-err').textContent=e.message;toast('Key generation failed: '+e.message,'error');}
 });
}

var pathsDirty=false;
function renderPermissions(d){
 var mode=(d&&d.default_mode)||'all',paths=(d&&d.restricted_paths)||[];
 el('perm-default-mode').innerHTML='<span class="badge '+(mode==='all'?'badge-green':'badge-orange')+'">'+esc(mode)+'</span>';
 el('perm-default-desc').textContent=(mode==='all')?'— authenticated agents have unrestricted root execution.':'— agents are advised to respect the restricted paths below.';
 if(!pathsDirty){
  el('restricted-paths').value=paths.join(NL);
 }
}
async function loadPermissions(silent){
 try{
  var d=await api('GET','/api/permissions');
  el('perm-empty').classList.add('hidden');
  renderPermissions(d);setConn(true);
 }catch(e){loadFail(e,silent,null,'perm-empty','permissions');}
}
function onSavePaths(){
 var seen={},lines=[];
 el('restricted-paths').value.split(NL).forEach(function(s){
  s=s.trim();if(s&&!seen[s]){seen[s]=1;lines.push(s);}
 });
 withBusy(el('btn-save-paths'),async function(){
  try{
   var d=await api('POST','/api/permissions',{restricted_paths:lines});
   pathsDirty=false;el('paths-status').textContent='saved';
   setTimeout(function(){if(!pathsDirty)el('paths-status').textContent='';},2500);
   renderPermissions(d);
   toast('Advisory restricted paths saved.','ok');
  }catch(e){toast('Save failed: '+e.message,'error');}
 });
}

var sessSig='';
function renderSessions(s,j){
 var live=(s&&s.live)||[],hist=(s&&s.history)||[],jobs=(j&&j.jobs)||[];
 setTableState('live-table','live-empty',live.length===0);
 el('live-tbody').innerHTML=live.map(function(x){
  return '<tr>'+
   tdT('mono',x.session_id,esc(shortId(x.session_id,10)))+cmdCell(x.command,42)+
   td('mono dim',(x.pid==null)?'—':x.pid)+
   ttxt(x.created_at)+ttxt(x.last_activity)+
   td('mono dim',fmtBytes(x.buffered_bytes))+
   tdT('mono dim cell-preview',x.preview_tail||'','<span class="trunc">'+esc(trunc(x.preview_tail||'',120))+'</span>')+
   '<td class="cell-actions"><button class="btn btn-sm btn-danger" data-act="terminate" data-id="'+esc(x.session_id)+'" data-pid="'+((x.pid==null)?'':x.pid)+'">Terminate</button></td></tr>';
 }).join('');
 setTableState('jobs-table','jobs-empty',jobs.length===0);
 el('jobs-tbody').innerHTML=jobs.map(function(x){
  var run=x.status==='running';
  var exit=(x.exit_code==null)?'':String(x.exit_code);
  return '<tr>'+
   tdT('mono',x.job_id,esc(shortId(x.job_id,10)))+cmdCell(x.command,42)+
   td('',esc(x.key_name||x.key_id||'—'))+td('',badge(x.status))+
   ttxt(x.started_at)+ttxt(x.finished_at)+
   td('mono dim',(exit==='')?'—':exit)+
   '<td class="cell-actions">'+
   (run?'<button class="btn btn-sm btn-danger" data-act="cancel" data-id="'+esc(x.job_id)+'" data-pid="'+((x.pid==null)?'':x.pid)+'">Cancel</button> ':'')+
   '<button class="btn btn-sm" data-act="job-output" data-id="'+esc(x.job_id)+'" data-cmd="'+esc(x.command||'')+'" data-status="'+esc(x.status||'')+'" data-exit="'+exit+'">Output</button>'+
   '</td></tr>';
 }).join('');
 setTableState('hist-table','hist-empty',hist.length===0);
 el('hist-tbody').innerHTML=hist.map(function(x){
  return '<tr>'+
   tdT('mono',x.session_id,esc(shortId(x.session_id,10)))+
   td('',esc(x.key_name||x.key_id||'—'))+cmdCell(x.command,42)+
   td('',badge(x.status))+
   ttxt(x.created_at)+ttxt(x.closed_at)+
   '</tr>';
 }).join('');
}
async function loadSessions(silent){
 try{
  var both=await Promise.all([api('GET','/api/sessions'),api('GET','/api/jobs?limit=50')]);
  var sig=JSON.stringify(both);
  if(sig!==sessSig){sessSig=sig;renderSessions(both[0],both[1]);}
  el('sess-empty').classList.add('hidden');setConn(true);
 }catch(e){loadFail(e,silent,null,sessSig?null:'sess-empty','sessions');}
}
function onLiveClick(ev){
 var btn=ev.target.closest('button[data-act="terminate"]');
 if(!btn||btn.disabled)return;
 var id=btn.dataset.id,pid=btn.dataset.pid;
 confirmRun({title:'Terminate session',body:'Terminate PTY session '+shortId(id,10)+(pid?' (PID '+pid+')':'')+'? The shell process and its children will be killed.',confirmText:'Terminate',danger:true},btn,async function(){
  try{await api('POST','/api/sessions/'+encodeURIComponent(id)+'/terminate');toast('Session '+shortId(id,10)+' terminated.','ok');sessSig='';loadTab('sessions',true);}
  catch(e){toast('Terminate failed: '+e.message,'error');}
 });
}
function onJobsClick(ev){
 var btn=ev.target.closest('button[data-act]');
 if(!btn||btn.disabled)return;
 var id=btn.dataset.id;
 if(btn.dataset.act==='cancel'){
  var pid=btn.dataset.pid;
  confirmRun({title:'Cancel job',body:'Cancel background job '+shortId(id,10)+(pid?' (PID '+pid+')':'')+'? The process will be terminated.',confirmText:'Cancel job',danger:true},btn,async function(){
   try{await api('POST','/api/jobs/'+encodeURIComponent(id)+'/cancel');toast('Job '+shortId(id,10)+' cancelled.','ok');sessSig='';loadTab('sessions',true);}
   catch(e){toast('Cancel failed: '+e.message,'error');}
  });
 }else{
  var exitRaw=btn.dataset.exit;
  openJobOutput({job_id:id,command:btn.dataset.cmd,status:btn.dataset.status,exit_code:(exitRaw==='')?null:Number(exitRaw)});
 }
}

var logsCache=[],logsFilter='',expandedLogs={};
async function loadLogs(silent){
 try{
  var d=await api('GET','/api/logs?limit=100&offset=0');
  logsCache=(d&&d.logs)||[];renderLogs();setConn(true);
 }catch(e){loadFail(e,silent,logsCache.length?null:'logs-table',logsCache.length?null:'logs-empty','audit log');}
}
function renderLogs(){
 var q=logsFilter.trim().toLowerCase();
 var rows=logsCache.filter(function(l){
  if(!q)return true;
  var m=l.meta||{};
  return [l.tool,l.key_name,l.key_id,m.command].join(' ').toLowerCase().indexOf(q)!==-1;
 });
 if(!rows.length){
  el('logs-tbody').innerHTML='';
  setTableState('logs-table','logs-empty',true,logsCache.length?'No entries match the current filter.':null,false);
  return;
 }
 setTableState('logs-table','logs-empty',false);
 el('logs-tbody').innerHTML=rows.map(function(l){
  var m=l.meta||{},open=!!expandedLogs[String(l.id)];
  return '<tr class="log-row'+(open?' open':'')+'" data-id="'+l.id+'">'+
   ttxt(l.ts)+
   td('',esc(l.key_name||l.key_id||'—'))+
   td('mono dim',esc(l.tool||'—'))+
   td('',badge(l.status))+
   td('mono dim',(l.exit_code==null)?'—':l.exit_code)+
   td('mono dim',fmtDur(l.duration_ms))+
   cmdCell(m.command,64)+
   td('dim',open?'▾':'▸')+
   '</tr>'+(open?logDetail(l):'');
 }).join('');
}
function logDetail(l){
 var mj;
 try{mj=JSON.stringify(l.meta||{},null,2);}catch(e){mj=String(l.meta);}
 return '<tr class="detail-row"><td colspan="8">'+
  '<div class="detail-grid">'+
  '<div><div class="detail-title">Metadata</div><pre class="detail-pre">'+esc(mj)+'</pre></div>'+
  '<div><div class="detail-title">Output preview</div><pre class="detail-pre">'+esc(l.output_preview||'(no output captured)')+'</pre></div>'+
  '</div></td></tr>';
}
function onLogRowClick(ev){
 if(ev.target.closest('button,select,a,input,pre'))return;
 var tr=ev.target.closest('tr.log-row');
 if(!tr)return;
 var id=tr.dataset.id;
 if(expandedLogs[id]){delete expandedLogs[id];}else{expandedLogs[id]=true;}
 renderLogs();
}

function dd(k,v,m){return '<dt>'+esc(k)+'</dt><dd'+(m?' class="mono"':'')+'>'+((v==null||v==='')?'<span class="dim">—</span>':esc(v))+'</dd>';}
function tile(l,n){return '<div class="stat"><div class="num">'+((n==null)?'—':n)+'</div><div class="lbl">'+esc(l)+'</div></div>';}
function renderServer(d){
 var mcp=d.mcp||{},dash=d.dashboard||{},tls=d.tls||{},sum=d.summary||{};
 el('srv-service').innerHTML=dd('Version',d.version)+dd('Uptime',fmtUptime(d.uptime_seconds))+dd('Hostname',d.hostname)+dd('Exec user',d.service_user)+dd('Python',d.python_version)+dd('Platform',d.platform)+dd('Config path',d.config_path,1)+dd('Data dir',d.data_dir,1);
 el('srv-mcp').innerHTML=dd('URL',mcp.url,1)+dd('Bind',mcp.bind)+dd('Port',mcp.port)+dd('TLS mode',tls.mode)+dd('Cert subject',tls.subject)+dd('Cert issuer',tls.issuer)+dd('Cert expires',tls.expires)+dd('Cert path',tls.cert_path,1);
 el('srv-dash').innerHTML=dd('Bind',dash.bind)+dd('Port',dash.port)+dd('Authentication','none (network-restricted)');
 el('srv-summary').innerHTML=tile('Active keys',sum.keys_active)+tile('Revoked keys',sum.keys_revoked)+tile('Running jobs',sum.jobs_running)+tile('Open sessions',sum.sessions_open)+tile('Log entries',sum.log_entries);
 el('srv-bind-note').textContent='This dashboard is bound to a private interface ('+(dash.bind||'?')+':'+(dash.port||'?')+') and has no application login — keep it restricted to your LAN or loopback (PRD FR-015).';
}
async function loadServer(silent){
 try{
  var d=await api('GET','/api/server');
  el('srv-empty').classList.add('hidden');
  renderServer(d);setConn(true);
 }catch(e){loadFail(e,silent,null,'srv-empty','server info');}
}

var jobOut=null;
function openJobOutput(job){
 jobOut={id:job.job_id,stream:'stdout',offset:0,available:0,status:job.status||'',exit:job.exit_code};
 el('jobout-title').textContent='Job '+shortId(job.job_id,16);
 el('jobout-stream').value='stdout';
 el('jobout-pre').textContent='';
 el('jobout-more').disabled=false;el('jobout-more').textContent='Load more';
 el('jobout-meta').textContent='loading…';
 openModal('modal-job-output');
 fetchJobOutput(true);
}
async function fetchJobOutput(reset){
 if(!jobOut)return;
 if(reset){jobOut.offset=0;el('jobout-pre').textContent='';}
 el('jobout-more').disabled=true;
 try{
  var d=await api('GET','/api/jobs/'+encodeURIComponent(jobOut.id)+'/output?stream='+encodeURIComponent(jobOut.stream)+'&offset='+jobOut.offset+'&limit=65536');
  if(jobOut&&d&&d.job_id===jobOut.id&&d.stream===jobOut.stream){
   var pre=el('jobout-pre');
   pre.textContent+=(d.data||'');
   jobOut.offset=(d.offset!=null)?d.offset:jobOut.offset+(d.data||'').length;
   jobOut.available=(d.available!=null)?d.available:jobOut.offset;
   if(d.status)jobOut.status=d.status;
   if(d.exit_code!=null)jobOut.exit=d.exit_code;
   pre.scrollTop=pre.scrollHeight;
  }
 }catch(e){toast('Failed to load job output: '+e.message,'error');}
 jobOutMeta();
}
function jobOutMeta(){
 if(!jobOut)return;
 var run=jobOut.status==='running',done=!run&&jobOut.offset>=jobOut.available;
 el('jobout-meta').innerHTML=badge(jobOut.status)+' exit <span class="mono">'+((jobOut.exit==null)?'—':jobOut.exit)+'</span> · loaded <span class="mono">'+fmtBytes(jobOut.offset)+' / '+fmtBytes(jobOut.available)+'</span>'+(done?' · end of stream':(run?' · still running':''));
 var b=el('jobout-more');b.textContent=done?'All output loaded':'Load more';b.disabled=done;
}
function closeJobOutput(){jobOut=null;closeModal('modal-job-output');}

function showSecret(secret,subtitle){
 el('secret-sub').textContent=subtitle||'';
 el('secret-value').textContent=secret;
 el('secret-copy').disabled=false;el('secret-copy').textContent='Copy';
 openModal('modal-secret');
}

async function withBusy(b,fn){
 if(b)b.disabled=true;
 try{return await fn();}
 finally{if(b)b.disabled=false;}
}

var LOADERS={keys:loadKeys,permissions:loadPermissions,sessions:loadSessions,logs:loadLogs,server:loadServer};
function on(id,ev,fn){el(id).addEventListener(ev,fn);}
function boot(){
 toastsEl=el('toasts');
 $all('.tab-btn').forEach(function(b){b.addEventListener('click',function(){setTab(b.dataset.tab);});});
 on('refresh-ind','click',function(){loadOverview();if(activeTab)loadTab(activeTab,false);});
 on('confirm-ok','click',function(){settleConfirm(true);});
 on('confirm-cancel','click',function(){settleConfirm(false);});
 on('modal-confirm','mousedown',function(ev){if(ev.target===this)settleConfirm(false);});
 on('secret-copy','click',function(){
  var btn=this;
  copyText(el('secret-value').textContent).then(function(ok){
   if(ok){btn.textContent='Copied ✓';setTimeout(function(){btn.textContent='Copy';},2000);}
   else{toast('Copy failed — select the secret text and copy it manually (Ctrl+C).','error');}
  });
 });
 on('secret-saved','click',function(){
  closeModal('modal-secret');
  toast('Secret acknowledged — it will not be shown again.','info');
  if(activeTab==='keys'){keysSig='';loadTab('keys',true);}
 });
 on('btn-new-key','click',function(){
  var f=el('key-gen-form');f.classList.toggle('hidden');
  if(!f.classList.contains('hidden'))el('key-name').focus();
 });
 on('key-gen-cancel','click',function(){el('key-gen-form').classList.add('hidden');el('key-gen-err').textContent='';});
 on('key-gen-submit','click',onKeyGenSubmit);
 on('key-name','keydown',function(ev){if(ev.key==='Enter')onKeyGenSubmit();});
 on('keys-tbody','click',onKeysClick);
 on('restricted-paths','input',function(){pathsDirty=true;el('paths-status').textContent='unsaved changes';});
 on('btn-save-paths','click',onSavePaths);
 on('live-tbody','click',onLiveClick);
 on('jobs-tbody','click',onJobsClick);
 on('logs-tbody','click',onLogRowClick);
 var ft=null;
 on('log-filter','input',function(){
  var v=this.value;clearTimeout(ft);
  ft=setTimeout(function(){logsFilter=v;renderLogs();},120);
 });
 on('jobout-close','click',closeJobOutput);
 on('modal-job-output','mousedown',function(ev){if(ev.target===this)closeJobOutput();});
 on('jobout-stream','change',function(ev){if(!jobOut)return;jobOut.stream=ev.target.value;fetchJobOutput(true);});
 on('jobout-more','click',function(){fetchJobOutput(false);});
 // Esc cancels confirm / closes job output — but never the one-time secret
 document.addEventListener('keydown',function(ev){
  if(ev.key!=='Escape')return;
  if(el('modal-secret').classList.contains('open'))return;
  if(confirmResolve){settleConfirm(false);return;}
  if(jobOut)closeJobOutput();
 });
 window.addEventListener('hashchange',function(){
  var h=(window.location.hash||'').slice(1);
  if(TABS.indexOf(h)!==-1)setTab(h);
 });
 document.addEventListener('visibilitychange',function(){if(!document.hidden)periodicTick();});
 setTab((window.location.hash||'').slice(1));
 loadOverview();
 window.setInterval(periodicTick,5000);
}
boot();
})();
</script>
</body>
</html>
"""
