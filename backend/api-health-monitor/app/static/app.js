(function () {
  "use strict";

  var API_PREFIX = "/api/v1";
  var REFRESH_MS = 15000;

  var state = {
    endpoints: [],
    statuses: new Map(),
    channels: [],
    users: [],
    session: null,
    authMode: "login",
    loading: false,
    editingId: null,
    historyEndpoint: null,
    statsHours: 24,
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
    statsRange: document.getElementById("stats-range"),
    statsSummary: document.getElementById("stats-summary"),
    uptimeChart: document.getElementById("uptime-chart"),
    responseChart: document.getElementById("response-chart"),
    incidentList: document.getElementById("incident-list"),
    bootScreen: document.getElementById("boot-screen"),
    appShell: document.getElementById("app-shell"),
    authScreen: document.getElementById("auth-screen"),
    authForm: document.getElementById("auth-form"),
    authTitle: document.getElementById("auth-title"),
    authHint: document.getElementById("auth-hint"),
    authNameField: document.getElementById("auth-name-field"),
    authError: document.getElementById("auth-error"),
    authSubmit: document.getElementById("auth-submit"),
    authSwitch: document.getElementById("auth-switch"),
    userChip: document.getElementById("user-chip"),
    logout: document.getElementById("logout-button"),
    alertsPanel: document.getElementById("alerts-panel"),
    channelList: document.getElementById("channel-list"),
    channelEmpty: document.getElementById("channel-empty"),
    addChannel: document.getElementById("add-channel-button"),
    channelDialog: document.getElementById("channel-dialog"),
    channelForm: document.getElementById("channel-form"),
    channelDialogTitle: document.getElementById("channel-dialog-title"),
    channelError: document.getElementById("channel-error"),
    channelSave: document.getElementById("channel-save-button"),
    channelTargetHint: document.getElementById("channel-target-hint"),
    teamPanel: document.getElementById("team-panel"),
    userList: document.getElementById("user-list"),
    addUser: document.getElementById("add-user-button"),
    userDialog: document.getElementById("user-dialog"),
    userForm: document.getElementById("user-form"),
    userError: document.getElementById("user-error"),
    userSave: document.getElementById("user-save-button"),
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
        if (
          response.status === 401 &&
          elements.authScreen.hidden &&
          state.session &&
          state.session.auth_enabled
        ) {
          window.setTimeout(function () {
            refreshSession().then(showAuthFor);
          }, 0);
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
    return value === null || value === undefined ? "—" : Math.round(value) + " ms";
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
          (isAdmin()
            ? '<button class="action-button" type="button" data-action="check" data-id="' + endpoint.id + '">Check now</button>'
            : "") +
          '<button class="action-button" type="button" data-action="history" data-id="' + endpoint.id + '">History</button>' +
          (isAdmin()
            ? '<button class="action-button" type="button" data-action="edit" data-id="' + endpoint.id + '">Edit</button>' +
              '<span class="action-spacer"></span>' +
              '<button class="action-button" type="button" data-action="toggle" data-id="' + endpoint.id + '">' + activeLabel + "</button>" +
              '<button class="action-button danger" type="button" data-action="delete" data-id="' + endpoint.id + '">Delete</button>'
            : "") +
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
      elements.endpointForm.elements.alerts_enabled.checked = endpoint.alerts_enabled;
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
      is_active: form.elements.is_active.checked,
      alerts_enabled: form.elements.alerts_enabled.checked
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
    state.historyEndpoint = endpoint;
    state.statsHours = 24;
    updateRangeButtons();
    elements.historyTitle.textContent = "Checks for " + endpoint.name;
    elements.historySummary.textContent = "Loading check history...";
    elements.historyChart.innerHTML = "";
    elements.historyList.innerHTML = "";
    elements.historyDialog.showModal();
    loadStats();

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


  function isAdmin() {
    if (!state.session) {
      return false;
    }
    if (!state.session.auth_enabled) {
      return true;
    }
    return Boolean(state.session.user && state.session.user.role === "admin");
  }

  function refreshSession() {
    return api("/auth/session").then(function (session) {
      state.session = session;
      return session;
    });
  }

  function showAuthScreen() {
    elements.authScreen.hidden = false;
    elements.appShell.hidden = true;
    elements.bootScreen.hidden = true;
  }

  function hideAuthScreen() {
    elements.authScreen.hidden = true;
    elements.appShell.hidden = false;
    elements.bootScreen.hidden = true;
  }

  function applyAuthHint() {
    var session = state.session || {};
    if (state.authMode === "register") {
      elements.authHint.textContent =
        session.has_users === false
          ? "No accounts exist yet. The first account becomes the administrator."
          : "New accounts start as viewers who can see endpoints and history. An administrator can promote them.";
      return;
    }
    elements.authHint.textContent = session.registration_open
      ? "Sign in, or create an account for your team."
      : "Use the account an administrator created for you.";
  }

  function setAuthMode(mode) {
    state.authMode = mode;
    var registering = mode === "register";
    elements.authTitle.textContent = registering ? "Create your account" : "Sign in";
    elements.authNameField.hidden = !registering;
    elements.authForm.elements.name.required = registering;
    elements.authForm.elements.password.autocomplete = registering
      ? "new-password"
      : "current-password";
    elements.authSubmit.textContent = registering ? "Create account" : "Sign in";
    elements.authSwitch.textContent = registering
      ? "Already have an account? Sign in"
      : "Create an account";
    elements.authError.hidden = true;
    applyAuthHint();
  }

  function showAuthFor(session) {
    state.session = session;
    var firstRun = !session.has_users;
    setAuthMode(firstRun ? "register" : "login");
    elements.authSwitch.hidden = !session.registration_open || firstRun;
    applyAuthHint();
    showAuthScreen();
  }

  function applySession(session) {
    var manager = isAdmin();
    var signedIn = Boolean(session.auth_enabled && session.authenticated);

    elements.userChip.hidden = !signedIn;
    elements.logout.hidden = !signedIn;
    if (signedIn) {
      elements.userChip.textContent =
        session.user.name + " · " + session.user.role;
    }

    elements.add.hidden = !manager;
    elements.emptyAdd.hidden = !manager;
    elements.addChannel.hidden = !manager;
    elements.alertsPanel.hidden = !manager;
    elements.teamPanel.hidden = !manager || !session.auth_enabled;
  }

  function startDashboard(session) {
    if (session.auth_enabled && !session.authenticated) {
      applySession(session);
      showAuthFor(session);
      return;
    }

    applySession(session);
    hideAuthScreen();
    load();
    loadChannels();
    loadUsers();
  }

  function submitAuth(event) {
    event.preventDefault();
    var form = elements.authForm;
    var registering = state.authMode === "register";
    elements.authError.hidden = true;

    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    var payload = {
      email: form.elements.email.value.trim(),
      password: form.elements.password.value
    };
    if (registering) {
      payload.name = form.elements.name.value.trim();
    }

    elements.authSubmit.disabled = true;
    api(registering ? "/auth/register" : "/auth/login", {
      method: "POST",
      body: payload
    })
      .then(function () {
        form.reset();
        return refreshSession();
      })
      .then(startDashboard)
      .catch(function (error) {
        elements.authError.textContent = error.message;
        elements.authError.hidden = false;
      })
      .finally(function () {
        elements.authSubmit.disabled = false;
      });
  }

  function logout() {
    api("/auth/logout", { method: "POST" })
      .catch(function () {
        return null;
      })
      .then(function () {
        state.endpoints = [];
        state.statuses = new Map();
        state.channels = [];
        state.users = [];
        elements.list.innerHTML = "";
        elements.channelList.innerHTML = "";
        elements.userList.innerHTML = "";
        return refreshSession();
      })
      .then(showAuthFor)
      .catch(function (error) {
        toast(error.message, true);
      });
  }

  function loadChannels() {
    if (!isAdmin()) {
      return Promise.resolve();
    }
    return api("/channels")
      .then(function (channels) {
        state.channels = channels;
        renderChannels();
      })
      .catch(function (error) {
        toast(error.message, true);
      });
  }

  function renderChannels() {
    elements.channelList.innerHTML = state.channels
      .map(function (channel) {
        var status = channel.last_error
          ? '<span class="channel-error" title="' + escapeHtml(channel.last_error) + '">' + escapeHtml(channel.last_error) + "</span>"
          : channel.last_sent_at
            ? '<span class="channel-meta">Last sent ' + escapeHtml(formatTimeAgo(channel.last_sent_at)) + "</span>"
            : '<span class="channel-meta">No alerts sent yet</span>';

        return (
          '<article class="channel-row' + (channel.is_enabled ? "" : " is-paused") + '">' +
            '<div class="channel-identity">' +
              "<h3>" + escapeHtml(channel.name) + "</h3>" +
              '<span class="channel-target">' + escapeHtml(channel.target) + "</span>" +
            "</div>" +
            '<span class="badge badge-' + (channel.kind === "email" ? "pending" : "up") + '">' +
              (channel.kind === "email" ? "Email" : "Webhook") +
            "</span>" +
            '<div class="channel-status">' + status + "</div>" +
            '<div class="channel-actions">' +
              '<button class="action-button" type="button" data-channel-action="test" data-channel-id="' + channel.id + '">Send test</button>' +
              '<button class="action-button" type="button" data-channel-action="toggle" data-channel-id="' + channel.id + '">' +
                (channel.is_enabled ? "Disable" : "Enable") +
              "</button>" +
              '<button class="action-button danger" type="button" data-channel-action="delete" data-channel-id="' + channel.id + '">Delete</button>' +
            "</div>" +
          "</article>"
        );
      })
      .join("");

    elements.channelEmpty.hidden = state.channels.length !== 0;
    elements.channelList.hidden = state.channels.length === 0;
  }

  function updateChannelHint() {
    var kind = elements.channelForm.elements.kind.value;
    elements.channelTargetHint.textContent =
      kind === "email"
        ? "Alerts are sent through the SMTP server configured on the server."
        : "Slack and Discord webhook URLs work; any URL that accepts JSON is also supported.";
    elements.channelForm.elements.target.placeholder =
      kind === "email"
        ? "oncall@example.com"
        : "https://hooks.slack.com/services/...";
  }

  function openChannelDialog() {
    elements.channelForm.reset();
    elements.channelForm.elements.kind.value = "webhook";
    elements.channelForm.elements.is_enabled.checked = true;
    elements.channelError.hidden = true;
    updateChannelHint();
    elements.channelDialog.showModal();
    window.setTimeout(function () {
      elements.channelForm.elements.name.focus();
    }, 30);
  }

  function submitChannel(event) {
    event.preventDefault();
    var form = elements.channelForm;

    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    var payload = {
      name: form.elements.name.value.trim(),
      kind: form.elements.kind.value,
      target: form.elements.target.value.trim(),
      is_enabled: form.elements.is_enabled.checked
    };

    elements.channelSave.disabled = true;
    api("/channels", { method: "POST", body: payload })
      .then(function () {
        closeDialog("channel-dialog");
        toast("Alert channel added.");
        return loadChannels();
      })
      .catch(function (error) {
        elements.channelError.textContent = error.message;
        elements.channelError.hidden = false;
      })
      .finally(function () {
        elements.channelSave.disabled = false;
      });
  }

  function handleChannelAction(action, channel, button) {
    if (action === "delete") {
      if (!window.confirm('Delete the alert channel "' + channel.name + '"?')) {
        return;
      }
      api("/channels/" + channel.id, { method: "DELETE" })
        .then(function () {
          toast("Alert channel deleted.");
          return loadChannels();
        })
        .catch(function (error) {
          toast(error.message, true);
        });
      return;
    }

    if (action === "toggle") {
      button.disabled = true;
      api("/channels/" + channel.id, {
        method: "PATCH",
        body: { is_enabled: !channel.is_enabled }
      })
        .then(function () {
          toast(channel.is_enabled ? "Channel disabled." : "Channel enabled.");
          return loadChannels();
        })
        .catch(function (error) {
          toast(error.message, true);
        })
        .finally(function () {
          button.disabled = false;
        });
      return;
    }

    if (action === "test") {
      button.disabled = true;
      button.textContent = "Sending...";
      api("/channels/" + channel.id + "/test", { method: "POST" })
        .then(function (result) {
          if (result.sent) {
            toast("Test alert sent.");
          } else {
            toast(result.error || "The test alert could not be delivered.", true);
          }
          return loadChannels();
        })
        .catch(function (error) {
          toast(error.message, true);
        })
        .finally(function () {
          button.disabled = false;
          button.textContent = "Send test";
        });
    }
  }


  function loadUsers() {
    if (!isAdmin() || !state.session || !state.session.auth_enabled) {
      return Promise.resolve();
    }
    return api("/auth/users")
      .then(function (users) {
        state.users = users;
        renderUsers();
      })
      .catch(function (error) {
        toast(error.message, true);
      });
  }

  function renderUsers() {
    var currentUserId = state.session.user ? state.session.user.id : null;

    elements.userList.innerHTML = state.users
      .map(function (user) {
        var isSelf = user.id === currentUserId;
        var options = ["admin", "viewer"]
          .map(function (role) {
            return (
              '<option value="' + role + '"' +
                (user.role === role ? " selected" : "") +
              ">" + role.charAt(0).toUpperCase() + role.slice(1) + "</option>"
            );
          })
          .join("");

        return (
          '<article class="user-row' + (user.is_active ? "" : " is-paused") + '">' +
            '<div class="user-identity">' +
              "<h3>" + escapeHtml(user.name) + (isSelf ? " (you)" : "") + "</h3>" +
              '<span class="user-email">' + escapeHtml(user.email) + "</span>" +
            "</div>" +
            '<span class="badge badge-' + (user.role === "admin" ? "up" : "paused") + '">' + escapeHtml(user.role) + "</span>" +
            '<select class="role-select" data-user-id="' + user.id + '" aria-label="Role for ' + escapeHtml(user.name) + '"' + (isSelf ? " disabled" : "") + ">" + options + "</select>" +
            '<button class="action-button" type="button" data-user-action="toggle" data-user-id="' + user.id + '"' + (isSelf ? " disabled" : "") + ">" +
              (user.is_active ? "Deactivate" : "Activate") +
            "</button>" +
          "</article>"
        );
      })
      .join("");
  }

  function openUserDialog() {
    elements.userForm.reset();
    elements.userForm.elements.role.value = "viewer";
    elements.userError.hidden = true;
    elements.userDialog.showModal();
    window.setTimeout(function () {
      elements.userForm.elements.name.focus();
    }, 30);
  }

  function submitUser(event) {
    event.preventDefault();
    var form = elements.userForm;

    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    elements.userSave.disabled = true;
    api("/auth/users", {
      method: "POST",
      body: {
        name: form.elements.name.value.trim(),
        email: form.elements.email.value.trim(),
        password: form.elements.password.value,
        role: form.elements.role.value
      }
    })
      .then(function () {
        closeDialog("user-dialog");
        toast("Account created.");
        return loadUsers();
      })
      .catch(function (error) {
        elements.userError.textContent = error.message;
        elements.userError.hidden = false;
      })
      .finally(function () {
        elements.userSave.disabled = false;
      });
  }

  function updateUser(userId, changes, successMessage, onDone) {
    api("/auth/users/" + userId, { method: "PATCH", body: changes })
      .then(function () {
        toast(successMessage);
        return loadUsers();
      })
      .catch(function (error) {
        toast(error.message, true);
        return loadUsers();
      })
      .finally(onDone || function () {});
  }

  function handleUserAction(action, user, button) {
    if (action === "toggle") {
      button.disabled = true;
      updateUser(
        user.id,
        { is_active: !user.is_active },
        user.is_active ? "Account deactivated." : "Account activated."
      );
    }
  }

  function formatDuration(seconds) {
    var total = Math.max(0, Math.round(seconds || 0));
    if (total < 60) {
      return total + "s";
    }
    var minutes = Math.round(total / 60);
    if (minutes < 60) {
      return minutes + "m";
    }
    var hours = Math.floor(minutes / 60);
    var rest = minutes % 60;
    return rest ? hours + "h " + rest + "m" : hours + "h";
  }

  function formatShortTime(value) {
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    });
  }

  function renderBars(container, buckets, valueKey, titleFor, scaledTo100) {
    var values = buckets.map(function (bucket) {
      return bucket[valueKey];
    });
    var max = Math.max.apply(
      null,
      values.filter(function (value) {
        return typeof value === "number";
      }).concat([0])
    );

    container.innerHTML = buckets
      .map(function (bucket, index) {
        var value = values[index];
        var scale = scaledTo100 ? 100 : max;
        var hasValue = typeof value === "number" && scale > 0;
        var height = hasValue ? Math.max(4, Math.round((value / scale) * 100)) : 4;
        var classes = "stats-bar";
        if (!hasValue) {
          classes += " empty";
        } else if (scaledTo100 && bucket.failures > 0) {
          classes += " has-failure";
        }
        return (
          '<span class="' + classes + '" style="height:' + height + '%" title="' +
            escapeHtml(titleFor(bucket, value)) + '"></span>'
        );
      })
      .join("");
  }

  function renderIncidents(incidents) {
    if (!incidents.length) {
      elements.incidentList.innerHTML =
        '<p class="incidents-empty">No incidents in this window.</p>';
      return;
    }

    elements.incidentList.innerHTML =
      '<p class="stats-chart-label">Incidents</p>' +
      incidents
        .map(function (incident) {
          var ended = incident.ended_at
            ? formatShortTime(incident.ended_at)
            : "ongoing";
          return (
            '<div class="incident-row">' +
              '<span class="status-dot down" aria-hidden="true"></span>' +
              '<span class="incident-time">' + escapeHtml(formatShortTime(incident.started_at)) + " → " + escapeHtml(ended) + "</span>" +
              '<span class="incident-duration">' + formatDuration(incident.duration_seconds) + "</span>" +
              '<span class="incident-error" title="' + escapeHtml(incident.error || "") + '">' + escapeHtml(incident.error || "No error detail") + "</span>" +
            "</div>"
          );
        })
        .join("");
  }

  function renderStats(stats) {
    elements.statsSummary.innerHTML =
      '<div><span class="metric-label">Uptime</span><span class="metric-value">' + formatUptime(stats.uptime_percentage) + "</span></div>" +
      '<div><span class="metric-label">Avg response</span><span class="metric-value">' + formatResponseTime(stats.avg_response_time_ms) + "</span></div>" +
      '<div><span class="metric-label">p95 response</span><span class="metric-value">' + formatResponseTime(stats.p95_response_time_ms) + "</span></div>" +
      '<div><span class="metric-label">Checks</span><span class="metric-value">' + stats.total_checks + "</span></div>";

    renderBars(
      elements.uptimeChart,
      stats.buckets,
      "uptime_percentage",
      function (bucket, value) {
        return (
          formatShortTime(bucket.start) +
          " · " +
          (typeof value === "number" ? value.toFixed(1) + "% uptime" : "no checks") +
          " · " + bucket.checks + " checks"
        );
      },
      true
    );

    renderBars(
      elements.responseChart,
      stats.buckets,
      "avg_response_time_ms",
      function (bucket, value) {
        return formatShortTime(bucket.start) + " · " + formatResponseTime(value);
      },
      false
    );

    renderIncidents(stats.incidents);
  }

  function loadStats() {
    var endpoint = state.historyEndpoint;
    if (!endpoint) {
      return Promise.resolve();
    }

    elements.statsSummary.textContent = "Loading statistics...";
    elements.uptimeChart.innerHTML = "";
    elements.responseChart.innerHTML = "";
    elements.incidentList.innerHTML = "";

    return api("/endpoints/" + endpoint.id + "/stats?hours=" + state.statsHours)
      .then(renderStats)
      .catch(function (error) {
        elements.statsSummary.textContent = error.message;
      });
  }

  function updateRangeButtons() {
    elements.statsRange.querySelectorAll("[data-hours]").forEach(function (button) {
      button.classList.toggle(
        "is-active",
        Number(button.dataset.hours) === state.statsHours
      );
    });
  }

  function bootstrap() {
    refreshSession()
      .then(startDashboard)
      .catch(function (error) {
        elements.bootScreen.hidden = true;
        elements.appShell.hidden = false;
        showError(error.message);
      });
  }

  elements.refresh.addEventListener("click", function () {
    load();
  });

  elements.retry.addEventListener("click", function () {
    load();
  });

  elements.authForm.addEventListener("submit", submitAuth);

  elements.authSwitch.addEventListener("click", function () {
    setAuthMode(state.authMode === "register" ? "login" : "register");
    elements.authSwitch.hidden = false;
    elements.authError.hidden = true;
  });

  elements.logout.addEventListener("click", logout);

  elements.addChannel.addEventListener("click", openChannelDialog);

  elements.channelForm.addEventListener("submit", submitChannel);

  elements.channelForm.elements.kind.addEventListener(
    "change",
    updateChannelHint
  );

  elements.channelList.addEventListener("click", function (event) {
    var button = event.target.closest("[data-channel-action]");
    if (!button) {
      return;
    }
    var channel = state.channels.find(function (item) {
      return String(item.id) === String(button.dataset.channelId);
    });
    if (channel) {
      handleChannelAction(button.dataset.channelAction, channel, button);
    }
  });

  elements.addUser.addEventListener("click", openUserDialog);

  elements.userForm.addEventListener("submit", submitUser);

  elements.userList.addEventListener("click", function (event) {
    var button = event.target.closest("[data-user-action]");
    if (!button) {
      return;
    }
    var user = state.users.find(function (item) {
      return String(item.id) === String(button.dataset.userId);
    });
    if (user) {
      handleUserAction(button.dataset.userAction, user, button);
    }
  });

  elements.userList.addEventListener("change", function (event) {
    var select = event.target.closest("[data-user-id]");
    if (!select) {
      return;
    }
    var user = state.users.find(function (item) {
      return String(item.id) === String(select.dataset.userId);
    });
    if (user) {
      updateUser(
        user.id,
        { role: select.value },
        "Role updated to " + select.value + ".",
        function () {
          select.disabled = false;
        }
      );
      select.disabled = true;
    }
  });

  elements.statsRange.addEventListener("click", function (event) {
    var button = event.target.closest("[data-hours]");
    if (!button) {
      return;
    }
    state.statsHours = Number(button.dataset.hours);
    updateRangeButtons();
    loadStats();
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
    if (!document.hidden && elements.authScreen.hidden) {
      load({ quiet: true });
    }
  });

  window.setInterval(function () {
    if (
      !document.hidden &&
      elements.authScreen.hidden &&
      !document.querySelector("dialog[open]")
    ) {
      load({ quiet: true });
    }
  }, REFRESH_MS);

  bootstrap();
})();
