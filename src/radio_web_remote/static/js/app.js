// Control page. Talks only to the authenticated control WebSocket.
//
// Safety: the server enforces every PTT fail-safe on its own. This page adds
// fast paths only: it releases PTT on pointer up, key up, window blur, page
// hidden and page hide, and sends heartbeats while connected.

const $ = (id) => document.getElementById(id);

// Tuning presets inside each band (not band edges; limits come from the server).
const BANDS = [
  ["160", 1_900_000], ["80", 3_750_000], ["40", 7_150_000], ["30", 10_120_000],
  ["20", 14_200_000], ["17", 18_120_000], ["15", 21_250_000], ["12", 24_950_000],
  ["10", 28_400_000], ["6", 50_125_000],
];
const PX_PER_STEP = 8;        // drag distance per tuning step
const SEND_EVERY_MS = 60;     // throttle for tune and level messages
const STOP_TEXT = {
  released: "", connection_closed: "Connection lost: TX stopped.",
  heartbeat_lost: "Link too slow or interrupted: TX stopped.",
  hidden: "Page hidden: TX stopped.", timeout: "TX time limit reached. Release PTT, then press again.",
  out_of_limits: "Outside your allowed band or power: TX stopped.", logged_out: "Logged out: TX stopped.",
  shutdown: "Server shutting down: TX stopped.", radio_error: "Radio not responding: TX stopped.",
};

const app = {
  me: null, ws: null, hello: null, state: null, retry: 500,
  heartbeat: null, ptt: false, dragging: false, activeLevel: null,
  pendingHz: null, lastSent: 0, sendTimer: null, wakeLock: null,
};

// ---------- formatting ----------
function fmtHz(hz) {
  if (hz == null) return "--.---.---";
  const s = String(Math.round(hz)).padStart(7, "0");
  const mhz = s.slice(0, -6), khz = s.slice(-6, -3), h = s.slice(-3);
  return `${mhz}.${khz}.${h}`;
}
function pct(v) { return v == null ? "" : `${Math.round((v * 100) / 255)}%`; }
function meterText(m) {
  if (!m) return "-";
  if (m.unit === "S") return m.db_over_s9 != null ? `S9+${Math.round(m.db_over_s9)}` : `S${Math.round(m.value)}`;
  if (m.value == null) return m.unit === "SWR" ? ">3.0" : "over";
  return m.unit === "SWR" ? m.value.toFixed(1) : `${Math.round(m.value)}%`;
}
function showMsg(text) { $("msg").textContent = text || ""; }

// ---------- socket ----------
function send(obj) {
  if (app.ws && app.ws.readyState === WebSocket.OPEN) app.ws.send(JSON.stringify(obj));
}

function connect() {
  const radio = new URLSearchParams(location.search).get("radio") || app.me.radios[0]?.id || "";
  const ws = new WebSocket(`wss://${location.host}/ws?radio=${encodeURIComponent(radio)}`);
  app.ws = ws;
  setLink("connecting");
  ws.onopen = () => { app.retry = 500; setLink("up"); };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "hello") onHello(msg);
    else if (msg.type === "state") render(msg.state);
    else if (msg.type === "error") onError(msg);
  };
  ws.onclose = (ev) => {
    stopHeartbeat();
    localPtt(false);
    setLink("down");
    $("ptt").disabled = true;
    if (ev.code === 4001) { location.replace("/login"); return; }
    setTimeout(connect, app.retry);
    app.retry = Math.min(app.retry * 2, 5000);
  };
}

function setLink(state) {
  const el = $("link");
  el.dataset.state = state;
  el.textContent = { up: "Connected", down: "Reconnecting", connecting: "Connecting" }[state];
}

function onHello(msg) {
  app.hello = msg;
  stopHeartbeat();
  const every = Math.max(100, (msg.heartbeat_interval_s || 0.5) * 1000);
  app.heartbeat = setInterval(() => send({ type: "heartbeat" }), every);
  $("ptt").disabled = !msg.may_transmit;
  if (!msg.may_transmit) $("ptthint").textContent = "Your account can listen but not transmit.";
}
function stopHeartbeat() { if (app.heartbeat) clearInterval(app.heartbeat); app.heartbeat = null; }

function onError(msg) {
  if (["held_by_other", "not_permitted", "out_of_limits", "release_first", "radio_error"].includes(msg.code)) {
    localPtt(false);
  }
  const text = {
    held_by_other: `TX is held by ${msg.message.split(": ").pop()}.`,
    not_permitted: "Your account cannot transmit.",
    release_first: STOP_TEXT.timeout,
    control_denied: msg.message.replace(/^.*?: /, ""),
  }[msg.code] || msg.message;
  showMsg(text);
}

// ---------- rendering ----------
function render(s) {
  const prev = app.state;
  app.state = s;
  $("title").textContent = s.model ? `${s.model}` : "Radio Remote";
  const feats = new Set(s.features || []);
  document.querySelectorAll("[data-feature]").forEach((el) => {
    el.classList.toggle("hidden", !feats.has(el.dataset.feature));
  });

  if (!app.dragging && app.pendingHz == null) {
    $("freqtext").textContent = fmtHz(s.frequency_hz);
    $("freq").setAttribute("aria-valuetext", `${fmtHz(s.frequency_hz)} megahertz`);
  }
  if (!prev || (prev.modes || []).join() !== (s.modes || []).join()) buildModes(s.modes || []);
  document.querySelectorAll("#modes button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === s.mode)));
  document.querySelectorAll("[data-filter]").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.filter) === s.filter)));
  $("data").setAttribute("aria-pressed", String(!!s.data_mode));

  for (const [name, value] of Object.entries(s.levels || {})) {
    const input = $(`l-${name}`);
    if (!input) continue;
    if (app.activeLevel !== name) input.value = value;
    $(`l-${name}-t`).textContent = pct(value);
  }
  for (const key of ["s", "po", "swr", "alc"]) {
    const m = (s.meters || {})[key];
    const el = $(`m-${key}`);
    if (!el) continue;
    el.value = m ? m.raw : 0;
    $(`m-${key}-t`).textContent = meterText(m);
  }
  renderTx(s, prev);
  if (!s.connected) $("txtext").textContent = s.error ? `Radio not connected: ${s.error}` : "Radio not connected";
}

function renderTx(s, prev) {
  const tx = s.tx || {};
  const mine = tx.transmitting && app.ptt && app.hello && tx.holder === app.hello.user.callsign;
  const bar = $("txbar");
  if (tx.transmitting && mine) {
    bar.dataset.state = tx.keyed ? "tx" : "pending";
    $("txtext").textContent = tx.keyed ? `ON AIR  ${tx.holder}` : "Keying...";
  } else if (tx.transmitting) {
    bar.dataset.state = "held";
    $("txtext").textContent = `TX held by ${tx.holder}`;
  } else {
    bar.dataset.state = "rx";
    $("txtext").textContent = "Receive";
  }
  const others = tx.transmitting && !mine && !app.ptt;
  $("ptt").disabled = !app.hello?.may_transmit || !s.connected || others;
  // The server stopped our transmission (timeout, heartbeat, limits...): say why.
  if (app.ptt && prev?.tx?.transmitting && !tx.transmitting) {
    localPtt(false);
    showMsg(STOP_TEXT[tx.last_stop] ?? "TX stopped.");
  }
}

function buildModes(modes) {
  const box = $("modes");
  box.replaceChildren();
  for (const m of modes) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = m;
    b.dataset.mode = m;
    b.addEventListener("click", () => send({ type: "set_mode", mode: m, filter: null }));
    box.append(b);
  }
}

// ---------- PTT ----------
function localPtt(down) {
  app.ptt = down;
  $("ptt").setAttribute("aria-pressed", String(down));
}
function setPtt(down) {
  if (down === app.ptt) return;
  if (down) { showMsg(""); requestWakeLock(); }
  localPtt(down);
  send({ type: "ptt", down });
}

function wirePtt() {
  const btn = $("ptt");
  const toggle = $("toggle");
  btn.addEventListener("pointerdown", (ev) => {
    if (btn.disabled) return;
    ev.preventDefault();
    if (toggle.checked) { setPtt(!app.ptt); return; }
    btn.setPointerCapture(ev.pointerId);
    setPtt(true);
  });
  const release = () => { if (!toggle.checked) setPtt(false); };
  btn.addEventListener("pointerup", release);
  btn.addEventListener("pointercancel", () => setPtt(false));
  btn.addEventListener("lostpointercapture", release);
  btn.addEventListener("contextmenu", (ev) => ev.preventDefault());
  toggle.addEventListener("change", () => {
    setPtt(false);
    $("ptthint").textContent = toggle.checked ? "Tap once to transmit, tap again to stop." : "Hold to talk. Spacebar works on a keyboard.";
  });

  const typing = (el) => el && (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA");
  window.addEventListener("keydown", (ev) => {
    if (ev.code !== "Space" || typing(document.activeElement) || ev.repeat || btn.disabled) return;
    ev.preventDefault();
    if (toggle.checked) setPtt(!app.ptt); else setPtt(true);
  });
  window.addEventListener("keyup", (ev) => {
    if (ev.code === "Space" && !toggle.checked) { ev.preventDefault(); setPtt(false); }
  });
  // Fast paths for the server-side fail-safes.
  window.addEventListener("blur", () => setPtt(false));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { setPtt(false); send({ type: "visibility", hidden: true }); }
    else requestWakeLock();
  });
  window.addEventListener("pagehide", () => { setPtt(false); send({ type: "visibility", hidden: true }); });
}

// ---------- tuning ----------
function step() { return Number($("step").value); }

function queueTune(hz) {
  hz = Math.max(1, Math.round(hz / step()) * step());
  app.pendingHz = hz;
  $("freqtext").textContent = fmtHz(hz);
  const wait = SEND_EVERY_MS - (performance.now() - app.lastSent);
  if (wait <= 0) flushTune();
  else if (!app.sendTimer) app.sendTimer = setTimeout(flushTune, wait);
}
function flushTune() {
  app.sendTimer = null;
  if (app.pendingHz == null) return;
  app.lastSent = performance.now();
  send({ type: "set_frequency", hz: app.pendingHz });
  const sent = app.pendingHz;
  setTimeout(() => { if (app.pendingHz === sent && !app.dragging) app.pendingHz = null; }, 400);
}
function current() { return app.pendingHz ?? app.state?.frequency_hz ?? 0; }

function wireTuning() {
  const f = $("freq");
  let startX = 0, startHz = 0, lastX = 0, lastT = 0, velocity = 0, inertia = null;
  f.addEventListener("pointerdown", (ev) => {
    if (!app.state?.frequency_hz) return;
    cancelAnimationFrame(inertia);
    f.setPointerCapture(ev.pointerId);
    app.dragging = true;
    f.classList.add("dragging");
    startX = lastX = ev.clientX;
    startHz = current();
    lastT = performance.now();
    velocity = 0;
  });
  f.addEventListener("pointermove", (ev) => {
    if (!app.dragging) return;
    const now = performance.now();
    velocity = (ev.clientX - lastX) / Math.max(1, now - lastT);
    lastX = ev.clientX; lastT = now;
    const steps = Math.round((ev.clientX - startX) / PX_PER_STEP);
    queueTune(startHz + steps * step());
  });
  const end = () => {
    if (!app.dragging) return;
    app.dragging = false;
    f.classList.remove("dragging");
    // Flick: keep tuning briefly in the flick direction, slowing down.
    let v = velocity, pos = 0, base = current();
    const tick = () => {
      v *= 0.92;
      pos += v * 16;
      if (Math.abs(v) < 0.05) { flushTune(); return; }
      queueTune(base + Math.round(pos / PX_PER_STEP) * step());
      inertia = requestAnimationFrame(tick);
    };
    if (Math.abs(v) > 0.5) inertia = requestAnimationFrame(tick); else flushTune();
  };
  f.addEventListener("pointerup", end);
  f.addEventListener("pointercancel", end);
  f.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    queueTune(current() + (ev.deltaY < 0 ? 1 : -1) * step());
  }, { passive: false });
  f.addEventListener("keydown", (ev) => {
    const n = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 10, PageDown: -10 }[ev.key];
    if (n) { ev.preventDefault(); queueTune(current() + n * step()); }
  });
  $("up").addEventListener("click", () => queueTune(current() + step()));
  $("down").addEventListener("click", () => queueTune(current() - step()));
  $("direct").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const v = parseFloat($("entry").value.replace(",", "."));
    if (!Number.isFinite(v) || v <= 0) return;
    const hz = Math.round(v < 1000 ? v * 1e6 : v * 1e3);  // MHz, or kHz if 1000 or more
    app.pendingHz = hz;
    send({ type: "set_frequency", hz });
    $("entry").value = "";
    $("entry").blur();
  });
  const bands = $("bands");
  for (const [name, hz] of BANDS) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `${name}m`;
    b.addEventListener("click", () => { app.pendingHz = hz; send({ type: "set_frequency", hz }); });
    bands.append(b);
  }
}

// ---------- mode, filter, levels ----------
function wireControls() {
  document.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
    if (app.state?.mode) send({ type: "set_mode", mode: app.state.mode, filter: Number(b.dataset.filter) });
  }));
  $("data").addEventListener("click", () => send({ type: "set_data_mode", on: !app.state?.data_mode }));
  document.querySelectorAll("input[data-level]").forEach((input) => {
    const name = input.dataset.level;
    let timer = null;
    const push = () => { timer = null; send({ type: "set_level", level: name, value: Number(input.value) }); };
    input.addEventListener("pointerdown", () => { app.activeLevel = name; });
    input.addEventListener("input", () => {
      $(`l-${name}-t`).textContent = pct(Number(input.value));
      if (!timer) timer = setTimeout(push, SEND_EVERY_MS);
    });
    input.addEventListener("change", () => { push(); app.activeLevel = null; });
  });
}

// ---------- misc ----------
async function requestWakeLock() {
  try {
    if ("wakeLock" in navigator && !document.hidden && !app.wakeLock) {
      app.wakeLock = await navigator.wakeLock.request("screen");
      app.wakeLock.addEventListener("release", () => { app.wakeLock = null; });
    }
  } catch { /* not supported or refused: fine */ }
}

async function logout() {
  setPtt(false);
  try {
    await fetch("/api/logout", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": app.me.csrf } });
  } finally {
    location.replace("/login");
  }
}

async function boot() {
  const r = await fetch("/api/me", { credentials: "same-origin" });
  if (r.status === 401) { location.replace("/login"); return; }
  app.me = await r.json();
  $("who").textContent = app.me.callsign ? `${app.me.callsign} (${app.me.role})` : `${app.me.username} (${app.me.role})`;
  $("logout").addEventListener("click", logout);
  wirePtt();
  wireTuning();
  wireControls();
  document.addEventListener("pointerdown", requestWakeLock, { once: true });
  connect();
}

boot();
