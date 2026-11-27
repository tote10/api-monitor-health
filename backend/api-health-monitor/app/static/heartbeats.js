"use strict";

window.heartbeatUI = (() => {
  const get = (id) => document.getElementById(id);
  const root = "/api/v1/heartbeats";
  const dialog = get("heartbeat-dialog");
  const form = get("heartbeat-form");
  const status = get("heartbeat-status");
  let endpoint;
  let refresh;
  let revision = 0;

  function open(monitor, reload) {
    revision += 1;
    endpoint = monitor;
    refresh = reload;
    form.reset();
    status.textContent = "";
    get("heartbeat-result").hidden = true;
    get("heartbeat-token").value = "";
    get("heartbeat-actions").hidden = !endpoint;
    get("heartbeat-heading").textContent = endpoint ? "Edit heartbeat" : "Add heartbeat";
    if (endpoint) {
      form.elements.name.value = endpoint.name;
      form.elements.interval.value = endpoint.interval_seconds;
      form.elements.grace.value = endpoint.heartbeat_grace_seconds;
    }
    dialog.showModal();
  }

  async function save(button, action, payload) {
    button.disabled = true;
    const currentRevision = revision;
    status.textContent = "";
    try {
      const path = root + (endpoint ? "/" + endpoint.id : "") + (action ? "/" + action : "");
      const response = await fetch(path, {
        method: endpoint && !action ? "PATCH" : "POST",
        headers: payload ? {"Content-Type": "application/json"} : {},
        body: payload ? JSON.stringify(payload) : undefined
      });
      if (response.status === 401) window.location.replace("/login");
      const data = response.status === 204 ? null : await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check the heartbeat settings.");
      if (currentRevision !== revision || !dialog.open) return;
      await refresh();
      if (response.status === 204) {
        get("heartbeat-token").value = "";
        get("heartbeat-result").hidden = true;
        status.textContent = "Credential revoked.";
      } else {
        endpoint = data.endpoint || data;
        get("heartbeat-actions").hidden = false;
        status.textContent = "Heartbeat saved.";
        if (data.token) {
          get("heartbeat-url").value = window.location.origin + root + "/" + endpoint.id + "/ping";
          get("heartbeat-token").value = data.token;
          get("heartbeat-result").hidden = false;
          status.textContent = "Copy the token now. It is shown only once.";
        }
      }
    } catch (error) {
      if (currentRevision === revision) status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    save(event.submitter, null, {
      name: form.elements.name.value,
      interval_seconds: Number(form.elements.interval.value),
      heartbeat_grace_seconds: Number(form.elements.grace.value)
    });
  });
  get("heartbeat-rotate").addEventListener("click", (event) => {
    if (window.confirm("Rotate the heartbeat token? Update the job before its next check-in.")) save(event.target, "rotate");
  });
  get("heartbeat-revoke").addEventListener("click", (event) => {
    if (window.confirm("Revoke this heartbeat token? The job will need a new token to check in.")) save(event.target, "revoke");
  });
  dialog.addEventListener("close", () => {
    get("heartbeat-token").value = "";
  });
  return {open};
})();
