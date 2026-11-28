"use strict";

const form = document.getElementById("login-form");
const submit = form.querySelector("button");
const errorMessage = document.getElementById("login-error");

const query = new URLSearchParams(window.location.search);

if (query.get("registered") === "1") {
  document.getElementById("account-created").hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submit.disabled = true;
  errorMessage.hidden = true;

  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(Object.fromEntries(new FormData(form)))
    });

    if (response.ok) {
      const endpoint = query.get("endpoint");
      const incident = query.get("incident");
      const hasIncident = /^[0-9]{1,10}$/.test(endpoint) && /^[0-9]{1,10}$/.test(incident);
      window.location.replace(hasIncident ? `/?endpoint=${endpoint}&incident=${incident}` : "/");
      return;
    }
    errorMessage.textContent = response.status === 401
      ? "Invalid username or password."
      : "Sign-in failed. Try again.";
  } catch {
    errorMessage.textContent = "Could not reach the server. Try again.";
  } finally {
    submit.disabled = false;
  }

  errorMessage.hidden = false;
});
