// Receive audio player: 16 kHz int16 frames in, device-rate float out.
//
// Jitter buffer: playback starts once START_MS is buffered. If the buffer
// grows past MAX_MS (a burst after a stall), it skips ahead to TRIM_MS so
// latency stays bounded. On underrun it plays silence and re-primes.

const NET_RATE = 16000;
const START_MS = 80;
const MAX_MS = 400;
const TRIM_MS = 120;

class RxPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(NET_RATE * 2);  // 2 s ring
    this.read = 0;      // fractional read position, in samples
    this.write = 0;     // integer write position
    this.primed = false;
    this.step = NET_RATE / sampleRate;
    this.underruns = 0;
    this.trims = 0;
    this.lastReport = 0;
    this.port.onmessage = (ev) => this.push(ev.data);
  }

  get buffered() { return this.write - this.read; }

  push(samples) {  // Int16Array
    const n = samples.length, cap = this.buf.length;
    if (this.buffered + n > cap) this.read = this.write + n - cap;  // overflow: drop oldest
    for (let i = 0; i < n; i++) this.buf[(this.write + i) % cap] = samples[i] / 32768;
    this.write += n;
    if (this.buffered > (MAX_MS * NET_RATE) / 1000) {
      this.read = this.write - (TRIM_MS * NET_RATE) / 1000;
      this.trims++;
    }
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    const cap = this.buf.length;
    if (!this.primed && this.buffered >= (START_MS * NET_RATE) / 1000) this.primed = true;
    for (let i = 0; i < out.length; i++) {
      if (!this.primed || this.buffered < 2) {
        out[i] = 0;
        if (this.primed) { this.primed = false; this.underruns++; }
        continue;
      }
      const p = Math.floor(this.read), f = this.read - p;
      const a = this.buf[p % cap], b = this.buf[(p + 1) % cap];
      out[i] = a + (b - a) * f;
      this.read += this.step;
    }
    for (let ch = 1; ch < outputs[0].length; ch++) outputs[0][ch].set(out);
    if (currentTime - this.lastReport > 1) {
      this.lastReport = currentTime;
      this.port.postMessage({
        bufferedMs: Math.round((this.buffered * 1000) / NET_RATE),
        underruns: this.underruns, trims: this.trims,
      });
    }
    return true;
  }
}

registerProcessor("rx-player", RxPlayer);
