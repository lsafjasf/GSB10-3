"""对拍用例定义：每个用例给出输入记录与参考实现算出的期望会话。

用例覆盖：单条记录、跨超时边界、同一五元组先后复用、乱序到达、
多键混合、随机压力（固定种子，可复现）。
"""

from __future__ import annotations

import random
from typing import List

from flowagg import FlowRecord, aggregate_reference, sessions_to_canonical

K1 = dict(src_ip="10.0.0.1", dst_ip="10.0.0.2", src_port=5000, dst_port=80, protocol=6)
K2 = dict(src_ip="10.0.0.1", dst_ip="10.0.0.2", src_port=5001, dst_port=80, protocol=6)
K3 = dict(src_ip="192.168.1.5", dst_ip="8.8.8.8", src_port=53, dst_port=53, protocol=17)


def rec(key: dict, start: float, end: float, packets: int, bytes_: int) -> FlowRecord:
    return FlowRecord(start=start, end=end, packets=packets, bytes=bytes_, **key)


def build_cases(idle_timeout: float = 60.0) -> List[dict]:
    """返回用例列表；records 按导出顺序（时间升序）排列。"""
    t = idle_timeout
    cases: List[dict] = []

    # 1) 单条记录
    cases.append({
        "name": "single_record",
        "records": [rec(K1, 1000.0, 1005.0, 3, 300)],
    })

    # 2) 跨超时边界：gap == t 合并；gap == t + 1 切分
    cases.append({
        "name": "timeout_boundary",
        "records": [
            rec(K1, 0.0, 10.0, 1, 100),
            rec(K1, 10.0 + t, 20.0 + t, 2, 200),          # gap == t -> 同会话
            rec(K1, 20.0 + 2 * t + 1.0, 30.0 + 2 * t, 4, 400),  # gap == t+1 -> 新会话
        ],
    })

    # 3) 同一五元组先后复用（两个会话）
    cases.append({
        "name": "five_tuple_reuse",
        "records": [
            rec(K1, 0.0, 5.0, 1, 60),
            rec(K1, 6.0, 9.0, 2, 120),
            rec(K1, 9.0 + t + 0.5, 12.0 + t, 1, 60),      # gap > t -> 新会话
            rec(K1, 13.0 + t, 15.0 + t, 3, 180),
        ],
    })

    # 4) 乱序到达（同一会话的记录乱序，间隔都在超时内）
    cases.append({
        "name": "out_of_order",
        "records": [
            rec(K1, 30.0, 35.0, 1, 10),
            rec(K1, 0.0, 5.0, 2, 20),
            rec(K1, 15.0, 20.0, 3, 30),
        ],
    })

    # 5) 多键混合交织
    cases.append({
        "name": "multi_key_interleaved",
        "records": [
            rec(K1, 0.0, 4.0, 1, 100),
            rec(K2, 1.0, 2.0, 5, 500),
            rec(K3, 2.0, 8.0, 2, 80),
            rec(K1, 10.0, 12.0, 1, 100),
            rec(K2, 3.0 + 2 * t, 4.0 + 2 * t, 5, 500),    # K2 超时复用
            rec(K3, 9.0, 11.0, 2, 80),
        ],
    })

    # 6) 随机压力（固定种子）：多个五元组、多会话、边界间隔
    rng = random.Random(20261003)
    keys = [
        dict(src_ip=f"10.1.{i}.1", dst_ip=f"172.16.{i}.9",
             src_port=10000 + i, dst_port=443, protocol=6)
        for i in range(8)
    ]
    records: List[FlowRecord] = []
    clock = 0.0
    for i in range(400):
        key = rng.choice(keys)
        # 70% 紧接上一条（小间隔），20% 边界间隔 == t，10% 超过超时
        r = rng.random()
        if r < 0.70:
            gap = rng.uniform(0.0, t * 0.5)
        elif r < 0.90:
            gap = t
        else:
            gap = t + rng.uniform(0.001, t)
        start = clock + gap
        dur = rng.uniform(0.0, 5.0)
        records.append(rec(key, start, start + dur,
                           rng.randint(1, 20), rng.randint(40, 20000)))
        clock = start + dur
    records.sort(key=lambda r: (r.start, r.end))
    cases.append({"name": "random_stress", "records": records})

    # 汇总：用参考实现计算期望输出
    out = []
    for case in cases:
        expected = sessions_to_canonical(
            aggregate_reference(case["records"], idle_timeout))
        out.append({
            "name": case["name"],
            "idle_timeout": idle_timeout,
            "records": [
                {
                    "src_ip": r.src_ip, "dst_ip": r.dst_ip,
                    "src_port": r.src_port, "dst_port": r.dst_port,
                    "protocol": r.protocol,
                    "start": r.start, "end": r.end,
                    "packets": r.packets, "bytes": r.bytes,
                }
                for r in case["records"]
            ],
            "expected_sessions": expected,
        })
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(build_cases(), indent=2, sort_keys=True))
