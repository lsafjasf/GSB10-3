#!/usr/bin/env python3
"""Self-tests for silence_split.py — synthesized audio, standard library only.

Run:  python3 -m unittest discover -s tests -v
"""
import math
import os
import random
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import silence_split as ss


# --------------------------------------------------------------------------- #
# audio synthesis helpers
# --------------------------------------------------------------------------- #
def add_tone(buf, rate, start, dur, freq=440.0, amp=0.4):
    a, b = int(start * rate), int((start + dur) * rate)
    for i in range(a, min(b, len(buf))):
        buf[i] += amp * math.sin(2 * math.pi * freq * (i - a) / rate)


def add_noise(buf, rate, start, dur, amp, seed):
    rng = random.Random(seed)
    a, b = int(start * rate), int((start + dur) * rate)
    for i in range(a, min(b, len(buf))):
        buf[i] += rng.gauss(0.0, amp)


def make_wav(rate, total_s, builder):
    buf = [0.0] * int(rate * total_s)
    builder(buf, rate)
    return buf, rate


def write_tmp_wav(buf, rate, width=2):
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    if width == 1:
        data = bytes(max(0, min(255, int(round(x * 127 + 128)))) for x in buf)
    elif width == 2:
        data = b"".join(struct.pack("<h", max(-32768, min(32767, int(round(x * 32767)))))
                     for x in buf)
    elif width == 4:
        data = b"".join(struct.pack("<i", max(-2147483648, min(2147483647,
                     int(round(x * 2147483647))))) for x in buf)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(data)
    return path


def analyze_buf(buf, rate, **kw):
    opts = dict(min_silence_s=0.4, margin_s=0.1, min_segment_s=1.0)
    opts.update(kw)
    return ss.analyze(buf, rate, **opts)


# --------------------------------------------------------------------------- #
# cases
# --------------------------------------------------------------------------- #
class TestAllDigitalSilence(unittest.TestCase):
    def test_zero_samples(self):
        buf, rate = make_wav(16000, 6.0, lambda b, r: None)
        rep = analyze_buf(buf, rate)
        self.assertEqual(rep["segments"], [])
        self.assertEqual(rep["silences"], [])            # no interior run
        self.assertEqual(rep["edge_silences"], [[0.0, 6.0]])
        self.assertEqual(rep["split_points"], [])
        self.assertEqual(rep["merge_count"], 0)
        self.assertTrue(any("all-silent" in n for n in rep["notes"]))

    def test_quiet_noise_only(self):
        def build(b, r):
            add_noise(b, r, 0, 6.0, amp=0.0005, seed=1)  # ~-66 dBFS
        buf, rate = make_wav(16000, 6.0, build)
        rep = analyze_buf(buf, rate)
        self.assertEqual(rep["segments"], [], "noise-only file must not split")
        # threshold sits above the noise bed and still marks everything silent
        self.assertGreater(rep["global_threshold_db"], rep["global_floor_db"])


class TestNoSilence(unittest.TestCase):
    def test_continuous_tone(self):
        def build(b, r):
            add_tone(b, r, 0, 6.0, amp=0.3)
            add_noise(b, r, 0, 6.0, amp=0.002, seed=2)
        buf, rate = make_wav(16000, 6.0, build)
        rep = analyze_buf(buf, rate)
        self.assertEqual(len(rep["segments"]), 1)
        a, c = rep["segments"][0]
        self.assertAlmostEqual(a, 0.0, places=3)
        self.assertAlmostEqual(c, 6.0, places=3)
        self.assertEqual(rep["silences"], [])
        self.assertEqual(rep["merge_count"], 0)
        self.assertTrue(any("no-silence" in n for n in rep["notes"]))


class TestBasicSplitAndMargins(unittest.TestCase):
    def test_three_bursts(self):
        def build(b, r):
            add_noise(b, r, 0, 10.0, amp=0.002, seed=3)
            for t in (0.3, 3.5, 6.7):
                add_tone(b, r, t, 2.2, amp=0.35)
        buf, rate = make_wav(16000, 10.0, build)
        rep = analyze_buf(buf, rate)
        self.assertEqual(len(rep["segments"]), 3)
        self.assertEqual(len(rep["silences"]), 2)
        margin = rep["margin_s"]
        for (s, e), p in zip(rep["silences"], rep["split_points"]):
            # cut strictly inside the silence run, margin on both sides
            self.assertGreaterEqual(p, s + margin - 1e-9)
            self.assertLessEqual(p, e - margin + 1e-9)
            self.assertTrue(s < p < e)
        # segments are contiguous and cover the whole file
        boundaries = [rep["segments"][0][0]] + [c for _, c in rep["segments"]]
        self.assertAlmostEqual(boundaries[0], 0.0, places=3)
        self.assertAlmostEqual(boundaries[-1], 10.0, places=3)
        for x, y in zip(boundaries, boundaries[1:]):
            self.assertLess(x, y)


class TestVaryingNoise(unittest.TestCase):
    def test_noise_steps_up_and_down(self):
        # noise bed: quiet 0-6s, loud 6-12s (~24 dB louder), quiet 12-18s.
        # A fixed absolute threshold either misses the silence inside the
        # loud band or cuts the quiet bands; the local floor must track both.
        def build(b, r):
            add_noise(b, r, 0.0, 6.0, amp=0.002, seed=4)
            add_noise(b, r, 6.0, 6.0, amp=0.03, seed=5)
            add_noise(b, r, 12.0, 6.0, amp=0.002, seed=6)
            # bursts straddle the band boundaries; the two gaps between
            # them sit fully inside the loud band and the quiet band
            add_tone(b, r, 4.5, 3.0, amp=0.4)   # 4.5-7.5  (crosses 6s)
            add_tone(b, r, 10.5, 3.0, amp=0.4)  # 10.5-13.5(crosses 12s)
            add_tone(b, r, 15.0, 2.5, amp=0.4)  # 15.0-17.5
        buf, rate = make_wav(16000, 18.0, build)
        rep = analyze_buf(buf, rate, window_s=4.0)
        times, floors = rep["times"], rep["floors"]

        def floor_near(t):
            i = min(range(len(times)), key=lambda k: abs(times[k] - t))
            return floors[i]

        quiet_floor = floor_near(3.5)   # noise-only point, quiet band
        loud_floor = floor_near(9.0)    # noise-only point, loud band
        self.assertGreater(loud_floor - quiet_floor, 12.0,
                           "local floor must rise with the noise bed")
        # one tone burst per band -> three surviving segments
        self.assertEqual(len(rep["segments"]), 3)
        # the gap between burst 1 and 2 (and between 2 and 3) is found
        self.assertEqual(len(rep["silences"]), 2)
        margin = rep["margin_s"]
        for (s, e), p in zip(rep["silences"], rep["split_points"]):
            self.assertGreaterEqual(p, s + margin - 2e-3)
            self.assertLessEqual(p, e - margin + 2e-3)

    def test_global_threshold_relation(self):
        def build(b, r):
            add_noise(b, r, 0, 8.0, amp=0.003, seed=7)
            add_tone(b, r, 2.0, 2.0, amp=0.3)
        buf, rate = make_wav(16000, 8.0, build)
        rep = analyze_buf(buf, rate)
        self.assertAlmostEqual(
            rep["global_threshold_db"],
            rep["global_floor_db"] + rep["delta_db"], places=6)


class TestSampleRates(unittest.TestCase):
    RATES = (8000, 16000, 44100, 48000)

    def test_same_structure_at_all_rates(self):
        def build(b, r):
            add_noise(b, r, 0, 9.0, amp=0.002, seed=8)
            for t in (0.3, 3.3, 6.3):
                add_tone(b, r, t, 2.0, amp=0.3)
        for rate in self.RATES:
            with self.subTest(rate=rate):
                buf, _ = make_wav(rate, 9.0, build)
                rep = analyze_buf(buf, rate)
                self.assertEqual(len(rep["segments"]), 3)
                self.assertEqual(len(rep["silences"]), 2)
                for (s, e), p in zip(rep["silences"], rep["split_points"]):
                    self.assertGreaterEqual(p, s + rep["margin_s"] - 2e-3)
                    self.assertLessEqual(p, e - rep["margin_s"] + 2e-3)
                self.assertAlmostEqual(rep["duration"], 9.0, places=2)

    def test_wav_roundtrip_widths(self):
        buf, rate = make_wav(16000, 1.0, lambda b, r: add_tone(b, r, 0, 1.0))
        for width in (1, 2, 4):
            with self.subTest(width=width):
                path = write_tmp_wav(buf, rate, width)
                try:
                    samples, r = ss.read_wav(path)
                    self.assertEqual(r, 16000)
                    self.assertEqual(len(samples), 16000)
                    self.assertTrue(any(abs(x) > 0.05 for x in samples))
                finally:
                    os.unlink(path)


class TestMergeShortSegments(unittest.TestCase):
    def test_short_burst_merges(self):
        # long, very short, long: the middle segment is below min_segment
        def build(b, r):
            add_noise(b, r, 0, 7.0, amp=0.002, seed=9)
            add_tone(b, r, 0.3, 2.2, amp=0.3)
            add_tone(b, r, 3.1, 0.3, amp=0.3)   # short -> must be merged
            add_tone(b, r, 4.0, 2.2, amp=0.3)
        buf, rate = make_wav(16000, 7.0, build)
        rep = analyze_buf(buf, rate, min_silence_s=0.4, margin_s=0.1,
                          min_segment_s=1.0)
        self.assertEqual(len(rep["raw_segments"]), 3)
        self.assertEqual(len(rep["segments"]), 2)
        self.assertEqual(rep["merge_count"], 1)
        # surviving segments cover the whole file
        self.assertAlmostEqual(rep["segments"][0][0], 0.0, places=3)
        self.assertAlmostEqual(rep["segments"][-1][1], 7.0, places=3)
        # the short burst survives only as part of a segment >= min length
        for a, b in rep["segments"]:
            self.assertGreaterEqual(b - a, 1.0 - 1e-9)

    def test_everything_short_collapses_to_one(self):
        segs = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.4)]
        out, merges = ss.merge_short_segments(segs, 2.0)
        self.assertEqual(out, [(0.0, 1.4)])
        self.assertEqual(merges, 2)

    def test_merge_with_shorter_neighbour(self):
        # the short middle segment merges with its shorter neighbour
        # (next, 1.0s) rather than the longer one (previous, 3.0s)
        segs = [(0.0, 3.0), (3.0, 3.5), (3.5, 4.5)]
        out, merges = ss.merge_short_segments(segs, 1.0)
        self.assertEqual(out, [(0.0, 3.0), (3.0, 4.5)])
        self.assertEqual(merges, 1)

    def test_merge_with_previous_when_last(self):
        segs = [(0.0, 5.0), (5.0, 5.4)]
        out, merges = ss.merge_short_segments(segs, 1.0)
        self.assertEqual(out, [(0.0, 5.4)])
        self.assertEqual(merges, 1)


class TestEndToEndFiles(unittest.TestCase):
    def test_split_wav_writes_parts(self):
        def build(b, r):
            add_noise(b, r, 0, 10.0, amp=0.002, seed=10)
            for t in (0.3, 3.5, 6.7):
                add_tone(b, r, t, 2.2, amp=0.35)
        buf, rate = make_wav(16000, 10.0, build)
        src = write_tmp_wav(buf, rate)
        d = tempfile.mkdtemp()
        try:
            rep = ss.split_wav(src, d, min_silence_s=0.4, margin_s=0.1,
                               min_segment_s=1.0)
            files = sorted(os.listdir(d))
            self.assertEqual(len(files), len(rep["segments"]))
            with wave.open(os.path.join(d, files[0])) as wf:
                self.assertEqual(wf.getframerate(), rate)
                self.assertGreater(wf.getnframes(), rate)  # > 1 s of audio
        finally:
            os.unlink(src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
