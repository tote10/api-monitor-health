(function () {
  "use strict";

  var API_PREFIX = "/api/v1";
  var REFRESH_MS = 15000;

  var state = {
    endpoints: [],
    statuses: new Map(),
    loading: false,
    editingId: null,
    search: "",
    filter: "all"
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
    pending: "Pending",
    paused: "Paused"
  };

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
    var activeLabel = endpoint.is_active ? "Pause" : "Resume";
    var uptime = status ? formatUptime(status.uptime_percentage) : "—";
    var checkCount = status ? status.total_checks : 0;

    return (
      '<article class="endpoint-card status-' + kind + '" data-endpoint-id="' + endpoint.id + '">' +
        '<div class="card-top">' +
          '<div class="endpoint-identity">' +
            '<span class="status-dot ' + kind + '" aria-hidden="true"></span>' +
            "<div>" +
              '<h3 class="endpoint-name">' + escapeHtml(endpoint.name) + "</h3>" +
              '<a class="endpoint-url" href="' + escapeHtml(endpoint.url) + '" target="_blank" rel="noreferrer noopener" title="' + escapeHtml(endpoint.url) + '">' +
                escapeHtml(endpoint.url) +
              "</a>" +
            "</div>" +
          "</div>" +
          '<span class="badge badge-' + kind + '">' + statusLabels[kind] + "</span>" +
        "</div>" +
        '<div class="card-metrics">' +
          '<div><span class="metric-label">Last check</span><span class="metric-value" title="' + escapeHtml(formatTimestamp(lastChecked)) + '">' + formatTimeAgo(lastChecked) + "</span></div>" +
          '<div><span class="metric-label">Response</span><span class="metric-value">' + formatResponseTime(status ? status.last_response_time_ms : null) + "</span></div>" +
          '<div><span class="metric-label">Uptime</span><span class="metric-value" title="' + checkCount + ' checks">' + uptime + "</span></div>" +
          '<div><span class="metric-label">Schedule</span><span class="metric-value">' + formatInterval(endpoint.interval_seconds) + "</span></div>" +
        "</div>" +
        (error
          ? '<div class="card-error">' + warningIcon() + "<span>" + escapeHtml(error) + "</span></div>"
          : "") +
        '<div class="card-actions">' +
          '<button class="action-button" type="button" data-action="check" data-id="' + endpoint.id + '">Check now</button>' +
          '<button class="action-button" type="button" data-action="history" data-id="' + endpoint.id + '">History</button>' +
          '<button class="action-button" type="button" data-action="edit" data-id="' + endpoint.id + '">Edit</button>' +
          '<span class="action-spacer"></span>' +
          '<button class="action-button" type="button" data-action="toggle" data-id="' + endpoint.id + '">' + activeLabel + "</button>" +
          '<button class="action-button danger" type="button" data-action="delete" data-id="' + endpoint.id + '">Delete</button>' +
        "</div>" +
      "</article>"
    );
  }

  function matchesFilters(endpoint) {
    var query = state.search.trim().toLowerCase();
    if (query) {
      var haystack = (endpoint.name + " " + endpoint.url).toLowerCase();
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
    var up = 0;
    var down = 0;
    var paused = 0;

    state.endpoints.forEach(function (endpoint) {
      var kind = statusClass(endpoint);
      if (kind === "up") {
        up += 1;
      } else if (kind === "down") {
        down += 1;
      } else if (kind === "paused") {
        paused += 1;
      }
    });

    elements.summaryTotal.textContent = String(state.endpoints.length);
    elements.summaryUp.textContent = String(up);
    elements.summaryDown.textContent = String(down);
    elements.summaryPaused.textContent = String(paused);

    var visible = state.endpoints.filter(matchesFilters);
    elements.list.innerHTML = visible.map(renderCard).join("");

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

    return api("/endpoints")
      .then(function (endpoints) {
        return Promise.all(
          endpoints.map(function (endpoint) {
            return api("/endpoints/" + endpoint.id + "/status").catch(function () {
              return null;
            });
          })
        ).then(function (statuses) {
          state.endpoints = endpoints;
          state.statuses = new Map();
          endpoints.forEach(function (endpoint, index) {
            if (statuses[index]) {
              state.statuses.set(endpoint.id, statuses[index]);
            }
          });
          elements.errorBanner.hidden = true;
          elements.loading.hidden = true;
          render();
        });
      })
      .catch(function (error) {
        showError(error.message);
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
      elements.endpointForm.elements.is_active.checked = endpoint.is_active;
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
      is_active: form.elements.is_active.checked
    };

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

  function renderHistory(endpoint, checks) {
    var status = statusFor(endpoint);
    var successCount = checks.filter(function (check) {
      return check.is_success;
    }).length;
    var uptime = checks.length
      ? ((successCount / checks.length) * 100).toFixed(1) + "%"
      : "—";

    elements.historySummary.innerHTML =
      "<span><strong>" + checks.length + "</strong> recent checks</span>" +
      "<span><strong>" + successCount + "</strong> passed</span>" +
      "<span><strong>" + (checks.length - successCount) + "</strong> failed</span>" +
      "<span>Window uptime <strong>" + uptime + "</strong></span>" +
      (status && status.last_checked_at
        ? "<span>Last check <strong>" + escapeHtml(formatTimestamp(status.last_checked_at)) + "</strong></span>"
        : "");

    if (checks.length === 0) {
      elements.historyChart.innerHTML = "";
      elements.historyList.innerHTML =
        '<p class="history-empty">No checks have run yet. Use "Check now" to run one immediately.</p>';
      return;
    }

    var chronological = checks.slice().reverse();
    var durations = chronological
      .map(function (check) {
        return check.response_time_ms === null ? 0 : check.response_time_ms;
      })
      .filter(function (value) {
        return value > 0;
      });
    var maxDuration = durations.length ? Math.max.apply(null, durations) : 0;

    elements.historyChart.innerHTML = chronological
      .map(function (check) {
        var duration = check.response_time_ms === null ? 0 : check.response_time_ms;
        var height = maxDuration
          ? Math.max(6, Math.round((duration / maxDuration) * 100))
          : 6;
        return (
          '<span class="history-bar' + (check.is_success ? "" : " failed") + '" ' +
            'style="height:' + height + '%" ' +
            'title="' + escapeHtml(formatTimestamp(check.checked_at) + " · " + (check.is_success ? "passed" : "failed")) + '"></span>'
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

  function openHistory(endpoint) {
    elements.historyTitle.textContent = "Checks for " + endpoint.name;
    elements.historySummary.textContent = "Loading check history...";
    elements.historyChart.innerHTML = "";
    elements.historyList.innerHTML = "";
    elements.historyDialog.showModal();

    api("/endpoints/" + endpoint.id + "/checks?limit=50")
      .then(function (checks) {
        renderHistory(endpoint, checks);
      })
      .catch(function (error) {
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

  elements.endpointForm.addEventListener("submit", submitEndpoint);

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
    } else if (button.dataset.action === "edit") {
      openEndpointDialog(endpoint);
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

  load();
})();
