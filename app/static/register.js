"use strict";

const form = document.getElementById("register-form");
const fields = document.getElementById("register-fields");
const errorMessage = document.getElementById("register-error");
const token = new URLSearchParams(window.location.hash.slice(1)).get("invite");

if (token) {
  fields.hidden = false;
  fields.disabled = false;
} else {
  document.getElementById("invite-missing").hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorMessage.hidden = true;
  const password = form.elements.password.value;
  if (password !== document.getElementById("confirmation").value) {
    errorMessage.textContent = "Passwords do not match.";
    errorMessage.hidden = false;
    return;
  }

  fields.disabled = true;
  try {
    const response = await fetch("/auth/register", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        username: form.elements.username.value,
        password,
        invite_token: token
      })
    });
    if (response.ok) {
      window.location.replace("/login?registered=1");
      return;
    }
    const data = await response.json();
    errorMessage.textContent = typeof data.detail === "string"
      ? data.detail
      : "Check your username, password, and invitation link.";
  } catch {
    errorMessage.textContent = "Could not create the account. Try again.";
  } finally {
    fields.disabled = false;
  }
  errorMessage.hidden = false;
});
