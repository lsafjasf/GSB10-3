"""演示：跨页范围扫描 + 中途页不可读跳过 + 与全量过滤对拍。

运行：python3 demo.py
"""
from rangescan import BPlusTree, PageStore


def main():
    store = PageStore()
    tree = BPlusTree(store, max_keys=8)
    for k in range(100):
        tree.insert(k, k * 10)

    lo, hi = 10, 90
    print("边界语义: [lo, hi) 左闭右开, 即 %d <= key < %d" % (lo, hi))

    # 1) 无故障：与全量过滤对拍
    res = tree.range_scan(lo, hi)
    reference = [(k, k * 10) for k in range(100) if lo <= k < hi]
    assert res.items == reference and res.skipped == []
    print("无故障: %d 行, 与全量过滤一致" % len(res.items))

    # 2) 腐蚀中间一页：跳过并记录，扫描不中断
    bad = tree._locate(50)[0]
    lost = list(store.get_for_write(bad).keys)
    store.bad_pages.add(bad)
    res = tree.range_scan(lo, hi)
    expect = [(k, k * 10) for k in range(100)
              if lo <= k < hi and k not in lost]
    assert res.items == expect
    print("页 %d 不可读: 跳过键 %s, 记录 skipped=%s, 其余 %d 行完整"
          % (bad, lost, res.skipped, len(res.items)))

    # 3) 页恢复后重扫，结果重新完整
    store.bad_pages.clear()
    res = tree.range_scan(lo, hi)
    assert res.items == reference
    print("页恢复后重扫: %d 行, 再次与全量过滤一致" % len(res.items))


if __name__ == "__main__":
    main()
