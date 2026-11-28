"use strict";

const action = location.pathname.split("/").pop();
const title = action === "confirm" ? "Confirm email address" : "Unsubscribe from this service";
const button = document.getElementById("email-action-button");
const result = document.getElementById("email-action-result");
const token = location.hash.slice(1);
document.getElementById("email-action-title").textContent = title;
button.textContent = title;
button.hidden = !token;
if (!token) result.textContent = "The link is missing its token.";
button.addEventListener("click", async () => {
  button.disabled = true;
  try {
    const response = await fetch(location.pathname, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Invalid email link.");
    result.textContent = data.message;
    history.replaceState(null, "", location.pathname);
    button.hidden = true;
  } catch (error) {
    result.textContent = error.message;
    button.disabled = false;
  }
});
