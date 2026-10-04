"""audionorm 自测：生成合成信号，验证归一/淡入淡出，输出对比数据。仅标准库。"""
import math
import os
import tempfile

from audionorm import (Audio, concat, crossfade, loudness_report, normalize_gain,
                       read_wav, write_wav, apply_fade, NEG_INF)

SR = 8000  # 自测用低采样率，数据量小


def sine(freq, amp, dur, sr=SR):
    n = int(dur * sr)
    return Audio([[amp * math.sin(2 * math.pi * freq * i / sr) for i in range(n)]], sr)


def silence(dur, sr=SR):
    return Audio([[0.0] * int(dur * sr)], sr)


def square(freq, amp, dur, sr=SR):
    n = int(dur * sr)
    return Audio([[amp if math.sin(2 * math.pi * freq * i / sr) >= 0 else -amp
                   for i in range(n)]], sr)


def pulses(dur, period=200, sr=SR):
    """满幅脉冲串：峰值 1.0（0 dBFS），RMS 很低（高波峰因数）。"""
    n = int(dur * sr)
    return Audio([[1.0 if i % period == 0 else 0.0 for i in range(n)]], sr)


def fmt(db):
    return "  -inf " if db == NEG_INF else f"{db:+7.2f}"


def main():
    print("=" * 78)
    print("1) 响度口径对比（peak dBFS vs rms dBFS，crest = peak - rms）")
    print("=" * 78)
    segs = {
        "sine_0.05 (微弱正弦)": sine(440, 0.05, 0.5),
        "sine_0.50 (中等正弦)": sine(440, 0.50, 0.5),
        "silence   (静音段)  ": silence(0.5),
        "square_1.0(全幅方波)": square(220, 1.0, 0.5),
        "pulses(满幅脉冲串)  ": pulses(0.5),
        "tiny_32smp(极短片段)": sine(440, 0.30, 32 / SR),
    }
    print(f"{'素材':<26}{'peak dBFS':>10}{'rms dBFS':>10}{'crest dB':>10}")
    for name, a in segs.items():
        r = loudness_report(a)
        print(f"{name:<26}{fmt(r['peak_dbfs']):>10}{fmt(r['rms_dbfs']):>10}"
              f"{fmt(r['crest_db']):>10}")

    print()
    print("=" * 78)
    print("2) 增益归一（目标 -20 dBFS RMS，ceiling -0.3 dBFS，超限则压增益保不削顶）")
    print("=" * 78)
    hdr = (f"{'素材':<26}{'测量':>8}{'请求增益':>9}{'实际增益':>9}"
           f"{'限制':>5}{'跳过':>5}{'将削顶':>7}{'实削顶':>7}")
    print(hdr)
    normed = {}
    for name, a in segs.items():
        w = a.copy()
        rep = normalize_gain(w, target_dbfs=-20.0, metric="rms", ceiling_dbfs=-0.3)
        normed[name] = w
        print(f"{name:<26}{fmt(rep.measured_dbfs):>8}"
              f"{fmt(rep.requested_gain_db):>9}{fmt(rep.applied_gain_db):>9}"
              f"{str(rep.limited):>6}{str(rep.skipped):>5}"
              f"{rep.would_clip_samples:>7}{rep.clipped_samples:>7}")
        # 断言：应用后绝不削顶
        assert rep.clipped_samples == 0, "出现硬钳位样本"
        peak_after = max(abs(s) for ch in w.channels for s in ch) if w.n_samples else 0
        assert peak_after <= 10 ** (-0.3 / 20) + 1e-9, "超过 ceiling"

    print()
    print("=" * 78)
    print("3) 拼接处淡入淡出能量变化（归一后素材，交叉淡出 50ms）")
    print("=" * 78)
    order = ["sine_0.05 (微弱正弦)", "sine_0.50 (中等正弦)",
             "silence   (静音段)  ", "square_1.0(全幅方波)",
             "pulses(满幅脉冲串)  "]
    for curve in ("linear", "sine", "exp"):
        joined, reports = concat([normed[n] for n in order], fade_s=0.05, curve=curve)
        print(f"-- 曲线 {curve:<7} 总时长 {joined.duration:.3f}s")
        print(f"   {'拼接点':<22}{'点前rms':>9}{'重叠rms':>9}{'点后rms':>9}"
              f"{'入跳变dB':>9}{'出跳变dB':>9}")
        for i, rep in enumerate(reports):
            label = f"{order[i][:9]}->{order[i+1][:9]}"
            print(f"   {label:<22}{fmt(rep.rms_before_dbfs):>9}"
                  f"{fmt(rep.rms_overlap_dbfs):>9}{fmt(rep.rms_after_dbfs):>9}"
                  f"{fmt(rep.delta_in_db):>9}{fmt(rep.delta_out_db):>9}")

    print()
    print("=" * 78)
    print("4) 边界用例断言")
    print("=" * 78)

    # 4.1 极短片段：淡入淡出时长自动收缩，不崩溃
    tiny = segs["tiny_32smp(极短片段)"].copy()
    n = apply_fade(tiny, 1.0, "sine", fade_in=True)
    assert n == 32, f"淡入应收缩到 32 样本, got {n}"
    j, rep = crossfade(tiny, sine(440, 0.3, 0.1), 1.0, "linear")
    assert rep.overlap_samples == 32, "交叉淡出应收缩到短段长度"
    print("ok 极短片段: 1s 淡入收缩到 32 样本; 交叉淡出收缩到短段长度")

    # 4.2 静音段：跳过归一，增益 0 dB，不产生 inf/nan
    sil = silence(0.2)
    rep = normalize_gain(sil, -20.0, "rms")
    assert rep.skipped and rep.applied_gain_db == 0.0
    assert all(s == 0.0 for s in sil.channels[0])
    print("ok 静音段: 跳过归一, 增益 0 dB, 输出仍为静音")

    # 4.3 全幅信号 + 请求增益>0dB：脉冲串 RMS 低，放大后峰值必削顶 -> 限制
    full = pulses(0.2)
    rep = normalize_gain(full, -15.0, "rms", ceiling_dbfs=-0.3)
    assert rep.limited and rep.requested_gain_db > 0
    assert abs(rep.applied_gain_db - (-0.3)) < 1e-9
    assert rep.would_clip_samples > 0 and rep.clipped_samples == 0
    print(f"ok 全幅脉冲: 请求 +{rep.requested_gain_db:.2f} dB -> 峰值限制到 "
          f"{rep.applied_gain_db:+.2f} dB, 避免 {rep.would_clip_samples} 个削顶样本")

    # 4.4 增益超过 0 dB（正常放大，不触限）
    quiet = sine(440, 0.01, 0.2)  # rms≈-43dB -> 需 +23dB
    rep = normalize_gain(quiet, -20.0, "rms")
    assert rep.applied_gain_db > 0 and not rep.limited
    assert abs(max(abs(s) for s in quiet.channels[0]) - 0.01 * 10**(rep.applied_gain_db/20)) < 1e-9
    print(f"ok 增益>0dB: 微弱信号放大 {rep.applied_gain_db:+.2f} dB, 未触限")

    # 4.5 峰值口径归一
    p = sine(440, 0.1, 0.2)
    rep = normalize_gain(p, -6.0, "peak")
    assert abs(max(abs(s) for s in p.channels[0]) - 10**(-6/20)) < 1e-6
    print("ok peak 口径: 峰值精确归一到 -6 dBFS")

    # 4.6 WAV 读写往返
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.wav")
        src = sine(440, 0.5, 0.1)
        write_wav(src, path)
        back = read_wav(path)
        assert back.sample_rate == SR and back.n_samples == src.n_samples
        err = max(abs(a - b) for a, b in zip(src.channels[0], back.channels[0]))
        assert err < 1.0 / 32767, f"16bit 往返误差 {err}"
    print("ok WAV 16bit 读写往返误差 < 1 LSB")

    print()
    print("全部断言通过。")


if __name__ == "__main__":
    main()
