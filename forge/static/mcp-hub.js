/*
 * MCP Hub sidebar — vanilla JS, self-mounting.
 *
 * Auto-initializes inside <aside id="mcp-hub"> on DOMContentLoaded.
 * Pairs with /static/mcp-hub.css — include both and the hub lights up.
 *
 * Host integration contract:
 *   window.refreshMCPHub()        — force a full re-render
 *   window.onMCPEvent(event)      — feed SSE / websocket events
 *                                   Recognized event.type values:
 *                                     "mcp_tool_call"  → { namespace, tool_name }
 *                                     "mcp_store"      → { namespace, key }
 *                                     "mcp_recall"     → { namespace, query }
 *                                     "task_complete"  → triggers a refresh
 */
(function () {
  "use strict";

  const MOUNT_ID = "mcp-hub";
  const POLL_MS = 30000;
  let state = {
    root: null,
    expanded: null,    // namespace currently expanded, or null
    activity: {},      // namespace → { toolCalls, stores, recalls, lastTool }
    lastRefresh: 0,
    namespaces: null,  // last /api/mcp/namespaces payload
    status: null,      // last /api/mcp/status payload
    pollHandle: null,
  };

  // ── Utilities ─────────────────────────────────────────────────────────

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "class") el.className = v;
        else if (k === "dataset") Object.assign(el.dataset, v);
        else if (k.startsWith("on") && typeof v === "function") {
          el.addEventListener(k.slice(2).toLowerCase(), v);
        } else if (v !== null && v !== undefined && v !== false) {
          el.setAttribute(k, v);
        }
      }
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      el.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return el;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function fmtMs(ms) {
    if (ms === null || ms === undefined) return "—";
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  // ── Fetchers ──────────────────────────────────────────────────────────

  async function fetchJSON(url) {
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    if (!r.ok && r.status !== 404) {
      throw new Error(`HTTP ${r.status} for ${url}`);
    }
    return r.json();
  }

  async function refresh() {
    try {
      const [namespaces, status] = await Promise.all([
        fetchJSON("/api/mcp/namespaces"),
        fetchJSON("/api/mcp/status"),
      ]);
      state.namespaces = namespaces;
      state.status = status;
      state.lastRefresh = Date.now();
      render();
    } catch (err) {
      renderError(err);
    }
  }

  async function pingNamespace(ns) {
    const btn = state.root.querySelector(`[data-ping="${ns}"]`);
    if (btn) { btn.disabled = true; btn.textContent = "…"; }
    try {
      const data = await fetchJSON(`/api/mcp/status?namespace=${encodeURIComponent(ns)}&live=true`);
      if (state.status && state.status.servers) {
        state.status.servers[ns] = data.servers[ns];
      }
      render();
    } catch (err) {
      if (btn) { btn.textContent = "FAIL"; }
    }
  }

  // ── Rendering ─────────────────────────────────────────────────────────

  function classifyStatus(serverEntry) {
    if (!serverEntry) return "unknown";
    if (serverEntry.enabled === false) return "disabled";
    if (serverEntry.reachable === true) return "up";
    if (serverEntry.reachable === false) return "down";
    return "unknown";
  }

  function render() {
    if (!state.root) return;
    clear(state.root);
    if (!state.namespaces) {
      state.root.appendChild(h("div", { class: "hub-loading" }, "Polling MCP router…"));
      return;
    }
    if (state.namespaces.enabled === false) {
      state.root.appendChild(h("div", { class: "hub-loading" },
        "MCP disabled. Set FORGE_MCP_ENABLED=true and restart."));
      return;
    }

    state.root.appendChild(renderHeader());
    state.root.appendChild(renderInternalSection());
    state.root.appendChild(renderExternalSection());
    state.root.appendChild(renderFooter());
  }

  function renderError(err) {
    if (!state.root) return;
    clear(state.root);
    state.root.appendChild(renderHeader());
    state.root.appendChild(h("div", { class: "hub-error" },
      `MCP hub fetch failed: ${err && err.message ? err.message : String(err)}`));
    state.root.appendChild(renderFooter());
  }

  function renderHeader() {
    const summary = (state.namespaces && state.namespaces.summary) || "";
    return h("div", { class: "hub-header" },
      h("div", { class: "hub-elbow" }),
      h("div", { class: "hub-title" }, "MCP HUB"),
      h("div", { class: "hub-subtitle" }, summary)
    );
  }

  function renderInternalSection() {
    const internal = state.namespaces.internal || {};
    const total = (internal["forge:vault"]?.entry_count ?? 0)
      + (internal["forge:graph"]?.node_count ?? 0);
    const rows = [];

    const vaultData = internal["forge:vault"];
    if (vaultData) {
      rows.push(renderInternalRow("forge:vault", "vault", vaultData, {
        primary: `entries ${vaultData.entry_count ?? 0}`,
        secondary: renderActivitySummary("forge:vault"),
        bar: `${vaultData.entry_count ?? 0}`,
      }));
      if (state.expanded === "forge:vault") {
        rows.push(renderVaultDetail(vaultData));
      }
    }

    const graphData = internal["forge:graph"];
    if (graphData) {
      rows.push(renderInternalRow("forge:graph", "graph", graphData, {
        primary: `nodes ${graphData.node_count ?? 0} · edges ${graphData.edge_count ?? 0}`,
        secondary: renderActivitySummary("forge:graph"),
        bar: `${graphData.node_count ?? 0}`,
      }));
      if (state.expanded === "forge:graph") {
        rows.push(renderGraphDetail(graphData));
      }
    }

    return h("div", { class: "section internal" },
      h("div", { class: "section-label" },
        h("div", { class: "tab" }, "INT"),
        h("div", { class: "name" }, "INTERNAL"),
        h("div", { class: "count" }, `${total} ITEMS`)
      ),
      ...rows
    );
  }

  function renderInternalRow(ns, cls, _data, meta) {
    const isActive = state.expanded === ns;
    return h("div", {
      class: `ns-row ${cls}${isActive ? " active" : ""}`,
      onclick: () => toggleExpand(ns),
      dataset: { ns },
    },
      h("div", { class: "ns-bar" }, meta.bar),
      h("div", { class: "ns-body" },
        h("div", { class: "ns-name" }, ns),
        h("div", { class: "ns-meta" },
          h("span", null, meta.primary),
          meta.secondary ? h("span", null, meta.secondary) : null
        )
      )
    );
  }

  function renderVaultDetail(data) {
    if (data.error) {
      return h("div", { class: "ns-detail err" }, data.error);
    }
    const topics = data.recent_topics || [];
    return h("div", { class: "ns-detail" },
      "recent topics",
      topics.length === 0
        ? h("div", null, "  (none)")
        : h("ul", null, ...topics.map((t) =>
            h("li", null, `${t.topic} · conf ${t.confidence}`)
          )),
      h("div", { class: "detail-actions" },
        h("button", {
          class: "btn ghost",
          onclick: (e) => { e.stopPropagation(); window.refreshMCPHub(); },
        }, "REFRESH")
      )
    );
  }

  function renderGraphDetail(data) {
    if (data.error) {
      return h("div", { class: "ns-detail err" }, data.error);
    }
    const nodes = data.recent_nodes || [];
    return h("div", { class: "ns-detail" },
      "recent nodes",
      nodes.length === 0
        ? h("div", null, "  (none)")
        : h("ul", null, ...nodes.map((n) =>
            h("li", null, `${n.kind}: ${n.label || n.id}`)
          )),
      h("div", { class: "detail-actions" },
        h("button", {
          class: "btn ghost",
          onclick: (e) => { e.stopPropagation(); window.refreshMCPHub(); },
        }, "REFRESH")
      )
    );
  }

  function renderExternalSection() {
    const external = state.namespaces.external || [];
    const statusServers = (state.status && state.status.servers) || {};
    const activeCount = external.filter((s) => s.enabled).length;
    const rows = [];
    for (const srv of external) {
      const status = statusServers[srv.namespace];
      const cls = srv.enabled ? classifyStatus(status) : "disabled";
      rows.push(renderExternalRow(srv, status, cls));
      if (state.expanded === srv.namespace) {
        rows.push(renderExternalDetail(srv, status));
      }
    }
    return h("div", { class: "section external" },
      h("div", { class: "section-label" },
        h("div", { class: "tab" }, "EXT"),
        h("div", { class: "name" }, "EXTERNAL"),
        h("div", { class: "count" }, `${activeCount}/${external.length} ON`)
      ),
      rows.length === 0
        ? h("div", { class: "hub-loading" }, "No external MCP servers configured.")
        : rows
    );
  }

  function renderExternalRow(srv, status, cls) {
    const isActive = state.expanded === srv.namespace;
    const pingText = status && status.ping_ms !== null && status.ping_ms !== undefined
      ? fmtMs(status.ping_ms)
      : "—";
    const toolCount = status && status.tool_count != null ? status.tool_count : null;
    const activity = renderActivitySummary(srv.namespace);
    return h("div", {
      class: `ns-row ${cls}${isActive ? " active" : ""}`,
      onclick: () => toggleExpand(srv.namespace),
      dataset: { ns: srv.namespace },
    },
      h("div", { class: "ns-bar" }, cls === "up" ? "OK" : cls === "down" ? "ERR" : cls === "disabled" ? "OFF" : "?"),
      h("div", { class: "ns-body" },
        h("div", { class: "ns-name" }, srv.namespace,
          " ",
          h("span", { class: `pill ${cls}` }, cls)
        ),
        h("div", { class: "ns-meta" },
          h("span", null, `ping ${pingText}`),
          toolCount !== null ? h("span", null, `tools ${toolCount}`) : null,
          activity ? h("span", null, activity) : null
        )
      )
    );
  }

  function renderExternalDetail(srv, status) {
    const err = status && status.last_error;
    const cmd = (srv.command || []).join(" ");
    return h("div", { class: "ns-detail" },
      h("div", { class: "cmd" }, `$ ${cmd || "(no command)"}`),
      h("div", null, `timeout: ${srv.timeout}s · auto_start: ${srv.auto_start}`),
      err ? h("div", { class: "err" }, `last error: ${err}`) : null,
      h("div", { class: "detail-actions" },
        h("button", {
          class: "btn",
          dataset: { ping: srv.namespace },
          onclick: (e) => { e.stopPropagation(); pingNamespace(srv.namespace); },
        }, "PING"),
        h("button", {
          class: "btn ghost",
          onclick: (e) => { e.stopPropagation(); listTools(srv.namespace); },
        }, "TOOLS")
      )
    );
  }

  function renderFooter() {
    const stamp = state.lastRefresh
      ? new Date(state.lastRefresh).toTimeString().split(" ")[0]
      : "—";
    return h("div", { class: "hub-footer" },
      h("button", {
        class: "btn",
        onclick: (e) => { e.stopPropagation(); window.refreshMCPHub(); },
      }, "REFRESH"),
      h("span", { class: "stamp" }, `updated ${stamp}`)
    );
  }

  function renderActivitySummary(ns) {
    const a = state.activity[ns];
    if (!a) return null;
    const parts = [];
    if (a.toolCalls) parts.push(`calls ${a.toolCalls}`);
    if (a.stores) parts.push(`stores ${a.stores}`);
    if (a.recalls) parts.push(`recalls ${a.recalls}`);
    return parts.length ? parts.join(" · ") : null;
  }

  // ── Interactions ──────────────────────────────────────────────────────

  function toggleExpand(ns) {
    state.expanded = state.expanded === ns ? null : ns;
    render();
  }

  async function listTools(ns) {
    // Fire and surface result in detail panel via an ephemeral fetch.
    try {
      // Uses the generic mcp_list_tools path through mcp router;
      // there's no dedicated endpoint, so we reuse /api/mcp/status which
      // already knows the tool_count. The PING button triggers this —
      // here we expose a spot for future tool-enumeration UI.
      await pingNamespace(ns);
    } catch (_) { /* noop */ }
  }

  // ── Event ingestion ───────────────────────────────────────────────────

  function bumpActivity(ns, kind, tool) {
    if (!ns) return;
    if (!state.activity[ns]) {
      state.activity[ns] = { toolCalls: 0, stores: 0, recalls: 0, lastTool: null };
    }
    const bucket = state.activity[ns];
    if (kind === "mcp_tool_call") bucket.toolCalls += 1;
    else if (kind === "mcp_store") bucket.stores += 1;
    else if (kind === "mcp_recall") bucket.recalls += 1;
    if (tool) bucket.lastTool = tool;
    render();
  }

  function handleEvent(event) {
    if (!event || !event.type) return;
    switch (event.type) {
      case "mcp_tool_call":
        bumpActivity(event.namespace, "mcp_tool_call", event.tool_name);
        break;
      case "mcp_store":
        bumpActivity(event.namespace, "mcp_store");
        break;
      case "mcp_recall":
        bumpActivity(event.namespace, "mcp_recall");
        break;
      case "task_complete":
        window.refreshMCPHub();
        break;
      default:
        // Ignore events the hub doesn't know about.
        break;
    }
  }

  // ── Bootstrap ─────────────────────────────────────────────────────────

  function mount() {
    state.root = document.getElementById(MOUNT_ID);
    if (!state.root) return;
    window.refreshMCPHub = refresh;
    window.onMCPEvent = handleEvent;
    render();
    refresh();
    if (state.pollHandle) clearInterval(state.pollHandle);
    state.pollHandle = setInterval(refresh, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }

  // Expose for manual tests / console poking
  window.__mcpHub = { refresh, state, handleEvent };
})();
