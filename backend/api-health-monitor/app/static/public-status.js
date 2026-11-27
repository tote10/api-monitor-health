"use strict";

function publicList(id, entries, empty) {
  const items = (entries.length ? entries : [empty]).map((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  });
  document.getElementById(id).replaceChildren(...items);
}

async function loadPublicStatus() {
  const errorBanner = document.getElementById("public-error");
  const date = (value) => new Date(value).toLocaleString();
  try {
    const response = await fetch(window.location.pathname.replace(/\/$/, "") + "/snapshot");
    if (!response.ok) throw new Error("This status page is unavailable.");
    const snapshot = await response.json();
    document.getElementById("public-title").textContent = snapshot.title;
    document.title = snapshot.title;
    publicList("public-services", snapshot.services.map((item) => item.name + " · " + item.state),
      "No services published.");
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
