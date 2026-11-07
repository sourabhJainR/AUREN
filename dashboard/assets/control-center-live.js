/* Production tracking layer for the AUREN control center.
 * Read-only projection over canonical runtime endpoints; never executes work.
 */
(() => {
  "use strict";
  const POLL_MS = 15000;
  const REQUEST_TIMEOUT_MS = 4000;
  const root = document.querySelector("main.content");
  if (!root || document.getElementById("auren-live-ops")) return;

  const panel = document.createElement("section");
  panel.id = "auren-live-ops";
  panel.className = "panel";
  panel.setAttribute("aria-label", "Live operations and delivery tracking");
  panel.innerHTML = `
    <style>
      #auren-live-ops .ops-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
      #auren-live-ops .ops-tile{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}
      #auren-live-ops .ops-value{display:block;font-size:22px;font-weight:700;margin:5px 0}
      #auren-live-ops .ops-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
      #auren-live-ops .ops-state{font-size:12px;color:var(--muted)}
      #auren-live-ops .ops-state[data-state="healthy"]{color:var(--green)}
      #auren-live-ops .ops-state[data-state="degraded"],#auren-live-ops .ops-state[data-state="unknown"]{color:var(--yellow)}
      #auren-live-ops .ops-state[data-state="failed"]{color:var(--red)}
      #auren-live-ops .ops-foot{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-top:12px}
      #auren-live-ops .ops-lower{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:16px}
      #auren-live-ops .ops-subpanel{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}
      #auren-live-ops .ops-subpanel h3{font-size:13px;margin:0 0 8px}
      #auren-live-ops .ops-row{padding:8px 0;border-top:1px solid var(--line);font-size:12px;overflow-wrap:anywhere}
      #auren-live-ops .ops-row strong{display:block;font-size:12px;margin-bottom:3px}
      #auren-live-ops .ops-row span{color:var(--muted)}
      #auren-live-ops .ops-sources{font-size:12px;color:var(--muted);line-height:1.6}
      @media(max-width:900px){#auren-live-ops .ops-lower{grid-template-columns:1fr}}
      @media(max-width:700px){#auren-live-ops .ops-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
    </style>
    <div class="panelhead"><h2>Live operations and delivery</h2><span id="auren-ops-state" class="ops-state" data-state="unknown">Connecting to runtime</span></div>
    <div class="ops-grid">
      <div class="ops-tile"><span class="ops-label">Active sessions</span><strong id="auren-ops-active" class="ops-value">—</strong><span class="ops-state">Durable session inventory</span></div>
      <div class="ops-tile"><span class="ops-label">Blocked / stale</span><strong id="auren-ops-attention" class="ops-value">—</strong><span class="ops-state">Requires inspection</span></div>
      <div class="ops-tile"><span class="ops-label">Tracked tasks</span><strong id="auren-ops-tasks" class="ops-value">—</strong><span class="ops-state">Supervisor progress journal</span></div>
      <div class="ops-tile"><span class="ops-label">Trace outcomes</span><strong id="auren-ops-traces" class="ops-value">—</strong><span class="ops-state">Latest observability snapshot</span></div>
    </div>
    <div class="ops-lower">
      <section class="ops-subpanel"><h3>Supervisor task queue</h3><div id="auren-ops-task-list" class="ops-sources">Waiting for task inventory</div></section>
      <section class="ops-subpanel"><h3>Sessions and plans</h3><div id="auren-ops-session-list" class="ops-sources">Waiting for session inventory</div><div id="auren-ops-plan-list" class="ops-sources"></div></section>
      <section class="ops-subpanel"><h3>Evidence and delivery health</h3><div id="auren-ops-integrity" class="ops-sources">Waiting for evidence telemetry</div><div id="auren-ops-delivery" class="ops-sources">Delivery telemetry: not yet reported</div></section>
    </div>
    <div class="ops-foot"><span id="auren-ops-updated" class="ops-sources">No live sample yet</span><span id="auren-ops-errors" class="ops-sources" role="status">Live tracking is connecting. No sample values are assumed.</span></div>
  `;
  const notice = document.getElementById("connectionnote");
  if (notice?.parentNode) notice.insertAdjacentElement("afterend", panel);
  else root.prepend(panel);

  const el = id => document.getElementById(id);
  const safe = value => String(value ?? "").replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const number = value => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString();
  let timer = null;
  let inFlight = false;
  let lastSuccess = 0;

  async function get(path) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(path, {
        headers: {Accept: "application/json"},
        cache: "no-store",
        credentials: "same-origin",
        signal: controller.signal
      });
      if (!response.ok) throw new Error(path + " returned HTTP " + response.status);
      const payload = await response.json();
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
        throw new Error(path + " returned an invalid response");
      }
      return payload;
    } finally {
      clearTimeout(timeout);
    }
  }

  function statusCounts(counts) {
    const source = counts && typeof counts === "object" ? counts : {};
    const pick = (...keys) => keys.reduce((sum, key) => sum + (Number.isFinite(Number(source[key])) ? Number(source[key]) : 0), 0);
    return {
      active: source.active !== undefined ? Number(source.active) : pick("running", "waiting", "queued", "recovering"),
      attention: pick("blocked", "stale", "unknown", "unverified", "failed"),
      completed: pick("complete", "completed", "verified")
    };
  }

  function renderRows(targetId, rows, titleOf, detailOf) {
    const target = el(targetId);
    target.replaceChildren();
    if (!Array.isArray(rows) || rows.length === 0) {
      const empty = document.createElement("div");
      empty.className = "ops-row";
      empty.textContent = "No records reported";
      target.appendChild(empty);
      return;
    }
    rows.slice(0, 6).forEach(row => {
      const item = document.createElement("div");
      item.className = "ops-row";
      const title = document.createElement("strong");
      title.textContent = titleOf(row) || "Unnamed record";
      const detail = document.createElement("span");
      detail.textContent = detailOf(row) || "Details unavailable";
      item.append(title, detail);
      target.appendChild(item);
    });
  }

  function render(sessions, traces, health) {
    const counts = statusCounts(sessions.counts);
    const taskRows = Array.isArray(sessions.tasks) ? sessions.tasks : [];
    const sessionRows = Array.isArray(sessions.sessions) ? sessions.sessions : [];
    const planRows = Array.isArray(sessions.plan_runs) ? sessions.plan_runs : [];
    const traceTotal = traces.status === "unavailable" ? null : (traces.trace_count ?? traces.total_traces ?? traces.total ?? null);
    const outcomeCounts = traces.status_counts || traces.outcomes;
    const traceOutcomes = outcomeCounts && typeof outcomeCounts === "object"
      ? Object.entries(outcomeCounts).map(([k,v]) => k + ": " + number(v)).join(" · ")
      : "Outcome breakdown unavailable";
    el("auren-ops-active").textContent = number(counts.active);
    el("auren-ops-attention").textContent = number(counts.attention);
    el("auren-ops-tasks").textContent = number(sessions.counts?.tasks ?? taskRows.length);
    el("auren-ops-traces").textContent = number(traceTotal);
    renderRows("auren-ops-task-list", taskRows,
      row => row.task_id || "Task",
      row => [row.state || row.reported_state, row.stage, row.next_action].filter(Boolean).join(" · "));
    renderRows("auren-ops-session-list", sessionRows,
      row => row.session_id || row.task_id || "Session",
      row => [row.status, row.stage, row.seconds_since_update == null ? null : number(row.seconds_since_update) + "s ago"].filter(Boolean).join(" · "));
    renderRows("auren-ops-plan-list", planRows,
      row => "Plan " + (row.run_id || "run"),
      row => [row.status, row.task_counts ? JSON.stringify(row.task_counts) : null, row.evidence_count == null ? null : number(row.evidence_count) + " evidence items"].filter(Boolean).join(" · "));
    const delivery = sessions.delivery_metrics || {};
    const capacity = delivery.capacity || {};
    const shared = delivery.shared_capacity || {};
    const deliveryParts = [];
    if (capacity.active_workers !== undefined) deliveryParts.push("local workers " + number(capacity.active_workers));
    if (shared.active_workers !== undefined) deliveryParts.push("shared workers " + number(shared.active_workers));
    if (delivery.pending_tasks !== undefined) deliveryParts.push("pending " + number(delivery.pending_tasks));
    if (delivery.unknown_outcomes !== undefined) deliveryParts.push("unknown outcomes " + number(delivery.unknown_outcomes));
    if (delivery.acknowledged_chunks !== undefined) deliveryParts.push("chunks acknowledged " + number(delivery.acknowledged_chunks));
    el("auren-ops-delivery").textContent = deliveryParts.length ? deliveryParts.join(" · ") : "Delivery telemetry unavailable; not interpreted as zero.";
    const integrity = delivery.integrity || {};
    const integrityOk = integrity.unknown_outcomes_block_replay === true &&
      integrity.completion_requires_receiver_ack === true &&
      integrity.atomic_protocols_are_not_chunked === true;
    const traceIntegrity = [
      "Trace status " + (traces.status || "unknown"),
      "corrupt lines " + number(traces.corrupt_line_count),
      "sequence gaps " + number(traces.event_sequence_gap_count),
      "drift alerts " + number(Array.isArray(traces.live_drift_alerts) ? traces.live_drift_alerts.length : null),
      "delivery integrity " + (integrityOk ? "verified policy flags" : "unknown")
    ];
    el("auren-ops-integrity").textContent = traceIntegrity.join(" · ");
    const generated = sessions.generated_at || traces.generated_at;
    el("auren-ops-updated").textContent = generated ? "Source timestamp: " + safe(generated) : "Source timestamp unavailable";
    const ok = health.status === "ok";
    const degraded = counts.attention > 0 || traces.status === "partial" || traces.status === "corrupt" ||
      delivery.unknown_outcomes > 0 || !integrityOk;
    const state = el("auren-ops-state");
    state.dataset.state = !ok ? "failed" : degraded ? "degraded" : "healthy";
    state.textContent = !ok ? "Health check failed" : degraded ? "Runtime reachable · attention required" : "Runtime reachable";
    el("auren-ops-errors").textContent = "Trace outcomes: " + (traceOutcomes || "unavailable") +
      ". Counts are source-reported; this panel does not certify CI, review, merge or production readiness.";
    lastSuccess = Date.now();
    const updated = document.getElementById("updated");
    if (updated) updated.textContent = "Live tracking refreshed " + new Date(lastSuccess).toLocaleTimeString();
  }

  async function refresh() {
    if (inFlight || document.visibilityState === "hidden") return;
    inFlight = true;
    const state = el("auren-ops-state");
    try {
      const [sessions, traces, health] = await Promise.all([
        get("/api/sessions?offset=0&limit=50"),
        get("/api/observability"),
        get("/api/health")
      ]);
      render(sessions, traces, health);
    } catch (error) {
      state.dataset.state = lastSuccess ? "degraded" : "failed";
      state.textContent = lastSuccess ? "Refresh failed · last sample retained" : "Live telemetry unavailable";
      el("auren-ops-errors").textContent = safe(error?.name === "AbortError" ? "A runtime endpoint timed out." : (error?.message || "Runtime endpoint unavailable.")) +
        ". No missing value is treated as zero or success.";
      const connection = document.getElementById("connection");
      if (connection) connection.textContent = "Live telemetry unavailable";
    } finally {
      inFlight = false;
    }
  }

  function schedule() {
    if (timer !== null) clearInterval(timer);
    timer = setInterval(refresh, POLL_MS);
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh();
  });
  window.addEventListener("online", refresh);
  window.addEventListener("offline", () => {
    const state = el("auren-ops-state");
    state.dataset.state = "degraded";
    state.textContent = "Browser offline · last sample retained";
  });
  refresh();
  schedule();
})();
