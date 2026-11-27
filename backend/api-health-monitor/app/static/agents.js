"use strict";

window.agentUI = (() => {
  const get = (id) => document.getElementById(id);
  const dialog = get("agents-dialog");
  const form = get("agent-form");
  const monitorForm = get("private-monitor-form");
  const status = get("agents-status");
  const prefix = "/api/v1/agents";
  let reloadDashboard;

  async function request(path, method = "GET", body) {
    const response = await fetch(prefix + path, {
      method,
      headers: body ? {"Content-Type": "application/json"} : {},
      body: body ? JSON.stringify(body) : undefined
    });
    if (response.status === 401) window.location.replace("/login");
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check the agent settings.");
    return data;
  }

  function writable() {
    return ["admin", "member"].includes(document.body.dataset.role);
  }

  function action(label, callback) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "action-button";
    button.textContent = label;
    button.addEventListener("click", () => run(button, callback));
    return button;
  }

  function showCredential(agent) {
    if (!dialog.open) return;
    get("agent-id").value = agent.id;
    get("agent-token").value = agent.token;
    get("agent-credential").hidden = false;
    status.textContent = "Copy the credential now. It is shown only once.";
  }

  async function load() {
    const agents = await request("");
    monitorForm.elements.agent.replaceChildren(...agents.map((agent) => new Option(agent.name, agent.id)));
    monitorForm.hidden = !writable() || !agents.length;
    get("agents-list").replaceChildren(...agents.map((agent) => {
      const row = document.createElement("li");
      const name = document.createElement("strong");
      name.textContent = agent.name + (agent.online ? " · Online" : " · Offline");
      const actions = document.createElement("div");
      actions.className = "report-actions";
      if (writable()) {
        actions.append(action("Rotate token", async () => {
          if (window.confirm("Rotate this credential? Update the running agent before its next poll.")) {
            const credential = await request("/" + agent.id + "/rotate", "POST");
            await load();
            showCredential(credential);
          }
        }), action("Revoke", async () => {
          if (window.confirm("Revoke this agent’s credential? Private checks will stop.")) {
            await request("/" + agent.id + "/revoke", "POST");
            clearCredential();
            await load();
          }
        }));
      }
      row.append(name, actions);
      return row;
    }));
    status.textContent = agents.length ? "" : "No private agents registered.";
  }

  async function run(button, callback) {
    if (button) button.disabled = true;
    try {
      await callback();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      if (button) button.disabled = false;
    }
  }

  function clearCredential() {
    get("agent-token").value = "";
    get("agent-credential").hidden = true;
  }

  function open(reload) {
    reloadDashboard = reload;
    clearCredential();
    form.reset();
    form.hidden = !writable();
    run(null, load);
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(event.submitter, async () => {
      const credential = await request("", "POST", {name: form.elements.name.value});
      form.reset();
      await load();
      showCredential(credential);
    });
  });

  monitorForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const fields = monitorForm.elements;
    run(event.submitter, async () => {
      await request("/" + fields.agent.value + "/monitors", "POST", {
        name: fields.name.value, target: fields.target.value, interval_seconds: Number(fields.interval.value),
        timeout_seconds: Number(fields.timeout.value), expected_status_code: Number(fields.expected.value)
      });
      monitorForm.reset();
      status.textContent = "Private monitor added.";
      await reloadDashboard();
    });
  });
  dialog.addEventListener("close", clearCredential);
  return {open};
})();
