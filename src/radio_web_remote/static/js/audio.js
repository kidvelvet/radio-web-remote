// Browser audio: receive playback and microphone capture, driven by app.js.
//
// Must start from a user gesture (iOS Safari and others require it for both
// AudioContext and getUserMedia). Browser audio processing (echo
// cancellation, noise suppression, auto gain) is off unless the user turns it
// on, because it can distort SSB transmit audio.

const NET_RATE = 16000;
const HEADER = 12;
const TYPE_TX = 3;

export class AudioUnavailable extends Error {}

export class BrowserAudio {
  constructor({ onMicFrame, onStats }) {
    this.onMicFrame = onMicFrame;   // (Int16Array, peak 0..1) => void
    this.onStats = onStats;         // ({bufferedMs, underruns, trims}) => void
    this.ctx = null;
    this.player = null;
    this.gain = null;
    this.mic = null;
    this.capture = null;
    this.txSeq = 0;
    this.diag = null;
  }

  get running() { return !!this.ctx; }

  async start({ playback, microphone, processing, volume, micId, speakerId }) {
    if (!window.isSecureContext || !window.AudioContext) {
      throw new AudioUnavailable("This page is not a secure context. Install the certificate from /setup.");
    }
    if (microphone && !navigator.mediaDevices?.getUserMedia) {
      throw new AudioUnavailable("Microphone access is not available here. Install the certificate from /setup.");
    }
    // Ask for the microphone first, inside the user gesture.
    let stream = null;
    if (microphone) {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            ...(micId ? { deviceId: { exact: micId } } : {}),
            channelCount: 1,
            echoCancellation: !!processing.echoCancellation,
            noiseSuppression: !!processing.noiseSuppression,
            autoGainControl: !!processing.autoGainControl,
          },
        });
      } catch (e) {
        throw new AudioUnavailable(
          e && e.name === "NotAllowedError"
            ? "Microphone permission was denied. Allow it in the browser's site settings."
            : `Microphone not available: ${e && e.message ? e.message : e}`);
      }
    }
    this.mic = stream;  // now, so stop() can release it if anything below fails
    const ctx = new AudioContext({ latencyHint: "interactive" });
    this.ctx = ctx;
    await ctx.resume();
    if (speakerId && typeof ctx.setSinkId === "function") {
      await ctx.setSinkId(speakerId).catch(() => {});  // falls back to the system default
    }
    await ctx.audioWorklet.addModule("/static/js/worklets/player.js");
    await ctx.audioWorklet.addModule("/static/js/worklets/capture.js");
    if (playback) {
      this.player = new AudioWorkletNode(ctx, "rx-player", { numberOfInputs: 0, outputChannelCount: [2] });
      this.player.port.onmessage = (ev) => this.onStats?.(ev.data);
      this.gain = new GainNode(ctx, { gain: volume });
      this.player.connect(this.gain).connect(ctx.destination);
      this.startDiag();
    }
    // tools/bench_audio.py measures round-trip latency at this point.
    if (new URLSearchParams(location.search).has("bench")) window.rwrBench = { ctx, out: this.gain };
    if (stream) {
      const src = new MediaStreamAudioSourceNode(ctx, { mediaStream: stream });
      this.capture = new AudioWorkletNode(ctx, "mic-capture", { numberOfOutputs: 1 });
      this.capture.port.onmessage = (ev) => this.onMicFrame?.(ev.data.pcm, ev.data.peak);
      // Keep the capture node pulled by the graph without sending the mic to the speakers.
      const mute = new GainNode(ctx, { gain: 0 });
      src.connect(this.capture).connect(mute).connect(ctx.destination);
    }
  }

  async stop() {
    if (this.lagTimer) clearInterval(this.lagTimer);
    this.lagTimer = null;
    this.diag = null;
    this.mic?.getTracks().forEach((t) => t.stop());
    this.mic = null;
    if (this.ctx) await this.ctx.close().catch(() => {});
    this.ctx = this.player = this.gain = this.capture = null;
  }

  setVolume(v) { if (this.gain) this.gain.gain.value = v; }

  // Receive audio frame from the server (type 0x02).
  playFrame(buf) {
    if (!this.player) return;
    const v = new DataView(buf);
    const count = v.getUint16(10, true);
    if (v.getUint16(8, true) !== NET_RATE || buf.byteLength !== HEADER + 2 * count) return;
    this.noteArrival(v.getUint32(4, true));
    const samples = new Int16Array(buf, HEADER, count);  // a view: no copy
    this.player.port.postMessage(samples, [buf]);
  }

  // Receive diagnostics: frame arrival gaps (network), sequence gaps (frames
  // the server dropped), and main-thread stalls (page too busy to hand frames
  // to the player). takeDiag() returns and resets one window.
  noteArrival(seq) {
    const d = this.diag, now = performance.now();
    if (!d) return;
    d.frames++;
    if (d.lastSeq != null) {
      const missed = (seq - d.lastSeq - 1) >>> 0;
      if (missed > 0 && missed < 1000) d.lost += missed;
    }
    if (d.lastArr != null) {
      const gap = now - d.lastArr;
      if (gap > 100) d.late++;
      if (gap > d.maxGap) d.maxGap = gap;
    }
    d.lastSeq = seq;
    d.lastArr = now;
  }

  startDiag() {
    this.diag = { frames: 0, lost: 0, late: 0, maxGap: 0, maxLag: 0, lastSeq: null, lastArr: null };
    let expect = performance.now() + 50;
    this.lagTimer = setInterval(() => {
      const now = performance.now();
      if (this.diag) this.diag.maxLag = Math.max(this.diag.maxLag, now - expect);
      expect = now + 50;
    }, 50);
  }

  takeDiag() {
    const d = this.diag;
    if (!d) return null;
    const out = { frames: d.frames, lost: d.lost, late: d.late,
      max_gap_ms: Math.round(d.maxGap), max_lag_ms: Math.max(0, Math.round(d.maxLag)) };
    Object.assign(d, { frames: 0, lost: 0, late: 0, maxGap: 0, maxLag: 0 });
    return out;
  }

  // Wrap mic samples as a transmit frame (type 0x03).
  txFrame(samples) {
    const buf = new ArrayBuffer(HEADER + samples.length * 2);
    const v = new DataView(buf);
    v.setUint8(0, TYPE_TX);
    v.setUint8(1, 1);
    v.setUint32(4, this.txSeq++ >>> 0, true);
    v.setUint16(8, NET_RATE, true);
    v.setUint16(10, samples.length, true);
    new Int16Array(buf, HEADER).set(samples);
    return buf;
  }
}

// The radio's own codec must never be the browser's mic or speaker: it would
// loop receive audio into transmit.
export function isRadioCodec(label) { return /USB Audio CODEC/i.test(label || ""); }

export async function listDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) return { inputs: [], outputs: [] };
  const all = await navigator.mediaDevices.enumerateDevices();
  const pick = (kind) => all.filter((d) => d.kind === kind && d.deviceId && d.deviceId !== "default"
    && d.deviceId !== "communications");
  return { inputs: pick("audioinput"), outputs: pick("audiooutput") };
}

export function canPickSpeaker() {
  return typeof AudioContext !== "undefined" && typeof AudioContext.prototype.setSinkId === "function";
}
