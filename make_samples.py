"""生成往返对拍用的报文语料。

- samples/corpus/  : 合法报文，要求 parse -> rebuild 逐字节一致
- samples/invalid/ : 非法报文，要求 parse 抛出 LogParseError

用法: python3 make_samples.py
"""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def frame(body: bytes) -> bytes:
    """加上八位组计数长度声明。"""

    return str(len(body)).encode("ascii") + b" " + body


CORPUS = {
    # 纯传统格式：无长度声明、无结构化块
    "01_pure_traditional.bin": b"<13>web01 nginx 404 burst detected",
    # 纯结构化格式：长度声明 + 重复键 + 空消息
    "02_pure_structured.bin": frame(
        b'<134>db01 [app="postgres" app="pgbouncer" env="prod"]'
    ),
    # 混合：转义引号 / 反斜杠 / 方括号 / 换行 / 制表符
    "03_mixed_escaped.bin": frame(
        b'<165>fw01 [rule="a\\"b\\\\c\\]d" '
        b'note="line\\nbreak\\tend"] denied tcp 443'
    ),
    # 非 ASCII（UTF-8）取值与消息，长度按字节计数
    "04_non_ascii.bin": frame(
        '<190>cache01 [user="张三" city="北京"] 缓存刷新完成'.encode("utf-8")
    ),
    # 显式空结构化块
    "05_empty_sd.bin": b"<0>edge01 [] boot ok",
    # 结构化块 + 空消息
    "06_empty_message.bin": frame(b'<191>mon01 [alert="disk"]'),
    # PRI 边界值 0
    "07_pri_zero.bin": b"<0>node00 kernel panic",
}

INVALID = {
    # 长度声明与实际字节数不符
    "01_bad_length.bin": b"58 <13>web01 short body",
    # 优先级越界（192 起非法）
    "02_pri_too_large.bin": b"<192>web01 out of range",
    # 优先级远超范围，且不能被截断成 0x7f 之类
    "03_pri_huge.bin": b"<99999>web01 way out of range",
    # 非法转义序列 \x
    "04_bad_escape.bin": b'<13>web01 [k="a\\xb"] oops',
    # ] 未转义
    "05_unescaped_bracket.bin": b'<13>web01 [k="a]b"] oops',
    # 取值缺少结束引号
    "06_unterminated_value.bin": b'<13>web01 [k="abc] oops',
    # 缺少 >
    "07_missing_gt.bin": b"<13web01 oops",
    # 长度声明带前导零（非规范化）
    "08_leading_zero_length.bin": b"012 <13>web01 ok",
    # 长度声明与实际差 1（非 ASCII 取值必须按字节计数）
    "09_length_off_by_one.bin": (
        lambda body: str(len(body) + 1).encode("ascii") + b" " + body
    )('<13>cache01 [city="北京"] ok'.encode("utf-8")),
}


def main() -> None:
    for subdir, corpus in (
        ("corpus", CORPUS),
        ("invalid", INVALID),
    ):
        target = os.path.join(ROOT, "samples", subdir)
        os.makedirs(target, exist_ok=True)
        for name, data in corpus.items():
            path = os.path.join(target, name)
            with open(path, "wb") as fh:
                fh.write(data)
            print(f"wrote samples/{subdir}/{name} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
