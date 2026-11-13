// Control page. Talks only to the authenticated control WebSocket.
//
// Safety: the server enforces every PTT fail-safe on its own. This page adds
// fast paths only: it releases PTT on pointer up, key up, window blur, page
// hidden and page hide, and sends heartbeats while connected.

import { AudioUnavailable, BrowserAudio, canPickSpeaker, isRadioCodec, listDevices } from "./audio.js";
import { Scope } from "./scope.js";

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
  ws.binaryType = "arraybuffer";
  app.ws = ws;
  setLink("connecting");
  ws.onopen = () => { app.retry = 500; setLink("up"); };
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

  // The radio has caught up with what we asked for: show its state again.
  if (app.pendingHz != null && !app.dragging && s.frequency_hz === app.pendingHz) app.pendingHz = null;
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
  app.scope?.setMarker(s.frequency_hz);
  const hostOpt = document.querySelector("#au-mode option[value=host]");
  if (hostOpt) hostOpt.disabled = !s.audio?.host_mode;
  if (s.audio && !s.audio.available && app.audioMode) {
    $("au-status").textContent = `Radio audio is not available: ${s.audio.error || "unknown"}`;
  }
  renderScopeSettings(s.scope);
  const listener = app.hello && !app.hello.may_transmit && app.hello.user.role === "listener";
  document.querySelectorAll("#scope-controls select, #sc-hold").forEach((el) => { el.disabled = !!listener; });
  renderTx(s, prev);
  if (!s.connected) $("txtext").textContent = s.error ? `Radio not connected: ${s.error}` : "Radio not connected";
}

function renderTx(s, prev) {
  const tx = s.tx || {};
  const mine = tx.transmitting && app.ptt && app.hello && tx.holder === app.hello.user.callsign;
  const bar = $("txbar");
  if (tx.unconfirmed) {
    bar.dataset.state = "danger";
    $("txtext").textContent = "TX OFF NOT CONFIRMED: the radio may still be transmitting. Retrying.";
  } else if (tx.transmitting && mine) {
    bar.dataset.state = tx.keyed ? "tx" : "pending";
    const lvl = app.txLevel != null && app.txLevel > -120 ? `  audio ${Math.round(app.txLevel)} dBFS` : "";
    $("txtext").textContent = tx.keyed ? `ON AIR  ${tx.holder}${lvl}` : "Keying...";
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
  if (down) {
    showMsg(app.audioMode === "remote" || app.audioMode === "host"
      ? "" : "No microphone path: start audio with a mic route to transmit voice.");
    requestWakeLock();
  }
  localPtt(down);
  app.audio?.setTxEnabled(down && app.audioMode === "remote");
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
  // Release on pointer up anywhere (capture can be lost or never granted on
  // some engines), and always on pointer cancel.
  const release = () => { if (!toggle.checked) setPtt(false); };
  window.addEventListener("pointerup", release);
  btn.addEventListener("pointercancel", () => setPtt(false));
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
  setTimeout(() => {
    if (app.pendingHz === sent && !app.dragging) { app.pendingHz = null; if (app.state) render(app.state); }
  }, 1500);
}
function current() { return app.pendingHz ?? app.state?.frequency_hz ?? 0; }

// Jump straight to a frequency (direct entry, band button).
function jumpTo(hz) {
  app.pendingHz = hz;
  $("freqtext").textContent = fmtHz(hz);
  send({ type: "set_frequency", hz });
  setTimeout(() => { if (app.pendingHz === hz) { app.pendingHz = null; if (app.state) render(app.state); } }, 1500);
}

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
    jumpTo(Math.round(v < 1000 ? v * 1e6 : v * 1e3));  // MHz, or kHz if 1000 or more
    $("entry").value = "";
    $("entry").blur();
  });
  const bands = $("bands");
  for (const [name, hz] of BANDS) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `${name}m`;
    b.addEventListener("click", () => jumpTo(hz));
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

function renderScopeSettings(sc) {
  if (!sc) return;
  const known = sc.mode === "center" || sc.mode === "fixed";
  $("sc-note").textContent = sc.mode && !known ? `Radio is in ${sc.mode}` : "";
  for (const sel of document.querySelectorAll("#scope-controls select[data-scope]")) {
    const v = sc[sel.dataset.scope];
    if (v != null && document.activeElement !== sel) sel.value = String(v);
  }
  document.querySelectorAll(".sc-center").forEach((el) => el.classList.toggle("hidden", sc.mode !== "center"));
  document.querySelectorAll(".sc-fixed").forEach((el) => el.classList.toggle("hidden", sc.mode === "center"));
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
  if (st?.rtc) return app.audioMode === "monitor" ? "Listening (WebRTC)." : "Mic and speaker on (WebRTC).";
  const lvl = st && st.peakDb != null ? (st.peakDb <= -100 ? ", silent" : `, level ${st.peakDb} dB`) : "";
  const rx = st ? `buffer ${st.bufferedMs} ms, ${st.underruns} gaps${lvl}` : "waiting for audio";
  return app.audioMode === "monitor" ? `Listening: ${rx}.` : `Mic and speaker on: ${rx}.`;
}

function onMicFrame(samples, peak) {
  const db = peak > 0 ? Math.max(-60, 20 * Math.log10(peak)) : -60;
  $("au-mic").value = db;
  $("au-mic-t").textContent = `${Math.round(db)} dB`;
  // Send microphone audio only while this page holds PTT; the server ignores it otherwise.
  if (app.ptt && app.audioMode === "remote" && app.ws?.readyState === WebSocket.OPEN && !app.audio.pc) {
    app.ws.send(app.audio.txFrame(samples));
  }
}

async function startAudio() {
  const mode = $("au-mode").value;
  const btn = $("au-start");
  btn.disabled = true;
  // Tell the server first: the WebRTC offer (hidden experiment) needs a mode set.
  send({ type: "audio", mode });
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
        // Hidden experiment (ADR 0002): ?transport=webrtc
        rtc: new URLSearchParams(location.search).get("transport") === "webrtc"
          ? { conn: app.hello?.conn, csrf: app.me.csrf } : null,
      });
      await fillDevices();  // labels are only visible after mic permission
    }
    app.audioMode = mode;
    btn.textContent = "Stop audio";
    btn.classList.remove("primary");
    $("au-mode").disabled = true;
  } catch (e) {
    send({ type: "audio", mode: null });
    await app.audio?.stop();
    app.audio = null;
    const text = e instanceof AudioUnavailable ? e.message : `Audio failed: ${e && e.message ? e.message : e}`;
    $("au-status").textContent = text;
    showMsg(text);
    return;
  } finally {
    btn.disabled = false;
  }
  $("au-status").textContent = audioStatus();
}

async function stopAudio() {
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
