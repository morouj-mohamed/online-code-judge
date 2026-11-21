// page-auth.js - the "Log in" and "Create account" pages.
//
// Both send JSON to /api/auth/login or /api/auth/signup. On success the server
// sets an HttpOnly cookie (JavaScript never sees the login token) and moves the
// anonymous submissions of this browser to the account.

import { api, describeError } from "./api.js";
import { boot } from "./app.js";
import { escapeHtml } from "./format.js";
import { icon } from "./icons.js";

boot();

const form = document.querySelector(".js-auth-form");
const mode = form.dataset.mode;
const errorBox = form.querySelector(".js-error");
const submitButton = form.querySelector(".js-submit");
const username = form.querySelector("#username");
const password = form.querySelector("#password");
const reveal = form.querySelector(".js-reveal");

/** Only same-site paths are allowed as ?next=..., never another website. */
function nextUrl() {
  const next = new URLSearchParams(location.search).get("next") || "/";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

function showError(message, field) {
  errorBox.innerHTML = `${icon("alert-octagon")}<span>${escapeHtml(message)}</span>`;
  errorBox.hidden = false;
  [username, password].forEach((el) => el.removeAttribute("aria-invalid"));
  if (field) {
    field.setAttribute("aria-invalid", "true");
    field.focus();
  }
}

reveal.addEventListener("click", () => {
  const show = password.type === "password";
  password.type = show ? "text" : "password";
  reveal.textContent = show ? "Hide" : "Show";
  reveal.setAttribute("aria-pressed", String(show));
  password.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  if (!username.value.trim()) return showError("Enter your username.", username);
  if (!password.value) return showError("Enter your password.", password);
  if (mode === "signup" && !username.checkValidity()) {
    return showError("Username: 3 to 32 characters, letters, digits, “_”, “.” or “-”.", username);
  }
  if (mode === "signup" && password.value.length < 8) {
    return showError("The password needs at least 8 characters.", password);
  }
  submitButton.classList.add("is-busy");
  submitButton.disabled = true;
  try {
    const call = mode === "login" ? api.login : api.signup;
    const result = await call(username.value.trim(), password.value);
    if (result.moved_submissions) {
      sessionStorage.setItem("ocj-flash", `${result.moved_submissions} submission(s) from this browser were added to your account.`);
    }
    window.location.href = nextUrl();
  } catch (error) {
    const aboutUsername = error.code === "username_taken" ||
      (error.code === "invalid_account" && /username/i.test(error.message));
    const field = aboutUsername ? username : password;
    showError(describeError(error), field);
    submitButton.classList.remove("is-busy");
    submitButton.disabled = false;
  }
});

username.focus();
