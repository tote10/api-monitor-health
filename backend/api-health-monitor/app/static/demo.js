"use strict";

const demoControls = [...document.querySelectorAll("[data-demo-action]")];
const demoElement = (id) => document.getElementById(id);
const result = demoElement("demo-result");
const messageChoice = demoElement("demo-message");
const formatChoice = demoElement("demo-format");
const htmlPreview = demoElement("demo-email-html");
const textPreview = demoElement("demo-email-text");
let messages = [];

function renderMessage() {
  const message = messages.find((item) => String(item.id) === messageChoice.value);
  if (!message) return;
  demoElement("demo-recipient").textContent = "To " + message.to;
  htmlPreview.hidden = formatChoice.value !== "html";
  textPreview.hidden = !htmlPreview.hidden;
  if (htmlPreview.hidden) {
    textPreview.textContent = message.text;
  } else {
    htmlPreview.srcdoc = message.html;
  }
}

function renderDemo(state) {
  const status = state.status;
  const health = status.is_up === true ? "up" : status.is_up === false ? "down" : "pending";
  demoElement("demo-health").innerHTML = window.monitorVisuals.badge(health);
  demoElement("demo-uptime").textContent = status.uptime_percentage == null
    ? "Uptime unknown" : `${status.uptime_percentage}% uptime in this session`;
  demoElement("demo-charts").innerHTML = window.monitorVisuals.checkCharts(state.recent_checks);
  demoElement("incident").classList.toggle("has-incidents", state.active_incident);
  demoElement("demo-incident-state").textContent = state.active_incident
    ? "! Active incident" : state.timeline ? "✓ Incident resolved" : "No active incidents.";
  if (state.timeline) window.monitorVisuals.timeline(demoElement("demo-timeline"), state.timeline, {capture: true});

  const previous = messages[0]?.id === state.messages[0]?.id ? messageChoice.value : "";
  messages = state.messages;
  demoElement("demo-empty-inbox").hidden = Boolean(messages.length);
  demoElement("demo-inbox").hidden = !messages.length;
  messageChoice.replaceChildren(...messages.map((item) => new Option(item.subject, item.id)));
  if (previous) messageChoice.value = previous;
  renderMessage();
}

async function updateDemo(action, button) {
  demoControls.forEach((control) => control.disabled = true);
  try {
    const response = await fetch(action ? "/demo/" + action : "/demo/state", {method: action ? "POST" : "GET"});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Demo unavailable.");
    renderDemo(data);
    result.textContent = action ? "Check completed." : "Demo ready.";
    demoControls.forEach((control) => control.disabled = false);
  } catch (error) {
    result.textContent = error.message;
    if (button) demoControls.forEach((control) => control.disabled = false);
  }
}

messageChoice.addEventListener("change", renderMessage);
formatChoice.addEventListener("change", renderMessage);
demoControls.forEach((button) => button.addEventListener("click", () => updateDemo(button.dataset.demoAction, button)));
updateDemo();
