"""端到端演示：下混、相位检测与纠正、接近满量程处理。

运行: python3 demo.py
产物: out/ 目录下的 16-bit PCM WAV 文件，可直接试听对比。
"""

import math
import os

from channel_mixer import (
    correct_phase, detect_phase, mix, preset_matrix, read_wav, write_wav,
)

RATE = 48000
OUT = "out"


def sine(amp, freq, n, phase=0.0):
    return [amp * math.sin(2 * math.pi * freq * i / RATE + phase) for i in range(n)]


def show(title, result):
    print("-" * 60)
    print(title)
    print("  保护前峰值: %.4f  保护后峰值: %.4f" % (result.peak_before, result.peak_after))
    print("  施加增益: %.4f  超满量程样本数: %d  裁剪样本数: %d"
          % (result.gain_applied, result.overflow_samples, result.clipped_samples))


def main():
    os.makedirs(OUT, exist_ok=True)
    n = RATE // 2  # 0.5 秒

    # 1) 5.1 -> 立体声下混（声道顺序故意错位，结果不受影响）
    fl = sine(0.5, 440, n); fr = sine(0.5, 554, n)
    fc = sine(0.6, 330, n); sl = sine(0.3, 220, n); sr = sine(0.3, 277, n)
    wrong_order = ["FL", "FC", "FR", "SL", "SR", "LFE"]
    frames_51 = [list(p) for p in zip(fl, fc, fr, sl, sr, [0.0] * n)]
    dm = preset_matrix(wrong_order, "stereo")
    r = mix(frames_51, wrong_order, dm)
    write_wav(os.path.join(OUT, "downmix_51_to_stereo.wav"), r.frames, RATE)
    show("1) 5.1 -> 立体声 ITU 下混（错位顺序输入）", r)

    # 2) 相位检测样例：右声道整体反相
    left = sine(0.8, 440, n)
    frames_bad = [[l, -l] for l in left]
    rep = detect_phase(frames_bad, "stereo")
    print("-" * 60)
    print("2) 相位检测（右声道反相）")
    print(rep.summary())

    naive = mix(frames_bad, "stereo", preset_matrix("stereo", "mono"))
    show("   直接下混（抵消）", naive)
    write_wav(os.path.join(OUT, "phase_bad_mono.wav"), naive.frames, RATE)

    fixed = correct_phase(frames_bad, "stereo", rep)
    restored = mix(fixed, "stereo", preset_matrix("stereo", "mono"))
    show("   翻正后下混（恢复）", restored)
    write_wav(os.path.join(OUT, "phase_fixed_mono.wav"), restored.frames, RATE)

    # 3) 接近满量程：0.9 + 0.9 能量保持下混
    frames_loud = [[0.9, 0.9] for _ in range(n)]
    m = preset_matrix("stereo", "mono").normalized("energy")
    rg = mix(frames_loud, "stereo", m, ceiling=1.0, strategy="gain")
    show("3) 接近满量程 -> 静态增益保护（energy 归一化）", rg)
    write_wav(os.path.join(OUT, "loud_gain.wav"), rg.frames, RATE)

    rc = mix(frames_loud, "stereo", m, ceiling=1.0, strategy="clip")
    show("   对照：硬裁剪策略", rc)
    print("-" * 60)
    print("WAV 产物目录: %s/" % OUT)


if __name__ == "__main__":
    main()
