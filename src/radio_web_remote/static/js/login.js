// Login: fetch a pre-login CSRF token (also set as an HttpOnly cookie),
// then POST credentials with the token in a header (double submit).

const form = document.getElementById("login");
const msg = document.getElementById("msg");
const go = document.getElementById("go");

async function csrf() {
  const r = await fetch("/api/login-csrf", { credentials: "same-origin" });
  return (await r.json()).csrf;
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  msg.textContent = "";
  go.disabled = true;
  try {
    const token = await csrf();
    const r = await fetch("/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
      body: JSON.stringify({
        username: form.username.value,
        password: form.password.value,
      }),
    });
    if (r.ok) {
      location.replace("/");
      return;
    }
    const body = await r.json().catch(() => ({}));
    if (r.status === 423) {
      msg.textContent = `Account locked. Try again in ${Math.ceil((body.retry_after || 60) / 60)} min.`;
    } else if (r.status === 429) {
      msg.textContent = "Too many attempts from this device. Wait a few minutes.";
    } else {
      msg.textContent = "Wrong username or password.";
    }
  } catch (e) {
    msg.textContent = "Could not reach the server.";
  } finally {
    go.disabled = false;
    form.password.value = "";
  }
});
