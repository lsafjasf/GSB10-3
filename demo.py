"""演示：相位检测样例 + 下混/上混 + 防削顶报告。运行: python3 demo.py"""

import math

from channel_mixer import detect_phase, mix, remap_channels


def sine(n, freq=10.0, amp=1.0, sr=1000.0):
    return [amp * math.sin(2 * math.pi * freq * i / sr) for i in range(n)]


print("=== 1. 相位检测样例：右声道被反相的立体声 ===")
sig = sine(500)
frames = [[s, -s] for s in sig]          # R 被错误反相
issues = detect_phase(frames, "stereo")
for i in issues:
    print(f"  反相: {i.name_a} vs {i.name_b}, 相关系数 = {i.correlation:+.3f}, 判定 = {i.verdict}")

r = mix(frames, "stereo", "mono", matrix=[[1.0, 1.0]], normalize="none",
        phase_policy="ignore", limiter="none")
print(f"  策略 ignore : 直接相加 -> 峰值 {r.peak_out:.3f}（完全抵消，静音）")
r = mix(frames, "stereo", "mono", matrix=[[1.0, 1.0]], normalize="none",
        phase_policy="invert", limiter="none")
print(f"  策略 invert : 翻回正相再混 -> 峰值 {r.peak_out:.3f}（信号恢复）")
r = mix(frames, "stereo", "mono", matrix=[[1.0, 1.0]], normalize="none",
        phase_policy="mute", limiter="none")
print(f"  策略 mute   : 屏蔽反相路 -> 峰值 {r.peak_out:.3f}（保留一路）")

print("\n=== 2. 5.1 下混立体声（ITU 风格默认矩阵 + rowsum 归一化）===")
n = 200
frames_51 = [[sine(n, 8)[i], sine(n, 8, amp=0.6)[i], sine(n, 12, amp=0.9)[i],
              0.0, sine(n, 5, amp=0.4)[i], sine(n, 5, amp=0.4)[i]]
             for i in range(n)]
r = mix(frames_51, "5.1", "stereo")
print(f"  输出布局 {r.out_layout}, 峰值 {r.peak_out:.3f}, 裁剪样本数 {r.clipped_count}")

print("\n=== 3. 接近满量程：两个满幅同相声道相加 ===")
hot = [[1.0, 1.0]] * 16
r = mix(hot, "stereo", "mono", matrix=[[1.0, 1.0]], normalize="none",
        limiter="normalize", headroom_db=1.0)
print(f"  限幅前峰值 {r.peak_in:.2f}，超量样本 {r.clipped_count} 个")
print(f"  limiter=normalize + 1dB 余量 -> 增益 {r.gain_applied:.4f}, 输出峰值 {r.peak_out:.3f}")
r = mix(hot, "stereo", "mono", matrix=[[1.0, 1.0]], normalize="none",
        limiter="clip")
print(f"  limiter=clip -> 输出峰值 {r.peak_out:.3f}，被裁剪样本 {r.clipped_count} 个")

print("\n=== 4. 通道顺序错位：文件实为 [R, L] 却声明为 [L, R] ===")
mislabeled = [[0.9, 0.2]]               # 实际是 R=0.9, L=0.2
wrong = mix(mislabeled, ["L", "R"], "stereo", normalize="none")
print(f"  直接混（错位）: L={wrong.frames[0][0]}, R={wrong.frames[0][1]}  <- 声像左右颠倒")
fixed = remap_channels(mislabeled, ["R", "L"], ["L", "R"])
right = mix(fixed, "stereo", "stereo", normalize="none")
print(f"  remap 修正后 : L={right.frames[0][0]}, R={right.frames[0][1]}")

print("\n=== 5. 单声道 <-> 立体声 / 上混 ===")
r = mix([[0.5], [-0.5]], "mono", "stereo", normalize="none")
print(f"  单声道 -> 立体声: {r.frames}")
r = mix([[0.4, 0.4]], "stereo", "mono")
print(f"  立体声 -> 单声道(rowsum): {r.frames}")
