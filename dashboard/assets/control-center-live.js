/* AUREN operator workbench.
 * State is projected from canonical runtime endpoints. Commands are proxied
 * through the authenticated local MCP service and remain subject to Guard.
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
      #auren-live-ops .ops-form{display:grid;gap:8px}
      #auren-live-ops .ops-form input,#auren-live-ops .ops-form select,#auren-live-ops .ops-form textarea{width:100%;box-sizing:border-box;background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:8px;font:inherit}
      #auren-live-ops .ops-button{border:1px solid var(--line);border-radius:6px;padding:5px 9px;margin:4px 5px 0 0;background:var(--panel);color:var(--text);cursor:pointer;font:inherit;font-size:12px}
      #auren-live-ops .ops-button:disabled{opacity:.5;cursor:not-allowed}
      #auren-live-ops .ops-button.danger{border-color:var(--red)}
      #auren-live-ops .ops-status{min-height:1.4em;margin-top:6px}
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
      <section class="ops-subpanel"><h3>Start a governed task</h3>
        <form id="auren-task-form" class="ops-form">
          <label for="auren-task-input">Engineering goal</label>
          <textarea id="auren-task-input" rows="3" maxlength="12000" required placeholder="Describe the outcome and acceptance criteria"></textarea>
          <label for="auren-task-mode">Execution mode</label>
          <select id="auren-task-mode"><option value="auto">Automatic safe mode</option><option value="proposal">Read-only proposal</option><option value="coding">Guarded coding</option></select>
          <button class="ops-button" type="submit">Submit task</button>
        </form>
        <div id="auren-task-submit-status" class="ops-sources ops-status" role="status"></div>
      </section>
      <section class="ops-subpanel"><h3>Pending approvals</h3><div id="auren-ops-approvals" class="ops-sources">Waiting for approval inventory</div><p class="ops-sources">Approval decisions are sent to the canonical MCP approval store. A decision is never inferred from a UI click alone.</p></section>
      <section class="ops-subpanel"><h3>Decision-required work</h3><div id="auren-ops-decisions" class="ops-sources">Waiting for decision inventory</div></section>
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
  const actionLabel = value => String(value || "").replaceAll("_", " ").replace(/\b\w/g, character => character.toUpperCase());
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

  async function post(path, payload) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json", Accept: "application/json"},
        credentials: "same-origin",
        cache: "no-store",
        body: JSON.stringify(payload),
        signal: controller.signal
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || (path + " returned HTTP " + response.status));
      return data;
    } finally {
      clearTimeout(timeout);
    }
  }

  function operatorReason(action) {
    const reason = window.prompt("Enter a reason for " + action + ".");
    return typeof reason === "string" ? reason.trim() : "";
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

  function renderTaskQueue(rows) {
    const target = el("auren-ops-task-list");
    target.replaceChildren();
    if (!Array.isArray(rows) || rows.length === 0) {
      const empty = document.createElement("div");
      empty.className = "ops-row";
      empty.textContent = "No tasks reported";
      target.appendChild(empty);
      return;
    }
    rows.slice(0, 8).forEach(row => {
      const item = document.createElement("div");
      item.className = "ops-row";
      const title = document.createElement("strong");
      title.textContent = row.task_id || "Task";
      const detail = document.createElement("span");
      detail.textContent = [row.lifecycle?.label || row.state || row.reported_state, row.stage,
        row.next_action || (row.lifecycle?.next_action ? actionLabel(row.lifecycle.next_action) : null)]
        .filter(Boolean).join(" · ");
      item.append(title, detail);
      const state = String(row.state || row.reported_state || row.status || row.trigger_status || row.dispatch_status || "").toLowerCase();
      if (row.task_id && !["completed", "complete", "success", "succeeded", "failed", "cancelled", "canceled", "timed_out", "expired"].includes(state)) {
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "ops-button danger";
        cancel.textContent = "Cancel task";
        cancel.addEventListener("click", async () => {
          const reason = operatorReason("task cancellation");
          if (!reason) return;
          if (!window.confirm("Request cancellation for task " + row.task_id + "?")) return;
          cancel.disabled = true;
          try {
            await post("/api/tasks/" + encodeURIComponent(row.task_id) + "/cancel", {reason});
            el("auren-ops-errors").textContent = "Cancellation request sent to the canonical task controller.";
            await refresh();
          } catch (error) {
            el("auren-ops-errors").textContent = "Cancellation was not confirmed: " + (error.message || "request failed");
            cancel.disabled = false;
          }
        });
        item.appendChild(cancel);
      }
      target.appendChild(item);
    });
  }

  function renderApprovals(payload) {
    const target = el("auren-ops-approvals");
    target.replaceChildren();
    if (!payload || payload.status === "unavailable") {
      const item = document.createElement("div");
      item.className = "ops-row";
      item.textContent = "Approval service unavailable. Start the authenticated local MCP host to inspect requests.";
      target.appendChild(item);
      return;
    }
    const rows = Array.isArray(payload.approvals) ? payload.approvals : [];
    if (!rows.length) {
      const item = document.createElement("div");
      item.className = "ops-row";
      item.textContent = "No pending approval requests";
      target.appendChild(item);
      return;
    }
    rows.slice(0, 8).forEach(row => {
      const item = document.createElement("div");
      item.className = "ops-row";
      const title = document.createElement("strong");
      title.textContent = row.requested_action || "Approval request";
      const detail = document.createElement("span");
      detail.textContent = [
        "Approval " + (row.approval_id || "unidentified"),
        "Task " + (row.task_id || "unreported"),
        "Required role " + (row.required_role || "unreported"),
        "Scope " + (row.scope || "unreported"),
        "Reason " + (row.reason || "not supplied")
      ].join(" · ");
      item.append(title, detail);
      if (row.status === "pending" && row.approval_id) {
        for (const decision of ["approve", "reject"]) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "ops-button" + (decision === "reject" ? " danger" : "");
          button.textContent = decision === "approve" ? "Approve" : "Reject";
          button.addEventListener("click", async () => {
            const reason = operatorReason(decision === "approve" ? "approval" : "rejection");
            if (!reason) return;
            if (!window.confirm("Confirm " + decision + " for " + row.approval_id + "?")) return;
            button.disabled = true;
            try {
              await post("/api/approvals/" + encodeURIComponent(row.approval_id) + "/decision", {decision, reason});
              el("auren-ops-errors").textContent = "Approval decision submitted to the canonical approval store.";
              await refresh();
            } catch (error) {
              el("auren-ops-errors").textContent = "Approval decision was not confirmed: " + (error.message || "request failed");
              button.disabled = false;
            }
          });
          item.appendChild(button);
        }
      }
      target.appendChild(item);
    });
  }

  function render(sessions, traces, health, approvals, taskInventory) {
    const counts = statusCounts(sessions.counts);
    const progressRows = Array.isArray(sessions.tasks) ? sessions.tasks : [];
    const triggerRows = Array.isArray(taskInventory?.trigger_tasks) ? taskInventory.trigger_tasks : [];
    const controllerRows = Array.isArray(taskInventory?.controller_tasks) ? taskInventory.controller_tasks : [];
    const taskRowsById = new Map();
    [...progressRows, ...controllerRows, ...triggerRows].forEach(row => {
      if (row && typeof row.task_id === "string" && row.task_id) {
        taskRowsById.set(row.task_id, {...(taskRowsById.get(row.task_id) || {}), ...row});
      }
    });
    const taskRows = [...taskRowsById.values()];
    const sessionRows = Array.isArray(sessions.sessions) ? sessions.sessions : [];
    const planRows = Array.isArray(sessions.plan_runs) ? sessions.plan_runs : [];
    const traceTotal = traces.status === "unavailable" ? null : (traces.trace_count ?? traces.total_traces ?? traces.total ?? null);
    const outcomeCounts = traces.status_counts || traces.outcomes;
    const traceOutcomes = outcomeCounts && typeof outcomeCounts === "object"
      ? Object.entries(outcomeCounts).map(([k,v]) => k + ": " + number(v)).join(" · ")
      : "Outcome breakdown unavailable";
    el("auren-ops-active").textContent = number(counts.active);
    el("auren-ops-attention").textContent = number(counts.attention);
    el("auren-ops-tasks").textContent = number(taskRows.length);
    el("auren-ops-traces").textContent = number(traceTotal);
    renderTaskQueue(taskRows);
    if (taskInventory?.status === "unavailable") {
      el("auren-ops-task-list").dataset.state = "unavailable";
      el("auren-ops-task-list").prepend(document.createTextNode("Canonical task inventory unavailable; showing progress-journal tasks only. "));
    } else {
      delete el("auren-ops-task-list").dataset.state;
    }
    renderApprovals(approvals);
    const decisionRows = taskRows.filter(row => row.decision_required === true);
    renderRows("auren-ops-decisions", decisionRows,
      row => row.task_id || "Task awaiting decision",
      row => [row.lifecycle?.label || row.state || row.reported_state, row.stage,
        row.next_action || "Review the canonical decision record",
        row.parent_task_id ? "Parent: " + row.parent_task_id : null].filter(Boolean).join(" · "));
    renderRows("auren-ops-session-list", sessionRows,
      row => row.session_id || row.task_id || "Session",
      row => [row.lifecycle?.label || row.status, row.stage, row.lifecycle?.attention_required ? "attention required" : null, row.seconds_since_update == null ? null : number(row.seconds_since_update) + "s ago"].filter(Boolean).join(" · "));
    renderRows("auren-ops-plan-list", planRows,
      row => "Plan " + (row.run_id || "run"),
      row => [row.lifecycle?.label || row.status, row.lifecycle?.next_action ? actionLabel(row.lifecycle.next_action) : null, row.task_counts ? JSON.stringify(row.task_counts) : null, row.evidence_count == null ? null : number(row.evidence_count) + " evidence items"].filter(Boolean).join(" · "));
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
    const runtimeChecks = health.checks && typeof health.checks === "object"
      ? Object.entries(health.checks).map(([name, value]) => name + ": " + String(value)).join(" · ")
      : "Runtime checks unavailable";
    const traceIntegrity = [
      "Trace status " + (traces.status || "unknown"),
      "corrupt lines " + number(traces.corrupt_line_count),
      "sequence gaps " + number(traces.event_sequence_gap_count),
      "drift alerts " + number(Array.isArray(traces.live_drift_alerts) ? traces.live_drift_alerts.length : null),
      "delivery integrity " + (integrityOk ? "verified policy flags" : "unknown"),
      "runtime checks " + runtimeChecks,
      "provider " + (health.provider_health || "not checked"),
      "PostgreSQL " + (health.postgres_health || "not checked"),
      "host identity " + (health.host_identity || "not attested")
    ];
    el("auren-ops-integrity").textContent = traceIntegrity.join(" · ");
    const generated = sessions.generated_at || traces.generated_at;
    el("auren-ops-updated").textContent = generated ? "Source timestamp: " + safe(generated) : "Source timestamp unavailable";
    const ok = health.status === "ok";
    const readinessDegraded = health.readiness !== "ready";
    const degraded = readinessDegraded || counts.attention > 0 || traces.status === "partial" || traces.status === "corrupt" ||
      delivery.unknown_outcomes > 0 || !integrityOk;
    const state = el("auren-ops-state");
    state.dataset.state = !ok ? "failed" : degraded ? "degraded" : "healthy";
    state.textContent = !ok ? "Health check failed" :
      readinessDegraded ? "Runtime reachable · readiness degraded" :
      degraded ? "Runtime reachable · attention required" : "Runtime ready";
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
      const [approvals, taskInventory] = await Promise.all([
        get("/api/approvals?status=pending")
          .catch(error => ({status: "unavailable", reason: error?.name || "request_failed"})),
        get("/api/tasks?limit=50")
          .catch(error => ({status: "unavailable", reason: error?.name || "request_failed"}))
      ]);
      render(sessions, traces, health, approvals, taskInventory);
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

  const taskForm = el("auren-task-form");
  const pendingSubmissionStorageKey = "auren.operator.pendingSubmission.v1";
  let pendingSubmission = null;
  try {
    const stored = window.sessionStorage.getItem(pendingSubmissionStorageKey);
    const parsed = stored ? JSON.parse(stored) : null;
    if (parsed && typeof parsed.task === "string" && typeof parsed.mode === "string" &&
        typeof parsed.idempotencyKey === "string") pendingSubmission = parsed;
  } catch (_) {
    pendingSubmission = null;
  }
  if (pendingSubmission && el("auren-task-submit-status")) {
    el("auren-task-submit-status").textContent =
      "An earlier submission has an unresolved outcome. Retry the same task and mode to reuse its idempotency key.";
    if (el("auren-task-input")) el("auren-task-input").value = pendingSubmission.task;
    if (el("auren-task-mode")) el("auren-task-mode").value = pendingSubmission.mode;
  }
  taskForm?.addEventListener("submit", async event => {
    event.preventDefault();
    const task = el("auren-task-input").value.trim();
    if (!task) return;
    const submit = taskForm.querySelector('button[type="submit"]');
    const status = el("auren-task-submit-status");
    const mode = el("auren-task-mode").value;
    if (pendingSubmission && (pendingSubmission.task !== task || pendingSubmission.mode !== mode)) {
      status.textContent = "A previous submission has an unresolved outcome. Retry the same task and mode before starting another.";
      return;
    }
    if (!pendingSubmission) {
      const idempotencyKey = window.crypto?.randomUUID
        ? window.crypto.randomUUID()
        : "auren-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
      pendingSubmission = {task, mode, idempotencyKey};
      try { window.sessionStorage.setItem(pendingSubmissionStorageKey, JSON.stringify(pendingSubmission)); }
      catch (_) {
        pendingSubmission = null;
        status.textContent = "Browser session storage is unavailable; submission stopped to avoid an unsafe retry.";
        return;
      }
    }
    submit.disabled = true;
    status.textContent = "Submitting through the authenticated local task service…";
    try {
      const acceptance = await post("/api/tasks", {
        task, execution_mode: mode,
        priority: "normal", max_attempts: 3, context: {},
        idempotency_key: pendingSubmission.idempotencyKey
      });
      const acceptedTaskId = acceptance?.task_id || acceptance?.trigger_id || null;
      pendingSubmission = null;
      try { window.sessionStorage.removeItem(pendingSubmissionStorageKey); } catch (_) {}
      status.textContent = "Task submission accepted by the canonical MCP service" +
        (acceptedTaskId ? " (task " + acceptedTaskId + ")." : ".") +
        " The durable trigger inventory will show it when available.";
      el("auren-task-input").value = "";
      await refresh().catch(() => {
        el("auren-ops-errors").textContent = "Task was accepted; the follow-up dashboard refresh failed.";
      });
    } catch (error) {
      status.textContent = "Task submission not confirmed: " + (error.message || "request failed") +
        ". Retry the same task and mode; AUREN will reuse the original idempotency key.";
    } finally {
      submit.disabled = false;
    }
  });

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
