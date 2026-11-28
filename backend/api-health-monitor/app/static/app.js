(function () {
  "use strict";

  var API_PREFIX = "/api/v1";
  var REFRESH_MS = 15000;

  var state = {
    endpoints: [],
    statuses: new Map(),
    recentChecks: new Map(),
    loading: false,
    editingId: null,
    search: "",
    filter: "all",
    sort: "priority",
    group: "",
    loaded: false
  };

  var elements = {
    list: document.getElementById("endpoint-list"),
    loading: document.getElementById("loading-state"),
    empty: document.getElementById("empty-state"),
    noResults: document.getElementById("no-results-state"),
    errorBanner: document.getElementById("error-banner"),
    errorMessage: document.getElementById("error-message"),
    summaryTotal: document.getElementById("summary-total"),
    summaryUp: document.getElementById("summary-up"),
    summaryDown: document.getElementById("summary-down"),
    summaryPaused: document.getElementById("summary-paused"),
    search: document.getElementById("search-input"),
    filter: document.getElementById("status-filter"),
    sort: document.getElementById("sort-endpoints"),
    group: document.getElementById("group-filter"),
    refresh: document.getElementById("refresh-button"),
    add: document.getElementById("add-button"),
    emptyAdd: document.getElementById("empty-add-button"),
    retry: document.getElementById("retry-button"),
    endpointDialog: document.getElementById("endpoint-dialog"),
    endpointForm: document.getElementById("endpoint-form"),
    dialogTitle: document.getElementById("dialog-title"),
    dialogEyebrow: document.getElementById("dialog-eyebrow"),
    formError: document.getElementById("form-error"),
    saveButton: document.getElementById("save-button"),
    historyDialog: document.getElementById("history-dialog"),
    historyTitle: document.getElementById("history-title"),
    historySummary: document.getElementById("history-summary"),
    historyChart: document.getElementById("history-chart"),
    responseTimeChart: document.getElementById("response-time-chart"),
    rtAvg: document.getElementById("rt-avg"),
    uptimeAvg: document.getElementById("uptime-avg"),
    historyList: document.getElementById("history-list"),
    toast: document.getElementById("toast")
  };

  var toastTimer = null;

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value).replace(
      /[&<>"']/g,
      function (character) {
        return {
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;"
        }[character];
      }
    );
  }

  function errorMessage(data, status) {
    if (data && Array.isArray(data.detail)) {
      return data.detail
        .map(function (item) {
          var field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : "";
          return (field ? field + ": " : "") + (item.msg || "Invalid value");
        })
        .join(", ");
    }
    if (data && typeof data.detail === "string") {
      return data.detail;
    }
    return "Request failed with status " + status + ".";
  }

  function api(path, options) {
    var config = Object.assign({}, options || {});
    config.headers = Object.assign({}, config.headers || {});
    if (config.body && typeof config.body !== "string") {
      config.headers["Content-Type"] = "application/json";
      config.body = JSON.stringify(config.body);
    }

    return fetch(API_PREFIX + path, config)
      .then(function (response) {
        if (response.status === 401) {
          window.location.replace("/login");
        }
        if (response.status === 204) {
          return null;
        }
        return response.text().then(function (text) {
          var data = null;
          if (text) {
            try {
              data = JSON.parse(text);
            } catch (error) {
              data = null;
            }
          }
          if (!response.ok) {
            throw new Error(errorMessage(data, response.status));
          }
          return data;
        });
      })
      .catch(function (error) {
        if (error instanceof TypeError) {
          throw new Error("Cannot reach the API. Check that the service is running.");
        }
        throw error;
      });
  }

  function toast(message, isError) {
    window.clearTimeout(toastTimer);
    elements.toast.textContent = message;
    elements.toast.classList.toggle("error", Boolean(isError));
    elements.toast.classList.add("visible");
    toastTimer = window.setTimeout(function () {
      elements.toast.classList.remove("visible");
    }, 3200);
  }

  function statusFor(endpoint) {
    return state.statuses.get(endpoint.id) || null;
  }

  function statusClass(endpoint) {
    if (!endpoint.is_active) {
      return "paused";
    }
    var status = statusFor(endpoint);
    if (!status || status.is_up === null || status.is_up === undefined) {
      return "pending";
    }
    return status.is_up ? "up" : "down";
  }

  var statusLabels = {
    up: "Operational",
    down: "Down",
    pending: "Unknown",
    paused: "Paused"
  };
  var statusPriority = {down: 0, pending: 1, up: 2, paused: 3};
  var summaryFilters = document.querySelectorAll("[data-summary-filter]");
  var overviewLegend = document.getElementById("overview-legend");
  var overviewSegments = document.querySelectorAll("[data-health-segment]");
  var overviewSync = document.getElementById("overview-sync");

  function renderOverview(counts, total) {
    var title = document.getElementById("overview-title");
    var description = document.getElementById("overview-description");
    if (!total) {
      title.textContent = "Ready to start monitoring";
      description.textContent = "Your workspace has no endpoints yet.";
    } else if (counts.down) {
      title.textContent = counts.down === 1 ? "1 endpoint is down" : counts.down + " endpoints are down";
      description.textContent = "Open an affected endpoint’s history to investigate failed checks.";
    } else if (counts.pending) {
      title.textContent = "Waiting for check results";
      description.textContent = "Some endpoints do not have a status yet.";
    } else if (counts.up) {
      title.textContent = counts.paused ? "All active endpoints operational" : "All endpoints operational";
      description.textContent = "The latest check passed for every active endpoint.";
    } else {
      title.textContent = "Monitoring is paused";
      description.textContent = "Resume an endpoint to start collecting checks again.";
    }

    var items = [];
    overviewSegments.forEach(function (segment) {
      var kind = segment.dataset.healthSegment;
      segment.style.flexGrow = counts[kind];
      segment.hidden = counts[kind] === 0;
      var item = document.createElement("span");
      item.className = "legend-item legend-" + kind;
      item.textContent = statusLabels[kind] + " " + counts[kind];
      items.push(item);
    });
    overviewLegend.replaceChildren(...items);
  }

  function formatTimeAgo(value) {
    if (!value) {
      return "Never";
    }
    var timestamp = new Date(value).getTime();
    if (Number.isNaN(timestamp)) {
      return "Unknown";
    }
    var seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
    if (seconds < 5) {
      return "just now";
    }
    if (seconds < 60) {
      return seconds + "s ago";
    }
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) {
      return minutes + "m ago";
    }
    var hours = Math.floor(minutes / 60);
    if (hours < 24) {
      return hours + "h ago";
    }
    var days = Math.floor(hours / 24);
    return days + "d ago";
  }

  function formatTimestamp(value) {
    if (!value) {
      return "";
    }
    var date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
  }

  function formatInterval(seconds) {
    if (seconds >= 3600 && seconds % 3600 === 0) {
      return "Every " + seconds / 3600 + "h";
    }
    if (seconds >= 60 && seconds % 60 === 0) {
      return "Every " + seconds / 60 + "m";
    }
    return "Every " + seconds + "s";
  }

  function formatResponseTime(value) {
    return value === null || value === undefined ? "—" : value + " ms";
  }

  function formatUptime(value) {
    return value === null || value === undefined ? "—" : value.toFixed(1) + "%";
  }

  function warningIcon() {
    return (
      '<svg viewBox="0 0 20 20" aria-hidden="true">' +
      '<path d="M10 6.5v4M10 13.5v.1M8.5 3.7 2.9 14a1.4 1.4 0 0 0 1.2 2.1h11.8a1.4 1.4 0 0 0 1.2-2.1L11.5 3.7a1.7 1.7 0 0 0-3 0Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>' +
      "</svg>"
    );
  }

  function renderCard(endpoint) {
    var status = statusFor(endpoint);
    var kind = statusClass(endpoint);
    var lastChecked = status ? status.last_checked_at : null;
    var error = status ? status.last_error : null;
    var heartbeat = endpoint.kind === "heartbeat";
    var privateAgent = endpoint.kind === "agent";
    var activeLabel = endpoint.is_active ? "Pause" : "Resume";
    var uptime = status?.uptime_percentage == null ? "Unknown" : formatUptime(status.uptime_percentage);
    var checkCount = status ? status.total_checks : 0;

    return (
      '<article class="endpoint-card status-' + kind + '" data-endpoint-id="' + endpoint.id + '">' +
        '<div class="card-top">' +
          '<div class="endpoint-identity">' +
            '<span class="status-dot ' + kind + '" aria-hidden="true"></span>' +
            "<div>" +
              '<h3 class="endpoint-name">' + escapeHtml(endpoint.name) + "</h3>" +
              (heartbeat
                ? '<span class="endpoint-url">Heartbeat · ' + (endpoint.last_heartbeat_at
                  ? 'last check-in ' + formatTimeAgo(endpoint.last_heartbeat_at) : 'awaiting first check-in') + '</span>'
                : privateAgent ? '<span class="endpoint-url">Private agent · ' + escapeHtml(endpoint.agent_target) + '</span>'
                : '<a class="endpoint-url" href="' + escapeHtml(endpoint.url) + '" target="_blank" rel="noreferrer noopener" title="' +
                  escapeHtml(endpoint.url) + '">' + escapeHtml(endpoint.url) + '</a>') +
            "</div>" +
          "</div>" +
          window.monitorVisuals.badge(kind, kind === "pending" && status?.agent_offline ? "Unknown · Agent offline" :
            status?.is_stale && kind === "pending" ? "Unknown · Stale" : statusLabels[kind]) +
        "</div>" +
        ((endpoint.service_group || endpoint.tags.length) ? '<div class="monitor-tags">' +
          [endpoint.service_group, ...endpoint.tags].filter(Boolean).map(function (tag) {
            return '<span>' + escapeHtml(tag) + '</span>';
          }).join("") + '</div>' : "") +
        '<div class="card-metrics">' +
          '<div><span class="metric-label">Last check</span><span class="metric-value" title="' + escapeHtml(formatTimestamp(lastChecked)) + '">' + formatTimeAgo(lastChecked) + "</span></div>" +
          '<div><span class="metric-label">Response</span><span class="metric-value">' + (status?.last_response_time_ms == null ? "Unknown" : formatResponseTime(status.last_response_time_ms)) + (status?.is_slow ? " · Slow" : "") + "</span></div>" +
          '<div><span class="metric-label">Uptime</span><span class="metric-value" title="' + checkCount + ' checks">' + uptime + "</span></div>" +
          '<div><span class="metric-label">Schedule</span><span class="metric-value">' + formatInterval(endpoint.interval_seconds) + "</span></div>" +
        "</div>" +
        window.monitorVisuals.checkCharts(state.recentChecks.get(endpoint.id) || []) +
        (error
          ? '<div class="card-error">' + warningIcon() + "<span>" + escapeHtml(error) + "</span></div>"
          : "") +
        '<div class="card-actions">' +
          (heartbeat || privateAgent ? "" : '<button class="action-button" type="button" data-action="check" data-id="' + endpoint.id + '">Check now</button>') +
          '<button class="action-button" type="button" data-action="history" data-id="' + endpoint.id + '">History</button>' +
          '<button class="action-button" type="button" data-action="edit" data-id="' + endpoint.id + '">Edit</button>' +
          '<button class="action-button" type="button" data-action="maintenance" data-id="' + endpoint.id + '">Maintenance</button>' +
          '<span class="action-spacer"></span>' +
          '<button class="action-button" type="button" data-action="toggle" data-id="' + endpoint.id + '">' + activeLabel + "</button>" +
          '<button class="action-button danger" type="button" data-action="delete" data-id="' + endpoint.id + '">Delete</button>' +
        "</div>" +
      "</article>"
    );
  }

  function matchesFilters(endpoint) {
    if (state.group && endpoint.service_group !== state.group) return false;
    var query = state.search.trim().toLowerCase();
    if (query) {
      var haystack = [endpoint.name, endpoint.url, endpoint.service_group, ...endpoint.tags].join(" ").toLowerCase();
      if (haystack.indexOf(query) === -1) {
        return false;
      }
    }
    if (state.filter === "all") {
      return true;
    }
    return statusClass(endpoint) === state.filter;
  }

  function render() {
    if (!state.loaded) return;

    var counts = {up: 0, down: 0, paused: 0, pending: 0};
    state.endpoints.forEach(function (endpoint) {
      counts[statusClass(endpoint)] += 1;
    });

    var total = state.endpoints.length;
    elements.summaryTotal.textContent = String(total);
    elements.summaryUp.textContent = String(counts.up);
    elements.summaryDown.textContent = String(counts.down);
    elements.summaryPaused.textContent = String(counts.paused);
    summaryFilters.forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.summaryFilter === state.filter));
    });
    renderOverview(counts, total);

    var visible = state.endpoints.filter(matchesFilters).sort(function (left, right) {
      var difference = 0;
      if (state.sort === "priority") {
        difference = statusPriority[statusClass(left)] - statusPriority[statusClass(right)];
      } else if (state.sort === "latency") {
        difference = (statusFor(right)?.last_response_time_ms ?? -1) -
          (statusFor(left)?.last_response_time_ms ?? -1);
      }
      return difference || left.name.localeCompare(right.name);
    });
    elements.list.innerHTML = visible.map(renderCard).join("");
    document.getElementById("results-count").textContent = visible.length === total
      ? total + (total === 1 ? " endpoint" : " endpoints")
      : visible.length + " of " + total + " endpoints";

    elements.empty.hidden = state.endpoints.length !== 0;
    elements.noResults.hidden = state.endpoints.length === 0 || visible.length !== 0;
    elements.list.hidden = visible.length === 0;
  }

  function setLoading(isLoading) {
    state.loading = isLoading;
    elements.refresh.classList.toggle("is-spinning", isLoading);
    elements.refresh.setAttribute("aria-busy", String(isLoading));
  }

  function showError(message) {
    elements.errorMessage.textContent = message;
    elements.errorBanner.hidden = false;
    elements.loading.hidden = true;
  }

  function load(options) {
    var quiet = options && options.quiet;
    if (state.loading) {
      return Promise.resolve();
    }

    setLoading(true);
    if (!quiet && state.endpoints.length === 0) {
      elements.loading.hidden = false;
      elements.empty.hidden = true;
      elements.noResults.hidden = true;
    }

    return api("/endpoints/operations/overview")
      .then(function (data) {
        state.endpoints = data.monitors.map(function (item) { return item.endpoint; });
        var groups = [...new Set(state.endpoints.map(function (endpoint) { return endpoint.service_group; }).filter(Boolean))].sort();
        elements.group.replaceChildren(new Option("All groups", ""), ...groups.map(function (group) {
          return new Option(group, group);
        }));
        if (!groups.includes(state.group)) state.group = "";
        elements.group.value = state.group;
        state.statuses = new Map(data.monitors.map(function (item) {
          return [item.endpoint.id, item.status];
        }));
        state.recentChecks = new Map(data.monitors.map(function (item) {
          return [item.endpoint.id, item.recent_checks];
        }));
        elements.errorBanner.hidden = true;
        elements.loading.hidden = true;
        state.loaded = true;
        overviewSync.textContent = "Updated " + new Date().toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
        render();
        window.monitorOperations.renderActiveIncidents(data.active_incidents);
        window.monitorOperations.refreshIncidents();
        window.monitorOperations.renderRecentFailures(data.recent_failures);
        window.monitorOperations.renderWorkspaceTrends(data.trends, data.maintenance);
      })
      .catch(function (error) {
        showError(error.message);
        overviewSync.textContent = "Refresh failed";
        if (!state.loaded) {
          document.getElementById("overview-title").textContent = "Could not load service health";
          document.getElementById("overview-description").textContent = "Refresh to try again.";
        }
        if (state.endpoints.length === 0) {
          elements.empty.hidden = true;
          elements.noResults.hidden = true;
          elements.list.hidden = true;
        }
      })
      .finally(function () {
        setLoading(false);
      });
  }

  function openEndpointDialog(endpoint) {
    state.editingId = endpoint ? endpoint.id : null;
    elements.endpointForm.reset();
    var fields = elements.endpointForm.elements;
    var privateAgent = endpoint?.kind === "agent";
    for (var name of ["url", "method", "request_options", "clear_request_options"]) {
      fields[name].disabled = privateAgent;
      fields[name].closest("label").hidden = privateAgent;
    }
    document.getElementById("request-option-hint").hidden = privateAgent;
    document.getElementById("request-option-hint").textContent = endpoint?.has_request_options
      ? "Stored options are hidden. Leave blank to keep them, or enter JSON to replace them."
      : "Optional. May include headers, body, and response text to match.";
    elements.formError.hidden = true;

    if (endpoint) {
      elements.dialogTitle.textContent = "Edit endpoint";
      elements.dialogEyebrow.textContent = "Update target";
      elements.saveButton.textContent = "Save changes";
      elements.endpointForm.elements.name.value = endpoint.name;
      elements.endpointForm.elements.url.value = endpoint.url;
      elements.endpointForm.elements.interval_seconds.value = endpoint.interval_seconds;
      elements.endpointForm.elements.timeout_seconds.value = endpoint.timeout_seconds;
      elements.endpointForm.elements.expected_status_code.value = endpoint.expected_status_code;
      fields.is_active.checked = endpoint.is_active;
      fields.method.value = endpoint.method;
      fields.expected_status_codes.value = endpoint.expected_status_codes?.join(", ") || "";
      fields.latency_budget_ms.value = endpoint.latency_budget_ms ?? "";
      fields.failure_threshold.value = endpoint.failure_threshold;
      fields.slo_target_percentage.value = endpoint.slo_target_percentage ?? "";
      fields.slo_window_hours.value = endpoint.slo_window_hours;
      fields.service_group.value = endpoint.service_group || "";
      fields.tags.value = endpoint.tags.join(", ");
      fields.severity.value = endpoint.severity;
      fields.public_label.value = endpoint.public_label || "";
      fields.retention_days.value = endpoint.retention_days ?? "";
      fields.recovery_threshold.value = endpoint.recovery_threshold;
    } else {
      elements.dialogTitle.textContent = "Add endpoint";
      elements.dialogEyebrow.textContent = "New target";
      elements.saveButton.textContent = "Save endpoint";
    }

    elements.endpointDialog.showModal();
    window.setTimeout(function () {
      elements.endpointForm.elements.name.focus();
    }, 30);
  }

  function closeDialog(id) {
    var dialog = document.getElementById(id);
    if (dialog && dialog.open) {
      dialog.close();
    }
    if (id === "endpoint-dialog") {
      state.editingId = null;
    }
  }

  function submitEndpoint(event) {
    event.preventDefault();
    var form = elements.endpointForm;
    elements.formError.hidden = true;

    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    var urlValue = form.elements.url.value.trim();
    try {
      var parsed = new URL(urlValue);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        throw new Error("protocol");
      }
    } catch (error) {
      elements.formError.textContent = "Enter a complete http:// or https:// URL.";
      elements.formError.hidden = false;
      form.elements.url.focus();
      return;
    }

    var payload = {
      name: form.elements.name.value.trim(),
      url: urlValue,
      interval_seconds: Number(form.elements.interval_seconds.value),
      timeout_seconds: Number(form.elements.timeout_seconds.value),
      expected_status_code: Number(form.elements.expected_status_code.value),
      method: form.elements.method.value,
      expected_status_codes: form.elements.expected_status_codes.value.trim()
        ? form.elements.expected_status_codes.value.split(",").map(Number) : null,
      latency_budget_ms: Number(form.elements.latency_budget_ms.value) || null,
      failure_threshold: Number(form.elements.failure_threshold.value) || 1,
      slo_target_percentage: Number(form.elements.slo_target_percentage.value) || null,
      slo_window_hours: Number(form.elements.slo_window_hours.value) || 720,
      service_group: form.elements.service_group.value.trim() || null,
      tags: [...new Set(form.elements.tags.value.split(",").map(function (value) { return value.trim(); }).filter(Boolean))],
      severity: form.elements.severity.value,
      public_label: form.elements.public_label.value.trim() || null,
      retention_days: form.elements.retention_days.value === "" ? null : Number(form.elements.retention_days.value),
      recovery_threshold: Number(form.elements.recovery_threshold.value) || 1,
      is_active: form.elements.is_active.checked
    };

    try {
      if (form.elements.clear_request_options.checked) {
        payload.request_options = null;
      } else if (form.elements.request_options.value.trim()) {
        payload.request_options = JSON.parse(form.elements.request_options.value);
      }
    } catch {
      elements.formError.textContent = "Request options must contain valid JSON.";
      elements.formError.hidden = false;
      return;
    }

    if (form.elements.url.disabled) {
      delete payload.url;
      delete payload.method;
    }
    var editingId = state.editingId;
    elements.saveButton.disabled = true;
    elements.saveButton.textContent = "Saving...";

    var request = editingId
      ? api("/endpoints/" + editingId, { method: "PATCH", body: payload })
      : api("/endpoints", { method: "POST", body: payload });

    request
      .then(function () {
        closeDialog("endpoint-dialog");
        toast(editingId ? "Endpoint updated." : "Endpoint added.");
        return load();
      })
      .catch(function (error) {
        elements.formError.textContent = error.message;
        elements.formError.hidden = false;
      })
      .finally(function () {
        elements.saveButton.disabled = false;
        elements.saveButton.textContent = editingId ? "Save changes" : "Save endpoint";
      });
  }

  function runCheck(endpoint, button) {
    button.disabled = true;
    button.textContent = "Checking...";
    api("/endpoints/" + endpoint.id + "/check", { method: "POST" })
      .then(function (result) {
        toast(result.is_success ? "Check passed." : "Check failed. See the endpoint details.");
        return load();
      })
      .catch(function (error) {
        toast(error.message, true);
      })
      .finally(function () {
        button.disabled = false;
        button.textContent = "Check now";
      });
  }

  function toggleEndpoint(endpoint, button) {
    var nextActive = !endpoint.is_active;
    button.disabled = true;
    api("/endpoints/" + endpoint.id, {
      method: "PATCH",
      body: { is_active: nextActive }
    })
      .then(function () {
        toast(nextActive ? "Monitoring resumed." : "Monitoring paused.");
        return load();
      })
      .catch(function (error) {
        toast(error.message, true);
      })
      .finally(function () {
        button.disabled = false;
      });
  }

  function deleteEndpoint(endpoint) {
    var confirmed = window.confirm(
      'Delete "' + endpoint.name + '"? Its check history will be removed too.'
    );
    if (!confirmed) {
      return;
    }

    api("/endpoints/" + endpoint.id, { method: "DELETE" })
      .then(function () {
        toast("Endpoint deleted.");
        return load();
      })
      .catch(function (error) {
        toast(error.message, true);
      });
  }

  function renderResponseTimeSvg(checks, deployments) {
    if (!checks.length) return "";
    var width = 320;
    var height = 80;
    var padding = { top: 10, right: 10, bottom: 15, left: 25 };
    var chartW = width - padding.left - padding.right;
    var chartH = height - padding.top - padding.bottom;

    var validValues = checks.map(function (c) {
      return c.response_time_ms === null ? 0 : c.response_time_ms;
    });
    var maxVal = Math.max.apply(null, validValues);
    if (maxVal <= 0) maxVal = 100;

    var first = Date.parse(checks[0].checked_at);
    var last = Date.parse(checks.at(-1).checked_at);
    function xAt(timestamp) {
      return padding.left + (timestamp - first) / Math.max(1, last - first) * chartW;
    }
    var points = checks.map(function (c) {
      var x = xAt(Date.parse(c.checked_at));
      var val = c.response_time_ms === null ? 0 : c.response_time_ms;
      var y = padding.top + chartH - (val / maxVal) * chartH;
      return { x: x, y: y, val: val, check: c };
    });

    var pathD = "M " + points.map(function (p) { return p.x.toFixed(1) + " " + p.y.toFixed(1); }).join(" L ");
    var areaD = pathD + " L " + (padding.left + chartW) + " " + (padding.top + chartH) + " L " + padding.left + " " + (padding.top + chartH) + " Z";

    var dots = points.map(function (p) {
      var tooltip = escapeHtml(formatTimestamp(p.check.checked_at) + " · " + p.val + " ms");
      return '<circle class="sparkline-dot" cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="3">' +
             '<title>' + tooltip + '</title></circle>';
    }).join("");

    var markers = deployments.filter(function (deployment) {
      var at = Date.parse(deployment.deployed_at);
      return at >= first && at <= last;
    }).map(function (deployment) {
      var x = xAt(Date.parse(deployment.deployed_at)).toFixed(1);
      return '<line class="deployment-line" x1="' + x + '" x2="' + x +
        '" y1="' + padding.top + '" y2="' + (padding.top + chartH) + '"><title>' +
        escapeHtml(deployment.label + " · " + formatTimestamp(deployment.deployed_at)) + '</title></line>';
    }).join("");
    return '<svg viewBox="0 0 ' + width + ' ' + height + '" preserveAspectRatio="none">' +
      '<line class="sparkline-grid" x1="' + padding.left + '" y1="' + padding.top + '" x2="' + (padding.left + chartW) + '" y2="' + padding.top + '" />' +
      '<line class="sparkline-grid" x1="' + padding.left + '" y1="' + (padding.top + chartH) + '" x2="' + (padding.left + chartW) + '" y2="' + (padding.top + chartH) + '" />' +
      '<text x="' + (padding.left - 4) + '" y="' + (padding.top + 4) + '" font-size="9" fill="var(--muted)" text-anchor="end">' + maxVal + '</text>' +
      '<text x="' + (padding.left - 4) + '" y="' + (padding.top + chartH) + '" font-size="9" fill="var(--muted)" text-anchor="end">0</text>' +
      '<path class="sparkline-area" d="' + areaD + '" />' +
      '<path class="sparkline-path" d="' + pathD + '" />' +
      dots + markers +
      '</svg>';
  }

  function renderHistory(endpoint, checks, deployments) {
    var status = statusFor(endpoint);
    var successCount = checks.filter(function (check) {
      return check.is_success;
    }).length;
    var uptime = checks.length
      ? ((successCount / checks.length) * 100).toFixed(1) + "%"
      : "—";

    var durations = checks
      .map(function (check) {
        return check.response_time_ms;
      })
      .filter(function (val) {
        return typeof val === "number" && val > 0;
      });
    var avgRt = durations.length
      ? Math.round(durations.reduce(function (a, b) { return a + b; }, 0) / durations.length)
      : 0;

    if (elements.rtAvg) elements.rtAvg.textContent = "Avg: " + (avgRt ? avgRt + " ms" : "—");
    if (elements.uptimeAvg) elements.uptimeAvg.textContent = "Uptime: " + uptime;

    elements.historySummary.innerHTML =
      "<span><strong>" + checks.length + "</strong> recent checks</span>" +
      "<span><strong>" + successCount + "</strong> passed</span>" +
      "<span><strong>" + (checks.length - successCount) + "</strong> failed</span>" +
      "<span>Window uptime <strong>" + uptime + "</strong></span>" +
      (status && status.last_checked_at
        ? "<span>Last check <strong>" + escapeHtml(formatTimestamp(status.last_checked_at)) + "</strong></span>"
        : "");

    if (checks.length === 0) {
      if (elements.responseTimeChart) elements.responseTimeChart.innerHTML = '<p class="history-empty" style="font-size:11px">No check data</p>';
      elements.historyChart.innerHTML = "";
      elements.historyList.innerHTML =
        '<p class="history-empty">No checks have run yet. Use "Check now" to run one immediately.</p>';
      return;
    }

    var chronological = checks.slice().reverse();
    if (elements.responseTimeChart) {
      elements.responseTimeChart.innerHTML = renderResponseTimeSvg(chronological.filter(function (check) { return check.response_time_ms !== null; }), deployments);
    }

    elements.historyChart.innerHTML = chronological
      .map(function (check) {
        return (
          '<span class="uptime-bar' + (check.is_success ? "" : " failed") + '" ' +
            'style="height:' + (check.is_success ? '80%' : '100%') + '" ' +
            'title="' + escapeHtml(formatTimestamp(check.checked_at) + " · " + (check.is_success ? "Passed" : (check.error || "Failed"))) + '"></span>'
        );
      })
      .join("");

    elements.historyList.innerHTML = checks
      .map(function (check) {
        return (
          '<div class="history-row">' +
            '<span class="status-dot ' + (check.is_success ? "up" : "down") + '" aria-hidden="true"></span>' +
            '<span class="history-time" title="' + escapeHtml(formatTimestamp(check.checked_at)) + '">' + escapeHtml(formatTimestamp(check.checked_at)) + "</span>" +
            '<span class="history-code">' + (check.status_code === null ? "—" : check.status_code) + "</span>" +
            '<span class="history-duration">' + formatResponseTime(check.response_time_ms) + "</span>" +
            '<span class="history-error" title="' + escapeHtml(check.error || "") + '">' + escapeHtml(check.error || "—") + "</span>" +
          "</div>"
        );
      })
      .join("");
  }

  function incidentDuration(seconds) {
    var parts = [];
    var days = Math.floor(seconds / 86400);
    var hours = Math.floor(seconds / 3600) % 24;
    var minutes = Math.floor(seconds / 60) % 60;
    if (days) parts.push(days + "d");
    if (hours) parts.push(hours + "h");
    if (minutes) parts.push(minutes + "m");
    parts.push(seconds % 60 + "s");
    return parts.join(" ");
  }

  function renderIncidents(list, incidents) {
    if (!incidents.length) {
      list.textContent = "No incidents in retained check history.";
      return;
    }

    list.innerHTML = incidents.map(function (incident) {
      var recovered = Boolean(incident.recovered_at);
      var start = incident.started_within_history ? "First failed check" : "Earliest recorded failure";
      return '<article class="incident-row">' +
        '<div class="incident-heading"><strong>' +
          (recovered ? "Recovered" : "No recovery recorded") +
        '</strong><span>' + incidentDuration(incident.duration_seconds) + ' observed</span></div>' +
        '<div>' + start + ' · ' + escapeHtml(formatTimestamp(incident.first_failed_at)) + '</div>' +
        (recovered ? '<div>First passing check · ' + escapeHtml(formatTimestamp(incident.recovered_at)) + '</div>' : '') +
        (incident.first_error ? '<div>' + escapeHtml(incident.first_error) + '</div>' : '') +
        '</article>';
    }).join("");
  }

  var historyRequest = 0;
  var metricsRequest = 0;
  var historyEndpoint;
  var historyRange = document.getElementById("history-range");

  async function loadRangeMetrics() {
    var request = ++metricsRequest;
    var output = document.getElementById("range-metrics");
    output.textContent = "Loading metrics…";
    try {
      var metrics = await api("/endpoints/" + historyEndpoint.id + "/metrics?hours=" + historyRange.value);
      if (request !== metricsRequest) return;
      output.textContent = metrics.total_checks
        ? "Availability " + formatUptime(metrics.uptime_percentage) +
          " · " + metrics.total_checks + " checks · p50 " + formatResponseTime(metrics.p50_ms) +
          " · p95 " + formatResponseTime(metrics.p95_ms) + " · p99 " + formatResponseTime(metrics.p99_ms)
        : "Unknown · no checks in this range.";
    } catch (error) {
      if (request === metricsRequest) output.textContent = error.message;
    }
  }

  historyRange.addEventListener("change", loadRangeMetrics);

  function openHistory(endpoint) {
    var request = ++historyRequest;
    historyEndpoint = endpoint;
    var expiry = statusFor(endpoint)?.certificate_expires_at;
    document.getElementById("certificate-expiry").textContent = expiry
      ? "TLS certificate expires " + formatTimestamp(expiry) : "TLS certificate expiry unavailable.";
    var historyPath = "/endpoints/" + endpoint.id;
    document.getElementById("export-checks").href = API_PREFIX + historyPath + "/checks.csv";
    document.getElementById("export-incidents").href = API_PREFIX + historyPath + "/incidents.csv";
    var reliabilityOutput = document.getElementById("reliability-metrics");
    reliabilityOutput.textContent = "";
    if (endpoint.slo_target_percentage !== null) {
      api(historyPath + "/reliability").then(function (data) {
        if (request !== historyRequest) return;
        reliabilityOutput.textContent = "SLO " + data.target_percentage + "% over " + data.window_hours + " hours · " +
          (data.target_met === null ? "No observations" :
            (data.target_met ? "On target" : "Below target") + " · " +
            Number(data.remaining_error_budget).toFixed(2) + " checks of error budget remaining · " +
            data.maintenance_checks + " checks during maintenance");
      }).catch(function (error) {
        if (request === historyRequest) reliabilityOutput.textContent = error.message;
      });
    }
    loadRangeMetrics();
    var incidentList = document.getElementById("incident-list");
    incidentList.textContent = "Loading incidents…";
    elements.historyTitle.textContent = "Checks for " + endpoint.name;
    elements.historySummary.textContent = "Loading check history...";
    elements.historyChart.innerHTML = "";
    elements.historyList.innerHTML = "";
    elements.historyDialog.showModal();

    api("/endpoints/" + endpoint.id + "/incidents")
      .then(function (incidents) {
        if (request === historyRequest) renderIncidents(incidentList, incidents);
      })
      .catch(function (error) {
        if (request === historyRequest) incidentList.textContent = error.message;
      });

    document.getElementById("chart-deployments").textContent = "";
    Promise.all([
      api("/endpoints/" + endpoint.id + "/checks?limit=50"),
      api("/endpoints/" + endpoint.id + "/deployments").catch(function () { return []; })
    ]).then(function ([checks, deployments]) {
        if (request === historyRequest) {
          renderHistory(endpoint, checks, deployments);
          if (checks.length) {
            var first = Date.parse(checks.at(-1).checked_at);
            var last = Date.parse(checks[0].checked_at);
            var markers = deployments.filter(function (marker) {
              var at = Date.parse(marker.deployed_at);
              return at >= first && at <= last;
            });
            document.getElementById("chart-deployments").textContent = markers.length
              ? "Deployments in chart: " + markers.map(function (marker) {
                return marker.label + " · " + formatTimestamp(marker.deployed_at);
              }).join(" | ")
              : "";
          }
        }
      })
      .catch(function (error) {
        if (request !== historyRequest) return;
        elements.historySummary.textContent = "";
        elements.historyList.innerHTML =
          '<p class="history-empty">' + escapeHtml(error.message) + "</p>";
      });
  }

  elements.refresh.addEventListener("click", function () {
    load();
  });

  elements.retry.addEventListener("click", function () {
    load();
  });

  elements.add.addEventListener("click", function () {
    openEndpointDialog(null);
  });

  elements.emptyAdd.addEventListener("click", function () {
    openEndpointDialog(null);
  });

  elements.search.addEventListener("input", function (event) {
    state.search = event.target.value;
    render();
  });

  elements.filter.addEventListener("change", function (event) {
    state.filter = event.target.value;
    render();
  });

  elements.group.addEventListener("change", function (event) {
    state.group = event.target.value;
    render();
  });

  elements.sort.addEventListener("change", function (event) {
    state.sort = event.target.value;
    render();
  });

  summaryFilters.forEach(function (button) {
    button.addEventListener("click", function () {
      state.filter = button.dataset.summaryFilter;
      elements.filter.value = state.filter;
      render();
    });
  });

  document.getElementById("clear-filters").addEventListener("click", function () {
    elements.search.value = state.search = "";
    elements.filter.value = state.filter = "all";
    elements.group.value = state.group = "";
    render();
    elements.search.focus();
  });

  elements.endpointForm.addEventListener("submit", submitEndpoint);
  elements.endpointDialog.addEventListener("close", function () {
    elements.endpointForm.elements.request_options.value = "";
  });

  elements.list.addEventListener("click", function (event) {
    var button = event.target.closest("[data-action]");
    if (!button) {
      return;
    }
    var endpoint = state.endpoints.find(function (item) {
      return String(item.id) === String(button.dataset.id);
    });
    if (!endpoint) {
      return;
    }

    if (button.dataset.action === "check") {
      runCheck(endpoint, button);
    } else if (button.dataset.action === "history") {
      openHistory(endpoint);
    } else if (button.dataset.action === "maintenance") {
      window.monitorOperations.openMaintenance(endpoint);
    } else if (button.dataset.action === "edit") {
      if (endpoint.kind === "heartbeat") window.heartbeatUI.open(endpoint, load);
      else openEndpointDialog(endpoint);
    } else if (button.dataset.action === "toggle") {
      toggleEndpoint(endpoint, button);
    } else if (button.dataset.action === "delete") {
      deleteEndpoint(endpoint);
    }
  });

  document.querySelectorAll("[data-close-dialog]").forEach(function (button) {
    button.addEventListener("click", function () {
      closeDialog(button.dataset.closeDialog);
    });
  });

  document.querySelectorAll("dialog").forEach(function (dialog) {
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) {
        closeDialog(dialog.id);
      }
    });
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) {
      load({ quiet: true });
    }
  });

  window.setInterval(function () {
    if (!document.hidden && !document.querySelector("dialog[open]")) {
      load({ quiet: true });
    }
  }, REFRESH_MS);

  var accountMenu = document.querySelector(".account-menu");
  var accountMenuToggle = accountMenu.querySelector("summary");
  document.addEventListener("click", function (event) {
    if (accountMenu.open && !accountMenu.contains(event.target)) accountMenu.open = false;
  });
  accountMenu.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && accountMenu.open) {
      accountMenu.open = false;
      accountMenuToggle.focus();
    }
  });

  function openAccountDialog(dialog) {
    accountMenu.open = false;
    accountMenuToggle.focus();
    dialog.showModal();
  }

  document.getElementById("agents-button").addEventListener("click", function () {
    openAccountDialog(document.getElementById("agents-dialog"));
    window.agentUI.open(load);
  });

  document.getElementById("integrations-button").addEventListener("click", function () {
    openAccountDialog(document.getElementById("integrations-dialog"));
    window.workspaceSettings.openIntegrations(state.endpoints);
  });

  document.getElementById("status-settings-button").addEventListener("click", function () {
    openAccountDialog(document.getElementById("status-settings-dialog"));
    window.workspaceSettings.openStatus();
  });

  document.getElementById("diagnostics-button").addEventListener("click", async function () {
    var content = document.getElementById("diagnostics-content");
    content.textContent = "Loading diagnostics…";
    openAccountDialog(document.getElementById("diagnostics-dialog"));
    try {
      var response = await fetch("/operations/diagnostics");
      if (!response.ok) throw new Error("Could not load diagnostics.");
      var data = await response.json();
      var lines = [
        data.scheduler_enabled ? "Scheduler enabled" : "Scheduler disabled",
        data.queued_checks + " queued checks · " + data.overdue_monitors + " overdue monitors",
        "Maximum lag " + data.max_lag_seconds + " s · " + data.estimated_missed_checks + " estimated missed checks",
        data.pending_alerts + " pending alerts · " + data.failed_alerts + " failed alerts"
      ];
      lines.push(...data.workers.map(function (worker) {
        return "Worker " + worker.id.slice(0, 8) + " · " + worker.state + " · " + formatTimestamp(worker.last_poll_at);
      }));
      if (!data.workers.length) lines.push("No recent scheduler workers.");
      content.replaceChildren(...lines.map(function (line) {
        var item = document.createElement("p");
        item.textContent = line;
        return item;
      }));
    } catch (error) {
      content.textContent = error.message;
    }
  });

  var usersDialog = document.getElementById("users-dialog");
  var usersList = document.getElementById("users-list");
  var usersStatus = document.getElementById("users-status");
  var loadingUsers = false;

  async function requestUsers(path, options) {
    var response = await fetch("/auth/users" + path, options);
    if (response.status === 401) window.location.replace("/login");
    if (response.status === 204) return null;
    var data = await response.json();
    if (!response.ok) throw new Error(errorMessage(data, response.status));
    return data;
  }

  async function loadUsers() {
    if (loadingUsers) return;
    loadingUsers = true;
    usersList.replaceChildren();
    usersStatus.textContent = "Loading accounts…";
    try {
      var users = await requestUsers("");
      users.forEach(function (user) {
        var row = document.createElement("li");
        var identity = document.createElement("div");
        var name = document.createElement("strong");
        var role = document.createElement("small");
        name.textContent = user.username;
        role.textContent = user.can_delete ? user.role : user.role + " · You";
        identity.append(name, role);
        row.append(identity);

        if (user.can_delete) {
          var button = document.createElement("button");
          button.type = "button";
          button.className = "action-button danger";
          button.textContent = "Delete";
          button.setAttribute("aria-label", "Delete account " + user.username);
          button.addEventListener("click", async function () {
            if (!window.confirm("Delete " + user.username + " and all endpoints and check history owned by this account? This cannot be undone.")) return;
            button.disabled = true;
            usersStatus.textContent = "Deleting account…";
            try {
              await requestUsers("/" + user.id, {method: "DELETE"});
              row.remove();
              usersStatus.textContent = "Account deleted.";
            } catch (error) {
              usersStatus.textContent = error.message;
              button.disabled = false;
            }
          });
          row.append(button);
        }
        usersList.append(row);
      });
      usersStatus.textContent = users.length ? "" : "No accounts found.";
    } catch (error) {
      usersStatus.textContent = error.message;
    } finally {
      loadingUsers = false;
    }
  }

  document.getElementById("users-button").addEventListener("click", function () {
    openAccountDialog(usersDialog);
    loadUsers();
  });

  var inviteDialog = document.getElementById("invite-dialog");
  var inviteForm = document.getElementById("invite-form");
  var inviteStatus = document.getElementById("invite-status");
  var inviteResult = document.getElementById("invite-result");
  var inviteLink = document.getElementById("invite-link");

  function showInviteStatus(message) {
    inviteStatus.textContent = message;
    inviteStatus.hidden = !message;
  }

  document.getElementById("invite-button").addEventListener("click", function () {
    inviteForm.reset();
    inviteLink.value = "";
    inviteResult.hidden = true;
    showInviteStatus("");
    openAccountDialog(inviteDialog);
  });

  inviteForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    var submit = inviteForm.querySelector('[type="submit"]');
    submit.disabled = true;
    inviteResult.hidden = true;
    showInviteStatus("");
    try {
      var response = await fetch("/auth/invites", {
        method: "POST"
      });
      if (response.status === 401) {
        window.location.replace("/login");
        return;
      }
      var data = await response.json();
      if (!response.ok) throw new Error(errorMessage(data, response.status));
      inviteLink.value = window.location.origin + "/register#invite=" + data.token;
      inviteResult.hidden = false;
    } catch (error) {
      showInviteStatus(error.message);
    } finally {
      submit.disabled = false;
    }
  });

  document.getElementById("copy-invite").addEventListener("click", async function () {
    try {
      await navigator.clipboard.writeText(inviteLink.value);
      showInviteStatus("Invitation link copied.");
    } catch {
      inviteLink.select();
      showInviteStatus("Copy the selected link to share it.");
    }
  });

  document.getElementById("logout-button").addEventListener("click", async function () {
    try {
      var response = await fetch("/auth/logout", {method: "POST"});
      if (response.ok) {
        window.location.replace("/login");
        return;
      }
    } catch (error) {
      console.error(error);
    }
    window.alert("Sign-out failed. Try again.");
  });

  fetch("/auth/me")
    .then(function (response) {
      if (response.status === 401) {
        window.location.replace("/login");
        return null;
      }
      if (!response.ok) {
        throw new Error("Could not load account permissions.");
      }
      return response.json();
    })
    .then(function (account) {
      if (account) {
        document.body.dataset.role = account.role;
        document.getElementById("account-name").textContent = account.username;
        var workspace = document.getElementById("workspace-label");
        workspace.textContent = account.role === "member" ? "Private workspace" : account.role === "admin"
          ? "Shared workspace · Administrator" : "Shared workspace · Viewer";
        workspace.hidden = false;
      }
    })
    .catch(function (error) {
      console.error(error);
    });

  document.getElementById("add-heartbeat").addEventListener("click", function () {
    window.heartbeatUI.open(null, load);
  });
  load();
  var route = new URLSearchParams(window.location.search);
  if (/^\d+$/.test(route.get("endpoint")) && /^\d+$/.test(route.get("incident"))) {
    window.monitorOperations.openIncident(route.get("endpoint"), route.get("incident"));
  }
})();
