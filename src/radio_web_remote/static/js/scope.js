// Spectrum and waterfall on <canvas>, fed by binary scope-line frames.
// Frame layout: see src/radio_web_remote/web/scope_wire.py (24-byte header).

const HEADER = 24;

export function decodeLine(buf) {
  const v = new DataView(buf);
  if (v.getUint8(0) !== 1 || v.getUint8(1) !== 1) return null;
  const flags = v.getUint8(2);
  const max = v.getUint16(4, true);
  const count = v.getUint16(6, true);
  return {
    outOfRange: !!(flags & 1),
    fixed: !!(flags & 2),
    max,
    low: v.getFloat64(8, true),
    high: v.getFloat64(16, true),
    points: new Uint8Array(buf, HEADER, count),
  };
}

// Dark blue -> blue -> cyan -> yellow -> red -> white.
function buildPalette() {
  const stops = [
    [0, [4, 8, 24]], [0.25, [16, 40, 140]], [0.45, [20, 140, 220]],
    [0.65, [240, 220, 40]], [0.85, [235, 60, 30]], [1, [255, 255, 255]],
  ];
  const lut = new Uint8ClampedArray(256 * 4);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let k = 0;
    while (k < stops.length - 2 && t > stops[k + 1][0]) k++;
    const [t0, c0] = stops[k], [t1, c1] = stops[k + 1];
    const f = (t - t0) / (t1 - t0);
    for (let j = 0; j < 3; j++) lut[i * 4 + j] = c0[j] + (c1[j] - c0[j]) * f;
    lut[i * 4 + 3] = 255;
  }
  return lut;
}

function load(key, fallback) {
  try { const v = localStorage.getItem(key); return v == null ? fallback : Number(v); } catch { return fallback; }
}
function save(key, value) { try { localStorage.setItem(key, String(value)); } catch { /* private mode */ } }

export class Scope {
  constructor({ spectrum, waterfall, floor, ceiling, edges, onTune, stepHz }) {
    this.spec = spectrum;
    this.fall = waterfall;
    this.floorInput = floor;
    this.ceilInput = ceiling;
    this.edges = edges;
    this.onTune = onTune;
    this.stepHz = stepHz;
    this.palette = buildPalette();
    this.queue = [];
    this.last = null;
    this.markerHz = null;
    this.lines = 0;
    this.drawing = false;
    this.floorInput.value = load("rwr.scope.floor", 0);
    this.ceilInput.value = load("rwr.scope.ceiling", 100);
    for (const [el, key] of [[this.floorInput, "rwr.scope.floor"], [this.ceilInput, "rwr.scope.ceiling"]]) {
      el.addEventListener("input", () => { save(key, el.value); this.redrawSpectrum(); });
    }
    this.resize();
    window.addEventListener("resize", () => this.resize());
    for (const c of [this.spec, this.fall]) this.wireTap(c);
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    for (const c of [this.spec, this.fall]) {
      const w = Math.max(1, Math.round(c.clientWidth * dpr));
      const h = Math.max(1, Math.round(c.clientHeight * dpr));
      if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    }
    this.fallCtx = this.fall.getContext("2d", { willReadFrequently: false });
    this.specCtx = this.spec.getContext("2d");
    this.row = this.fallCtx.createImageData(this.fall.width, 1);
    this.redrawSpectrum();
  }

  // Map a level to 0..1 using the user's floor and ceiling (percent of the radio's scale).
  norm(value, max) {
    const lo = (Number(this.floorInput.value) / 100) * max;
    const hi = Math.max(lo + 1, (Number(this.ceilInput.value) / 100) * max);
    return Math.min(1, Math.max(0, (value - lo) / (hi - lo)));
  }

  push(buf) {
    const line = decodeLine(buf);
    if (!line || line.points.length < 2) return;
    this.queue.push(line);
    if (this.queue.length > 8) this.queue.shift();
    if (!this.drawing) {
      this.drawing = true;
      requestAnimationFrame(() => this.flush());
    }
  }

  flush() {
    this.drawing = false;
    const lines = this.queue.splice(0);
    for (const line of lines) this.addRow(line);
    if (lines.length) {
      this.last = lines[lines.length - 1];
      this.lines += lines.length;
      this.redrawSpectrum();
      this.edges.textContent = `${(this.last.low / 1e6).toFixed(3)} - ${(this.last.high / 1e6).toFixed(3)} MHz` +
        (this.last.outOfRange ? "  (out of range)" : "");
    }
  }

  // Each pixel column shows the strongest point that falls in it.
  columnValue(points, x, width) {
    const n = points.length;
    const a = Math.floor((x * n) / width);
    const b = Math.max(a + 1, Math.floor(((x + 1) * n) / width));
    let m = 0;
    for (let i = a; i < b && i < n; i++) if (points[i] > m) m = points[i];
    return m;
  }

  addRow(line) {
    const ctx = this.fallCtx, w = this.fall.width, h = this.fall.height;
    // About 120 rows of history: roughly 30 s at the IC-7300's 4 sweeps per second.
    const rowH = Math.max(1, Math.round(h / 120));
    ctx.drawImage(this.fall, 0, 0, w, h - rowH, 0, rowH, w, h - rowH);  // scroll down
    const d = this.row.data;
    for (let x = 0; x < w; x++) {
      const c = Math.round(this.norm(this.columnValue(line.points, x, w), line.max) * 255) * 4;
      d[x * 4] = this.palette[c]; d[x * 4 + 1] = this.palette[c + 1];
      d[x * 4 + 2] = this.palette[c + 2]; d[x * 4 + 3] = 255;
    }
    ctx.putImageData(this.row, 0, 0);
    if (rowH > 1) ctx.drawImage(this.fall, 0, 0, w, 1, 0, 1, w, rowH - 1);  // thicken the row
  }

  redrawSpectrum() {
    const ctx = this.specCtx;
    if (!ctx) return;
    const w = this.spec.width, h = this.spec.height;
    const css = getComputedStyle(this.spec);
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = css.getPropertyValue("--scope-grid") || "#334";
    ctx.lineWidth = 1;
    for (let i = 1; i < 10; i++) {
      const x = Math.round((i * w) / 10) + 0.5;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }
    const line = this.last;
    if (line) {
      ctx.beginPath();
      ctx.moveTo(0, h);
      for (let x = 0; x < w; x++) {
        ctx.lineTo(x, h - this.norm(this.columnValue(line.points, x, w), line.max) * (h - 2));
      }
      ctx.lineTo(w, h);
      ctx.closePath();
      ctx.fillStyle = css.getPropertyValue("--scope-fill") || "rgba(91,155,255,.35)";
      ctx.fill();
      ctx.strokeStyle = css.getPropertyValue("--scope-line") || "#5b9bff";
      ctx.stroke();
      if (this.markerHz != null && this.markerHz >= line.low && this.markerHz <= line.high) {
        const x = Math.round(((this.markerHz - line.low) / (line.high - line.low)) * w) + 0.5;
        ctx.strokeStyle = css.getPropertyValue("--scope-marker") || "#ff5252";
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
      }
    }
  }

  setMarker(hz) {
    if (hz !== this.markerHz) { this.markerHz = hz; this.redrawSpectrum(); }
  }

  // Tap or click to tune. Small movement only, so vertical page scrolling still works.
  wireTap(canvas) {
    let start = null;
    canvas.addEventListener("pointerdown", (ev) => { start = { x: ev.clientX, y: ev.clientY }; });
    canvas.addEventListener("pointerup", (ev) => {
      if (!start || !this.last) return;
      const moved = Math.hypot(ev.clientX - start.x, ev.clientY - start.y);
      start = null;
      if (moved > 8) return;
      const r = canvas.getBoundingClientRect();
      const f = (ev.clientX - r.left) / r.width;
      const hz = this.last.low + f * (this.last.high - this.last.low);
      const step = this.stepHz();
      this.onTune(Math.round(hz / step) * step);
    });
  }
}
