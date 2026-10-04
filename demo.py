"""端到端演示：6 种场景下的校验定位、修复决策与版本/校验值变化数据。

运行：python3 demo.py
"""
from __future__ import annotations

import os
import tempfile

from pagestore import (
    CRC_OFFSET,
    HEADER_SIZE,
    Decision,
    MultiReplicaStore,
    ReplicaStatus,
    ReplicaStore,
)


def build(tmpdir, n=3, init=True, size=128, seed=0):
    paths = [os.path.join(tmpdir, f"replica{i}.log") for i in range(n)]
    stores = [ReplicaStore(p) for p in paths]
    cluster = MultiReplicaStore(stores)
    data = bytes(((i * 7 + seed) % 256) for i in range(size))
    if init:
        cluster.write_page(1, data)
    return cluster, stores, data


def show_read(report):
    for d in report.replicas:
        loc = "; ".join(str(c) for c in d.corruptions) or "无"
        stored = f"0x{d.stored_crc:08X}" if d.stored_crc is not None else "-"
        computed = f"0x{d.computed_crc:08X}" if d.computed_crc is not None else "-"
        print(f"  副本{d.replica} {d.status.value:<15} 存储校验值={stored} "
              f"重算校验值={computed} 损坏位置: {loc}")


def show_changes(report):
    if not report.changes:
        print("  副本动作: 全部放弃，不做任何写入")
        return
    print("  版本/校验值变化:")
    for c in report.changes:
        def vx(v):
            return "-" if v is None else str(v)

        def cx(crc):
            return "-" if crc is None else f"0x{crc:08X}"

        if c.action == "keep":
            print(f"    副本{c.replica} 保留      版本 {vx(c.version_before):>3}        "
                  f"校验值 {cx(c.crc_before)}")
        else:
            print(f"    副本{c.replica} 写新页    版本 {vx(c.version_before):>3} -> "
                  f"{vx(c.version_after):<3} 校验值 {cx(c.crc_before)} -> "
                  f"{cx(c.crc_after)}")


def run(title, fn):
    print("=" * 72)
    print(title)
    print("-" * 72)
    tmp = tempfile.TemporaryDirectory()
    cluster, stores, data = fn(tmp.name)
    read_report = cluster.read_page(1)
    print(f"读页 page_no={read_report.page_no}，"
          f"过半一致: {'是' if read_report.healthy else '否'}")
    show_read(read_report)
    repair = cluster.repair_page(1)
    verdict = {
        Decision.REPAIRED: "修复（写新页）",
        Decision.NO_REPAIR_NEEDED: "无需修复",
        Decision.ABORT_NO_MAJORITY: "放弃修复",
        Decision.ABORT_NO_HEALTHY_SOURCE: "放弃修复",
    }[repair.decision]
    print(f"  决策: {repair.decision.value}（{verdict}）")
    print(f"  依据: {repair.reason}")
    show_changes(repair)
    print()
    tmp.cleanup()


def s1(tmp):
    cluster, stores, data = build(tmp)
    stores[0].tamper(1, HEADER_SIZE + 10)
    return cluster, stores, data


def s2(tmp):
    cluster, stores, data = build(tmp)
    stores[1].tamper(1, CRC_OFFSET)
    return cluster, stores, data


def s3(tmp):
    cluster, stores, data = build(tmp)
    stores[0].tamper(1, HEADER_SIZE + 3)
    stores[1].tamper(1, HEADER_SIZE + 9)
    return cluster, stores, data


def s4(tmp):
    cluster, stores, data = build(tmp)
    for i, s in enumerate(stores):
        s.tamper(1, HEADER_SIZE + i)
    return cluster, stores, data


def s5(tmp):
    paths = [os.path.join(tmp, f"replica{i}.log") for i in range(4)]
    stores = [ReplicaStore(p) for p in paths]
    cluster = MultiReplicaStore(stores)
    data_a = bytes((i % 251) for i in range(128))
    data_b = bytes(((i + 1) % 251) for i in range(128))
    stores[0].append_page(1, 1, data_a)
    stores[1].append_page(1, 1, data_a)
    stores[2].append_page(1, 1, data_b)
    stores[3].append_page(1, 1, data_b)
    return cluster, stores, data_a


def s6(tmp):
    cluster, stores, data = build(tmp)
    os.remove(os.path.join(tmp, "replica2.log"))
    return cluster, stores, data


if __name__ == "__main__":
    print("=" * 72)
    print("修复决策表（n=副本数，k=校验自洽副本中最大同镜像票数）")
    print("-" * 72)
    table = [
        ("全部一致健康", "k == n", "无需修复"),
        ("少数副本损坏", "k > n/2", "以多数镜像为来源，对少数副本写新页"),
        ("仅校验值/版本字段存疑", "数据与多数一致、crc 不自洽", "按多数重写该副本新页"),
        ("票数相同", "最高票并列（如 2:2、1:1:1）", "放弃修复并报告"),
        ("多数副本损坏", "k <= n/2（健康副本不足过半）", "放弃修复，防止坏数据覆盖好数据"),
        ("全部副本损坏", "无校验自洽副本", "放弃修复并报告"),
        ("副本缺失/落后", "其余副本仍构成过半", "以多数数据写新页补齐/追平"),
    ]
    for case, cond, action in table:
        print(f"  {case:<14} 条件: {cond:<34} -> {action}")
    print()

    run("场景 1：单副本数据损坏（1/3）", s1)
    run("场景 2：校验值本身存疑，数据完好（crc 字段被改）", s2)
    run("场景 3：多数副本损坏（2/3，损坏方式不同）", s3)
    run("场景 4：全部副本损坏（3/3）", s4)
    run("场景 5：票数相同（4 副本，2:2，均校验自洽）", s5)
    run("场景 6：单副本缺失（2/3 健康）", s6)
