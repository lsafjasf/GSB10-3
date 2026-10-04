"""多声道混音与声道映射库（仅使用 Python 标准库）。

设计要点：
- 声道一律用名字标识（FL/FR/FC/LFE/SL/SR/BL/BR），混音矩阵按名字寻址，
  输入数据的声道物理顺序不影响结果，从根上解决"通道顺序错位"问题。
- MixMatrix 行 = 输出声道，列 = 输入声道，权重完全可配置，
  支持 none / rowsum / energy 三种归一化方式。
- mix() 混音后做峰值保护，保证不削顶，并报告超满量程样本数与裁剪样本数。
- detect_phase() 用零延迟归一化互相关检测相位相反的通道，
  correct_phase() 可在混音前翻转反相通道，避免相加抵消。
"""

import math
import struct
import wave
from dataclasses import dataclass, field

FULL_SCALE = 1.0

LAYOUTS = {
    "mono":   ["FC"],
    "stereo": ["FL", "FR"],
    "2.1":    ["FL", "FR", "LFE"],
    "4.0":    ["FL", "FR", "SL", "SR"],
    "5.1":    ["FL", "FR", "FC", "LFE", "SL", "SR"],
    "7.1":    ["FL", "FR", "FC", "LFE", "SL", "SR", "BL", "BR"],
}


def resolve_layout(layout):
    """接受布局名（如 "5.1"）或声道名列表，返回声道名列表。"""
    if isinstance(layout, str):
        if layout not in LAYOUTS:
            raise ValueError("未知布局 %r，可选: %s" % (layout, sorted(LAYOUTS)))
        return list(LAYOUTS[layout])
    return list(layout)


class MixMatrix:
    """混音权重矩阵：行 = 输出声道，列 = 输入声道，按声道名寻址。"""

    def __init__(self, out_channels, in_channels):
        self.out_channels = list(out_channels)
        self.in_channels = list(in_channels)
        self._w = {o: {i: 0.0 for i in self.in_channels} for o in self.out_channels}

    @classmethod
    def from_rows(cls, out_channels, in_channels, rows):
        """rows[i][j] = 第 i 个输出声道对第 j 个输入声道的权重。"""
        m = cls(out_channels, in_channels)
        if len(rows) != len(m.out_channels):
            raise ValueError("行数与输出声道数不一致")
        for o, row in zip(m.out_channels, rows):
            if len(row) != len(m.in_channels):
                raise ValueError("列数与输入声道数不一致")
            for i, w in zip(m.in_channels, row):
                m.set(o, i, float(w))
        return m

    def set(self, out_ch, in_ch, weight):
        if out_ch not in self._w:
            raise ValueError("未知输出声道: %r" % out_ch)
        if in_ch not in self._w[out_ch]:
            raise ValueError("未知输入声道: %r" % in_ch)
        self._w[out_ch][in_ch] = float(weight)

    def get(self, out_ch, in_ch):
        return self._w[out_ch][in_ch]

    def row(self, out_ch):
        return dict(self._w[out_ch])

    def normalized(self, mode="rowsum"):
        """返回归一化后的新矩阵。

        none   : 原样返回（不缩放）。
        rowsum : 每行除以该行 |权重| 之和 —— 峰值安全：
                 任意输入下输出峰值都不会超过输入峰值，从矩阵层面防削顶。
        energy : 每行除以 sqrt(权重平方和) —— 对不相关信号保持总能量，
                 响度更自然，但不保证不削顶（仍需 mix() 的峰值保护兜底）。
        """
        if mode == "none":
            scale = {o: 1.0 for o in self.out_channels}
        elif mode == "rowsum":
            scale = {}
            for o in self.out_channels:
                s = sum(abs(w) for w in self._w[o].values())
                scale[o] = 1.0 / s if s > 0 else 1.0
        elif mode == "energy":
            scale = {}
            for o in self.out_channels:
                s = math.sqrt(sum(w * w for w in self._w[o].values()))
                scale[o] = 1.0 / s if s > 0 else 1.0
        else:
            raise ValueError("未知归一化方式: %r（可选 none/rowsum/energy）" % mode)
        m = MixMatrix(self.out_channels, self.in_channels)
        for o in self.out_channels:
            for i in self.in_channels:
                m._w[o][i] = self._w[o][i] * scale[o]
        return m

    def describe(self):
        lines = ["out\\in\t" + "\t".join(self.in_channels)]
        for o in self.out_channels:
            lines.append(o + "\t" + "\t".join("%.4g" % self._w[o][i] for i in self.in_channels))
        return "\n".join(lines)


def preset_matrix(in_layout, out_layout):
    """常用下混/上混预设矩阵（未归一化，可按需再调 .normalized()）。"""
    il = resolve_layout(in_layout)
    ol = resolve_layout(out_layout)
    m = MixMatrix(ol, il)
    iset, oset = set(il), set(ol)

    # 5.1/7.1 -> 立体声：ITU-R BS.775 下混（C/S 衰减 3 dB，LFE 默认丢弃）
    if ol == LAYOUTS["stereo"] and {"FL", "FR", "FC", "SL", "SR"} <= iset:
        m.set("FL", "FL", 1.0); m.set("FL", "FC", 0.7071); m.set("FL", "SL", 0.7071)
        m.set("FR", "FR", 1.0); m.set("FR", "FC", 0.7071); m.set("FR", "SR", 0.7071)
        if "BL" in iset:
            m.set("FL", "BL", 0.5)
            m.set("FR", "BR", 0.5)
        return m

    # 任意 -> 单声道：非 LFE 声道等权平均（天然 rowsum 归一化，峰值安全）
    if ol == LAYOUTS["mono"]:
        names = [c for c in il if c != "LFE"]
        w = 1.0 / len(names)
        for c in names:
            m.set("FC", c, w)
        return m

    # 立体声 -> 5.1：基础 upmix（中置取和、环绕取差，LFE 置零）
    if ol == LAYOUTS["5.1"] and il == LAYOUTS["stereo"]:
        m.set("FL", "FL", 1.0)
        m.set("FR", "FR", 1.0)
        m.set("FC", "FL", 0.5); m.set("FC", "FR", 0.5)
        m.set("SL", "FL", 0.5); m.set("SL", "FR", -0.5)
        m.set("SR", "FR", 0.5); m.set("SR", "FL", -0.5)
        return m

    # 单声道 -> 任意：FC 直通到所有非 LFE 声道
    if il == LAYOUTS["mono"]:
        for o in ol:
            if o != "LFE":
                m.set(o, "FC", 1.0)
        return m

    # 兜底：同名声道 1:1 直通，其余置零（如 7.1 -> 5.1 丢弃 BL/BR）
    for o in ol:
        if o in iset:
            m.set(o, o, 1.0)
    return m


@dataclass
class MixResult:
    frames: list            # 混音输出，frames[帧][输出声道]
    out_layout: list
    peak_before: float      # 保护前峰值
    peak_after: float       # 保护后峰值（保证 <= ceiling）
    gain_applied: float     # 峰值保护施加的静态增益（1.0 = 未触发）
    overflow_samples: int   # 保护前 |样本| > ceiling 的数量（不保护就会削顶的数量）
    clipped_samples: int    # strategy="clip" 时实际被裁剪的样本数（gain 策略恒为 0）
    ceiling: float
    strategy: str


def mix(frames, in_layout, matrix, ceiling=FULL_SCALE, strategy="gain"):
    """按矩阵混音并做峰值保护。

    frames   : list[list[float]]，每帧 len == len(in_layout)，取值 [-1, 1]
    in_layout: 输入数据的实际声道顺序（布局名或声道名列表）
    ceiling  : 输出允许的最大绝对值，默认 1.0（满量程）
    strategy : "gain" 整体静态增益（无失真，默认）；
               "clip" 硬裁剪到 ±ceiling 并统计裁剪样本数。
    """
    il = resolve_layout(in_layout)
    idx = {name: k for k, name in enumerate(il)}
    for o in matrix.out_channels:
        for i in matrix.in_channels:
            if matrix.get(o, i) != 0.0 and i not in idx:
                raise ValueError("矩阵引用了输入布局中不存在的声道: %r" % i)
    if strategy not in ("gain", "clip"):
        raise ValueError("strategy 只能是 'gain' 或 'clip'")

    out = []
    for n, frame in enumerate(frames):
        if len(frame) != len(il):
            raise ValueError("第 %d 帧声道数 %d 与布局 %s 不符" % (n, len(frame), il))
        oframe = []
        for o in matrix.out_channels:
            acc = 0.0
            for i in matrix.in_channels:
                w = matrix.get(o, i)
                if w != 0.0:
                    acc += w * frame[idx[i]]
            oframe.append(acc)
        out.append(oframe)

    flat = [s for f in out for s in f]
    peak = max((abs(s) for s in flat), default=0.0)
    overflow = sum(1 for s in flat if abs(s) > ceiling)
    clipped = 0
    gain = 1.0

    if strategy == "gain":
        if peak > ceiling and peak > 0.0:
            gain = ceiling / peak
            out = [[s * gain for s in f] for f in out]
    else:  # clip
        if overflow:
            clipped = overflow
            out = [[max(-ceiling, min(ceiling, s)) for s in f] for f in out]

    peak_after = max((abs(s) for f in out for s in f), default=0.0)
    return MixResult(out, matrix.out_channels, peak, peak_after,
                     gain, overflow, clipped, ceiling, strategy)


@dataclass
class PhaseReport:
    reference: str               # 参考声道（能量最大者）
    polarity: dict               # 声道 -> +1 / -1（相对参考声道）
    correlation: dict            # 声道 -> 与参考声道的零延迟互相关系数
    inverted: list               # 判定为反相的声道
    inverted_pairs: list         # (声道A, 声道B, 相关系数)，所有强负相关声道对
    threshold: float

    def summary(self):
        lines = ["参考声道: %s（能量最大）" % self.reference]
        for ch, c in self.correlation.items():
            tag = "  <== 反相" if ch in self.inverted else ""
            lines.append("  %-4s 相关系数 %+.4f 极性 %+d%s" % (ch, c, self.polarity[ch], tag))
        if self.inverted_pairs:
            lines.append("强负相关声道对:")
            for a, b, c in self.inverted_pairs:
                lines.append("  %s <-> %s  r=%+.4f" % (a, b, c))
        else:
            lines.append("未发现反相通道。")
        return "\n".join(lines)


def _energy(xs):
    return sum(x * x for x in xs)


def _correlation(a, b):
    ea, eb = _energy(a), _energy(b)
    if ea == 0.0 or eb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / math.sqrt(ea * eb)


def detect_phase(frames, layout, threshold=-0.5):
    """检测相位相反的通道。

    方法：以能量最大的声道为参考，计算各声道与参考声道的零延迟
    归一化互相关（皮尔逊相关系数）；同时枚举所有声道对。
    相关系数 < threshold（默认 -0.5）判定为反相。
    """
    il = resolve_layout(layout)
    cols = [list(c) for c in zip(*frames)] if frames else [[] for _ in il]
    energies = [_energy(c) for c in cols]
    ref_i = max(range(len(il)), key=lambda k: energies[k]) if il else 0

    correlation, polarity, inverted = {}, {}, []
    for k, name in enumerate(il):
        r = _correlation(cols[k], cols[ref_i]) if k != ref_i else 1.0
        correlation[name] = r
        p = -1 if r < threshold else 1
        polarity[name] = p
        if p < 0:
            inverted.append(name)

    pairs = []
    for a in range(len(il)):
        for b in range(a + 1, len(il)):
            r = _correlation(cols[a], cols[b])
            if r < threshold:
                pairs.append((il[a], il[b], r))

    return PhaseReport(il[ref_i], polarity, correlation, inverted, pairs, threshold)


def correct_phase(frames, layout, report):
    """按 PhaseReport 翻转反相通道，返回新 frames（不改动原数据）。

    处理策略：检测默认只报告；确认是接线/制作错误导致的整体反相后，
    再调用本函数翻正，然后再混音，避免下混时正负抵消。
    """
    il = resolve_layout(layout)
    flip = [report.polarity.get(name, 1) for name in il]
    return [[s * f for s, f in zip(frame, flip)] for frame in frames]


DEFAULT_LAYOUT_BY_COUNT = {1: "mono", 2: "stereo", 4: "4.0", 6: "5.1", 8: "7.1"}


def read_wav(path):
    """读取 16-bit PCM WAV，返回 (frames, layout, sample_rate)。"""
    with wave.open(str(path), "rb") as w:
        n_ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("仅支持 16-bit PCM WAV，当前位宽: %d 字节" % width)
    data = struct.unpack("<%dh" % (len(raw) // 2), raw)
    frames = [[s / 32768.0 for s in data[i:i + n_ch]] for i in range(0, len(data), n_ch)]
    layout = resolve_layout(DEFAULT_LAYOUT_BY_COUNT.get(n_ch, ["CH%d" % k for k in range(n_ch)]))
    return frames, layout, rate


def write_wav(path, frames, sample_rate):
    """把 float frames 写成 16-bit PCM WAV（写入前裁剪到 [-1, 1]）。"""
    n_ch = len(frames[0]) if frames else 1
    pcm = bytearray()
    for f in frames:
        for s in f:
            v = int(round(max(-1.0, min(1.0, s)) * 32767.0))
            pcm += struct.pack("<h", v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(n_ch)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(pcm))
