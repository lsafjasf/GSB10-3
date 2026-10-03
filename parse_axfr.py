"""解析区域传送流文件，按 (名称, 类型) 分组输出 JSON。

用法：
    python3 parse_axfr.py [--raw] INPUT [-o OUTPUT]

    --raw   输入是单个裸 DNS 报文（无 TCP 两字节长度前缀）
    -o      结果写入文件；缺省打印到标准输出

退出码：0 成功；2 报文截断/长度不符；3 压缩指针成环；1 其他解析错误。
"""

import argparse
import json
import sys

from dns_axfr import (
    DNSError,
    NameLoopError,
    TruncatedError,
    group_records,
    parse_message,
    parse_stream,
)


def build_result(messages):
    return {
        "messages": [
            {
                "header": message.header.to_dict(),
                "questions": [q.to_dict() for q in message.questions],
                "record_count": message.record_count,
            }
            for message in messages
        ],
        "record_count": sum(m.record_count for m in messages),
        "groups": group_records(messages),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="输入文件（区域传送流或裸报文）")
    parser.add_argument("--raw", action="store_true", help="输入为裸 DNS 报文")
    parser.add_argument("-o", "--output", help="输出 JSON 文件路径")
    args = parser.parse_args(argv)

    with open(args.input, "rb") as handle:
        payload = handle.read()

    try:
        messages = [parse_message(payload)] if args.raw else parse_stream(payload)
    except TruncatedError as exc:
        result = {
            "error": "truncated",
            "detail": exc.detail,
            "records_parsed": exc.records_parsed,
            "missing_bytes": exc.missing_bytes,
        }
        exit_code = 2
    except NameLoopError as exc:
        result = {"error": "pointer_loop", "detail": str(exc), "offset": exc.offset}
        exit_code = 3
    except DNSError as exc:
        result = {"error": "parse_error", "detail": str(exc)}
        exit_code = 1
    else:
        result = build_result(messages)
        exit_code = 0

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
