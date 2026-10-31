(() => {
  const byId = id => document.getElementById(id);
  const safe = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[ch]));
  const state = { snapshot: null, busy: false, filter: "all", query: "", offset: 0, limit: 50, refreshAgain: false };
  const time = value => {
    if (value === null || value === undefined || value === "") return "—";
    const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
  };
  const age = seconds => {
    if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "—";
    if (seconds < 60) return Math.round(seconds) + "s ago";
    if (seconds < 3600) return Math.round(seconds / 60) + "m ago";
    if (seconds < 86400) return Math.round(seconds / 3600) + "h ago";
    return Math.round(seconds / 86400) + "d ago";
  };
  const statusClass = value => String(value || "unknown").toLowerCase().replace(/[^a-z0-9_-]/g, "");
  const active = row => ["running", "waiting", "queued", "recovering"].includes(row.status || row.state);
  const matches = row => {
    const haystack = [row.session_id,row.task_id,row.active_provider,row.stage,row.status,row.state]
      .concat(row.completed_batches || [], row.remaining_batches || []).join(" ").toLowerCase();
    if (state.query && !haystack.includes(state.query)) return false;
    const status = row.status || row.state || "unknown";
    if (state.filter === "active") return active(row);
    if (state.filter === "complete") return ["complete","completed","verified","passed"].includes(status);
    if (state.filter === "blocked") return ["blocked","failed","cancelled","canceled"].includes(status);
    if (state.filter === "stale") return status === "stale";
    if (state.filter === "unverified") return ["unverified","unknown"].includes(status);
    return true;
  };
  function metric(label, value, hint) {
    return '<article class="session-metric"><span>' + safe(label) + '</span><strong>' +
      safe(value) + '</strong><small class="muted">' + safe(hint || "") + '</small></article>';
  }
  function renderMetrics(data) {
    const c = data.counts || {};
    byId("session-metrics").innerHTML = [
      metric("Active sessions", c.active ?? "—", "Running or waiting"),
      metric("Completed", c.completed ?? "—", "Checkpoint says complete"),
      metric("Blocked", c.blocked ?? "—", "Needs recovery or review"),
      metric("Stale", c.stale ?? "—", "No recent checkpoint heartbeat"),
      metric("Unverified", c.unverified ?? "—", "State needs inspection"),
      metric("Durable tasks", c.tasks ?? "—", "Supervisor journal records")
    ].join("");
  }
  function sessionCard(row) {
    const status = row.status || "unknown";
    const completed = (row.completed_batches || []).length;
    const remaining = (row.remaining_batches || []).length;
    const percent = Math.max(0, Math.min(100, Number(row.progress) || 0));
    const details = [
      ["Task ID", row.task_id], ["Stage", row.stage], ["Provider", row.active_provider],
      ["Attempt", row.attempt], ["Updated", time(row.updated_at)], ["Last seen", age(row.seconds_since_update)], ["Checkpoint", (row.state_digest || "").slice(0, 14) || "—"]
    ];
    const batchText = (row.completed_batches || []).length || (row.remaining_batches || []).length
      ? '<div class="session-batches"><strong>' + completed + ' completed</strong> · ' + remaining + ' remaining</div>' : "";
    const detailHtml = details.map(([key,value]) =>
      '<div><small>' + safe(key) + '</small><strong>' + safe(value ?? "—") + '</strong></div>'
    ).join("");
    const sessionSearch = safe([row.session_id,row.task_id,row.active_provider,row.stage].join(" ").toLowerCase());
    return '<article class="session-card" data-search="' + sessionSearch + '">' +
      '<div class="session-card-head"><div><div class="session-id">' + safe(row.session_id || "Unnamed session") +
      '</div><div class="session-meta">' + safe(row.task_id || "No task ID") + ' · updated ' + safe(time(row.updated_at)) +
      '</div></div><span class="status ' + statusClass(status) + '">' + safe(status) + '</span></div>' +
      '<div class="session-progress"><span style="width:' + percent + '%"></span></div>' +
      '<div class="session-meta">' + percent + '% stage progress · ' + safe(row.stage || "unknown stage") + '</div>' +
      '<div class="session-detail">' + detailHtml + '</div>' + batchText +
      (row.error_present ? '<div class="notice">A prior error is recorded. Error content is hidden; inspect the durable recovery record.</div>' : "") +
      '<details><summary>Checkpoint details</summary><div class="session-meta">Completed batches: ' +
      safe((row.completed_batches || []).join(", ") || "None recorded") + '</div><div class="session-meta">Remaining batches: ' +
      safe((row.remaining_batches || []).join(", ") || "None recorded") + '</div></details></article>';
  }
  function taskCard(row) {
    const status = row.status_category || row.state || "unknown";
    const progress = row.progress_percent;
    const label = progress === null || progress === undefined ? "Progress not measured" : Number(progress) + "%";
    return '<article class="session-task"><div><strong>' + safe(row.task_id || "Unidentified task") +
      '</strong><div class="muted">' + safe(row.stage || "unknown stage") + '</div></div><div>' +
      '<div class="muted">' + safe(row.next_action || "No recovery action recorded") + '</div><div class="muted">Updated ' +
      safe(time(row.last_update)) + ' · ' + safe(age(row.seconds_since_update)) + '</div></div><div><span class="status ' +
      statusClass(status) + '">' + safe(status) + '</span><div class="muted">' + safe(label) + '</div></div></article>';
  }
  function planCard(row) {
    const counts = row.task_counts || {};
    const tasks = Object.entries(counts).map(([key,value]) => safe(key) + ": " + safe(value)).join(" · ") || "Task counts unavailable";
    return '<article class="session-task"><div><strong>' + safe(row.run_id || "Unidentified plan") +
      '</strong><div class="muted">Revision ' + safe(row.revision ?? "—") + '</div></div><div class="muted">' +
      tasks + '</div><div><span class="status ' + statusClass(row.status) + '">' + safe(row.status || "unknown") +
      '</span><div class="muted">' + safe(row.evidence_count ?? "—") + ' evidence records</div></div></article>';
  }
  function render() {
    const data = state.snapshot;
    if (!data) return;
    renderMetrics(data);
    const sessions = data.sessions || [];
    byId("session-count").textContent = (data.pagination?.total ?? sessions.length) + " matching sessions";
    const page = data.pagination || {offset:0,limit:state.limit,total:(data.sessions || []).length,has_more:false};
    const pageNumber = Math.floor(page.offset / page.limit) + 1;
    const pageCount = Math.max(1, Math.ceil(page.total / page.limit));
    byId("session-page-label").textContent = "Page " + pageNumber + " of " + pageCount + " · " + page.total + " sessions";
    byId("session-page-prev").disabled = page.offset <= 0;
    byId("session-page-next").disabled = !page.has_more;
    byId("session-list").innerHTML = sessions.length ? sessions.map(sessionCard).join("") :
      '<div class="empty session-empty">No sessions match this filter. Missing records are not treated as completed.</div>';
    const tasks = data.tasks || [];
    byId("session-task-count").textContent = tasks.length + " tasks";
    byId("session-task-list").innerHTML = tasks.length ? tasks.map(taskCard).join("") :
      '<div class="empty">No supervisor task journal records are available.</div>';
    const plans = data.plan_runs || [];
    byId("session-plan-list").innerHTML = plans.length ? plans.map(planCard).join("") :
      '<div class="empty">No durable engineering plan runs found.</div>';
    byId("sessions-updated").textContent = "Updated " + new Date(data.generated_at).toLocaleTimeString();
  }
  async function refresh() {
    if (state.busy) { state.refreshAgain = true; return; }
    state.busy = true;
    try {
      const params = new URLSearchParams({offset:String(state.offset),limit:String(state.limit),q:state.query,status:state.filter});
      const response = await fetch("/api/sessions?" + params.toString(), {cache:"no-store"});
      if (!response.ok) throw new Error("HTTP " + response.status);
      state.snapshot = await response.json();
      state.offset = state.snapshot.pagination?.offset ?? state.offset;
      render();
    } catch (error) {
      byId("sessions-updated").textContent = "Session data unavailable";
      byId("session-list").innerHTML = '<div class="empty session-empty">The durable session service could not be read. Existing data is not cleared or marked complete.</div>';
    } finally {
      state.busy = false;
      if (state.refreshAgain) {
        state.refreshAgain = false;
        refresh();
      }
    }
  }
  let searchTimer = null;
  byId("session-search").addEventListener("input", event => {
    state.query = event.target.value.trim();
    state.offset = 0;
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(refresh, 250);
  });
  byId("session-filter").addEventListener("change", event => {
    state.filter = event.target.value;
    state.offset = 0;
    refresh();
  });
  byId("session-refresh").addEventListener("click", refresh);
  byId("session-page-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); refresh(); });
  byId("session-page-next").addEventListener("click", () => { state.offset += state.limit; refresh(); });
  refresh();
  window.setInterval(refresh, 4000);
})();
