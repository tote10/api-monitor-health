"use strict";

window.monitorVisuals = (() => {
  const states = {up: ["✓", "Operational"], down: ["!", "Down"], pending: ["?", "Unknown"], paused: ["Ⅱ", "Paused"]};

  function badge(state, text = states[state][1]) {
    return '<span class="badge badge-' + state + '"><span aria-hidden="true">' +
      states[state][0] + "</span> " + text + "</span>";
  }

  function checkCharts(checks) {
    const samples = [...Array(Math.max(0, 24 - checks.length)).fill(null), ...checks];
    const labels = samples.map((sample) => sample?.ok === true ? "Passed" : sample?.ok === false ? "Failed" : "Unknown");
    const segments = labels.map((label) => {
      const type = label.toLowerCase();
      const icon = {passed: "✓", failed: "!", unknown: "·"}[type];
      return `<span class="check-cell check-${type}" title="${label}" aria-hidden="true">${icon}</span>`;
    }).join("");
    const counts = ["Passed", "Failed", "Unknown"].map((label) =>
      `${labels.filter((value) => value === label).length} ${label.toLowerCase()}`).join(", ");

    const values = samples.map((sample) => sample?.latency_ms);
    const known = values.filter(Number.isFinite);
    const max = Math.max(1, ...known);
    const points = values.map((value, index) => Number.isFinite(value)
      ? [4 + 4 * index, 26 - 22 * value / max] : null);
    const lines = points.map((point, index) => {
      if (!point) return "";
      const previous = points[index - 1];
      return previous
        ? `<line x1="${previous[0]}" y1="${previous[1]}" x2="${point[0]}" y2="${point[1]}"></line>`
        : `<circle cx="${point[0]}" cy="${point[1]}" r="1.4"></circle>`;
    }).join("");
    const trend = known.length
      ? `<svg class="latency-sparkline" viewBox="0 0 100 30" preserveAspectRatio="none"
          role="img" aria-label="Recent latency, ${Math.min(...known)} to ${Math.max(...known)} milliseconds">${lines}</svg>
         <span class="sparkline-last">${known.at(-1)} ms</span>`
      : '<span class="sparkline-last">Unknown</span>';
    return `<div class="service-charts">
      <div><span class="metric-label">Recent checks</span>
        <div class="check-strip" role="img" aria-label="${counts}">${segments}</div>
      </div>
      <div><span class="metric-label">Latency trend</span><div class="sparkline-row">${trend}</div></div>
    </div>`;
  }

  function timeline(container, timelineData) {
    const events = timelineData.events;
    const order = document.createElement("ol");
    order.className = "incident-timeline";
    const labels = {
      failed_check: ["!", "Failed check"], opened: ["●", "Incident opened"],
      acknowledged: ["✓", "Acknowledged"], recovered: ["✓", "Recovered"], notification: ["↗", "Notification"],
    };
    const outcomes = {
      pending: "Queued", accepted: "Accepted by provider", sent: "Sent (legacy record)",
      failed: "Failed", unknown: "Outcome unknown", canceled: "Canceled", in_progress: "In progress",
    };
    const eventLabels = {"endpoint.down": "Outage", "endpoint.recovered": "Recovery", "endpoint.escalated": "Escalation"};
    for (const event of events) {
      const item = document.createElement("li");
      item.className = "timeline-" + event.kind;
      const icon = document.createElement("span");
      icon.className = "timeline-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = labels[event.kind][0];
      const main = document.createElement("div");
      const heading = document.createElement("strong");
      const time = document.createElement("time");
      time.dateTime = event.at;
      time.textContent = new Date(event.at).toLocaleString();
      heading.textContent = event.kind === "notification"
        ? `${eventLabels[event.event] || "Notification"} · ${event.channel}${event.number ? " · Attempt " + event.number : ""} · ${outcomes[event.outcome]}`
        : labels[event.kind][1];
      main.append(heading, time);
      if (event.detail && ["notification", "failed_check"].includes(event.kind)) {
        const detail = document.createElement("div");
        detail.textContent = event.detail;
        main.append(detail);
      }
      item.append(icon, main);
      order.append(item);
    }
    container.replaceChildren(order);
    if (timelineData.truncated) {
      const note = document.createElement("p");
      note.textContent = "Showing recent timeline entries.";
      container.prepend(note);
    }
  }

  return {badge, checkCharts, timeline};
})();
