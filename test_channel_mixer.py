"""channel_mixer 自测（标准库 unittest，直接 python3 test_channel_mixer.py 运行）。"""

import math
import unittest

from channel_mixer import (
    LAYOUTS, MixResult, default_matrix, deinterleave, detect_phase,
    float_to_int16, interleave, int16_to_float, mix, normalize_matrix,
    remap_channels,
)


def sine(n, freq=10.0, amp=1.0, sr=1000.0):
    return [amp * math.sin(2 * math.pi * freq * i / sr) for i in range(n)]


def stereo_frames(n, amp=1.0, invert_right=False):
    left = sine(n, amp=amp)
    sign = -1.0 if invert_right else 1.0
    return [[l, sign * l] for l in left]


class TestDefaultMatrix(unittest.TestCase):
    def test_downmix_51_to_stereo(self):
        m, ins, outs = default_matrix("5.1", "stereo")
        self.assertEqual(ins, LAYOUTS["5.1"])
        self.assertEqual(outs, ["L", "R"])
        l_row, r_row = m
        # L 行：L=1, C=0.7071, Ls=0.7071，不混入 R/Rs/LFE
        self.assertAlmostEqual(l_row[0], 1.0)
        self.assertAlmostEqual(l_row[2], 0.7071, places=4)
        self.assertAlmostEqual(l_row[4], 0.7071, places=4)
        self.assertEqual(l_row[1], 0.0)   # R 不进左声道
        self.assertEqual(l_row[3], 0.0)   # LFE 不进左右
        self.assertEqual(l_row[5], 0.0)   # Rs 不进左声道
        self.assertAlmostEqual(r_row[1], 1.0)

    def test_upmix_mono_to_stereo(self):
        m, _, _ = default_matrix("mono", "stereo")
        self.assertEqual(m, [[1.0], [1.0]])

    def test_downmix_stereo_to_mono(self):
        m, _, _ = default_matrix("stereo", "mono")
        self.assertEqual(m, [[0.5, 0.5]])

    def test_lfe_passthrough_only(self):
        m, _, outs = default_matrix("5.1", "5.1")
        lfe_row = m[outs.index("LFE")]
        self.assertEqual(lfe_row, [0.0, 0.0, 0.0, 1.0, 0.0, 0.0])


class TestNormalization(unittest.TestCase):
    def test_rowsum_never_clips(self):
        # 行绝对值和 = 1 => 输入 |x|<=1 时输出必然 |y|<=1
        m = normalize_matrix([[1.0, 1.0]], "rowsum")
        self.assertEqual(m, [[0.5, 0.5]])

    def test_energy_preserves_power(self):
        m = normalize_matrix([[3.0, 4.0]], "energy")
        self.assertAlmostEqual(m[0][0], 0.6)
        self.assertAlmostEqual(m[0][1], 0.8)

    def test_peak_keeps_relative_balance(self):
        m = normalize_matrix([[1.0, 1.0], [0.5, 0.0]], "peak")
        self.assertEqual(m, [[0.5, 0.5], [0.25, 0.0]])

    def test_none_is_identity(self):
        m = normalize_matrix([[2.0, 2.0]], "none")
        self.assertEqual(m, [[2.0, 2.0]])

    def test_zero_row_safe(self):
        m = normalize_matrix([[0.0, 0.0]], "rowsum")
        self.assertEqual(m, [[0.0, 0.0]])

    def test_bad_mode(self):
        with self.assertRaises(ValueError):
            normalize_matrix([[1.0]], "bogus")


class TestPhaseDetection(unittest.TestCase):
    def test_inverted_pair_detected(self):
        frames = stereo_frames(200, invert_right=True)
        issues = detect_phase(frames, "stereo")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].verdict, "inverted")
        self.assertAlmostEqual(issues[0].correlation, -1.0, places=6)
        self.assertEqual((issues[0].name_a, issues[0].name_b), ("L", "R"))

    def test_in_phase_pair_ok(self):
        issues = detect_phase(stereo_frames(200), "stereo")
        self.assertEqual(issues, [])

    def test_uncorrelated_not_flagged(self):
        frames = [[math.sin(i * 0.1), math.sin(i * 0.317 + 1.0)]
                  for i in range(500)]
        self.assertEqual(detect_phase(frames, "stereo"), [])

    def test_silent_channel_not_flagged(self):
        frames = [[s, 0.0] for s in sine(100)]
        self.assertEqual(detect_phase(frames, "stereo"), [])

    def test_multichannel_finds_the_flipped_one(self):
        base = sine(300)
        frames = [[base[i], base[i], -base[i], 0.0, base[i], base[i]]
                  for i in range(300)]
        issues = detect_phase(frames, "5.1")
        flipped = {i.name_b for i in issues} | {i.name_a for i in issues}
        self.assertIn("C", flipped)
        self.assertNotIn("LFE", flipped)

    def test_cancellation_when_ignored(self):
        # 反相直接相加 -> 完全抵消，输出为静音
        frames = stereo_frames(100, invert_right=True)
        r = mix(frames, "stereo", "mono", normalize="none",
                matrix=[[1.0, 1.0]], phase_policy="ignore")
        self.assertEqual(len(r.phase_issues), 1)
        self.assertAlmostEqual(r.peak_out, 0.0)

    def test_invert_policy_recovers_signal(self):
        frames = stereo_frames(100, invert_right=True)
        r = mix(frames, "stereo", "mono", normalize="none",
                matrix=[[1.0, 1.0]], phase_policy="invert", limiter="none")
        # 翻回正相后 L+R = 2*sin，峰值约 2
        self.assertAlmostEqual(r.peak_out, 2.0, delta=0.1)

    def test_mute_policy_avoids_cancellation(self):
        frames = stereo_frames(100, invert_right=True)
        r = mix(frames, "stereo", "mono", normalize="none",
                matrix=[[1.0, 1.0]], phase_policy="mute", limiter="none")
        self.assertAlmostEqual(r.peak_out, 1.0, delta=0.05)


class TestClipping(unittest.TestCase):
    def test_rowsum_downmix_no_clip(self):
        # 两个满量程同相声道，rowsum 归一化后不削顶
        frames = [[1.0, 1.0]] * 50
        r = mix(frames, "stereo", "mono", normalize="rowsum", limiter="none")
        self.assertLessEqual(r.peak_out, 1.0)
        self.assertEqual(r.clipped_count, 0)

    def test_unnormalized_would_clip_and_is_counted(self):
        frames = [[1.0, 1.0]] * 10
        r = mix(frames, "stereo", "mono", normalize="none",
                matrix=[[1.0, 1.0]], limiter="none")
        self.assertAlmostEqual(r.peak_in, 2.0)
        self.assertEqual(r.clipped_count, 10)  # 每帧 1 个输出样本超量

    def test_normalize_limiter_scales_and_reports(self):
        frames = [[1.0, 1.0]] * 10
        r = mix(frames, "stereo", "mono", normalize="none",
                matrix=[[1.0, 1.0]], limiter="normalize")
        self.assertEqual(r.clipped_count, 10)          # 报告限幅前的超量数
        self.assertAlmostEqual(r.peak_out, 1.0)
        self.assertAlmostEqual(r.gain_applied, 0.5)

    def test_headroom(self):
        frames = [[1.0, 1.0]] * 10
        r = mix(frames, "stereo", "mono", normalize="none",
                matrix=[[1.0, 1.0]], limiter="normalize", headroom_db=6.0)
        self.assertAlmostEqual(r.peak_out, 10 ** (-6.0 / 20.0), places=6)

    def test_clip_limiter_counts_and_clamps(self):
        frames = [[0.9, 0.9]] * 4
        r = mix(frames, "stereo", "mono", normalize="none",
                matrix=[[1.0, 1.0]], limiter="clip")
        self.assertEqual(r.clipped_count, 4)
        self.assertTrue(all(abs(s) <= 1.0 for f in r.frames for s in f))

    def test_near_full_scale_not_counted(self):
        # 恰好 1.0 不算削顶
        frames = [[0.5, 0.5]] * 8
        r = mix(frames, "stereo", "mono", normalize="none",
                matrix=[[1.0, 1.0]], limiter="none")
        self.assertAlmostEqual(r.peak_in, 1.0)
        self.assertEqual(r.clipped_count, 0)


class TestLayoutsAndRemap(unittest.TestCase):
    def test_mono_upmix_duplicates(self):
        frames = [[0.25], [-0.5]]
        r = mix(frames, "mono", "stereo", normalize="none")
        self.assertEqual(r.frames, [[0.25, 0.25], [-0.5, -0.5]])

    def test_51_downmix_folds_center_and_surround(self):
        # 只给 C 声道信号，应等分到 L/R（rowsum 后各行权重和为 1）
        frames = [[0.0, 0.0, 1.0, 0.0, 0.0, 0.0]]
        r = mix(frames, "5.1", "stereo", normalize="rowsum")
        left, right = r.frames[0]
        self.assertAlmostEqual(left, right, places=6)
        self.assertGreater(left, 0.0)

    def test_wrong_channel_order_gives_wrong_result(self):
        # 文件实际是 [R, L] 顺序，却按 [L, R] 声明 -> 立体声像被交换
        frames = [[0.8, 0.1]]  # 实际 R=0.8, L=0.1
        r = mix(frames, ["L", "R"], "stereo", normalize="none")
        self.assertEqual(r.frames[0], [0.8, 0.1])  # 错位：L 声道拿到 R 的内容

    def test_remap_fixes_channel_order(self):
        frames = [[0.8, 0.1]]  # 实际顺序 [R, L]
        fixed = remap_channels(frames, ["R", "L"], ["L", "R"])
        self.assertEqual(fixed, [[0.1, 0.8]])
        r = mix(fixed, "stereo", "stereo", normalize="none")
        self.assertEqual(r.frames[0], [0.1, 0.8])

    def test_remap_drops_and_fills(self):
        frames = [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]]
        out = remap_channels(frames, "5.1", "stereo")
        self.assertEqual(out, [[0.1, 0.2]])
        up = remap_channels([[0.1, 0.2]], "stereo", "5.1")
        self.assertEqual(up, [[0.1, 0.2, 0.0, 0.0, 0.0, 0.0]])

    def test_custom_matrix(self):
        # 自定义：只取右声道
        r = mix([[0.3, 0.9]], "stereo", "mono",
                matrix=[[0.0, 1.0]], normalize="none")
        self.assertAlmostEqual(r.frames[0][0], 0.9)

    def test_matrix_shape_checked(self):
        with self.assertRaises(ValueError):
            mix([[0.1, 0.2]], "stereo", "mono", matrix=[[1.0]])
        with self.assertRaises(ValueError):
            mix([[0.1, 0.2]], "stereo", "stereo", matrix=[[1.0, 0.0]])

    def test_frame_width_checked(self):
        with self.assertRaises(ValueError):
            mix([[0.1, 0.2, 0.3]], "stereo", "mono")


class TestEdgeCases(unittest.TestCase):
    def test_empty_input(self):
        r = mix([], "stereo", "mono")
        self.assertEqual(r.frames, [])
        self.assertEqual(r.peak_out, 0.0)
        self.assertEqual(r.clipped_count, 0)

    def test_all_silence(self):
        r = mix([[0.0, 0.0]] * 32, "stereo", "mono")
        self.assertEqual(r.peak_out, 0.0)
        self.assertEqual(r.gain_applied, 1.0)

    def test_dc_offset(self):
        r = mix([[1.0], [1.0]], "mono", "mono", normalize="none")
        self.assertEqual(r.frames, [[1.0], [1.0]])

    def test_negative_full_scale(self):
        r = mix([[-1.0, -1.0]], "stereo", "mono", normalize="none",
                matrix=[[1.0, 1.0]], limiter="clip")
        self.assertEqual(r.frames, [[-1.0]])
        self.assertEqual(r.clipped_count, 1)

    def test_int16_roundtrip_and_interleave(self):
        pcm = [0, 32767, -32768, 16384, -16384, 100]
        flt = int16_to_float(pcm)
        self.assertAlmostEqual(flt[1], 32767 / 32768.0)
        self.assertEqual(flt[2], -1.0)
        back = float_to_int16(flt)
        self.assertEqual(back[0], 0)
        self.assertEqual(back[2], -32767)  # 对称量化到 [-32767, 32767]
        frames = deinterleave(pcm[:4], 2)
        self.assertEqual(frames, [[0, 32767], [-32768, 16384]])
        self.assertEqual(interleave(frames), pcm[:4])
        with self.assertRaises(ValueError):
            deinterleave([1, 2, 3], 2)

    def test_result_metadata(self):
        r = mix(stereo_frames(64, invert_right=True), "stereo", "mono")
        self.assertIsInstance(r, MixResult)
        self.assertEqual(r.out_layout, ["M"])
        self.assertEqual(len(r.phase_issues), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
