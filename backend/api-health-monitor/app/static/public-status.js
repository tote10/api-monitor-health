"use strict";

function publicList(id, entries, empty) {
  const items = (entries.length ? entries : [empty]).map((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  });
  document.getElementById(id).replaceChildren(...items);
}

const statusPath = window.location.pathname.replace(/\/$/, "");
const subscribe = document.getElementById("public-subscribe");
const subscriptionResult = document.getElementById("subscription-result");

function renderServices(services) {
  const list = document.getElementById("public-services");
  if (!services.length) {
    publicList("public-services", [], "No services published.");
    return;
  }
  const icons = {operational: "✓", unavailable: "!", paused: "Ⅱ", unknown: "?"};
  list.replaceChildren(...services.map((service) => {
    const item = document.createElement("li");
    item.id = "service-" + service.id;
    item.textContent = `${service.name} · ${icons[service.state]} ${service.state}`;
    return item;
  }));
  const linkedService = document.getElementById(location.hash.slice(1));
  if (linkedService) linkedService.scrollIntoView();
}

subscribe.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const response = await fetch(statusPath + "/services/" + subscribe.elements.service.value + "/subscribe", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({address: subscribe.elements.address.value}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Enter a valid email address.");
    subscriptionResult.textContent = data.message;
  } catch (error) {
    subscriptionResult.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

async function loadPublicStatus() {
  const errorBanner = document.getElementById("public-error");
  const date = (value) => new Date(value).toLocaleString();
  try {
    const response = await fetch(statusPath + "/snapshot");
    if (!response.ok) throw new Error("This status page is unavailable.");
    const snapshot = await response.json();
    document.getElementById("public-title").textContent = snapshot.title;
    document.title = snapshot.title;
    renderServices(snapshot.services);
    if (snapshot.subscriptions_enabled && snapshot.services.length) {
      subscribe.elements.service.replaceChildren(...snapshot.services.map((item) => new Option(item.name, item.id)));
      document.getElementById("subscriptions").hidden = false;
    }
    publicList("public-incidents", snapshot.incidents.map((item) =>
      item.service + " · " + (item.resolved_at ? "Resolved" : "Open") + " · " + date(item.opened_at) +
      " · " + item.message), "No public incident updates.");
    publicList("public-maintenance", snapshot.maintenance.map((item) =>
      item.service + " · " + date(item.starts_at) + " to " + date(item.ends_at)), "No scheduled maintenance.");
    errorBanner.hidden = true;
  } catch (error) {
    errorBanner.textContent = error.message;
  }
}
loadPublicStatus();
