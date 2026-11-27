"use strict";

window.workspaceSettings = (() => {
  const get = (id) => document.getElementById(id);
  const writable = () => ["admin", "member"].includes(document.body.dataset.role);
  const base = "/api/v1/integrations";
  const settingsPath = "/operations/status-settings";
  const routeForm = get("integration-form");
  const routeStatus = get("integration-status");
  const testEndpoint = get("integration-test-endpoint");
  const statusForm = get("status-settings-form");
  const statusResult = get("status-settings-result");

  function node(tag, text) {
    const element = document.createElement(tag);
    element.textContent = text;
    return element;
  }

  async function request(path, method = "GET", body) {
    const response = await fetch(path, {
      method,
      headers: body === undefined ? {} : {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    if (response.status === 401) window.location.replace("/login");
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed. Check the settings.");
    return data;
  }

  async function routeAction(button, path, method, payload) {
    button.disabled = true;
    try {
      await request(path, method, payload);
      await loadRoutes();
    } catch (error) {
      routeStatus.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  async function loadRoutes() {
    routeStatus.textContent = "Loading routes…";
    try {
      const [routes, audit] = await Promise.all([request(base), request(base + "/audit")]);
      get("integration-list").replaceChildren(...routes.map((route) => {
        const row = node("li", "");
        const info = node("div", "");
        const labels = [route.kind, route.enabled ? "Enabled" : "Disabled", route.service_group, route.tag, route.severity, route.endpoint_id ? "Monitor #" + route.endpoint_id : null]
          .filter(Boolean).join(" · ");
        info.append(node("strong", route.name), node("small", labels));
        const delivery = route.last_delivery;
        if (delivery) info.append(node("small", "Latest delivery: " + delivery.state + " (" + delivery.attempts + " attempts)"));
        row.append(info);
        if (writable()) {
          const actions = node("div", "");
          actions.className = "report-actions";
          const toggle = node("button", route.enabled ? "Disable" : "Enable");
          toggle.type = "button";
          toggle.className = "action-button";
          toggle.addEventListener("click", () => routeAction(toggle, base + "/" + route.id, "PATCH", {enabled: !route.enabled}));
          const test = node("button", "Send test");
          test.type = "button";
          test.className = "action-button";
          test.disabled = !route.enabled || !testEndpoint.options.length;
          test.addEventListener("click", () => routeAction(test, base + "/" + route.id + "/test", "POST", {
            endpoint_id: Number(testEndpoint.value)
          }));
          actions.append(toggle, test);
          row.append(actions);
        }
        return row;
      }));
      get("audit-list").replaceChildren(...audit.map((entry) => node("li", [
        new Date(entry.created_at).toLocaleString(), entry.actor, entry.action,
        entry.subject_id ? "#" + entry.subject_id : null
      ].filter(Boolean).join(" · "))));
      routeStatus.textContent = routes.length ? "" : "No alert routes configured.";
    } catch (error) {
      routeStatus.textContent = error.message;
    }
  }

  function openIntegrations(endpoints) {
    routeForm.reset();
    routeForm.hidden = !writable();
    get("integration-test-field").hidden = !writable();
    testEndpoint.replaceChildren(...endpoints.map((endpoint) => new Option(endpoint.name, endpoint.id)));
    routeForm.elements.endpoint_id.replaceChildren(new Option("All monitors", ""),
      ...endpoints.map((endpoint) => new Option(endpoint.name, endpoint.id)));
    loadRoutes();
  }

  routeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fields = routeForm.elements;
    event.submitter.disabled = true;
    try {
      await request(base, "POST", {
        name: fields.name.value, kind: fields.kind.value, destination: fields.destination.value,
        endpoint_id: Number(fields.endpoint_id.value) || null,
        service_group: fields.service_group.value || null, tag: fields.tag.value || null,
        severity: fields.severity.value || null, escalation_seconds: Number(fields.escalation_seconds.value) || null
      });
      routeForm.reset();
      await loadRoutes();
    } catch (error) {
      routeStatus.textContent = error.message;
    } finally {
      event.submitter.disabled = false;
    }
  });
  get("integrations-dialog").addEventListener("close", () => routeForm.reset());

  function renderStatus(settings) {
    statusForm.elements.title.value = settings.title;
    statusForm.elements.enabled.checked = settings.enabled;
    for (const field of statusForm.elements) field.disabled = !writable();
    const link = get("public-status-link");
    link.hidden = !(settings.enabled && settings.path);
    if (!link.hidden) link.href = settings.path;
  }

  async function openStatus() {
    statusForm.hidden = true;
    get("public-status-link").hidden = true;
    statusResult.textContent = "Loading settings…";
    try {
      renderStatus(await request(settingsPath));
      statusForm.hidden = false;
      statusResult.textContent = "";
    } catch (error) {
      statusResult.textContent = error.message;
    }
  }

  statusForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    event.submitter.disabled = true;
    try {
      renderStatus(await request(settingsPath, "PUT", {
        title: statusForm.elements.title.value, enabled: statusForm.elements.enabled.checked
      }));
      statusResult.textContent = "Settings saved.";
    } catch (error) {
      statusResult.textContent = error.message;
    } finally {
      event.submitter.disabled = false;
    }
  });

  return {openIntegrations, openStatus};
})();
