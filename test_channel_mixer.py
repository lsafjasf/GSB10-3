"""channel_mixer 自测：python3 test_channel_mixer.py -v"""

import math
import os
import tempfile
import unittest

from channel_mixer import (
    LAYOUTS, MixMatrix, MixResult, correct_phase, detect_phase, mix,
    preset_matrix, read_wav, write_wav,
)


def sine(amp, freq, n, rate=48000, phase=0.0):
    return [amp * math.sin(2 * math.pi * freq * i / rate + phase) for i in range(n)]


class TestBasicMixing(unittest.TestCase):
    def test_mono_to_stereo(self):
        frames = [[0.5], [-0.25]]
        m = preset_matrix("mono", "stereo")
        r = mix(frames, "mono", m)
        self.assertEqual(r.out_layout, ["FL", "FR"])
        self.assertEqual(r.frames, [[0.5, 0.5], [-0.25, -0.25]])
        self.assertEqual(r.gain_applied, 1.0)

    def test_stereo_to_mono(self):
        frames = [[0.8, 0.4]]
        m = preset_matrix("stereo", "mono")
        r = mix(frames, "stereo", m)
        self.assertAlmostEqual(r.frames[0][0], 0.6)

    def test_51_to_stereo_itu_downmix(self):
        # 只有中置有信号时，左右应各得 0.7071 倍
        frames = [[0.0, 0.0, 1.0, 0.0, 0.0, 0.0]]
        m = preset_matrix("5.1", "stereo")
        r = mix(frames, "5.1", m)
        self.assertAlmostEqual(r.frames[0][0], 0.7071, places=4)
        self.assertAlmostEqual(r.frames[0][1], 0.7071, places=4)

    def test_51_downmix_lfe_dropped(self):
        frames = [[0.0, 0.0, 0.0, 0.9, 0.0, 0.0]]
        m = preset_matrix("5.1", "stereo")
        r = mix(frames, "5.1", m)
        self.assertEqual(r.frames[0], [0.0, 0.0])

    def test_stereo_to_51_upmix(self):
        frames = [[1.0, 0.0]]
        m = preset_matrix("stereo", "5.1")
        r = mix(frames, "stereo", m)
        fl, fr, fc, lfe, sl, sr = r.frames[0]
        self.assertEqual((fl, fr), (1.0, 0.0))
        self.assertAlmostEqual(fc, 0.5)
        self.assertAlmostEqual(sl, 0.5)
        self.assertAlmostEqual(sr, -0.5)
        self.assertEqual(lfe, 0.0)

    def test_custom_matrix(self):
        m = MixMatrix.from_rows(["L", "R"], ["A", "B"], [[0.9, 0.1], [0.1, 0.9]])
        r = mix([[1.0, -1.0]], ["A", "B"], m)
        self.assertAlmostEqual(r.frames[0][0], 0.8)
        self.assertAlmostEqual(r.frames[0][1], -0.8)


class TestChannelOrder(unittest.TestCase):
    def test_channel_order_misalignment(self):
        # 同一份 5.1 信号，两种物理声道顺序，下混结果必须一致
        sig = {"FL": 0.1, "FR": 0.2, "FC": 0.3, "LFE": 0.4, "SL": 0.5, "SR": 0.6}
        order_a = ["FL", "FR", "FC", "LFE", "SL", "SR"]          # SMPTE
        order_b = ["FL", "FC", "FR", "SL", "SR", "LFE"]          # 错位顺序
        fa = [[sig[c] for c in order_a]]
        fb = [[sig[c] for c in order_b]]
        m = preset_matrix("5.1", "stereo")
        ra = mix(fa, order_a, m)
        rb = mix(fb, order_b, m)
        self.assertEqual(ra.frames, rb.frames)

    def test_matrix_references_missing_channel_raises(self):
        m = preset_matrix("5.1", "stereo")
        with self.assertRaises(ValueError):
            mix([[0.1, 0.2]], "stereo", m)  # 矩阵引用 FC/SL/SR，输入没有

    def test_unknown_layout_raises(self):
        with self.assertRaises(ValueError):
            mix([[0.0]], "9.9", preset_matrix("mono", "stereo"))


class TestNormalization(unittest.TestCase):
    def test_rowsum_is_peak_safe(self):
        m = preset_matrix("5.1", "stereo").normalized("rowsum")
        for row in ("FL", "FR"):
            s = sum(abs(w) for w in m.row(row).values())
            self.assertAlmostEqual(s, 1.0)
        # 所有输入满幅同号，输出也不超过满幅
        r = mix([[1.0] * 6], "5.1", m)
        self.assertLessEqual(r.peak_before, 1.0)

    def test_energy_normalization(self):
        m = preset_matrix("5.1", "stereo").normalized("energy")
        ss = sum(w * w for w in m.row("FL").values())
        self.assertAlmostEqual(ss, 1.0)

    def test_none_normalization_keeps_weights(self):
        m = preset_matrix("5.1", "stereo").normalized("none")
        self.assertAlmostEqual(m.get("FL", "FC"), 0.7071, places=4)

    def test_bad_mode_raises(self):
        with self.assertRaises(ValueError):
            preset_matrix("5.1", "stereo").normalized("bogus")


class TestPhase(unittest.TestCase):
    def test_detect_inverted_channel(self):
        n = 4800
        left = sine(0.8, 440, n)
        right = [-x for x in left]  # 右声道整体反相
        frames = [list(p) for p in zip(left, right)]
        rep = detect_phase(frames, "stereo")
        self.assertEqual(rep.polarity["FL"], 1)
        self.assertEqual(rep.polarity["FR"], -1)
        self.assertEqual(rep.inverted, ["FR"])
        self.assertTrue(any(p[2] < -0.99 for p in rep.inverted_pairs))

    def test_no_false_positive_on_normal_stereo(self):
        n = 4800
        frames = [list(p) for p in zip(sine(0.8, 440, n), sine(0.6, 550, n))]
        rep = detect_phase(frames, "stereo")
        self.assertEqual(rep.inverted, [])
        self.assertEqual(rep.inverted_pairs, [])

    def test_phase_cancellation_and_correction(self):
        # 反相立体声直接下混成单声道会抵消；翻正后恢复
        n = 4800
        left = sine(0.8, 440, n)
        frames = [[l, -l] for l in left]
        m = preset_matrix("stereo", "mono")
        naive = mix(frames, "stereo", m)
        self.assertAlmostEqual(naive.peak_after, 0.0, places=7)  # 完全抵消

        rep = detect_phase(frames, "stereo")
        fixed = correct_phase(frames, "stereo", rep)
        restored = mix(fixed, "stereo", m)
        self.assertAlmostEqual(restored.peak_after, 0.8, places=3)

    def test_multichannel_phase_detection(self):
        n = 4800
        base = sine(0.7, 220, n)
        frames = [[b, b, b, 0.0, -b, b] for b in base]  # SL 反相
        rep = detect_phase(frames, "5.1")
        self.assertIn("SL", rep.inverted)
        self.assertNotIn("SR", rep.inverted)


class TestClipping(unittest.TestCase):
    def test_gain_strategy_prevents_clipping(self):
        # 接近满量程：0.9 + 0.9，等权 0.7071 下混 -> 峰值约 1.27
        n = 1000
        frames = [[0.9, 0.9]] * n
        m = MixMatrix.from_rows(["M"], ["FL", "FR"], [[0.7071, 0.7071]])
        r = mix(frames, "stereo", m, ceiling=1.0, strategy="gain")
        self.assertGreater(r.peak_before, 1.0)
        self.assertLessEqual(r.peak_after, 1.0)
        self.assertEqual(r.overflow_samples, n)      # 每帧都超满量程
        self.assertEqual(r.clipped_samples, 0)       # gain 策略不裁剪
        self.assertAlmostEqual(r.gain_applied, 1.0 / r.peak_before, places=6)

    def test_clip_strategy_counts_clipped_samples(self):
        frames = [[0.9, 0.9]] * 500
        m = MixMatrix.from_rows(["M"], ["FL", "FR"], [[0.7071, 0.7071]])
        r = mix(frames, "stereo", m, strategy="clip")
        self.assertEqual(r.clipped_samples, 500)
        self.assertLessEqual(r.peak_after, 1.0)

    def test_custom_ceiling_headroom(self):
        frames = [[0.95, 0.95]] * 100
        m = MixMatrix.from_rows(["M"], ["FL", "FR"], [[0.7071, 0.7071]])
        r = mix(frames, "stereo", m, ceiling=0.891)  # 约 -1 dBFS 余量
        self.assertLessEqual(r.peak_after, 0.891 + 1e-12)
        self.assertEqual(r.overflow_samples, 100)

    def test_no_protection_when_under_ceiling(self):
        r = mix([[0.3, 0.3]], "stereo", preset_matrix("stereo", "mono"))
        self.assertEqual(r.gain_applied, 1.0)
        self.assertEqual(r.overflow_samples, 0)


class TestEdgeCases(unittest.TestCase):
    def test_empty_frames(self):
        r = mix([], "stereo", preset_matrix("stereo", "mono"))
        self.assertEqual(r.frames, [])
        self.assertEqual(r.peak_after, 0.0)

    def test_frame_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            mix([[0.1, 0.2, 0.3]], "stereo", preset_matrix("stereo", "mono"))

    def test_silent_channel_phase_detection(self):
        frames = [[0.5, 0.0]] * 100
        rep = detect_phase(frames, "stereo")
        self.assertEqual(rep.inverted, [])  # 静音声道不误判反相

    def test_wav_roundtrip(self):
        frames = [[0.5, -0.5], [0.25, -0.25]]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.wav")
            write_wav(p, frames, 48000)
            back, layout, rate = read_wav(p)
        self.assertEqual(layout, ["FL", "FR"])
        self.assertEqual(rate, 48000)
        self.assertAlmostEqual(back[0][0], 0.5, places=4)
        self.assertAlmostEqual(back[0][1], -0.5, places=4)


if __name__ == "__main__":
    unittest.main()
