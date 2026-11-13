// Microphone capture: device-rate float in, 20 ms frames of 16 kHz int16 out.
//
// Downsampling averages the input samples that fall in each output sample
// (a box filter). Voice for SSB is under 3 kHz, so this is adequate; the
// radio's own TX filter does the real band-limiting.
// Every frame also reports its peak so the page can show a level meter even
// when not transmitting.

const NET_RATE = 16000;
const FRAME = 320;  // 20 ms

class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / NET_RATE;
    this.acc = 0;
    this.accN = 0;
    this.pos = 0;         // input samples consumed into the current output sample
    this.frame = new Int16Array(FRAME);
    this.n = 0;
    this.peak = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      this.acc += input[i];
      this.accN++;
      this.pos++;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.acc / this.accN));
        this.acc = 0;
        this.accN = 0;
        const s = Math.round(v * 32767);
        this.frame[this.n++] = s;
        const a = Math.abs(v);
        if (a > this.peak) this.peak = a;
        if (this.n === FRAME) {
          this.port.postMessage({ pcm: this.frame, peak: this.peak }, [this.frame.buffer]);
          this.frame = new Int16Array(FRAME);
          this.n = 0;
          this.peak = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("mic-capture", MicCapture);
