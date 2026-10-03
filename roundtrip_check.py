"""往返对拍脚本。

对 samples/corpus/ 下每条合法报文执行 parse -> rebuild，逐字节比对，
并把解析结果导出为 samples/parsed/*.json 样例；对 samples/invalid/
下每条非法报文验证解析器确实报错。

用法: python3 roundtrip_check.py
退出码: 0 = 全部通过；1 = 存在失败项。
"""

from __future__ import annotations

import json
import os
import sys

import logparse
import make_samples

ROOT = os.path.dirname(os.path.abspath(__file__))
CORPUS_DIR = os.path.join(ROOT, "samples", "corpus")
INVALID_DIR = os.path.join(ROOT, "samples", "invalid")
PARSED_DIR = os.path.join(ROOT, "samples", "parsed")


def first_diff(left: bytes, right: bytes) -> str:
    limit = min(len(left), len(right))
    for idx in range(limit):
        if left[idx] != right[idx]:
            return (
                f"首个差异在偏移 {idx}: "
                f"原文 0x{left[idx]:02x} vs 重建 0x{right[idx]:02x}"
            )
    return f"长度不同: 原文 {len(left)} 字节 vs 重建 {len(right)} 字节"


def check_corpus() -> int:
    failures = 0
    os.makedirs(PARSED_DIR, exist_ok=True)
    print("== 合法报文：parse -> rebuild 逐字节对拍 ==")
    for name in sorted(os.listdir(CORPUS_DIR)):
        path = os.path.join(CORPUS_DIR, name)
        with open(path, "rb") as fh:
            raw = fh.read()
        try:
            msg = logparse.parse(raw)
        except logparse.LogParseError as exc:
            print(f"  FAIL {name}: 解析报错 {exc}")
            failures += 1
            continue

        rebuilt = msg.rebuild()
        ok = rebuilt == raw
        record = msg.to_dict()
        record["raw_hex"] = raw.hex()
        record["roundtrip_ok"] = ok
        out_path = os.path.join(
            PARSED_DIR, os.path.splitext(name)[0] + ".json"
        )
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

        if ok:
            print(f"  PASS {name} ({len(raw)} bytes, 逐字节一致)")
        else:
            print(f"  FAIL {name}: {first_diff(raw, rebuilt)}")
            failures += 1
    return failures


def check_invalid() -> int:
    failures = 0
    print("== 非法报文：必须抛出 LogParseError ==")
    for name in sorted(os.listdir(INVALID_DIR)):
        path = os.path.join(INVALID_DIR, name)
        with open(path, "rb") as fh:
            raw = fh.read()
        try:
            logparse.parse(raw)
        except logparse.LogParseError as exc:
            print(f"  PASS {name}: 按预期拒绝（{exc}）")
        else:
            print(f"  FAIL {name}: 非法报文被错误接受")
            failures += 1
    return failures


def main() -> int:
    make_samples.main()
    failures = check_corpus() + check_invalid()
    if failures:
        print(f"对拍结果: {failures} 项失败")
        return 1
    print("对拍结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
