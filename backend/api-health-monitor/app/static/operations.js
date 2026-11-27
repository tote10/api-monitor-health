"use strict";

window.monitorOperations = (() => {
  const root = "/api/v1/endpoints";
  const get = (id) => document.getElementById(id);
  let selectedIncident;
  let selectedMonitor;
  let incidentRequest = 0;
  let listRequest = 0;
  let maintenanceRequest = 0;

  async function request(path, method = "GET", body) {
    const response = await fetch(root + path, {
      method,
      headers: body === undefined ? {} : {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    if (response.status === 401) window.location.replace("/login");
    const data = await response.json();
    if (!response.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === "string" ? detail :
        detail?.map((item) => item.msg).join("; ") || "Request failed.");
    }
    return data;
  }

  function node(tag, text) {
    const element = document.createElement(tag);
    element.textContent = text;
    return element;
  }

  function date(value) {
    return new Date(value).toLocaleString();
  }

  async function refreshIncidents() {
    const version = ++listRequest;
    const list = get("operations-list");
    const summary = get("operations-summary");
    try {
      const incidents = await request("/operations/incidents?incident_state=" + get("incident-filter").value);
      if (version !== listRequest) return;
      list.replaceChildren(...incidents.map((incident) => {
        const row = node("li", "");
        const text = node("div", "");
        text.append(node("strong", incident.endpoint_name),
          node("small", (incident.recovered_at ? "Resolved" : incident.acknowledged_at ? "Acknowledged" : "Open") +
            " · " + date(incident.opened_at)));
        const button = node("button", "View incident");
        button.type = "button";
        button.className = "action-button";
        button.addEventListener("click", () => openIncident(incident.endpoint_id, incident.id));
        row.append(text, button);
        return row;
      }));
      summary.textContent = incidents.length ? "Showing " + incidents.length : "No incidents match this filter.";
    } catch (error) {
      if (version === listRequest) summary.textContent = error.message;
    }
  }

  function renderIncident(incident) {
    const content = get("incident-detail");
    content.replaceChildren(
      node("p", incident.first_error || "No failure reason recorded."),
      node("p", "First failure · " + date(incident.first_failed_at)),
      node("p", "Incident opened · " + date(incident.opened_at)),
      node("p", incident.acknowledged_at ? "Acknowledged · " + date(incident.acknowledged_at) : "Awaiting acknowledgement"),
      node("p", incident.recovered_at ? "Recovered · " + date(incident.recovered_at) : "Incident is open")
    );
    for (const note of incident.notes) {
      content.append(node("p", note.author + " · " + date(note.at) + " — " + note.text));
    }
    const writable = ["admin", "member"].includes(document.body.dataset.role);
    get("incident-actions").hidden = !writable || Boolean(incident.acknowledged_at || incident.recovered_at);
    get("incident-note-form").hidden = !writable;
    const report = get("incident-report-form");
    report.elements.root_cause.value = incident.root_cause || "";
    report.elements.follow_up.value = incident.follow_up || "";
    report.elements.public_update.value = incident.public_update || "";
    for (const field of report.elements) field.disabled = !writable;
    get("report-json").href = root + selectedIncident + "/report";
    get("incident-report").hidden = false;
  }

  async function openIncident(endpointId, incidentId) {
    const dialog = get("incident-dialog");
    const version = ++incidentRequest;
    selectedIncident = "/" + endpointId + "/incident-records/" + incidentId;
    get("incident-detail").textContent = "Loading incident…";
    get("incident-deployments").hidden = true;
    get("incident-related").hidden = true;
    get("incident-action-status").textContent = "";
    get("incident-actions").hidden = true;
    get("incident-note-form").hidden = true;
    get("incident-report").hidden = true;
    get("incident-note-form").reset();
    if (!dialog.open) dialog.showModal();
    try {
      const incident = await request(selectedIncident);
      if (version !== incidentRequest) return;
      renderIncident(incident);
      const [markers, related] = await Promise.all([
        request(selectedIncident + "/deployments"), request(selectedIncident + "/related")
      ]);
      if (version !== incidentRequest) return;
      if (related.length) {
        const group = get("incident-related");
        group.replaceChildren(node("strong", "Related incidents · " + related[0].service_group),
          node("p", "Same service group with start times within 30 minutes."), ...related.map((incident) => {
            const button = node("button", incident.endpoint_name + " · " + date(incident.opened_at));
            button.type = "button";
            button.className = "action-button";
            button.addEventListener("click", () => openIncident(incident.endpoint_id, incident.incident_id));
            return button;
          }));
        group.hidden = false;
      }
      if (!markers.length) return;
      const panel = get("incident-deployments");
      panel.replaceChildren(node("strong", "Deployments near this incident"),
        node("p", "Deployments within one hour before the first failure or during the incident."),
        ...markers.map((marker) => node("p", date(marker.deployed_at) + " · " + marker.label +
          (marker.revision ? " (" + marker.revision + ")" : ""))));
      panel.hidden = false;
    } catch (error) {
      if (version === incidentRequest) get("incident-action-status").textContent = error.message;
    }
  }

  async function incidentAction(action, button, body, method = "POST") {
    const version = incidentRequest;
    button.disabled = true;
    get("incident-action-status").textContent = "";
    try {
      const incident = await request(selectedIncident + "/" + action, method, body);
      if (version === incidentRequest) {
        renderIncident(incident);
        get("incident-note-form").reset();
      }
      refreshIncidents();
    } catch (error) {
      if (version === incidentRequest) get("incident-action-status").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  async function loadMaintenance(endpointId) {
    const version = ++maintenanceRequest;
    const list = get("maintenance-list");
    const status = get("maintenance-status");
    status.textContent = "Loading maintenance…";
    list.replaceChildren();
    try {
      const windows = await request("/" + endpointId + "/maintenance");
      if (version !== maintenanceRequest) return;
      status.textContent = windows.length ? "" : "No maintenance windows.";
      list.replaceChildren(...windows.map((window) => {
        const row = node("li", "");
        const body = node("div", "");
        const start = Date.parse(window.starts_at);
        const end = Date.parse(window.ends_at);
        const state = window.canceled_at ? "Canceled" : Date.now() < start ? "Scheduled" : Date.now() < end ? "Active" : "Ended";
        body.append(node("strong", window.title),
          node("small", state + " · " + date(window.starts_at) + " to " + date(window.ends_at)));
        row.append(body);
        if (["admin", "member"].includes(document.body.dataset.role) && !window.canceled_at && Date.now() < end) {
          const cancel = node("button", "Cancel window");
          cancel.type = "button";
          cancel.className = "action-button";
          cancel.addEventListener("click", async () => {
            cancel.disabled = true;
            try {
              await request("/" + endpointId + "/maintenance/" + window.id + "/cancel", "POST");
              if (endpointId === selectedMonitor) loadMaintenance(endpointId);
            } catch (error) {
              status.textContent = error.message;
              cancel.disabled = false;
            }
          });
          row.append(cancel);
        }
        return row;
      }));
    } catch (error) {
      if (version === maintenanceRequest) status.textContent = error.message;
    }
  }

  function openMaintenance(endpoint) {
    selectedMonitor = endpoint.id;
    get("maintenance-heading").textContent = "Maintenance for " + endpoint.name;
    get("maintenance-form").reset();
    get("maintenance-form").hidden = !["admin", "member"].includes(document.body.dataset.role);
    get("maintenance-dialog").showModal();
    loadMaintenance(endpoint.id);
  }

  get("incident-filter").addEventListener("change", refreshIncidents);
  get("acknowledge-incident").addEventListener("click", (event) => incidentAction("acknowledge", event.target));
  get("incident-note-form").addEventListener("submit", (event) => {
    event.preventDefault();
    incidentAction("notes", event.submitter, {text: event.target.elements.text.value});
  });
  get("incident-report-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const fields = event.target.elements;
    incidentAction("report", event.submitter, {
      root_cause: fields.root_cause.value || null, follow_up: fields.follow_up.value || null,
      public_update: fields.public_update.value || null
    }, "PATCH");
  });
  get("maintenance-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const monitor = selectedMonitor;
    event.submitter.disabled = true;
    try {
      await request("/" + monitor + "/maintenance", "POST", {
        title: form.elements.title.value,
        starts_at: new Date(form.elements.start.value).toISOString(),
        ends_at: new Date(form.elements.end.value).toISOString()
      });
      if (selectedMonitor === monitor) {
        form.reset();
        loadMaintenance(monitor);
      }
    } catch (error) {
      if (selectedMonitor === monitor) get("maintenance-status").textContent = error.message;
    } finally {
      event.submitter.disabled = false;
    }
  });

  function renderRecentFailures(failures) {
    const rows = failures.map((failure) => {
      const row = node("li", "");
      const details = node("div", "");
      details.append(node("strong", failure.service),
        node("small", date(failure.checked_at) + " · " + (failure.reason || "Check failed")));
      row.append(details);
      return row;
    });
    get("recent-failure-list").replaceChildren(...(rows.length ? rows : [
      node("li", "No failed checks in the last 24 hours.")
    ]));
  }
  function trendChart(title, buckets, key, maximum, suffix) {
    const chart = node("div", "");
    chart.append(node("h3", title));
    const bars = node("ol", "");
    bars.className = "trend-bars";
    for (const bucket of buckets) {
      const amount = bucket[key];
      const label = new Date(bucket.starts_at).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
      const value = amount === null ? "Unknown" : amount + suffix;
      const item = node("li", "");
      item.title = date(bucket.starts_at) + " · " + value + " · " + bucket.total_checks + " checks";
      item.append(node("span", value));
      const track = node("span", "");
      const fill = node("span", "");
      track.className = "trend-bar-track";
      track.setAttribute("aria-hidden", "true");
      if (amount !== null) fill.style.height = 100 * amount / maximum + "%";
      track.append(fill);
      item.append(track, node("time", label));
      bars.append(item);
    }
    chart.append(bars);
    return chart;
  }

  function renderWorkspaceTrends(buckets, maintenance) {
    const largestLatency = Math.max(1, ...buckets.map((bucket) => bucket.average_latency_ms || 0));
    get("workspace-trends").replaceChildren(
      trendChart("Recorded availability", buckets, "availability", 100, "%"),
      trendChart("Average response time", buckets, "average_latency_ms", largestLatency, " ms")
    );
    get("workspace-maintenance-count").textContent = "(" + maintenance.length + ")";
    get("workspace-maintenance-list").replaceChildren(...(maintenance.length
      ? maintenance.map((window) => node("li", window.service + " · " + window.title +
        " · " + date(window.starts_at) + " to " + date(window.ends_at)))
      : [node("li", "No active or upcoming maintenance windows.")]));
  }

  return {refreshIncidents, renderRecentFailures, renderWorkspaceTrends, openMaintenance, openIncident};
})();
