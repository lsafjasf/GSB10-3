#!/usr/bin/env python3
"""解析 samples/ 下所有样本报文，结果写入 results/<样本名>.json。

用法：
    python3 parse_samples.py                 # 解析 samples/ 全部样本
    python3 parse_samples.py FILE [...]      # 解析指定文件
    python3 parse_samples.py -               # 从标准输入读取一个 TCP 流
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dns_axfr as dns  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE, "samples")
RESULT_DIR = os.path.join(BASE, "results")


def parse_one(data: bytes) -> dict:
    """解析一段 TCP 流；指针环等硬错误也记录进结果。"""
    try:
        return dns.parse_stream(data).to_dict()
    except dns.NameLoopError as exc:
        return {"error": {"type": "name_loop", "detail": str(exc),
                          "offset": exc.offset, "chain": exc.chain}}
    except dns.DnsError as exc:
        return {"error": {"type": "parse_error", "detail": str(exc)}}


def main(argv):
    os.makedirs(RESULT_DIR, exist_ok=True)
    if argv == ["-"]:
        result = parse_one(sys.stdin.buffer.read())
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0 if "error" not in result else 1

    files = argv or sorted(
        os.path.join(SAMPLE_DIR, f) for f in os.listdir(SAMPLE_DIR)
        if f.endswith(".bin"))
    exit_code = 0
    for path in files:
        with open(path, "rb") as f:
            result = parse_one(f.read())
        name = os.path.splitext(os.path.basename(path))[0] + ".json"
        out_path = os.path.join(RESULT_DIR, name)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        status = ("ERROR: " + result["error"]["type"]) if "error" in result \
            else "%d message(s), %d record(s)" % (
                result["message_count"], result["record_count"])
        if "error" in result:
            exit_code = 1
        print("%-20s -> %s  [%s]" % (os.path.basename(path), status, out_path))
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
