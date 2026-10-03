"""行滤波库：PNG 风格的五种逐行滤波器 + 自适应选择 + zlib 压缩。

滤波方式（filter type 与 PNG 规范一致）：
    0 None    out = x
    1 Sub     out = x - a
    2 Up      out = x - b
    3 Average out = x - floor((a + b) / 2)
    4 Paeth   out = x - Paeth(a, b, c)

其中 x 为当前字节，a 为左邻字节、b 为上邻字节、c 为左上字节
（按通道回退，即步长 bpp = 每像素字节数）。

每行独立选择“代价”最小的滤波方式。代价采用 PNG 常用的
绝对残差启发式：把残差看作有符号字节求绝对值之和。
"""

import zlib

FILTERS = (0, 1, 2, 3, 4)
FILTER_NAMES = {0: "None", 1: "Sub", 2: "Up", 3: "Average", 4: "Paeth"}

_MAGIC = b"RFLT1"


def paeth_predictor(a, b, c):
    """PNG 规范的 Paeth 预测器。"""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _signed_abs(v):
    """把 0..255 的字节当作有符号字节后的绝对值。"""
    return v if v < 128 else 256 - v


def filtered_cost(buf):
    """一段滤波输出的代价：有符号字节绝对值之和。"""
    total = 0
    for v in buf:
        total += v if v < 128 else 256 - v
    return total


def filter_row(row, prev, bpp, ftype):
    """对一行原始字节做前向滤波，返回新的 bytes。

    row  : 当前行原始字节（长度为 width*bpp）
    prev : 上一行原始字节；第一行传 b''
    bpp  : 每像素字节数（通道数）
    ftype: 0..4
    """
    n = len(row)
    out = bytearray(n)
    for i in range(n):
        x = row[i]
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i] if prev else 0
        c = prev[i - bpp] if prev and i >= bpp else 0
        if ftype == 0:
            pred = 0
        elif ftype == 1:
            pred = a
        elif ftype == 2:
            pred = b
        elif ftype == 3:
            pred = (a + b) >> 1
        else:
            pred = paeth_predictor(a, b, c)
        out[i] = (x - pred) & 0xFF
    return bytes(out)


def unfilter_row(filt, recon_prev, bpp, ftype):
    """滤波的逆运算：由残差行还原当前行原始字节，返回新的 bytes。

    recon_prev 为已还原的上一行；第一行传 b''。
    """
    n = len(filt)
    cur = bytearray(n)
    for i in range(n):
        d = filt[i]
        a = cur[i - bpp] if i >= bpp else 0
        b = recon_prev[i] if recon_prev else 0
        c = recon_prev[i - bpp] if recon_prev and i >= bpp else 0
        if ftype == 0:
            pred = 0
        elif ftype == 1:
            pred = a
        elif ftype == 2:
            pred = b
        elif ftype == 3:
            pred = (a + b) >> 1
        else:
            pred = paeth_predictor(a, b, c)
        cur[i] = (d + pred) & 0xFF
    return bytes(cur)


def choose_filter(row, prev, bpp, filters=FILTERS):
    """逐行挑选代价最小的滤波方式。

    并列时按 filter type 从小到大（None, Sub, Up, Average, Paeth）
    取第一个，保证结果确定。
    返回 (ftype, filtered_bytes, cost)。
    """
    best_ftype = None
    best_buf = None
    best_cost = None
    for ftype in filters:
        buf = filter_row(row, prev, bpp, ftype)
        cost = filtered_cost(buf)
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_ftype = ftype
            best_buf = buf
    return best_ftype, best_buf, best_cost


def encode(raw, width, height, bpp, level=9, force_filter=None):
    """编码整幅图像，返回压缩后的 bytes。

    raw          : 紧凑排列的原始像素字节（长度须等于 width*height*bpp）
    width/height : 图像尺寸（像素）
    bpp          : 每像素字节数，如灰度=1、RGB=3、RGBA=4
    level        : zlib 压缩级别 0..9
    force_filter:  None（默认）表示逐行自适应选择；
                   传入 0..4 表示整幅图强制使用该滤波器（用于对照）
    """
    stride = width * bpp
    if len(raw) != stride * height:
        raise ValueError("raw 长度与 width/height/bpp 不一致")
    if bpp <= 0 or width <= 0 or height <= 0:
        raise ValueError("width/height/bpp 必须为正数")

    payload = bytearray()
    prev = b""
    for y in range(height):
        row = bytes(raw[y * stride:(y + 1) * stride])
        if force_filter is None:
            ftype, buf, _ = choose_filter(row, prev, bpp)
        else:
            if force_filter not in FILTERS:
                raise ValueError("force_filter 必须是 0..4 或 None")
            ftype, buf = force_filter, filter_row(row, prev, bpp, force_filter)
        payload.append(ftype)
        payload.extend(buf)
        prev = row

    header = _MAGIC + width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([bpp])
    return header + zlib.compress(bytes(payload), level)


def decode(blob):
    """解码 encode() 的产物，返回 (raw, width, height, bpp, choices)。

    choices 为逐行实际使用的 filter type 列表。
    """
    if not blob.startswith(_MAGIC):
        raise ValueError("非法文件头")
    width = int.from_bytes(blob[5:9], "big")
    height = int.from_bytes(blob[9:13], "big")
    bpp = blob[13]
    payload = zlib.decompress(blob[14:])

    stride = width * bpp
    rows = []
    choices = []
    prev = b""
    pos = 0
    for _ in range(height):
        ftype = payload[pos]
        pos += 1
        if ftype not in FILTERS:
            raise ValueError("出现未知 filter type: %d" % ftype)
        filt = payload[pos:pos + stride]
        pos += stride
        if len(filt) != stride:
            raise ValueError("数据截断")
        row = unfilter_row(filt, prev, bpp, ftype)
        rows.append(row)
        choices.append(ftype)
        prev = row
    if pos != len(payload):
        raise ValueError("存在多余字节")
    raw = b"".join(rows)
    return raw, width, height, bpp, choices


def filter_distribution(raw, width, height, bpp, filters=FILTERS):
    """返回逐行选择结果与分布统计。

    返回 (choices, counts)，choices 为每行的 filter type；
    counts 为 {ftype: 行数}（未被选中的滤波器计数为 0）。
    """
    stride = width * bpp
    choices = []
    prev = b""
    for y in range(height):
        row = bytes(raw[y * stride:(y + 1) * stride])
        ftype, _, _ = choose_filter(row, prev, bpp, filters)
        choices.append(ftype)
        prev = row
    counts = {f: 0 for f in filters}
    for f in choices:
        counts[f] += 1
    return choices, counts


def encode_auto(raw, width, height, bpp, level=9):
    """在“逐行自适应”与 5 种单一滤波器共 6 种策略中取压缩体积最小者。

    逐行最小代价是熵的启发式估计，个别高度冗余的小图上，
    混合的 filter type 字节会干扰 zlib 的匹配，反而略逊于
    某一种单一滤波器。此函数保证结果不差于任何单一策略。
    """
    best = encode(raw, width, height, bpp, level=level)
    for ftype in FILTERS:
        blob = encode(raw, width, height, bpp, level=level, force_filter=ftype)
        if len(blob) < len(best):
            best = blob
    return best
