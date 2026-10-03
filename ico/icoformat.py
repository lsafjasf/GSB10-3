"""Windows ICO 图标容器（ICONDIR）的读取、校验、挑选与写出。

仅使用 Python 标准库；图像载荷（BMP-DIB / PNG）按字节原样搬运，
不调用任何图像编解码库。

ICO 结构：
    ICONDIR   : reserved(2) type(2) count(2)
    目录项 xN : width(1) height(1) colors(1) reserved(1)
                planes(2) bitcount(2) bytes_in_res(4) image_offset(4)
    图像数据  : BMP-DIB（无文件头，高度 = 2*实际高度，含 AND 掩码）
                或 PNG（完整 PNG 文件流，Vista+）

约定：
- 目录项里 0 表示 256。
- 本实现只支持 type=1（图标），不支持 type=2（光标）。
- 目录项元数据与图像数据内部信息不一致时，报错信息指明具体条目序号。
"""

import math
import struct
import zlib

__all__ = [
    "IconFile",
    "IconImage",
    "RankItem",
    "IconFormatError",
    "pick_best",
    "rank",
]

_ICONDIR = struct.Struct("<HHH")
_ENTRY = struct.Struct("<BBBBHHII")
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_BI_RGB = 0
_UPSCALE_PENALTY = 2.0  # 放大惩罚系数：放大 1 倍的代价按缩小 2 倍计
_BMP_HEADER_SIZES = (40, 52, 56, 108, 124)  # BITMAPINFOHEADER .. V5

_PNG_BPP = {0: {8: 8}, 2: {8: 24}, 4: {8: 16}, 6: {8: 32}}
_COLOR_NAME = {0: "灰度", 2: "RGB", 4: "灰度+Alpha", 6: "RGBA"}


class IconFormatError(ValueError):
    """ICO 文件结构或目录项与图像数据不一致。"""


def _entry_error(index, message):
    return IconFormatError("目录项 #%d: %s" % (index, message))


# ---------------------------------------------------------------------------
# 图像载荷探测（BMP-DIB / PNG）
# ---------------------------------------------------------------------------

def _inspect_bmp(blob):
    """解析 BMP-DIB 载荷，返回 (width, height, bpp, compressed, colors_used)。"""
    if len(blob) < 40:
        raise IconFormatError("BMP 载荷过短：%d 字节，不足 BITMAPINFOHEADER(40)" % len(blob))
    (bi_size, bi_w, bi_h, bi_planes, bi_bpp, bi_compression,
     _size_image, _xppm, _yppm, bi_clr_used, _clr_important) = struct.unpack_from(
        "<IiiHHIIiiII", blob, 0)
    if bi_size < 40:
        raise IconFormatError("BMP 头 biSize=%d 非法（<40）" % bi_size)
    if bi_planes != 1:
        raise IconFormatError("BMP biPlanes=%d，应为 1" % bi_planes)
    if bi_bpp not in (1, 4, 8, 16, 24, 32):
        raise IconFormatError("BMP 色深 %d bpp 不受支持" % bi_bpp)
    compressed = bi_compression != _BI_RGB
    if compressed:
        raise IconFormatError("BMP 压缩方式 biCompression=%d 不受支持（仅 BI_RGB）" % bi_compression)
    if bi_w <= 0 or bi_h <= 0:
        raise IconFormatError("BMP 尺寸非法：%dx%d" % (bi_w, bi_h))
    if bi_h % 2 != 0:
        raise IconFormatError("BMP 高度 %d 为奇数，无法按 XOR+AND 拆分" % bi_h)
    height = bi_h // 2
    xor_stride = ((bi_w * bi_bpp + 31) // 32) * 4
    and_stride = ((bi_w + 31) // 32) * 4
    palette = bi_clr_used if bi_clr_used else (1 << bi_bpp if bi_bpp <= 8 else 0)
    expect = bi_size + palette * 4 + xor_stride * height + and_stride * height
    if len(blob) < expect:
        raise IconFormatError(
            "BMP 数据长度不足：%d 字节，按头部推算至少 %d 字节" % (len(blob), expect))
    return bi_w, height, bi_bpp, compressed, bi_clr_used


def _inspect_png(blob):
    """解析 PNG 载荷，返回 (width, height, bpp)。"""
    if len(blob) < 8 or blob[:8] != _PNG_SIG:
        raise IconFormatError("PNG 签名错误")
    pos = 8
    ihdr = None
    saw_iend = False
    while pos + 12 <= len(blob):
        length, ctype = struct.unpack_from(">I4s", blob, pos)
        end = pos + 12 + length
        if end > len(blob):
            raise IconFormatError("PNG 块 %s 长度越界" % ctype.decode("latin1"))
        data = blob[pos + 8:pos + 8 + length]
        crc_expect = struct.unpack_from(">I", blob, pos + 8 + length)[0]
        crc_actual = zlib.crc32(ctype + data) & 0xFFFFFFFF
        if crc_actual != crc_expect:
            raise IconFormatError("PNG 块 %s CRC 校验失败" % ctype.decode("latin1"))
        if ctype == b"IHDR":
            if length != 13:
                raise IconFormatError("PNG IHDR 长度 %d 非法" % length)
            ihdr = struct.unpack(">IIBBBBB", data)
        elif ctype == b"IEND":
            saw_iend = True
            break
        pos = end
    if ihdr is None:
        raise IconFormatError("PNG 缺少 IHDR 块")
    if not saw_iend:
        raise IconFormatError("PNG 缺少 IEND 块")
    width, height, bit_depth, color_type, compression, flt, interlace = ihdr
    if width <= 0 or height <= 0:
        raise IconFormatError("PNG 尺寸非法：%dx%d" % (width, height))
    if compression != 0 or flt != 0 or interlace != 0:
        raise IconFormatError("PNG 压缩/滤波/隔行参数不受支持")
    bpp = _PNG_BPP.get(color_type, {}).get(bit_depth)
    if bpp is None:
        raise IconFormatError(
            "PNG 色彩类型 %d / 位深 %d 不受支持（支持：%s）"
            % (color_type, bit_depth,
               "、".join("%s8bit" % _COLOR_NAME[c] for c in sorted(_PNG_BPP))))
    return width, height, bpp


def probe_image(blob):
    """探测图像载荷，返回 dict(width, height, bpp, compressed, colors_used)。"""
    if blob[:8] == _PNG_SIG:
        width, height, bpp = _inspect_png(blob)
        return dict(width=width, height=height, bpp=bpp,
                    compressed=True, colors_used=0)
    width, height, bpp, compressed, colors_used = _inspect_bmp(blob)
    return dict(width=width, height=height, bpp=bpp,
                compressed=compressed, colors_used=colors_used)


# ---------------------------------------------------------------------------
# 图像与容器
# ---------------------------------------------------------------------------

class IconImage(object):
    """单个图标图像：元数据 + 原始载荷字节。"""

    def __init__(self, width, height, bpp, data, compressed=None, colors_used=0):
        if not (1 <= width <= 256 and 1 <= height <= 256):
            raise IconFormatError("图像尺寸 %dx%d 超出 1..256" % (width, height))
        if bpp not in (1, 4, 8, 16, 24, 32):
            raise IconFormatError("色深 %d bpp 不受支持" % bpp)
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data 必须是 bytes")
        self.width = width
        self.height = height
        self.bpp = bpp
        self.data = bytes(data)
        self.compressed = (self.data[:8] == _PNG_SIG) if compressed is None else bool(compressed)
        self.colors_used = colors_used

    @classmethod
    def from_blob(cls, blob):
        """从原始载荷构造，元数据取自载荷内部（BMP 头 / PNG IHDR）。"""
        info = probe_image(bytes(blob))
        return cls(info["width"], info["height"], info["bpp"], bytes(blob),
                   compressed=info["compressed"], colors_used=info["colors_used"])

    @property
    def is_png(self):
        return self.compressed

    def __repr__(self):
        return "IconImage(%dx%d, %dbpp, %s, %d bytes)" % (
            self.width, self.height, self.bpp,
            "PNG" if self.compressed else "BMP", len(self.data))


class IconFile(object):
    """一个 ICO 文件：若干 IconImage 的集合。"""

    def __init__(self, images=()):
        self.images = list(images)

    def __len__(self):
        return len(self.images)

    def __iter__(self):
        return iter(self.images)

    # -- 读取 ---------------------------------------------------------------

    @classmethod
    def read(cls, buf):
        """解析 ICO 字节流；目录与数据不一致时报错并指明条目序号。"""
        buf = bytes(buf)
        if len(buf) < _ICONDIR.size:
            raise IconFormatError("文件过短：%d 字节，不足 ICONDIR(6)" % len(buf))
        reserved, itype, count = _ICONDIR.unpack_from(buf, 0)
        if reserved != 0:
            raise IconFormatError("ICONDIR.reserved=%d，应为 0" % reserved)
        if itype != 1:
            raise IconFormatError("ICONDIR.type=%d，仅支持图标(1)，不支持光标(2)" % itype)
        dir_end = _ICONDIR.size + count * _ENTRY.size
        if len(buf) < dir_end:
            raise IconFormatError(
                "目录不完整：声明 %d 项需 %d 字节，文件仅 %d 字节" % (count, dir_end, len(buf)))

        entries = []
        for i in range(count):
            (b_w, b_h, b_colors, b_reserved, planes, bitcount,
             size, offset) = _ENTRY.unpack_from(buf, _ICONDIR.size + i * _ENTRY.size)
            n = i + 1
            if b_reserved != 0:
                raise _entry_error(n, "保留字节=%d，应为 0" % b_reserved)
            if size == 0:
                raise _entry_error(n, "数据长度为 0")
            if offset < dir_end:
                raise _entry_error(n, "数据偏移 %d 落在目录区（目录结束于 %d）" % (offset, dir_end))
            if offset + size > len(buf):
                raise _entry_error(
                    n, "数据越界：偏移 %d + 长度 %d > 文件长度 %d" % (offset, size, len(buf)))
            entries.append((n, b_w, b_h, b_colors, planes, bitcount, size, offset))

        # 重叠检测：完全相同的区间视为共享数据（合法），部分重叠视为损坏。
        intervals = sorted((off, off + sz, n) for (n, _w, _h, _c, _p, _b, sz, off) in entries)
        for (s1, e1, n1), (s2, e2, n2) in zip(intervals, intervals[1:]):
            if s2 < e1 and (s1, e1) != (s2, e2):
                raise _entry_error(
                    n2, "数据区间 [%d,%d) 与目录项 #%d 的 [%d,%d) 部分重叠"
                    % (s2, e2, n1, s1, e1))

        images = []
        for (n, b_w, b_h, b_colors, planes, bitcount, size, offset) in entries:
            blob = buf[offset:offset + size]
            if blob[:8] == _PNG_SIG:
                try:
                    width, height, bpp = _inspect_png(blob)
                except IconFormatError as exc:
                    raise _entry_error(n, "PNG 数据损坏：%s" % exc)
                compressed = True
                colors_used = 0
            elif (len(blob) >= 4
                    and struct.unpack_from("<I", blob, 0)[0] in _BMP_HEADER_SIZES):
                try:
                    width, height, bpp, compressed, colors_used = _inspect_bmp(blob)
                except IconFormatError as exc:
                    raise _entry_error(n, "BMP 数据损坏：%s" % exc)
            else:
                raise _entry_error(n, "无法识别的图像数据（既非 PNG 也非 BMP-DIB）")

            decl_w, decl_h = (b_w or 256), (b_h or 256)
            if (width, height) != (decl_w, decl_h):
                raise _entry_error(
                    n, "尺寸不一致：目录声明 %dx%d，数据实际 %dx%d"
                    % (decl_w, decl_h, width, height))
            if bitcount != bpp:
                raise _entry_error(
                    n, "色深不一致：目录声明 %d bpp，数据实际 %d bpp" % (bitcount, bpp))
            if not compressed and bpp <= 8 and b_colors not in (0, colors_used):
                raise _entry_error(
                    n, "调色板颜色数不一致：目录声明 %d，数据实际 %d" % (b_colors, colors_used))
            images.append(IconImage(width, height, bpp, blob,
                                    compressed=compressed, colors_used=colors_used))
        return cls(images)

    # -- 写出 ---------------------------------------------------------------

    def to_bytes(self):
        """序列化为 ICO 字节流；写前逐项自检元数据与载荷是否一致。"""
        count = len(self.images)
        if count > 0xFFFF:
            raise IconFormatError("图像数量 %d 超过 65535" % count)
        offset = _ICONDIR.size + count * _ENTRY.size
        entries = []
        payload = []
        for i, img in enumerate(self.images):
            n = i + 1
            info = probe_image(img.data)
            for field, declared, actual in (
                    ("宽度", img.width, info["width"]),
                    ("高度", img.height, info["height"]),
                    ("色深", img.bpp, info["bpp"]),
                    ("压缩标志", img.compressed, info["compressed"])):
                if declared != actual:
                    raise _entry_error(
                        n, "%s元数据与载荷不符：声明 %r，实际 %r" % (field, declared, actual))
            entries.append(_ENTRY.pack(
                img.width % 256, img.height % 256, img.colors_used, 0,
                1, img.bpp, len(img.data), offset))
            payload.append(img.data)
            offset += len(img.data)
        return _ICONDIR.pack(0, 1, count) + b"".join(entries) + b"".join(payload)

    # -- 挑选 ---------------------------------------------------------------

    def rank(self, target):
        return rank(self.images, target)

    def pick(self, target):
        return pick_best(self.images, target)


# ---------------------------------------------------------------------------
# 候选排序
# ---------------------------------------------------------------------------

class RankItem(object):
    """单个候选的评分结果。"""

    __slots__ = ("image", "index", "cost", "upscale", "ratio")

    def __init__(self, image, index, cost, upscale, ratio):
        self.image = image
        self.index = index
        self.cost = cost
        self.upscale = upscale
        self.ratio = ratio

    def __repr__(self):
        if self.ratio == 1.0:
            how = "精确匹配"
        elif self.upscale:
            how = "放大 x%.4g" % (1.0 / self.ratio)
        else:
            how = "缩小 x%.4g" % self.ratio
        return ("#%d %dx%d %dbpp %s cost=%.4f (%s)"
                % (self.index, self.image.width, self.image.height, self.image.bpp,
                   "PNG" if self.image.compressed else "BMP", self.cost, how))


def _score(image, target):
    """缩放代价：log2(尺寸比)，放大时乘以惩罚系数。返回 (cost, upscale, ratio)。"""
    ratio = image.width / float(target)
    cost = abs(math.log2(ratio))
    upscale = ratio < 1.0
    if upscale:
        cost *= _UPSCALE_PENALTY
    return cost, upscale, ratio


def rank(images, target):
    """按目标尺寸对候选排序，返回 RankItem 列表（最优在前）。

    排序规则（依次比较，全部确定性）：
      1. 缩放代价 cost 升序：cost = |log2(候选宽/目标宽)|，放大时乘惩罚系数 2；
      2. 色深 bpp 降序；
      3. PNG 优先于 BMP；
      4. 原始索引升序（稳定）。
    """
    if target <= 0:
        raise ValueError("目标尺寸必须为正数，收到 %r" % (target,))
    items = []
    for index, img in enumerate(images):
        cost, upscale, ratio = _score(img, target)
        items.append((RankItem(img, index, cost, upscale, ratio),
                      (cost, -img.bpp, 0 if img.compressed else 1, index)))
    items.sort(key=lambda pair: pair[1])
    return [item for item, _key in items]


def pick_best(images, target):
    """返回最适合目标尺寸的 IconImage；无候选时抛 IconFormatError。"""
    ranked = rank(images, target)
    if not ranked:
        raise IconFormatError("没有可用图像，无法为目标尺寸 %d 挑选候选" % target)
    return ranked[0].image
