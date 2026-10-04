"""channel_mixer —— 多声道混音与声道映射库（仅 Python 标准库）。

核心概念
--------
- 采样帧(frames): list[list[float]]，每个内层列表是一帧，长度 = 声道数，
  取值约定为 [-1.0, 1.0] 的浮点（满量程 = 1.0）。
- 布局(layout): 声道名列表，如 ["L", "R"]、["L","R","C","LFE","Ls","Rs"]，
  用来解决"通道顺序错位"问题：矩阵按声道名对齐，而不是按下标硬对。
- 权重矩阵(matrix): matrix[out_ch][in_ch]，行 = 输出声道，列 = 输入声道。
- 归一化(normalization): 见 normalize_matrix() 的文档字符串。
- 相位检测: 用余弦相似度判断两声道是否反相（接近 -1 即反相）。
- 防削顶: 混音后峰值超过满量程时，按 limiter 策略做整体增益或硬裁剪，
  并报告被裁剪/超量的样本数。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 布局与默认矩阵
# ---------------------------------------------------------------------------

LAYOUTS = {
    "mono":   ["M"],
    "stereo": ["L", "R"],
    "2.1":    ["L", "R", "LFE"],
    "4.0":    ["L", "R", "Ls", "Rs"],
    "5.1":    ["L", "R", "C", "LFE", "Ls", "Rs"],
    "7.1":    ["L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs"],
}

# 左/右声群：用于把环绕折回前方、或从中央/单声道派生前置。
_LEFT_GROUP = ("L", "Ls", "Lrs")
_RIGHT_GROUP = ("R", "Rs", "Rrs")

# 默认权重规则表：default_matrix() 按输出声道名查表。
# 数值采用常见下混约定：中央 -3dB(0.7071) 入左右，环绕 -3dB 折回同侧。
_DEFAULT_RULES = {
    "L":   {"L": 1.0, "M": 1.0, "C": 0.7071, "Ls": 0.7071, "Lrs": 0.7071},
    "R":   {"R": 1.0, "M": 1.0, "C": 0.7071, "Rs": 0.7071, "Rrs": 0.7071},
    "C":   {"L": 0.7071, "R": 0.7071, "M": 1.0, "C": 1.0},
    "M":   {"M": 1.0, "L": 0.5, "R": 0.5, "C": 0.7071, "Ls": 0.3536, "Rs": 0.3536},
    "LFE": {"LFE": 1.0},          # LFE 只透传，不向其它声道泄漏，也不合成
    "Ls":  {"Ls": 1.0, "Lrs": 0.7071},
    "Rs":  {"Rs": 1.0, "Rrs": 0.7071},
    "Lrs": {"Lrs": 1.0, "Ls": 0.7071},
    "Rrs": {"Rrs": 1.0, "Rs": 0.7071},
}


def default_matrix(in_layout, out_layout):
    """按声道名生成默认混音矩阵（行=输出，列=输入）。

    同名声道权重 1.0；中央/环绕按 _DEFAULT_RULES 折叠；没有规则的输入
    声道不进入该输出（权重 0）。返回 (matrix, in_names, out_names)。
    """
    in_names = _names(in_layout)
    out_names = _names(out_layout)
    matrix = []
    for out_ch in out_names:
        rules = _DEFAULT_RULES.get(out_ch, {out_ch: 1.0})
        row = [rules.get(in_ch, 0.0) for in_ch in in_names]
        matrix.append(row)
    return matrix, in_names, out_names


def _names(layout):
    """layout 可以是布局名（"stereo"）或声道名列表。"""
    if isinstance(layout, str):
        if layout not in LAYOUTS:
            raise ValueError(f"未知布局: {layout!r}，可选: {sorted(LAYOUTS)}")
        return list(LAYOUTS[layout])
    return list(layout)


# ---------------------------------------------------------------------------
# 矩阵归一化
# ---------------------------------------------------------------------------

def normalize_matrix(matrix, mode="rowsum"):
    """矩阵归一化。四种方式：

    - "none"   : 不归一化，原样使用（调用方对电平负责）。
    - "rowsum" : 每行除以该行 |权重| 之和。保证任一输出声道 |out| <= max|in|，
                 输入不削顶则输出绝不削顶；代价是相关信号（如同相双声道）
                 响度会偏保守。
    - "energy" : 每行除以 sqrt(权重平方和)。对不相关信号保持能量/响度，
                 但对相关信号仍可能超满量程（需配合 limiter）。
    - "peak"   : 全矩阵统一除以"最大行绝对值和"。只加一个整体增益，
                 保持声道间相对平衡，同样保证不削顶。
    """
    if mode == "none":
        return [row[:] for row in matrix]
    if mode == "rowsum":
        out = []
        for row in matrix:
            s = sum(abs(w) for w in row)
            out.append([w / s if s > 0 else 0.0 for w in row])
        return out
    if mode == "energy":
        out = []
        for row in matrix:
            e = math.sqrt(sum(w * w for w in row))
            out.append([w / e if e > 0 else 0.0 for w in row])
        return out
    if mode == "peak":
        m = max((sum(abs(w) for w in row) for row in matrix), default=0.0)
        if m <= 0:
            return [row[:] for row in matrix]
        return [[w / m for w in row] for row in matrix]
    raise ValueError(f"未知归一化方式: {mode!r}")


# ---------------------------------------------------------------------------
# 相位检测
# ---------------------------------------------------------------------------

@dataclass
class PhaseIssue:
    """一对声道之间的反相检测结果。"""
    ch_a: int
    ch_b: int
    name_a: str
    name_b: str
    correlation: float          # 余弦相似度，-1 = 完全反相
    verdict: str                # "inverted" / "ok"


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0              # 有一路静音，无法判断，视为不相关
    return dot / (na * nb)


def detect_phase(frames, layout=None, threshold=-0.5):
    """检测所有声道对是否反相。

    用余弦相似度：完全同相为 +1，完全反相为 -1，不相关约为 0。
    correlation <= threshold（默认 -0.5）判定为 "inverted"。
    返回 list[PhaseIssue]（只包含被判定反相的声道对）。
    """
    if not frames:
        return []
    n_ch = len(frames[0])
    names = _names(layout) if layout is not None else [f"ch{i}" for i in range(n_ch)]
    cols = [[f[i] for f in frames] for i in range(n_ch)]
    issues = []
    for i in range(n_ch):
        for j in range(i + 1, n_ch):
            c = _cosine(cols[i], cols[j])
            if c <= threshold:
                issues.append(PhaseIssue(i, j, names[i], names[j], c, "inverted"))
    return issues


# ---------------------------------------------------------------------------
# 声道重映射（解决通道顺序错位）
# ---------------------------------------------------------------------------

def remap_channels(frames, from_layout, to_layout):
    """按声道名把 frames 从 from_layout 重排到 to_layout。

    - to 中有、from 中没有的声道：补静音（0.0）。
    - from 中有、to 中没有的声道：丢弃。
    用于修正"文件实际声道顺序"与"声明顺序"不一致的场景。
    """
    src = _names(from_layout)
    dst = _names(to_layout)
    index = {name: i for i, name in enumerate(src)}
    out = []
    for frame in frames:
        out.append([frame[index[name]] if name in index else 0.0 for name in dst])
    return out


# ---------------------------------------------------------------------------
# 混音
# ---------------------------------------------------------------------------

@dataclass
class MixResult:
    frames: list                # 混音输出
    out_layout: list            # 输出声道名
    peak_in: float              # 限幅前峰值
    peak_out: float             # 限幅后峰值
    clipped_count: int          # 限幅前 |sample| > 1.0 的样本数
    gain_applied: float         # limiter="normalize" 时施加的整体增益
    phase_issues: list = field(default_factory=list)


def mix(frames, in_layout, out_layout, matrix=None, normalize="rowsum",
        phase_policy="ignore", phase_threshold=-0.5,
        limiter="normalize", headroom_db=0.0):
    """通道混音主入口。

    参数:
        frames        : list[list[float]]，每帧 len == len(in_layout)
        in_layout     : 输入布局名或声道名列表
        out_layout    : 输出布局名或声道名列表
        matrix        : 自定义权重矩阵（行=输出，列=输入）；None 用默认矩阵
        normalize     : 矩阵归一化方式，见 normalize_matrix()
        phase_policy  : 反相处理策略 —
                        "ignore" 照常相加（可能抵消，仅在结果中报告）
                        "invert" 把反相对中后一路翻回正相再混（默认推荐）
                        "mute"   把反相对中后一路置零，避免抵消
        limiter       : 防削顶 — "normalize" 整体增益缩减 / "clip" 硬裁剪 /
                        "none" 不处理（可能超满量程，由调用方负责）
        headroom_db   : limiter="normalize" 时预留的峰值余量（dB，<=0 无意义，
                        例如 1.0 表示峰值压到 -1dBFS）
    """
    in_names = _names(in_layout)
    out_names = _names(out_layout)
    if matrix is None:
        matrix, _, _ = default_matrix(in_names, out_names)
    _check_matrix(matrix, len(in_names), len(out_names))
    matrix = normalize_matrix(matrix, normalize)

    # --- 相位检测与处理 ---
    issues = detect_phase(frames, in_names, phase_threshold)
    work = [f[:] for f in frames]
    if issues and phase_policy in ("invert", "mute"):
        # 同一声道可能出现在多对里，只处理一次（按声道号去重）
        fixed = set()
        for issue in issues:
            if issue.ch_b in fixed:
                continue
            fixed.add(issue.ch_b)
            for frame in work:
                if phase_policy == "invert":
                    frame[issue.ch_b] = -frame[issue.ch_b]
                else:  # mute
                    frame[issue.ch_b] = 0.0
    elif issues and phase_policy not in ("ignore",):
        raise ValueError(f"未知相位策略: {phase_policy!r}")

    # --- 矩阵相乘 ---
    mixed = []
    for frame in work:
        if len(frame) != len(in_names):
            raise ValueError(
                f"帧声道数 {len(frame)} 与输入布局 {in_names} 不一致")
        mixed.append([sum(row[i] * frame[i] for i in range(len(in_names)))
                      for row in matrix])

    # --- 防削顶 ---
    peak_in = max((abs(s) for f in mixed for s in f), default=0.0)
    clipped = sum(1 for f in mixed for s in f if abs(s) > 1.0)
    gain = 1.0
    ceiling = 10 ** (-abs(headroom_db) / 20.0) if headroom_db > 0 else 1.0
    if limiter == "normalize":
        if peak_in > ceiling:
            gain = ceiling / peak_in
            mixed = [[s * gain for s in f] for f in mixed]
    elif limiter == "clip":
        mixed = [[max(-1.0, min(1.0, s)) for s in f] for f in mixed]
    elif limiter != "none":
        raise ValueError(f"未知限幅策略: {limiter!r}")
    peak_out = max((abs(s) for f in mixed for s in f), default=0.0)

    return MixResult(mixed, out_names, peak_in, peak_out, clipped,
                     gain, issues)


def _check_matrix(matrix, n_in, n_out):
    if len(matrix) != n_out:
        raise ValueError(f"矩阵应有 {n_out} 行（输出声道），实际 {len(matrix)}")
    for row in matrix:
        if len(row) != n_in:
            raise ValueError(f"矩阵每行应有 {n_in} 列（输入声道），实际 {len(row)}")


# ---------------------------------------------------------------------------
# 工具：int16 PCM 与交织(interleaved)缓冲互转
# ---------------------------------------------------------------------------

def int16_to_float(samples):
    return [max(-1.0, s / 32768.0) for s in samples]


def float_to_int16(samples):
    out = []
    for s in samples:
        s = max(-1.0, min(1.0, s))
        out.append(int(round(s * 32767.0)))
    return out


def deinterleave(samples, n_ch):
    """交织的一维采样 -> 帧列表。"""
    if len(samples) % n_ch != 0:
        raise ValueError("采样数不是声道数的整数倍")
    return [list(samples[i:i + n_ch]) for i in range(0, len(samples), n_ch)]


def interleave(frames):
    """帧列表 -> 交织的一维采样。"""
    return [s for f in frames for s in f]
