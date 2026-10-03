"""场景演示：打印修复决策表与修复前后校验值/版本号变化。

运行：python3 demo.py
"""
import os
import tempfile

from pagestore import HEADER_SIZE, PageStore

PAYLOAD = b"GSB10-3 demo page payload: checksum + majority repair" 


def flip(path, offset):
    with open(path, "r+b") as f:
        f.seek(offset)
        value = f.read(1)[0]
        f.seek(offset)
        f.write(bytes([value ^ 0xFF]))


def corrupt_payload(store, r, page=1):
    rec = store.locate(page, r)
    flip(store._paths[r], rec.offset + HEADER_SIZE + 10)


def corrupt_checksum(store, r, page=1):
    rec = store.locate(page, r)
    flip(store._paths[r], rec.offset + HEADER_SIZE + len(rec.payload) + 4)


def corrupt_header(store, r, page=1):
    rec = store.locate(page, r)
    flip(store._paths[r], rec.offset + 8)


def fresh_store(tmp, name):
    store = PageStore(os.path.join(tmp, name), replicas=3)
    store.write(1, PAYLOAD)
    return store


def show_report(title, report):
    print(f"\n### {title}")
    print(f"决策: {report.decision.value} | 法定多数: {report.quorum} | "
          f"票数: {{{', '.join(f'crc={c:#010x}: {n}票' for c, n in report.votes.items())}}}")
    print(f"原因: {report.reason}")
    for st in report.statuses:
        loc = f" region={st.region.value} offset={st.offset}" if st.region else ""
        diff = f" diff_offsets={st.diff_offsets}" if st.diff_offsets else ""
        print(f"  副本{st.replica}: {st.state.value}{loc}{diff}")
    if report.changes:
        print("  修复前后变化:")
        print("    副本 | 版本 前->后 | 校验值 前 -> 后")
        for ch in report.changes:
            before = f"{ch.before_crc:#010x}" if ch.before_crc is not None else "    -     "
            print(f"    {ch.replica:4d} | {ch.before_version} -> {ch.after_version}     "
                  f"| {before} -> {ch.after_crc:#010x}  ({ch.note})")


def main():
    tmp = tempfile.mkdtemp(prefix="pagestore-demo-")

    s = fresh_store(tmp, "s1")
    show_report("场景1 全部副本一致", s.repair(1))

    s = fresh_store(tmp, "s2")
    corrupt_payload(s, 0)
    show_report("场景2 单副本 payload 损坏 → 多数决修复", s.repair(1))

    s = fresh_store(tmp, "s3")
    corrupt_checksum(s, 1)
    show_report("场景3 单副本校验值存疑（数据完好）→ 重写校验值", s.repair(1))

    s = fresh_store(tmp, "s4")
    corrupt_header(s, 2)
    show_report("场景4 单副本页头损坏 → 重新同步后修复", s.repair(1))

    s = fresh_store(tmp, "s5")
    corrupt_payload(s, 0)
    corrupt_payload(s, 1)
    show_report("场景5 多数副本损坏（2/3）→ 放弃修复", s.repair(1))

    s = fresh_store(tmp, "s6")
    for r in range(3):
        corrupt_payload(s, r)
    show_report("场景6 全副本损坏 → 放弃修复", s.repair(1))

    s = fresh_store(tmp, "s7")
    s._append(1, 1, 2, b"divergent but valid content")
    corrupt_payload(s, 2)
    show_report("场景7 有效票 1:1 平票 → 放弃修复", s.repair(1))


if __name__ == "__main__":
    main()
