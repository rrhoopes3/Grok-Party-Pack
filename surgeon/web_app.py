"""Minimal web UI for Surgeon (standalone).

This is a first-pass extraction of the old Forge Surgeon tab.
It is deliberately simple. Feel free to make it beautiful later.

Run:
    python surgeon/web_app.py
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request, render_template_string

# Make sure we can import the local package when run directly
import sys
sys.path.insert(0, str(Path(__file__).parent))

from surgeon import check_dependencies, scan_model, operate, list_operations, load_operation

app = Flask(__name__)

INDEX_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Surgeon — OBLITERATUS</title>
<style>
body { font-family: system-ui, sans-serif; background: #111; color: #ddd; margin: 2rem; }
h1 { color: #ff9900; }
pre { background: #1a1a1a; padding: 1rem; overflow: auto; }
button { background: #ff9900; color: black; border: none; padding: 0.5rem 1rem; cursor: pointer; }
input, select { background: #222; color: #ddd; border: 1px solid #444; padding: 0.4rem; }
.card { background: #1a1a1a; padding: 1rem; margin: 1rem 0; border: 1px solid #333; }
</style>
</head>
<body>
<h1>Surgeon <span style="font-size:0.6em; color:#888;">(OBLITERATUS standalone)</span></h1>

<div class="card">
<h3>1. Check dependencies</h3>
<button onclick="check()">Check</button>
<pre id="deps"></pre>
</div>

<div class="card">
<h3>2. Scan a model</h3>
<input id="model" value="meta-llama/Llama-3.1-8B-Instruct" size="50">
<button onclick="scan()">Scan</button>
<pre id="scan"></pre>
</div>

<div class="card">
<h3>3. Operate</h3>
<input id="op-model" value="meta-llama/Llama-3.1-8B-Instruct" size="50">
<select id="method">
<option>advanced</option><option>aggressive</option><option>surgical</option><option>nuclear</option>
</select>
<button onclick="operateModel()">Operate (this will take a while)</button>
<pre id="op"></pre>
</div>

<div class="card">
<h3>Operations</h3>
<button onclick="refreshOps()">Refresh</button>
<pre id="ops"></pre>
</div>

<script>
async function check() {
    const r = await fetch('/api/check');
    document.getElementById('deps').textContent = JSON.stringify(await r.json(), null, 2);
}
async function scan() {
    const model = document.getElementById('model').value;
    const r = await fetch('/api/scan', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model})});
    document.getElementById('scan').textContent = JSON.stringify(await r.json(), null, 2);
}
async function operateModel() {
    const model = document.getElementById('op-model').value;
    const method = document.getElementById('method').value;
    const r = await fetch('/api/operate', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model, method})});
    document.getElementById('op').textContent = JSON.stringify(await r.json(), null, 2);
}
async function refreshOps() {
    const r = await fetch('/api/ops');
    document.getElementById('ops').textContent = JSON.stringify(await r.json(), null, 2);
}
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/api/check")
def api_check():
    return jsonify(check_dependencies())

@app.route("/api/scan", methods=["POST"])
def api_scan():
    data = request.json or {}
    model = data.get("model", "meta-llama/Llama-3.1-8B-Instruct")
    try:
        res = scan_model(model, device="auto")
        return jsonify(res.model_dump())
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/operate", methods=["POST"])
def api_operate():
    data = request.json or {}
    model = data.get("model")
    method = data.get("method", "advanced")
    if not model:
        return jsonify({"error": "model required"}), 400
    try:
        rec = operate(model_name=model, method=method, device="auto")
        return jsonify({"id": rec.id, "output_path": rec.output_path, "status": rec.status.value})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/ops")
def api_ops():
    return jsonify({"operations": list_operations()})

if __name__ == "__main__":
    port = int(os.getenv("SURGEON_PORT", 5001))
    print(f"Surgeon web UI running on http://localhost:{port}")
    print("Set OBLITERATUS_ROOT before starting if you haven't already.")
    app.run(host="0.0.0.0", port=port, debug=True)
