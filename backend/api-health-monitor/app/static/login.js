"use strict";

const form = document.getElementById("login-form");
const submit = form.querySelector("button");
const errorMessage = document.getElementById("login-error");

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
      window.location.replace("/");
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
