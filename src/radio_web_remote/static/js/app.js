// Control page. Talks only to the authenticated control WebSocket.
//
// Safety: the server enforces every PTT fail-safe on its own. This page adds
// fast paths only: it releases PTT on pointer up, key up, window blur, page
// hidden and page hide, and sends heartbeats while connected.

import { AudioUnavailable, BrowserAudio, canPickSpeaker, isRadioCodec, listDevices } from "./audio.js";
import { Scope } from "./scope.js";

const $ = (id) => document.getElementById(id);

// Tuning presets inside each band (not band edges; limits come from the server).
// Band shortcuts. Shown when the radio's documented coverage includes them;
// with no coverage on record (IC-7300), the HF and 6 m ones are shown.
const BANDS = [
  ["160m", 1_900_000], ["80m", 3_750_000], ["40m", 7_150_000], ["30m", 10_120_000],
  ["20m", 14_200_000], ["17m", 18_120_000], ["15m", 21_250_000], ["12m", 24_950_000],
  ["10m", 28_400_000], ["6m", 50_125_000], ["2m", 144_200_000], ["70cm", 432_100_000],
  ["23cm", 1_296_100_000],
];

function buildBands(coverage) {
  const box = $("bands");
  const key = JSON.stringify(coverage || []);
  if (box.dataset.coverage === key) return;
  box.dataset.coverage = key;
  const inside = (hz) => (coverage && coverage.length
    ? coverage.some(([lo, hi]) => hz >= lo && hz <= hi)
    : hz < 100_000_000);
  box.replaceChildren(...BANDS.filter(([, hz]) => inside(hz)).map(([name, hz]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = name;
    b.addEventListener("click", () => jumpTo(hz));
    return b;
  }));
}
const PX_PER_STEP = 8;        // drag distance per tuning step
const FLICK_STILL_MS = 80;    // pointer still this long before release: not a flick
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
  heartbeat: null, ptt: false, pressed: false, serverStop: false, pressId: 0, dragging: false, activeLevel: null,
  pendingHz: null, lastSent: 0, sendTimer: null, wakeLock: null,
  scope: null, scopeOn: false,
  audio: null, audioMode: null, rxStats: null, txLevel: null,
};

// ---------- formatting ----------
function fmtHz(hz) {
  if (hz == null) return "--.---.---";
  const s = String(Math.round(hz)).padStart(7, "0");
  const mhz = s.slice(0, -6), khz = s.slice(-6, -3), h = s.slice(-3);
  return `${mhz}.${khz}.${h}`;
}
// Write text only when it changed: render() runs on every state message, and
// #txtext is an assertive live region that screen readers announce.
function setText(id, text) {
  const el = $(id);
  if (el && el.textContent !== text) el.textContent = text;
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
  const asked = new URLSearchParams(location.search).get("radio");
  const known = (app.me.radios || []).map((r) => r.id);
  if (asked && !known.includes(asked)) {
    // A bookmark to a radio that was removed or renamed: the server would refuse it.
    showMsg(`No radio "${asked}" on this server${known.length ? `; showing ${known[0]}` : ""}.`);
    // Said once: reconnects must not repeat it over a newer message.
    history.replaceState(null, "", location.pathname + (known[0] ? `?radio=${encodeURIComponent(known[0])}` : ""));
  }
  const radio = asked && known.includes(asked) ? asked : known[0] || "";
  const ws = new WebSocket(`wss://${location.host}/ws?radio=${encodeURIComponent(radio)}`);
  ws.binaryType = "arraybuffer";
  app.ws = ws;
  setLink("connecting");
  ws.onopen = () => { app.retry = 500; ws.opened = true; setLink("up"); };
  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) {
      const kind = new Uint8Array(ev.data, 0, 1)[0];
      if (kind === 1) app.scope?.push(ev.data);
      else if (kind === 2) app.audio?.playFrame(ev.data);
      return;
    }
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "hello") onHello(msg);
    else if (msg.type === "state") render(msg.state);
    else if (msg.type === "error") onError(msg);
    else if (msg.type === "tx_level") { app.txLevel = msg.peak_dbfs; if (app.state) renderTx(app.state, app.state); }
  };
  ws.onclose = (ev) => {
    stopHeartbeat();
    localPtt(false);
    setLink("down");
    $("ptt").disabled = true;
    if ($("txbar").dataset.state !== "danger") {  // the last state shown is no longer known
      $("txbar").dataset.state = "held";
      setText("txtext", "No link to the server. It stops TX on its own.");
      setText("txlevel", "");
    }
    if (ev.code === 4001) { location.replace("/login"); return; }
    if (!ws.opened) {
      // Refused before it opened (the browser reports 1006): maybe the session
      // expired. Ask; go to the login page rather than retry forever.
      fetch("/api/me", { credentials: "same-origin" })
        .then((r) => { if (r.status === 401) location.replace("/login"); })
        .catch(() => {});
    }
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
  if (app.scopeOn) send({ type: "scope", on: true });  // resubscribe after a reconnect
  if (app.audioMode) send({ type: "audio", mode: app.audioMode });
  if (!msg.may_transmit) $("ptthint").textContent = "Your account can listen but not transmit.";
}
function stopHeartbeat() { if (app.heartbeat) clearInterval(app.heartbeat); app.heartbeat = null; }

function onError(msg) {
  // A refused or failed change: drop our optimistic display and show the radio's real state.
  if (["control_denied", "radio", "bad_request"].includes(msg.code) && app.pendingHz != null) {
    app.pendingHz = null;
    if (app.state) render(app.state);
  }
  if (["held_by_other", "not_permitted", "out_of_limits", "release_first", "radio_error"].includes(msg.code)) {
    // A late refusal of an earlier press must not drop a newer one the server keyed.
    if (msg.press != null && msg.press !== app.pressId) return;
    localPtt(false);
  }
  const text = {
    held_by_other: `TX is held by ${msg.message.split(": ").pop()}.`,
    not_permitted: "Your account cannot transmit.",
    release_first: STOP_TEXT.timeout,
    control_denied: msg.message.replace(/^.*?: /, ""),
    out_of_limits: msg.message.replace(/^.*?: /, ""),
    radio_error: msg.message.replace(/^.*?: /, ""),
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

  // The radio has caught up with what we asked for: show its state again.
  if (app.pendingHz != null && !app.dragging && s.frequency_hz === app.pendingHz) app.pendingHz = null;
  if (!app.dragging && app.pendingHz == null) {
    $("freqtext").textContent = fmtHz(s.frequency_hz);
    $("freq").setAttribute("aria-valuetext", `${fmtHz(s.frequency_hz)} megahertz`);
  }
  if (!prev || (prev.modes || []).join() !== (s.modes || []).join()) buildModes(s.modes || []);
  buildBands(s.coverage_hz);
  document.querySelectorAll("[data-band]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.band === s.band)));
  $("satnote").classList.toggle("hidden", !s.satellite);
  $("band-dw").setAttribute("aria-pressed", String(!!s.dualwatch));
  // The radio refuses to select SUB while its SUB receiver is off (H9700).
  document.querySelector("[data-band=sub]").disabled = s.dualwatch === false;
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
  app.scope?.setMarker(s.frequency_hz);
  const hostOpt = document.querySelector("#au-mode option[value=host]");
  if (hostOpt) hostOpt.disabled = !s.audio?.host_mode;
  if (s.audio && !s.audio.available && app.audioMode) {
    $("au-status").textContent = `Radio audio is not available: ${s.audio.error || "unknown"}`;
  }
  renderScopeSettings(s.scope, s);
  const listener = app.hello && !app.hello.may_transmit && app.hello.user.role === "listener";
  document.querySelectorAll("#scope-controls select, #sc-hold").forEach((el) => { el.disabled = !!listener; });
  renderTx(s, prev);
}

function renderTx(s, prev) {
  const tx = s.tx || {};
  const mine = tx.transmitting && app.ptt && app.hello && tx.holder === app.hello.user.callsign;
  const bar = $("txbar");
  if (tx.unconfirmed) {
    bar.dataset.state = "danger";
    setText("txtext", "TX OFF NOT CONFIRMED: the radio may still be transmitting. Retrying.");
    setText("txlevel", "");
  } else if (tx.transmitting && mine) {
    bar.dataset.state = tx.keyed ? "tx" : "pending";
    const lvl = app.txLevel != null && app.txLevel > -120 ? `audio ${Math.round(app.txLevel)} dBFS` : "";
    setText("txtext", tx.keyed ? `ON AIR  ${tx.holder}` : "Keying...");
    setText("txlevel", tx.keyed ? lvl : "");
  } else if (tx.transmitting) {
    bar.dataset.state = "held";
    setText("txtext", `TX held by ${tx.holder}`);
    setText("txlevel", "");
  } else {
    bar.dataset.state = "rx";
    setText("txtext", s.connected ? "Receive"
      : s.error ? `Radio not connected: ${s.error}` : "Radio not connected");
    setText("txlevel", "");
  }
  // Never disable the button under a press still in effect: it must stay releasable.
  const others = tx.transmitting && !mine && !app.ptt && !app.pressed;
  $("ptt").disabled = !app.hello?.may_transmit || !s.connected || others;
  // The server stopped our transmission (timeout, heartbeat, limits...): say why.
  // A stop the server made (timeout, limits); not the late echo of our own
  // release, which can arrive after a newer press is already in effect.
  if (app.ptt && prev?.tx?.transmitting && !tx.transmitting && tx.last_stop !== "released") {
    localPtt(false);
    app.serverStop = true;
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
// app.pressed: the user's press is still in effect, even when the server has
// ended the transmission (timeout, limits). The server then refuses a new key
// until it sees this press released, so the release is always sent.
function setPtt(down) {
  if (down) {
    if (app.ptt) return;
    if (app.pressed) {  // an earlier press still counts (denied, or stopped by the server)
      app.pressed = false;
      send({ type: "ptt", down: false });
      if (app.serverStop) {
        // Toggle mode after a server stop: this tap only releases. Keying again
        // needs another, deliberate tap (a tap meant as "stop" must not re-key).
        app.serverStop = false;
        showMsg("Released. Tap again to transmit.");
        return;
      }
    }
    app.serverStop = false;
    showMsg(app.audioMode === "remote" || app.audioMode === "host"
      ? "" : "No microphone path: start audio with a mic route to transmit voice.");
    requestWakeLock();
  } else if (!app.ptt && !app.pressed) {
    return;
  }
  app.pressed = down;
  localPtt(down);
  if (down) send({ type: "ptt", down, press: ++app.pressId });
  else send({ type: "ptt", down });
}

function wirePtt() {
  const btn = $("ptt");
  const toggle = $("toggle");
  btn.addEventListener("pointerdown", (ev) => {
    if (btn.disabled) return;
    ev.preventDefault();
    if (toggle.checked) { setPtt(!app.ptt); return; }
    btn.setPointerCapture(ev.pointerId);
    app.pttPointer = ev.pointerId;
    setPtt(true);
  });
  // Release on pointer up anywhere (capture can be lost or never granted on
  // some engines), and always on pointer cancel.
  // Only the pointer that pressed releases: a second finger tapping a band
  // button must not drop TX. Cancel, blur and hiding still release always.
  const release = (ev) => {
    // Space presses set no pointer: a mouse click elsewhere must not drop them.
    if (toggle.checked || app.pttPointer == null || ev.pointerId !== app.pttPointer) return;
    app.pttPointer = null;
    setPtt(false);
  };
  window.addEventListener("pointerup", release);
  btn.addEventListener("pointercancel", () => { app.pttPointer = null; setPtt(false); });
  btn.addEventListener("contextmenu", (ev) => ev.preventDefault());
  toggle.addEventListener("change", () => {
    setPtt(false);
    $("ptthint").textContent = toggle.checked ? "Tap once to transmit, tap again to stop." : "Hold to talk. Spacebar works on a keyboard.";
  });

  // Text entry keeps Space; sliders, checkboxes and buttons do not.
  const NOT_TEXT = new Set(["range", "checkbox", "radio", "button", "submit", "reset"]);
  const typing = (el) => el && ((el.tagName === "INPUT" && !NOT_TEXT.has(el.type))
    || el.tagName === "SELECT" || el.tagName === "TEXTAREA");
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
  setTimeout(() => {
    if (app.pendingHz === sent && !app.dragging) { app.pendingHz = null; if (app.state) render(app.state); }
  }, 1500);
}
function current() { return app.pendingHz ?? app.state?.frequency_hz ?? 0; }

// Jump straight to a frequency (direct entry, band button).
function jumpTo(hz) {
  app.stopInertia?.();  // a flick still coasting must not overwrite the jump
  app.pendingHz = hz;
  $("freqtext").textContent = fmtHz(hz);
  send({ type: "set_frequency", hz });
  setTimeout(() => { if (app.pendingHz === hz) { app.pendingHz = null; if (app.state) render(app.state); } }, 1500);
}

function wireTuning() {
  const f = $("freq");
  let startX = 0, startHz = 0, lastX = 0, lastT = 0, velocity = 0, inertia = null;
  app.stopInertia = () => { cancelAnimationFrame(inertia); inertia = null; };
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
    // Flick: keep tuning briefly in the flick direction, slowing down. Only if
    // the pointer was still moving at release: drag, hold still, lift = no flick.
    const stillFor = performance.now() - lastT;
    let v = stillFor > FLICK_STILL_MS ? 0 : velocity, pos = 0, base = current();
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
    // MHz, or kHz if 1000 or more; but a value the radio covers in MHz (1296.1
    // on an IC-9700) stays MHz.
    const cov = app.state?.coverage_hz || [];
    const inMhz = (hz) => cov.some(([lo, hi]) => hz >= lo && hz <= hi);
    jumpTo(Math.round(v < 1000 || inMhz(v * 1e6) ? v * 1e6 : v * 1e3));
    $("entry").value = "";
    $("entry").blur();
  });
  buildBands(null);
  document.querySelectorAll("[data-band]").forEach((b) => b.addEventListener("click", () => {
    send({ type: "set_band", band: b.dataset.band });
  }));
  $("band-swap").addEventListener("click", () => send({ type: "swap_bands" }));
  $("band-dw").addEventListener("click", () => send({ type: "set_dualwatch", on: !app.state?.dualwatch }));
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
    // A tap that does not move the slider fires no change: let it follow the radio again.
    for (const ev of ["pointerup", "pointercancel"]) {
      input.addEventListener(ev, () => { setTimeout(() => { if (app.activeLevel === name) app.activeLevel = null; }, 0); });
    }
    input.addEventListener("input", () => {
      $(`l-${name}-t`).textContent = pct(Number(input.value));
      if (!timer) timer = setTimeout(push, SEND_EVERY_MS);
    });
    input.addEventListener("change", () => { push(); app.activeLevel = null; });
  });
}

// ---------- scope ----------
function setScope(on) {
  app.scopeOn = on;
  $("scope-toggle").setAttribute("aria-pressed", String(on));
  $("scope-body").classList.toggle("hidden", !on);
  try { localStorage.setItem("rwr.scope.on", on ? "1" : "0"); } catch { /* fine */ }
  if (on && !app.scope) {
    app.scope = new Scope({
      spectrum: $("spectrum"), waterfall: $("waterfall"),
      floor: $("scope-floor"), ceiling: $("scope-ceiling"), edges: $("scope-edges"),
      onTune: (hz) => jumpTo(hz), stepHz: () => step(),
    });
  }
  if (on) app.scope.resize();
  send({ type: "scope", on });
}

const SCOPE_MODE_LABELS = { center: "Center", fixed: "Fixed", "scroll-c": "Scroll-C", "scroll-f": "Scroll-F" };
// Span applies to Center and Scroll-C; the fixed edge to Fixed and Scroll-F.
const SPAN_MODES = new Set(["center", "scroll-c"]);

function fillOptions(sel, items) {  // items: [value, label]; rebuilt only when they change
  const key = items.map((i) => i[0]).join();
  if (sel.dataset.items === key) return;
  sel.dataset.items = key;
  sel.replaceChildren(...items.map(([v, label]) => {
    const o = document.createElement("option");
    o.value = String(v);
    o.textContent = label;
    return o;
  }));
}

function renderScopeSettings(sc, s) {
  if (!sc) return;
  const modes = s.scope_modes && s.scope_modes.length ? s.scope_modes : ["center", "fixed"];
  fillOptions($("sc-mode"), modes.map((m) => [m, SCOPE_MODE_LABELS[m] || m]));
  const edges = s.scope_edges || 3;
  fillOptions($("sc-edge"), Array.from({ length: edges }, (_, i) => [i + 1, String(i + 1)]));
  const known = modes.includes(sc.mode);
  $("sc-note").textContent = sc.mode && !known ? `Radio is in ${sc.mode}` : "";
  for (const sel of document.querySelectorAll("#scope-controls select[data-scope]")) {
    const v = sc[sel.dataset.scope];
    if (v != null && document.activeElement !== sel) sel.value = String(v);
  }
  const span = SPAN_MODES.has(sc.mode);
  document.querySelectorAll(".sc-center").forEach((el) => el.classList.toggle("hidden", !span));
  document.querySelectorAll(".sc-fixed").forEach((el) => el.classList.toggle("hidden", span));
  $("sc-hold").setAttribute("aria-pressed", String(!!sc.hold));
}

function wireScope() {
  $("scope-toggle").addEventListener("click", () => setScope(!app.scopeOn));
  const ref = $("sc-ref");
  for (let db = 20; db >= -20; db -= 0.5) {
    const o = document.createElement("option");
    o.value = String(db);
    o.textContent = `${db > 0 ? "+" : ""}${db.toFixed(1)} dB`;
    ref.append(o);
  }
  for (const sel of document.querySelectorAll("#scope-controls select[data-scope]")) {
    sel.addEventListener("change", () => {
      const name = sel.dataset.scope;
      let value = sel.value;
      if (name === "span" || name === "edge") value = Number(value);
      if (name === "ref") value = Number(value);
      send({ type: "set_scope", setting: name, value });
      sel.blur();
    });
  }
  $("sc-hold").addEventListener("click", () => send({ type: "set_scope", setting: "hold", value: !app.state?.scope?.hold }));
  let saved = false;
  try { saved = localStorage.getItem("rwr.scope.on") === "1"; } catch { /* fine */ }
  if (saved) setScope(true);
}

// ---------- audio ----------
function audioStatus() {
  if (!app.audioMode) return "Audio is off. Tap Start audio.";
  if (app.audioMode === "host") return "Using the radio computer's microphone and speaker.";
  const st = app.rxStats;
  const lvl = st && st.peakDb != null ? (st.peakDb <= -100 ? ", silent" : `, level ${st.peakDb} dB`) : "";
  const tgt = st && st.targetMs != null ? ` (target ${st.targetMs})` : "";
  const rx = st
    ? `buffer ${st.bufferedMs} ms${tgt}, ${st.underruns} gaps, ${st.trims} skips${lvl}`
    : "waiting for audio";
  return app.audioMode === "monitor" ? `Listening: ${rx}.` : `Mic and speaker on: ${rx}.`;
}

function onMicFrame(samples, peak) {
  app.micPeak = Math.max(app.micPeak || 0, peak);  // the loudest frame since the last draw
  const now = performance.now();
  if (now - (app.micDrawn || 0) >= 100) {  // 50 frames/s; the meter needs ~10
    app.micDrawn = now;
    const db = app.micPeak > 0 ? Math.max(-60, 20 * Math.log10(app.micPeak)) : -60;
    app.micPeak = 0;
    $("au-mic").value = db;
    $("au-mic-t").textContent = `${Math.round(db)} dB`;
  }
  // Send microphone audio only while this page holds PTT; the server ignores it otherwise.
  if (app.ptt && app.audioMode === "remote" && app.ws?.readyState === WebSocket.OPEN) {
    app.ws.send(app.audio.txFrame(samples));
  }
}

async function startAudio() {
  if (app.audioStarting || app.audioMode) return;  // a double tap must not build two pipelines
  app.audioStarting = true;
  $("au-resume").disabled = true;
  const mode = $("au-mode").value;
  const btn = $("au-start");
  btn.disabled = true;
  try {
    if (mode !== "host") {
      app.audio = new BrowserAudio({
        onMicFrame,
        onStats: (st) => { app.rxStats = st; $("au-status").textContent = audioStatus(); },
      });
      await app.audio.start({
        playback: true,
        microphone: mode === "remote",
        processing: {
          echoCancellation: $("au-ec").checked,
          noiseSuppression: $("au-ns").checked,
          autoGainControl: $("au-agc").checked,
        },
        volume: Number($("au-vol").value) / 100,
        micId: $("au-mic-dev").value || null,
        speakerId: $("au-spk-dev").value || null,
      });
      await fillDevices();  // labels are only visible after mic permission
    }
    app.audioMode = mode;
    send({ type: "audio", mode });
    startDiag();
    remember("rwr.audio.mode", mode);
    remember("rwr.audio.on", "1");  // offer "Resume audio" if the page is reopened
    $("au-resume-bar").classList.add("hidden");
    btn.textContent = "Stop audio";
    btn.classList.remove("primary");
    $("au-mode").disabled = true;
  } catch (e) {
    await app.audio?.stop();
    app.audio = null;
    const text = e instanceof AudioUnavailable ? e.message : `Audio failed: ${e && e.message ? e.message : e}`;
    $("au-status").textContent = text;
    showMsg(text);
    return;
  } finally {
    btn.disabled = false;
    app.audioStarting = false;
    $("au-resume").disabled = false;
  }
  $("au-status").textContent = audioStatus();
}

// Every 5 s, report receive-audio health; the server logs it only when unhealthy.
function startDiag() {
  stopDiag();
  app.diagPrev = { underruns: 0, trims: 0 };
  app.diagTimer = setInterval(() => {
    const d = app.audio?.takeDiag();
    const st = app.rxStats;
    if (!d || !st) return;
    send({
      type: "audio_diag", ...d,
      gaps: Math.max(0, st.underruns - app.diagPrev.underruns),
      skips: Math.max(0, st.trims - app.diagPrev.trims),
      target_ms: st.targetMs ?? 0,
    });
    app.diagPrev = { underruns: st.underruns, trims: st.trims };
  }, 5000);
}
function stopDiag() { if (app.diagTimer) clearInterval(app.diagTimer); app.diagTimer = null; }

async function stopAudio() {
  stopDiag();
  remember("rwr.audio.on", "0");
  setPtt(false);
  send({ type: "audio", mode: null });
  app.audioMode = null;
  await app.audio?.stop();
  app.audio = null;
  app.rxStats = null;
  $("au-start").textContent = "Start audio";
  $("au-start").classList.add("primary");
  $("au-mode").disabled = false;
  $("au-mic").value = -60;
  $("au-mic-t").textContent = "-";
  $("au-status").textContent = audioStatus();
}

function remember(key, value) { try { localStorage.setItem(key, value); } catch { /* fine */ } }
function recall(key) { try { return localStorage.getItem(key) || ""; } catch { return ""; } }

async function fillDevices() {
  const { inputs, outputs } = await listDevices();
  const fill = (sel, devs, key) => {
    const keep = sel.value || recall(key);
    sel.replaceChildren(new Option("System default", ""));
    for (const d of devs) {
      const radio = isRadioCodec(d.label);
      const o = new Option(radio ? `${d.label} (the radio: do not use)` : d.label || "Unnamed device", d.deviceId);
      o.disabled = radio;
      sel.append(o);
    }
    if ([...sel.options].some((o) => o.value === keep && !o.disabled)) sel.value = keep;
  };
  fill($("au-mic-dev"), inputs, "rwr.audio.mic");
  fill($("au-spk-dev"), outputs, "rwr.audio.speaker");
}

function wireAudio() {
  $("au-spk-wrap").classList.toggle("hidden", !canPickSpeaker());
  $("au-mic-dev").addEventListener("change", () => remember("rwr.audio.mic", $("au-mic-dev").value));
  $("au-spk-dev").addEventListener("change", () => remember("rwr.audio.speaker", $("au-spk-dev").value));
  fillDevices().catch(() => {});
  navigator.mediaDevices?.addEventListener?.("devicechange", () => fillDevices().catch(() => {}));
  $("au-start").addEventListener("click", () => (app.audioMode ? stopAudio() : startAudio()));
  // Reopened after audio was on: restore the route and offer one tap to restart it.
  // Browsers (iOS in particular) only start audio from a user gesture.
  const mode = recall("rwr.audio.mode");
  if ([...$("au-mode").options].some((o) => o.value === mode)) $("au-mode").value = mode;
  if (recall("rwr.audio.on") === "1") {
    const label = $("au-mode").selectedOptions[0]?.textContent || "";
    $("au-resume").textContent = `Resume audio: ${label}`;
    $("au-resume-bar").classList.remove("hidden");
  }
  $("au-resume").addEventListener("click", () => { if (!app.audioMode) startAudio(); });
  $("au-resume-no").addEventListener("click", () => {
    remember("rwr.audio.on", "0");
    $("au-resume-bar").classList.add("hidden");
  });
  $("au-vol").addEventListener("input", () => {
    const v = Number($("au-vol").value);
    $("au-vol-t").textContent = `${v}%`;
    app.audio?.setVolume(v / 100);
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
  wireScope();
  wireAudio();
  document.addEventListener("pointerdown", requestWakeLock, { once: true });
  connect();
}

boot();
