// マイク音声を16kHz・16bitのPCMに変換し、約1秒ごとに画面側へ渡す。
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.buffer = new Int16Array(16000);
    this.length = 0;
    this.sum = 0;
    this.count = 0;
    this.position = 0;
    this.peak = 0;
    // 停止時に、1秒に満たない残りの音声も渡す
    this.port.onmessage = () => {
      const rest = this.buffer.slice(0, this.length).buffer;
      this.length = 0;
      this.port.postMessage({ pcm: rest, peak: this.peak, last: true }, [rest]);
    };
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      const sample = channel[i];
      this.peak = Math.max(this.peak, Math.abs(sample));
      this.sum += sample;
      this.count++;
      this.position++;
      if (this.position >= this.ratio) {
        this.position -= this.ratio;
        const value = Math.max(-1, Math.min(1, this.sum / this.count));
        this.buffer[this.length++] = value < 0 ? value * 32768 : value * 32767;
        this.sum = 0;
        this.count = 0;
        if (this.length === this.buffer.length) {
          this.port.postMessage({ pcm: this.buffer.buffer, peak: this.peak }, [this.buffer.buffer]);
          this.buffer = new Int16Array(16000);
          this.length = 0;
          this.peak = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor('pcm-capture', PcmCapture);
