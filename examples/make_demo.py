#!/usr/bin/env python3
"""Synthesize a demo recording that exercises every tricky case:

- a noise bed that steps quiet -> loud (~24 dB up) -> quiet
- several tone "utterances" separated by silence
- one very short utterance (triggers segment merging)
- long trailing silence (edge silence, must not create a cut)

Run:  python3 examples/make_demo.py [out.wav]
"""
import math
import os
import random
import struct
import sys
import wave

RATE = 16000
TOTAL = 24.0


def add_tone(buf, start, dur, freq=440.0, amp=0.4):
    a, b = int(start * RATE), int((start + dur) * RATE)
    for i in range(a, min(b, len(buf))):
        env = min(1.0, (i - a) / 200.0, (b - i) / 200.0)  # fade edges
        buf[i] += env * amp * math.sin(2 * math.pi * freq * (i - a) / RATE)


def add_noise(buf, start, dur, amp, seed):
    rng = random.Random(seed)
    a, b = int(start * RATE), int((start + dur) * RATE)
    for i in range(a, min(b, len(buf))):
        buf[i] += rng.gauss(0.0, amp)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "demo.wav")
    buf = [0.0] * int(RATE * TOTAL)
    # noise bed: quiet 0-8s, loud 8-16s, quiet 16-24s
    add_noise(buf, 0.0, 8.0, amp=0.002, seed=100)
    add_noise(buf, 8.0, 8.0, amp=0.03, seed=101)
    add_noise(buf, 16.0, 8.0, amp=0.002, seed=102)
    # utterances
    add_tone(buf, 0.5, 3.0)       # long
    add_tone(buf, 4.5, 2.5)       # long (gap 4.0s before it? -> 1.0s gap)
    add_tone(buf, 9.5, 2.0)       # long, inside loud band
    add_tone(buf, 12.5, 0.3)      # SHORT -> must be merged
    add_tone(buf, 15.0, 2.5)      # long, crosses back into quiet band
    # 17.5-24.0 trailing silence is deliberate

    pcm = b"".join(struct.pack(
        "<h", max(-32768, min(32767, int(round(x * 32767))))) for x in buf)
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(pcm)
    print("wrote %s (%.1fs, %d Hz)" % (out, TOTAL, RATE))


if __name__ == "__main__":
    main()
