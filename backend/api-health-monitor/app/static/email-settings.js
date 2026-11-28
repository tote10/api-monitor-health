(() => {
  "use strict";

  const dialog = document.getElementById("email-settings-dialog");
  const form = document.getElementById("email-settings-form");
  const result = document.getElementById("email-settings-result");
  const recipients = document.getElementById("email-recipients");
  const path = "/operations/email";

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  async function api(suffix = "", method = "GET", data) {
    const response = await fetch(path + suffix, {
      method, headers: data ? {"Content-Type": "application/json"} : {},
      body: data ? JSON.stringify(data) : undefined,
    });
    if (response.status === 204) return;
    const json = await response.json();
    if (!response.ok) throw new Error(typeof json.detail === "string" ? json.detail : "Check the email address and try again.");
    return json;
  }

  async function perform(button, action, message) {
    button.disabled = true;
    try {
      const detail = await action();
      await render();
      result.textContent = typeof message === "function" ? message(detail) : message;
    } catch (error) {
      result.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  function checkbox(value, checked, label) {
    const wrapper = element("label");
    const input = element("input");
    input.type = "checkbox";
    input.value = String(value);
    input.checked = checked;
    wrapper.append(input, document.createTextNode(" " + label));
    return {wrapper, input};
  }

  function button(label, action) {
    const control = element("button", label, "button");
    control.type = "button";
    control.addEventListener("click", () => action(control));
    return control;
  }

  function testStatus(test) {
    if (!test) return "";
    if (test.state === "sent") return "Last test accepted by SMTP.";
    if (test.state === "pending") return test.last_error
      ? `Last test will retry after ${test.last_error}.` : "Last test is queued.";
    return `Last test ${test.state}${test.last_error ? " (" + test.last_error + ")" : ""}.`;
  }

  function recipientCard(recipient, monitors, configured) {
    const card = element("section", "", "panel email-recipient");
    const recipientPath = "/recipients/" + recipient.id;
    card.append(element("h3", recipient.address));
    card.append(element("p", recipient.verified ? "✓ Verified" : "○ Awaiting verification"));
    const preferences = element("fieldset");
    preferences.append(element("legend", "Monitors that send alerts"));
    const monitorInputs = monitors.map((monitor) => checkbox(
      monitor.id, recipient.monitor_ids.includes(monitor.id), monitor.name,
    ));
    if (!monitorInputs.length) preferences.append(element("p", "Add a monitor to select it here."));
    monitorInputs.forEach(({wrapper}) => preferences.append(wrapper));
    const digest = checkbox("weekly", recipient.weekly_digest, "Weekly digest for selected monitors");
    const controls = element("div", "", "overview-actions");
    controls.append(button("Save preferences", (control) => perform(control, () => api(recipientPath, "PUT", {
      monitor_ids: monitorInputs.filter(({input}) => input.checked).map(({input}) => Number(input.value)),
      weekly_digest: digest.input.checked,
    }), "Email preferences saved.")));
    const test = button("Send test email", (control) => perform(
      control, () => api(recipientPath + "/test", "POST"), "Test email queued. Use Refresh delivery status to check acceptance.",
    ));
    test.disabled = !configured || !recipient.verified;
    controls.append(test);
    if (!recipient.verified && configured) controls.append(button("Resend verification", (control) => perform(
      control, () => api("/recipients", "POST", {address: recipient.address}), verificationStatus,
    )));
    controls.append(button("Remove", (control) => perform(
      control, () => api(recipientPath, "DELETE"), "Recipient removed.",
    )));
    card.append(preferences, digest.wrapper, controls);
    if (recipient.last_test) card.append(element("p", testStatus(recipient.last_test)));
    return card;
  }

  function verificationStatus(data) {
    return data.queued ? "Verification email queued. Check your inbox." : "Already verified or a verification email was requested recently.";
  }

  async function render() {
    const data = await api();
    form.hidden = !data.configured;
    recipients.replaceChildren(...data.recipients.map((recipient) => recipientCard(recipient, data.monitors, data.configured)));
    if (data.recipients.length) recipients.append(button("Refresh delivery status", (control) => perform(
      control, () => Promise.resolve(), "Delivery status refreshed.",
    )));
    result.textContent = data.configured ? "" : "Email notifications require SMTP and a public base URL.";
  }

  document.getElementById("email-settings-button").addEventListener("click", async (event) => {
    event.currentTarget.closest("details").open = false;
    dialog.showModal();
    recipients.replaceChildren();
    result.textContent = "Loading …";
    try {
      await render();
    } catch (error) {
      result.textContent = error.message;
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await perform(event.submitter, () => api("/recipients", "POST", {address: form.elements.address.value}), verificationStatus);
  });
})();
