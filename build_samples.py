"""构造样本 DNS 报文 / 区域传送流，写入 samples/ 目录。

同时作为库被测试与 CLI 复用：各 build_* 函数返回 bytes。
"""

import os
import struct

SAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")


class NameCompressor:
    """按 RFC 1035 §4.1.4 编码域名，重复后缀自动改写为压缩指针。

    后写入的名称若以后缀形式引用先写入的名称，即形成任意深度的回跳链。
    """

    def __init__(self):
        self.positions = {}  # 后缀(标签元组) -> 报文内绝对偏移

    def encode(self, name: str, offset: int) -> bytes:
        labels = [] if name == "." else name.split(".")
        out = bytearray()
        index = 0
        while index < len(labels):
            suffix = tuple(labels[index:])
            if suffix in self.positions:
                out += struct.pack("!H", 0xC000 | self.positions[suffix])
                return bytes(out)
            self.positions.setdefault(suffix, offset + len(out))
            label = labels[index].encode("ascii")
            out.append(len(label))
            out += label
            index += 1
        out.append(0)
        return bytes(out)


def _header(ident, flags, qd, an, ns, ar):
    return struct.pack("!HHHHHH", ident, flags, qd, an, ns, ar)


def build_single_message() -> bytes:
    """单条记录：查询 www.example.com/A，回答一个 A 记录（名称用指针）。"""
    compressor = NameCompressor()
    question = compressor.encode("www.example.com", 12) + struct.pack("!HH", 1, 1)
    answer_name = struct.pack("!H", 0xC000 | 12)  # 回指问题段中的域名
    answer = (
        answer_name
        + struct.pack("!HHIH", 1, 1, 300, 4)
        + bytes([93, 184, 216, 34])
    )
    return _header(0x1160, 0x8400, 1, 1, 0, 0) + question + answer


def build_pointers_message() -> bytes:
    """AXFR 风格报文：9 条记录，大量压缩指针与多级回跳链。"""
    compressor = NameCompressor()
    message = bytearray()
    message += _header(0x1161, 0x8400, 1, 9, 0, 0)
    message += compressor.encode("example.com", len(message))
    message += struct.pack("!HH", 252, 1)  # qtype=AXFR, qclass=IN

    def add_record(name, rtype, rdata_builder, ttl=300):
        name_bytes = compressor.encode(name, len(message))
        rdata_offset = len(message) + len(name_bytes) + 10
        rdata = rdata_builder(rdata_offset)
        message.extend(name_bytes)
        message.extend(struct.pack("!HHIH", rtype, 1, ttl, len(rdata)))
        message.extend(rdata)

    def soa_rdata(offset):
        mname = compressor.encode("ns1.example.com", offset)
        rname = compressor.encode("hostmaster.example.com", offset + len(mname))
        return mname + rname + struct.pack(
            "!IIIII", 2026100301, 7200, 3600, 1209600, 300
        )

    def name_rdata(target):
        return lambda offset: compressor.encode(target, offset)

    def raw_rdata(payload):
        return lambda offset: payload

    add_record("example.com", 6, soa_rdata)                        # 1 SOA
    add_record("example.com", 2, name_rdata("ns1.example.com"))    # 2 NS
    add_record("example.com", 2, name_rdata("ns2.example.com"))    # 3 NS
    add_record("ns1.example.com", 1, raw_rdata(bytes([192, 0, 2, 1])))   # 4 A（纯指针名）
    add_record("ns2.example.com", 1, raw_rdata(bytes([192, 0, 2, 2])))   # 5 A
    add_record("www.example.com", 1, raw_rdata(bytes([192, 0, 2, 80])))  # 6 A（www + 指针）
    add_record("www.example.com", 1, raw_rdata(bytes([192, 0, 2, 81])))  # 7 A（同名同型第 2 条）
    add_record("web.www.example.com", 5, name_rdata("www.example.com"))  # 8 CNAME 二级回跳
    add_record("example.com", 6, soa_rdata)                        # 9 SOA（收尾，全指针）
    return bytes(message)


def build_loop_message() -> bytes:
    """指针自指成环：应答记录的名称指针指向自己所在的偏移。"""
    header = _header(0x1162, 0x8400, 0, 1, 0, 0)
    answer = struct.pack("!H", 0xC000 | 12)  # 偏移 12 处的指针指向偏移 12
    answer += struct.pack("!HHIH", 1, 1, 300, 4) + bytes([192, 0, 2, 1])
    return header + answer


def build_mutual_loop_message() -> bytes:
    """两个指针互相指向对方，形成环路。"""
    header = _header(0x1163, 0x8400, 0, 1, 0, 0)
    answer = struct.pack("!H", 0xC000 | 14)   # 偏移 12 -> 14
    answer += struct.pack("!H", 0xC000 | 12)  # 偏移 14 -> 12
    answer += struct.pack("!HHIH", 1, 1, 300, 4) + bytes([192, 0, 2, 1])
    return header + answer


def frame(message: bytes) -> bytes:
    """加 TCP 两字节长度前缀。"""
    return struct.pack("!H", len(message)) + message


def build_stream() -> bytes:
    """完整区域传送流：单记录报文 + 多指针报文。"""
    return frame(build_single_message()) + frame(build_pointers_message())


def build_truncated_stream(missing: int = 20) -> bytes:
    """流尾部帧的长度前缀比实际内容多 `missing` 字节。"""
    good = frame(build_single_message())
    tail = build_pointers_message()
    bad = struct.pack("!H", len(tail) + missing) + tail
    return good + bad


def build_truncated_record_message() -> bytes:
    """单条 A 记录的报文，RDATA 声明 4 字节但只给 2 字节。"""
    return build_single_message()[:-2]


def build_dangling_prefix_stream() -> bytes:
    """流末尾只剩 1 个字节，凑不齐长度前缀。"""
    return frame(build_single_message()) + b"\x00"


SAMPLE_BUILDERS = {
    "single.bin": lambda: frame(build_single_message()),
    "pointers.bin": lambda: frame(build_pointers_message()),
    "stream.bin": build_stream,
    "loop.bin": build_loop_message,               # 裸报文（无长度前缀）
    "mutual_loop.bin": build_mutual_loop_message,  # 裸报文
    "truncated_stream.bin": build_truncated_stream,
    "truncated_record.bin": build_truncated_record_message,  # 裸报文
    "dangling_prefix.bin": build_dangling_prefix_stream,
}


def main():
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    for filename, builder in SAMPLE_BUILDERS.items():
        payload = builder()
        path = os.path.join(SAMPLES_DIR, filename)
        with open(path, "wb") as handle:
            handle.write(payload)
        print(f"wrote {path} ({len(payload)} bytes)")


if __name__ == "__main__":
    main()
