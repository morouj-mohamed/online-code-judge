// page-account.js - change your own password.
//
// The browser checks the obvious things first (both new passwords equal, at
// least 8 characters) so the user gets an answer at once; the server checks
// everything again and is the only one that can verify the current password.

import { api, describeError } from "./api.js";
import { announce, boot } from "./app.js";
import { toast } from "./toast.js";

boot();

const form = document.querySelector(".js-password-form");
const error = form.querySelector(".js-error");
const button = form.querySelector(".js-submit");
const field = (name) => form.elements.namedItem(name);

function showError(text, input) {
  error.textContent = text;
  error.hidden = false;
  if (input) input.focus();
  announce(text);
}

form.querySelector(".js-show").addEventListener("change", (event) => {
  for (const name of ["current", "new", "repeat"]) field(name).type = event.target.checked ? "text" : "password";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.hidden = true;
  const current = field("current").value, fresh = field("new").value, repeat = field("repeat").value;
  if (!current) return showError("Enter your current password.", field("current"));
  if (fresh.length < 8) return showError("The new password needs at least 8 characters.", field("new"));
  if (fresh !== repeat) return showError("The two new passwords are not the same.", field("repeat"));
  button.classList.add("is-busy");
  button.disabled = true;
  try {
    await api.request("POST", "/api/auth/password", { current_password: current, new_password: fresh });
    form.reset();
    toast({ title: "Password changed", message: "Other devices were logged out. You stay logged in here.", kind: "success" });
    announce("Password changed");
  } catch (err) {
    const input = err.code === "wrong_password" ? field("current") : field("new");
    showError(describeError(err).replace(/^./, (c) => c.toUpperCase()), input);
  } finally {
    button.classList.remove("is-busy");
    button.disabled = false;
  }
});
