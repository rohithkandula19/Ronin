"""The mobile web UI served by the relay at "/".

This is a single self-contained, phone-friendly HTML page. It has NO external
resources: no CDN scripts, no web fonts, no remote images, no analytics. The
markup, the CSS, and the vanilla JavaScript are all inline, so the page works on
a phone browser with one request and leaks nothing to a third party.

The page asks for the shared token once, stores it only in the browser (local
StorageState in this tab/device), and posts each task to POST /api/task with the
token as a bearer header. It does not embed the token in the served HTML, so the
relay can serve this page to anyone, and only a holder of the token can actually
reach the laptop.

PAGE_HTML is a module-level string so tests can assert it loads with no external
resources and that it posts to /api/task. The relay serves it as text/html.
"""

from __future__ import annotations

# Kept as a plain ASCII string. No smart quotes, no em dashes. Every asset is
# inline; the only network call the page makes is to the relay's own /api/task.
PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<meta name="robots" content="noindex, nofollow">
<title>Ronin Relay</title>
<style>
  :root {
    --bg: #0e1116;
    --panel: #171c24;
    --panel-2: #1f2630;
    --line: #2a323d;
    --text: #e6edf3;
    --muted: #95a1b0;
    --accent: #3b82f6;
    --accent-press: #2563eb;
    --ok: #2ea043;
    --err: #f85149;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.4;
    padding: env(safe-area-inset-top) 14px env(safe-area-inset-bottom) 14px;
  }
  .wrap { max-width: 560px; margin: 0 auto; padding-top: 18px; padding-bottom: 40px; }
  h1 { font-size: 20px; margin: 0 0 2px 0; }
  .sub { color: var(--muted); font-size: 13px; margin: 0 0 18px 0; }
  label { display: block; font-size: 13px; color: var(--muted); margin: 14px 0 6px 0; }
  input, textarea, select, button {
    font-family: inherit;
    font-size: 16px;
    width: 100%;
    border-radius: 12px;
    border: 1px solid var(--line);
    background: var(--panel);
    color: var(--text);
    padding: 14px;
  }
  textarea { min-height: 110px; resize: vertical; }
  .row { display: flex; gap: 10px; }
  .row > * { flex: 1; }
  button {
    border: none;
    background: var(--accent);
    color: #fff;
    font-weight: 600;
    min-height: 54px;
    margin-top: 16px;
    cursor: pointer;
  }
  button:active { background: var(--accent-press); }
  button.secondary { background: var(--panel-2); color: var(--text); border: 1px solid var(--line); }
  button:disabled { opacity: 0.55; cursor: default; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 16px; margin-bottom: 14px; }
  .saved { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .saved .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--ok); flex: none; }
  .saved .dot.off { background: var(--err); }
  .saved .lbl { font-size: 13px; color: var(--muted); }
  .saved button { width: auto; min-height: 0; margin: 0; padding: 8px 12px; font-size: 13px; }
  pre {
    background: #0b0e12;
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 14px;
    margin: 6px 0 0 0;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 13px;
    color: var(--text);
  }
  .status { font-size: 13px; margin-top: 8px; min-height: 18px; }
  .status.ok { color: var(--ok); }
  .status.err { color: var(--err); }
  .hint { color: var(--muted); font-size: 12px; margin-top: 6px; }
  .hidden { display: none !important; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Ronin Relay</h1>
  <p class="sub">Send a task to your laptop's Ronin gateway.</p>

  <div class="card" id="tokenCard">
    <label for="token">Access token</label>
    <input id="token" type="password" inputmode="text" autocomplete="off"
           autocapitalize="off" autocorrect="off" spellcheck="false"
           placeholder="paste your RONIN_RELAY_TOKEN">
    <div class="hint">Stored only on this device. Sent only to this relay over HTTPS.</div>
    <button id="saveToken">Save token</button>
  </div>

  <div class="card saved hidden" id="savedCard">
    <span class="dot" id="dot"></span>
    <span class="lbl" id="savedLbl">Token saved on this device.</span>
    <button class="secondary" id="forgetToken">Forget</button>
  </div>

  <div class="card" id="taskCard">
    <div class="row">
      <div style="flex: 0 0 38%;">
        <label for="method">Method</label>
        <select id="method">
          <option value="POST" selected>POST</option>
          <option value="GET">GET</option>
          <option value="PUT">PUT</option>
          <option value="DELETE">DELETE</option>
        </select>
      </div>
      <div>
        <label for="path">Path</label>
        <input id="path" type="text" inputmode="text" autocapitalize="off"
               autocorrect="off" spellcheck="false" value="/run">
      </div>
    </div>

    <label for="task">Task (JSON body)</label>
    <textarea id="task" autocapitalize="off" autocorrect="off" spellcheck="false">{
  "task": "status"
}</textarea>
    <div class="hint">Sent as the JSON body to the gateway path above.</div>

    <button id="send">Send</button>
    <div class="status" id="status"></div>
  </div>

  <div class="card hidden" id="replyCard">
    <label style="margin-top:0;">Reply</label>
    <pre id="reply"></pre>
  </div>
</div>

<script>
(function () {
  "use strict";
  var KEY = "ronin_relay_token";

  function $(id) { return document.getElementById(id); }

  function getToken() {
    try { return window.localStorage.getItem(KEY) || ""; }
    catch (e) { return window.__roninToken || ""; }
  }
  function setToken(t) {
    try { window.localStorage.setItem(KEY, t); }
    catch (e) { window.__roninToken = t; }
  }
  function clearToken() {
    try { window.localStorage.removeItem(KEY); }
    catch (e) { window.__roninToken = ""; }
  }

  function showSaved(has) {
    if (has) {
      $("tokenCard").classList.add("hidden");
      $("savedCard").classList.remove("hidden");
      $("dot").classList.remove("off");
    } else {
      $("tokenCard").classList.remove("hidden");
      $("savedCard").classList.add("hidden");
    }
  }

  function setStatus(msg, kind) {
    var el = $("status");
    el.textContent = msg || "";
    el.className = "status" + (kind ? " " + kind : "");
  }

  function init() {
    var existing = getToken();
    if (existing) { $("token").value = ""; showSaved(true); }
    else { showSaved(false); }

    $("saveToken").addEventListener("click", function () {
      var t = $("token").value.trim();
      if (!t) { setStatus("Enter a token first.", "err"); return; }
      setToken(t);
      $("token").value = "";
      showSaved(true);
      setStatus("Token saved.", "ok");
    });

    $("forgetToken").addEventListener("click", function () {
      clearToken();
      showSaved(false);
      setStatus("");
    });

    $("send").addEventListener("click", send);
  }

  function send() {
    var token = getToken();
    if (!token) { showSaved(false); setStatus("Save your token first.", "err"); return; }

    var bodyText = $("task").value.trim();
    var body = null;
    if (bodyText.length) {
      try { body = JSON.parse(bodyText); }
      catch (e) { setStatus("Task body is not valid JSON.", "err"); return; }
    }

    var payload = {
      method: $("method").value,
      path: $("path").value.trim() || "/",
      body: body
    };

    var btn = $("send");
    btn.disabled = true;
    setStatus("Sending...", "");
    $("replyCard").classList.add("hidden");

    // Relative URL: always posts to THIS relay's /api/task. Same origin, so the
    // page never reaches any third party. The token rides as a bearer header.
    fetch("/api/task", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token
      },
      body: JSON.stringify(payload)
    }).then(function (resp) {
      return resp.text().then(function (text) {
        var data;
        try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
        return { status: resp.status, data: data };
      });
    }).then(function (r) {
      $("reply").textContent = JSON.stringify(r.data, null, 2);
      $("replyCard").classList.remove("hidden");
      if (r.status === 200) {
        setStatus("Delivered (HTTP " + r.status + ").", "ok");
      } else if (r.status === 401) {
        setStatus("Unauthorized. Check your token.", "err");
      } else if (r.status === 503) {
        setStatus("Laptop offline. The connector is not attached.", "err");
      } else if (r.status === 429) {
        setStatus("Rate limited. Slow down and retry.", "err");
      } else {
        setStatus("Relay returned HTTP " + r.status + ".", "err");
      }
    }).catch(function (e) {
      setStatus("Network error: " + e.message, "err");
    }).then(function () {
      btn.disabled = false;
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
</script>
</body>
</html>
"""
