"""参考实现：逐字节手写解析 SOCKS5 应答，用于与 socks5.reply 对拍。

刻意不用 ipaddress / 切片辅助函数，全部用下标游标和手工位移计算，
保证与主解析器是两条独立的代码路径。

返回 dict：
    {"ver": int, "rep": int, "rsv": int, "atyp": int,
     "bnd_addr": str, "bnd_port": int}

失败时抛出 RefError，其 .kind 为机器可比的错误类别字符串。
"""

from __future__ import annotations


class RefError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind
        super().__init__(f"{kind}: {detail}" if detail else kind)


def _take(data: bytes, pos: int, count: int, field: str) -> tuple[bytes, int]:
    if pos + count > len(data):
        raise RefError("truncated", f"{field}: want {count} @ {pos}, len={len(data)}")
    return data[pos : pos + count], pos + count


def _fmt_ipv4(raw: bytes) -> str:
    return ".".join(str(b) for b in raw)


def _fmt_ipv6(raw: bytes) -> str:
    # 手工 RFC 5952 压缩：找最长全零段（长度 >= 2）压成 "::"。
    groups = [(raw[i] << 8) | raw[i + 1] for i in range(0, 16, 2)]
    best_start, best_len = -1, 0
    cur_start, cur_len = -1, 0
    for i, g in enumerate(groups):
        if g == 0:
            if cur_start < 0:
                cur_start, cur_len = i, 1
            else:
                cur_len += 1
            if cur_len > best_len:
                best_start, best_len = cur_start, cur_len
        else:
            cur_start, cur_len = -1, 0
    if best_len < 2:
        best_start, best_len = -1, 0
    parts = []
    i = 0
    while i < 8:
        if i == best_start:
            parts.append("")
            i += best_len
            if i >= 8:
                parts.append("")
        else:
            parts.append(format(groups[i], "x"))
            i += 1
    out = ":".join(parts)
    if best_start == 0:
        out = ":" + out
    return out


def parse_reply_reference(data: bytes) -> dict:
    pos = 0
    head, pos = _take(data, pos, 4, "header")
    ver, rep, rsv, atyp = head[0], head[1], head[2], head[3]

    if ver != 0x05:
        raise RefError("bad_version", f"0x{ver:02x}")
    if rep not in (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08):
        raise RefError("unknown_rep", f"0x{rep:02x}")

    if atyp == 0x01:
        raw, pos = _take(data, pos, 4, "bnd_addr(ipv4)")
        addr = _fmt_ipv4(raw)
    elif atyp == 0x03:
        lenb, pos = _take(data, pos, 1, "bnd_addr(domain length)")
        n = lenb[0]
        raw, pos = _take(data, pos, n, "bnd_addr(domain)")
        addr = raw.decode("utf-8", errors="replace")
    elif atyp == 0x04:
        raw, pos = _take(data, pos, 16, "bnd_addr(ipv6)")
        addr = _fmt_ipv6(raw)
    else:
        raise RefError("unknown_atyp", f"0x{atyp:02x}")

    portb, pos = _take(data, pos, 2, "bnd_port")
    port = (portb[0] << 8) | portb[1]

    return {
        "ver": ver,
        "rep": rep,
        "rsv": rsv,
        "atyp": atyp,
        "bnd_addr": addr,
        "bnd_port": port,
    }
