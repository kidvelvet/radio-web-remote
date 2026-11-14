// Receive audio player: 16 kHz int16 frames in, device-rate float out.
//
// Adaptive jitter buffer. Playback starts once `target` ms are buffered.
// Wi-Fi links (iPad, phones) stall now and then, after which TCP delivers the
// backlog in a burst, so:
//   - an underrun (gap) raises the target by STEP_UP_MS, up to MAX_TARGET_MS;
//   - DECAY_S seconds without a gap lowers it by STEP_DOWN_MS, down to MIN_TARGET_MS;
//   - when more than CATCHUP_MS above target, playback runs CATCHUP_RATE faster
//     (a slight pitch rise, no skip) until it is back on target;
//   - only a backlog more than HARD_TRIM_MS above target is skipped outright.

const NET_RATE = 16000;
const MIN_TARGET_MS = 80;
const MAX_TARGET_MS = 400;
const STEP_UP_MS = 40;
const STEP_DOWN_MS = 20;
const DECAY_S = 30;
const CATCHUP_MS = 60;
const CATCHUP_RATE = 1.03;
const HARD_TRIM_MS = 600;

const ms = (n) => (n * NET_RATE) / 1000;

class RxPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(NET_RATE * 2);  // 2 s ring
    this.read = 0;      // fractional read position, in samples
    this.write = 0;     // integer write position
    this.primed = false;
    this.step = NET_RATE / sampleRate;
    this.targetMs = MIN_TARGET_MS;
    this.catchingUp = false;
    this.lastGap = currentTime;
    this.underruns = 0;
    this.trims = 0;
    this.peak = 0;
    this.lastReport = 0;
    this.port.onmessage = (ev) => this.push(ev.data);
  }

  get buffered() { return this.write - this.read; }

  push(samples) {  // Int16Array
    const n = samples.length, cap = this.buf.length;
    if (this.buffered + n > cap) this.read = this.write + n - cap;  // overflow: drop oldest
    for (let i = 0; i < n; i++) {
      const v = samples[i] / 32768;
      this.buf[(this.write + i) % cap] = v;
      const a = v < 0 ? -v : v;
      if (a > this.peak) this.peak = a;
    }
    this.write += n;
    if (this.primed && this.buffered > ms(this.targetMs + HARD_TRIM_MS)) {
      this.read = this.write - ms(this.targetMs);
      this.trims++;
    }
  }

  gap() {
    this.primed = false;
    this.underruns++;
    this.lastGap = currentTime;
    this.targetMs = Math.min(MAX_TARGET_MS, this.targetMs + STEP_UP_MS);
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    const cap = this.buf.length;
    if (!this.primed && this.buffered >= ms(this.targetMs)) this.primed = true;
    if (this.targetMs > MIN_TARGET_MS && currentTime - this.lastGap > DECAY_S) {
      this.targetMs = Math.max(MIN_TARGET_MS, this.targetMs - STEP_DOWN_MS);
      this.lastGap = currentTime;
    }
    // Hysteresis: start catching up above target + CATCHUP_MS, stop at target.
    const extra = this.buffered - ms(this.targetMs);
    if (extra > ms(CATCHUP_MS)) this.catchingUp = true;
    else if (extra <= 0) this.catchingUp = false;
    const step = this.catchingUp ? this.step * CATCHUP_RATE : this.step;
    for (let i = 0; i < out.length; i++) {
      if (!this.primed || this.buffered < 2) {
        out[i] = 0;
        if (this.primed) this.gap();
        continue;
      }
      const p = Math.floor(this.read), f = this.read - p;
      const a = this.buf[p % cap], b = this.buf[(p + 1) % cap];
      out[i] = a + (b - a) * f;
      this.read += step;
    }
    for (let ch = 1; ch < outputs[0].length; ch++) outputs[0][ch].set(out);
    if (currentTime - this.lastReport > 1) {
      this.lastReport = currentTime;
      this.port.postMessage({
        bufferedMs: Math.round((this.buffered * 1000) / NET_RATE),
        targetMs: this.targetMs,
        underruns: this.underruns, trims: this.trims,
        peakDb: this.peak > 0 ? Math.round(20 * Math.log10(this.peak)) : -120,
      });
      this.peak = 0;
    }
    return true;
  }
}

registerProcessor("rx-player", RxPlayer);
