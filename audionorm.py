"""audionorm: 多段音频拼接的增益归一 + 淡入淡出库（仅标准库）。

响度口径：
  - peak: 采样峰值 dBFS = 20*log10(max|x|)。只反映瞬时最大值，与感知响度相关性弱。
  - rms : 均方根 dBFS = 20*log10(sqrt(mean(x^2)))。反映信号能量，更接近感知响度。
          严格的感知响度口径是 ITU-R BS.1770 / EBU R128 的 LUFS（K 加权 + 门控），
          本库用全段未加权 RMS 作为其标准库可实现的近似，并在报告中同时给出
          peak 以便对比两种口径的差异。

防削顶策略（明确策略）：
  1. 按目标响度计算请求增益 gain_req。
  2. 预测增益后峰值 peak_after = peak * gain_req。
  3. 若 peak_after > ceiling（默认 -0.3 dBFS），则把增益压到 ceiling/peak
     （峰值限制，优先保不削顶，响度让路），报告 limited=True。
  4. 报告 would_clip_samples：若不限制会有多少样本超过满量程；
     clipped_samples：实际应用增益后仍被硬钳位的样本数（正常应为 0）。
"""

import math
import struct
import wave
from dataclasses import dataclass, field

NEG_INF = float("-inf")


# ---------------------------------------------------------------- WAV I/O

class Audio:
    """多声道浮点音频，samples 取值 [-1.0, 1.0]。"""

    def __init__(self, channels, sample_rate):
        # channels: list[list[float]]，每个元素是一个声道
        if not channels:
            raise ValueError("至少需要一个声道")
        n = len(channels[0])
        for ch in channels:
            if len(ch) != n:
                raise ValueError("各声道长度不一致")
        self.channels = [list(ch) for ch in channels]
        self.sample_rate = int(sample_rate)

    @property
    def n_channels(self):
        return len(self.channels)

    @property
    def n_samples(self):
        return len(self.channels[0])

    @property
    def duration(self):
        return self.n_samples / self.sample_rate if self.sample_rate else 0.0

    def copy(self):
        return Audio(self.channels, self.sample_rate)


def read_wav(path):
    """读取 PCM WAV（8/16/24/32 bit），返回 Audio。"""
    with wave.open(path, "rb") as wf:
        n_ch = wf.getnchannels()
        width = wf.getsampwidth()
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if width == 1:  # 8-bit 无符号
        vals = [(b - 128) / 128.0 for b in raw]
    elif width in (2, 3, 4):
        vals = []
        step = width
        full = float(1 << (8 * width - 1))
        for i in range(0, len(raw), step):
            vals.append(int.from_bytes(raw[i:i + step], "little", signed=True) / full)
    else:
        raise ValueError(f"不支持的采样位宽: {width}")
    channels = [[] for _ in range(n_ch)]
    for i, v in enumerate(vals):
        channels[i % n_ch].append(v)
    return Audio(channels, sr)


def write_wav(audio, path, width=2):
    """写出 PCM WAV（默认 16-bit），超界样本硬钳位到 [-1, 1]。"""
    if width not in (1, 2, 3, 4):
        raise ValueError("width 必须为 1/2/3/4")
    n_ch, sr = audio.n_channels, audio.sample_rate
    frames = bytearray()
    for i in range(audio.n_samples):
        for ch in audio.channels:
            v = max(-1.0, min(1.0, ch[i]))
            if width == 1:
                frames.append(int(round(v * 127.0 + 128)) & 0xFF)
            else:
                full = (1 << (8 * width - 1)) - 1
                frames += int(round(v * full)).to_bytes(width, "little", signed=True)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(n_ch)
        wf.setsampwidth(width)
        wf.setframerate(sr)
        wf.writeframes(bytes(frames))


# ---------------------------------------------------------------- 响度口径

def to_dbfs(v):
    return 20.0 * math.log10(v) if v > 0 else NEG_INF


def peak_level(samples):
    """采样峰值（线性，0..1）。"""
    return max((abs(s) for s in samples), default=0.0)


def rms_level(samples):
    """均方根（线性，0..1）。静音返回 0。"""
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def all_samples(audio):
    for ch in audio.channels:
        yield from ch


def loudness_report(audio):
    """两种口径对比：peak dBFS 与 rms dBFS（crest = peak - rms）。"""
    samples = list(all_samples(audio))
    p = peak_level(samples)
    r = rms_level(samples)
    p_db, r_db = to_dbfs(p), to_dbfs(r)
    crest = (p_db - r_db) if (p > 0 and r > 0) else NEG_INF
    return {"peak_dbfs": p_db, "rms_dbfs": r_db, "crest_db": crest}


# ---------------------------------------------------------------- 增益归一

@dataclass
class GainReport:
    metric: str = "rms"
    measured_dbfs: float = NEG_INF
    target_dbfs: float = -20.0
    requested_gain_db: float = 0.0
    applied_gain_db: float = 0.0
    ceiling_dbfs: float = -0.3
    limited: bool = False          # 是否触发峰值限制
    skipped: bool = False          # 静音段：跳过归一
    would_clip_samples: int = 0    # 不限制时会超满量程的样本数
    clipped_samples: int = 0       # 实际应用后仍被钳位的样本数


def _measure(audio, metric):
    samples = list(all_samples(audio))
    if metric == "peak":
        return peak_level(samples)
    if metric == "rms":
        return rms_level(samples)
    raise ValueError(f"未知口径: {metric}")


def normalize_gain(audio, target_dbfs=-20.0, metric="rms", ceiling_dbfs=-0.3):
    """就地归一。返回 GainReport。

    策略：先按目标响度给增益；若预测峰值超过 ceiling，则把增益压到
    ceiling/peak（宁可响度不足也不削顶）；静音段跳过（增益 0 dB）。
    """
    rep = GainReport(metric=metric, target_dbfs=target_dbfs, ceiling_dbfs=ceiling_dbfs)
    samples = list(all_samples(audio))
    level = _measure(audio, metric)
    rep.measured_dbfs = to_dbfs(level)

    if level <= 0.0:  # 静音：无法归一，保持原样
        rep.skipped = True
        return rep

    gain_req = 10.0 ** ((target_dbfs - to_dbfs(level)) / 20.0)
    rep.requested_gain_db = 20.0 * math.log10(gain_req)

    # 不限制时的削顶样本数（按满量程 1.0 统计）
    rep.would_clip_samples = sum(1 for s in samples if abs(s) * gain_req > 1.0)

    peak = peak_level(samples)
    ceiling = 10.0 ** (ceiling_dbfs / 20.0)
    gain = gain_req
    if peak * gain_req > ceiling:
        gain = ceiling / peak
        rep.limited = True
    rep.applied_gain_db = 20.0 * math.log10(gain) if gain > 0 else NEG_INF

    clipped = 0
    for ch in audio.channels:
        for i, s in enumerate(ch):
            v = s * gain
            if v > 1.0:
                v, clipped = 1.0, clipped + 1
            elif v < -1.0:
                v, clipped = -1.0, clipped + 1
            ch[i] = v
    rep.clipped_samples = clipped
    return rep


# ---------------------------------------------------------------- 淡入淡出

def _curve_fn(name, fade_in):
    """返回增益函数 g(t)，t∈[0,1]。fade_in 为升，否则为降。"""
    if name == "linear":
        g = lambda t: t
    elif name == "sine":  # 等功率交叉淡出的半支：sin/cos 配对
        g = lambda t: math.sin(0.5 * math.pi * t)
    elif name == "exp":   # 指数（听感近似等响度）
        g = lambda t: (math.exp(3.0 * t) - 1.0) / (math.e ** 3.0 - 1.0)
    else:
        raise ValueError(f"未知曲线: {name}（可选 linear/sine/exp）")
    return g if fade_in else (lambda t: g(1.0 - t))


def apply_fade(audio, dur_s, curve="linear", fade_in=True):
    """就地淡入/淡出。片段短于淡出时长时自动收缩到片段长度。"""
    n = min(int(round(dur_s * audio.sample_rate)), audio.n_samples)
    if n <= 0:
        return 0
    g = _curve_fn(curve, fade_in)
    idx = range(n) if fade_in else range(audio.n_samples - n, audio.n_samples)
    for j, i in enumerate(idx):
        w = g((j + 1) / (n + 1)) if n > 1 else 1.0
        for ch in audio.channels:
            ch[i] *= w
    return n


def crossfade(a, b, dur_s, curve="sine"):
    """交叉淡出拼接 a+b，返回 (Audio, JoinReport)。时长超过任一片段则收缩。"""
    if a.sample_rate != b.sample_rate or a.n_channels != b.n_channels:
        raise ValueError("采样率/声道数不一致")
    n = int(round(dur_s * a.sample_rate))
    n = max(0, min(n, a.n_samples, b.n_samples))
    g_out = _curve_fn(curve, fade_in=False)
    g_in = _curve_fn(curve, fade_in=True)

    out_ch = [ch[: a.n_samples - n] for ch in a.channels]
    for c in range(a.n_channels):
        for j in range(n):
            t = (j + 1) / (n + 1) if n > 1 else 0.5
            out_ch[c].append(a.channels[c][a.n_samples - n + j] * g_out(t)
                             + b.channels[c][j] * g_in(t))
        out_ch[c].extend(b.channels[c][n:])
    joined = Audio(out_ch, a.sample_rate)
    return joined, JoinReport(a, b, joined, n, curve)


# ---------------------------------------------------------------- 拼接能量报告

@dataclass
class JoinReport:
    curve: str = "sine"
    overlap_samples: int = 0
    rms_before_dbfs: float = NEG_INF   # 拼接点前窗（长度=重叠区）
    rms_overlap_dbfs: float = NEG_INF  # 重叠区
    rms_after_dbfs: float = NEG_INF    # 拼接点后窗
    delta_in_db: float = NEG_INF       # overlap - before
    delta_out_db: float = NEG_INF      # after - overlap

    def __init__(self, a, b, joined, n, curve):
        self.curve = curve
        self.overlap_samples = n
        if n <= 0:
            return
        win = n
        pre = [s for ch in a.channels for s in ch[a.n_samples - 2 * win: a.n_samples - win]] \
            if a.n_samples >= 2 * win else [s for ch in a.channels for s in ch[:a.n_samples - win]]
        mid = [s for ch in joined.channels
               for s in ch[a.n_samples - win: a.n_samples]]
        post = [s for ch in joined.channels for s in ch[a.n_samples: a.n_samples + win]]
        self.rms_before_dbfs = to_dbfs(rms_level(pre))
        self.rms_overlap_dbfs = to_dbfs(rms_level(mid))
        self.rms_after_dbfs = to_dbfs(rms_level(post))
        self.delta_in_db = (self.rms_overlap_dbfs - self.rms_before_dbfs) \
            if self.rms_before_dbfs != NEG_INF and self.rms_overlap_dbfs != NEG_INF else NEG_INF
        self.delta_out_db = (self.rms_after_dbfs - self.rms_overlap_dbfs) \
            if self.rms_after_dbfs != NEG_INF and self.rms_overlap_dbfs != NEG_INF else NEG_INF


def concat(segments, fade_s=0.05, curve="sine"):
    """依次交叉淡出拼接，返回 (Audio, [JoinReport])。"""
    if not segments:
        raise ValueError("至少一段")
    joined = segments[0].copy()
    reports = []
    for seg in segments[1:]:
        joined, rep = crossfade(joined, seg, fade_s, curve)
        reports.append(rep)
    return joined, reports
