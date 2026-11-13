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
  }

  get running() { return !!this.ctx; }

  async start({ playback, microphone, processing, volume }) {
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
    const ctx = new AudioContext({ latencyHint: "interactive" });
    this.ctx = ctx;
    await ctx.resume();
    await ctx.audioWorklet.addModule("/static/js/worklets/player.js");
    await ctx.audioWorklet.addModule("/static/js/worklets/capture.js");
    if (playback) {
      this.player = new AudioWorkletNode(ctx, "rx-player", { numberOfInputs: 0, outputChannelCount: [2] });
      this.player.port.onmessage = (ev) => this.onStats?.(ev.data);
      this.gain = new GainNode(ctx, { gain: volume });
      this.player.connect(this.gain).connect(ctx.destination);
    }
    if (stream) {
      this.mic = stream;
      const src = new MediaStreamAudioSourceNode(ctx, { mediaStream: stream });
      this.capture = new AudioWorkletNode(ctx, "mic-capture", { numberOfOutputs: 1 });
      this.capture.port.onmessage = (ev) => this.onMicFrame?.(ev.data.pcm, ev.data.peak);
      // Keep the capture node pulled by the graph without sending the mic to the speakers.
      const mute = new GainNode(ctx, { gain: 0 });
      src.connect(this.capture).connect(mute).connect(ctx.destination);
    }
  }

  async stop() {
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
    const samples = new Int16Array(buf.slice(HEADER));
    this.player.port.postMessage(samples, [samples.buffer]);
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
