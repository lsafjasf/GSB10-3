"""icoformat — Windows ICO 图标封装格式的纯标准库读写库。

功能：
- 解析 ICONDIR / ICONDIRENTRY 目录项，提取每幅图像的偏移、尺寸、色深、压缩标志；
- 目录声明与图像数据不一致时，抛出带具体目录项序号的 IconFormatError；
- rank()/select() 按目标尺寸与缩放代价对候选排序，平局用确定性规则打破；
- build() 写出的文件偏移与长度自洽，parse()->to_bytes() 往返逐字节一致。

仅使用 Python 标准库，不依赖任何图像库。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
BMP_MIN_HEADER = 40  # BITMAPINFOHEADER

TYPE_ICON = 1
TYPE_CURSOR = 2


class IconFormatError(ValueError):
    """图标封装格式错误；消息中总是包含出问题的目录项序号（entry #k）。"""


@dataclass
class IconImage:
    """目录中的一项（一幅图像）。"""

    width: int
    height: int
    bit_count: int
    data: bytes
    compressed: bool = False          # True 表示 PNG 压缩封装
    color_count: int = 0              # 调色板颜色数（0 = 满表）
    planes: int = 1
    has_alpha: bool = False
    index: int = -1                   # 在目录中的序号，解析时填入
    offset: int = 0                   # 数据在文件中的偏移，解析/写出时填入

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass
class IconFile:
    images: list = field(default_factory=list)
    icon_type: int = TYPE_ICON

    def to_bytes(self) -> bytes:
        return build(self.images, icon_type=self.icon_type)

    def rank(self, target) -> list:
        return rank(self.images, target)

    def select(self, target) -> IconImage:
        return select(self.images, target)


# ---------------------------------------------------------------------------
# 图像数据探测（BMP-DIB / PNG），只读头部，不解码像素
# ---------------------------------------------------------------------------

def _probe_bmp(blob: bytes, entry_no: int):
    if len(blob) < BMP_MIN_HEADER:
        raise IconFormatError(
            f"entry #{entry_no}: BMP 头不足 {BMP_MIN_HEADER} 字节"
            f"（实际 {len(blob)} 字节）"
        )
    header_size = struct.unpack_from("<I", blob, 0)[0]
    if header_size < BMP_MIN_HEADER:
        raise IconFormatError(
            f"entry #{entry_no}: 不支持的 DIB 头长度 {header_size}"
        )
    if len(blob) < header_size:
        raise IconFormatError(
            f"entry #{entry_no}: 数据长度 {len(blob)} 小于 DIB 头长度 {header_size}"
        )
    width = struct.unpack_from("<i", blob, 4)[0]
    raw_height = struct.unpack_from("<i", blob, 8)[0]
    planes, bit_count = struct.unpack_from("<HH", blob, 12)
    compression = struct.unpack_from("<I", blob, 16)[0]
    if compression not in (0, 3):  # BI_RGB / BI_BITFIELDS
        raise IconFormatError(
            f"entry #{entry_no}: 不支持的 BMP 压缩方式 {compression}"
        )
    # ICO 内嵌 DIB 的高度是 XOR+AND 两个掩码之和，实际高度取一半
    height = raw_height // 2 if raw_height % 2 == 0 else raw_height
    if width <= 0 or height <= 0:
        raise IconFormatError(
            f"entry #{entry_no}: 非法尺寸 {width}x{height}"
        )

    has_alpha = False
    if bit_count == 32:
        colors_used = struct.unpack_from("<I", blob, 32)[0]
        palette = colors_used * 4
        px_off = header_size + palette
        px_len = width * height * 4
        if px_off + px_len <= len(blob):
            alpha = blob[px_off + 3: px_off + px_len: 4]
            has_alpha = any(b not in (0, 255) for b in alpha) or (
                0 in alpha and 255 in alpha
            )
    return width, height, bit_count, planes, has_alpha


def _probe_png(blob: bytes, entry_no: int):
    if len(blob) < 33 or blob[:8] != PNG_MAGIC or blob[12:16] != b"IHDR":
        raise IconFormatError(f"entry #{entry_no}: PNG 头损坏")
    width, height = struct.unpack_from(">II", blob, 16)
    bit_depth, color_type = blob[24], blob[25]
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise IconFormatError(
            f"entry #{entry_no}: 未知 PNG 颜色类型 {color_type}"
        )
    bit_count = bit_depth * channels
    has_alpha = color_type in (4, 6) or b"tRNS" in blob
    return width, height, bit_count, 1, has_alpha


def probe_image(blob: bytes, entry_no: int = 0):
    """返回 (width, height, bit_count, planes, compressed, has_alpha)。"""
    if blob[:8] == PNG_MAGIC:
        w, h, bc, planes, alpha = _probe_png(blob, entry_no)
        return w, h, bc, planes, True, alpha
    w, h, bc, planes, alpha = _probe_bmp(blob, entry_no)
    return w, h, bc, planes, False, alpha


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def parse(data: bytes) -> IconFile:
    if len(data) < 6:
        raise IconFormatError("文件头不足 6 字节")
    reserved, icon_type, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0:
        raise IconFormatError(f"文件头 reserved 字段应为 0，实际 {reserved}")
    if icon_type not in (TYPE_ICON, TYPE_CURSOR):
        raise IconFormatError(f"未知图标类型 {icon_type}")
    if count == 0:
        raise IconFormatError("目录项数量为 0")
    dir_end = 6 + 16 * count
    if len(data) < dir_end:
        raise IconFormatError(
            f"目录声明 {count} 项，需要 {dir_end} 字节，文件只有 {len(data)} 字节"
        )

    images = []
    ranges = []
    for i in range(count):
        (w_byte, h_byte, color_count, _res,
         planes, bit_count, size, offset) = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * i)
        width = w_byte or 256
        height = h_byte or 256

        if offset < dir_end:
            raise IconFormatError(
                f"entry #{i}: 数据偏移 {offset} 落在目录区（目录结束于 {dir_end}）"
            )
        if size == 0:
            raise IconFormatError(f"entry #{i}: 数据长度为 0")
        if offset + size > len(data):
            raise IconFormatError(
                f"entry #{i}: 数据区间 [{offset}, {offset + size}) "
                f"超出文件长度 {len(data)}"
            )
        for j, (po, ps) in enumerate(ranges):
            if offset < po + ps and po < offset + size:
                raise IconFormatError(
                    f"entry #{i}: 数据区间 [{offset}, {offset + size}) "
                    f"与 entry #{j} 的区间 [{po}, {po + ps}) 重叠"
                )
        ranges.append((offset, size))

        blob = data[offset:offset + size]
        aw, ah, abc, aplanes, compressed, has_alpha = probe_image(blob, i)

        if (aw, ah) != (width, height):
            raise IconFormatError(
                f"entry #{i}: 目录声明尺寸 {width}x{height}，"
                f"图像数据实际为 {aw}x{ah}"
            )
        if not compressed and abc != bit_count:
            raise IconFormatError(
                f"entry #{i}: 目录声明色深 {bit_count}bpp，"
                f"图像数据实际为 {abc}bpp"
            )

        images.append(IconImage(
            width=width, height=height, bit_count=bit_count, data=blob,
            compressed=compressed, color_count=color_count, planes=planes,
            has_alpha=has_alpha, index=i, offset=offset,
        ))
    return IconFile(images=images, icon_type=icon_type)


# ---------------------------------------------------------------------------
# 写出
# ---------------------------------------------------------------------------

def build(images, icon_type: int = TYPE_ICON) -> bytes:
    """把图像列表封装为 ICO 字节串；偏移与长度由数据实际长度计算，保证自洽。"""
    if not images:
        raise IconFormatError("至少需要一个目录项")
    n = len(images)
    header = struct.pack("<HHH", 0, icon_type, n)
    dir_len = 6 + 16 * n
    entries = []
    payload = []
    offset = dir_len
    for i, img in enumerate(images):
        blob = bytes(img.data)
        w, h, bc, planes, compressed, _alpha = probe_image(blob, i)
        width = img.width or w
        height = img.height or h
        if (width, height) != (w, h):
            raise IconFormatError(
                f"entry #{i}: 声明尺寸 {width}x{height} 与数据 {w}x{h} 不符，拒绝写出"
            )
        if not (1 <= width <= 256 and 1 <= height <= 256):
            raise IconFormatError(
                f"entry #{i}: 尺寸 {width}x{height} 超出 ICO 允许的 1..256"
            )
        bit_count = img.bit_count or bc
        entries.append(struct.pack(
            "<BBBBHHII",
            width % 256, height % 256,
            img.color_count, 0,
            img.planes if img.planes else planes,
            bit_count,
            len(blob),            # 长度取自真实数据
            offset,               # 偏移按目录区结束位置顺序排布
        ))
        payload.append(blob)
        offset += len(blob)
    return header + b"".join(entries) + b"".join(payload)


# ---------------------------------------------------------------------------
# 候选挑选：按目标尺寸与缩放代价排序，确定性打破平局
# ---------------------------------------------------------------------------
#
# 排序键（越小越优），全部确定：
#   1. 精确命中目标尺寸的排在最前；
#   2. 其余候选中，可缩小（候选 >= 目标）优先于需放大（放大丢失信息，代价更高）；
#   3. 缩放比 max/min 越接近 1 越优（缩放代价最小）；
#   4. 缩放比相同，色深高者优先（保留更多颜色/透明信息）；
#   5. 再相同，未压缩（BMP）优先于 PNG 压缩（免去解码）；
#   6. 最后按目录序号升序，保证结果完全确定。

def _target_wh(target):
    if isinstance(target, (int, float)):
        return int(target), int(target)
    w, h = target
    return int(w), int(h)


def _rank_key(img: IconImage, tw: int, th: int):
    exact = 0 if (img.width, img.height) == (tw, th) else 1
    scale = max(img.width / tw, img.height / th)
    downscale = 0 if scale >= 1.0 else 1
    ratio = max(scale, 1.0 / scale)
    return (exact, downscale, ratio, -img.bit_count, img.compressed, img.index)


def rank(images, target) -> list:
    """按目标尺寸对候选排序，返回最优在前的列表。"""
    tw, th = _target_wh(target)
    if tw <= 0 or th <= 0:
        raise ValueError(f"非法目标尺寸 {target!r}")
    images = list(images)

    def key(pos_img):
        pos, img = pos_img
        # 未经过 parse 的候选没有目录序号，用列表位置代替，保证确定性
        if img.index < 0:
            img = IconImage(**{**img.__dict__, "index": pos})
        return _rank_key(img, tw, th)

    return [img for _, img in sorted(enumerate(images), key=key)]


def select(images, target) -> IconImage:
    """返回目标尺寸下的最优候选。"""
    best = rank(images, target)
    if not best:
        raise IconFormatError("没有可用候选")
    return best[0]
