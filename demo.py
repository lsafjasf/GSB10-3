"""运行演示：打印增广路径法每轮的匹配数量变化，以及最大权匹配结果。

运行：python3 demo.py
"""

from bipartite_matching import max_matching, max_weight_matching, assert_valid_matching


def show(title, left, right, edges):
    print(f"\n=== {title} ===")
    matching, trace = max_matching(left, right, edges)
    print(f"轮次 | 尝试左节点 | 是否增广 | 增广前数量 | 增广后数量")
    for r in trace:
        print(f"{r['round']:>4} | {str(r['left']):>10} | "
              f"{'是' if r['augmented'] else '否':^8} | "
              f"{r['size_before']:>10} | {r['size_after']:>10}")
    print(f"最大匹配：{matching}，匹配数：{len(matching)}")
    return matching


def main():
    # 1) 教科书小例子（含“重新分配”的增广过程）
    left = ["u1", "u2", "u3"]
    right = ["v1", "v2"]
    edges = [("u1", "v1"), ("u2", "v1"), ("u2", "v2"), ("u3", "v2")]
    show("基础例子（含增广翻转）", left, right, edges)

    # 2) 完全二分图 K3,3
    left = ["u1", "u2", "u3"]
    right = ["v1", "v2", "v3"]
    edges = [(u, v) for u in left for v in right]
    show("完全二分图 K3,3", left, right, edges)

    # 3) 空图 + 孤点
    show("空图（含孤点）", ["u1", "u2"], ["v1", "v2"], [])

    # 4) 多重边
    show("多重边（重复边不影响结果）", ["u1", "u2"], ["v1"],
         [("u1", "v1"), ("u1", "v1"), ("u2", "v1"), ("u2", "v1")])

    # 5) 带权最大匹配
    print("\n=== 最大权匹配（3x3）===")
    left = ["u1", "u2", "u3"]
    right = ["v1", "v2", "v3"]
    wedges = [("u1", "v1", 3), ("u1", "v2", 1), ("u1", "v3", 2),
              ("u2", "v1", 1), ("u2", "v2", 2), ("u2", "v3", 4),
              ("u3", "v1", 2), ("u3", "v2", 5), ("u3", "v3", 1)]
    matching, total = max_weight_matching(left, right, wedges)
    print(f"最大权匹配：{matching}，总权重：{total}（u1-v1=3, u2-v3=4, u3-v2=5）")

    # 6) 带权与不带权的关系：同图对比
    print("\n=== 带权 vs 不带权（同一图）===")
    left = ["u1", "u2", "u3"]
    right = ["v1", "v2"]
    edges = [("u1", "v1"), ("u2", "v1"), ("u2", "v2"), ("u3", "v2")]
    weights = {("u1", "v1"): 1, ("u2", "v1"): 9, ("u2", "v2"): 8, ("u3", "v2"): 1}
    m_card, _ = max_matching(left, right, edges)
    m_opt, total = max_weight_matching(
        left, right, [(u, v, w) for (u, v), w in weights.items()])
    card_weight = sum(weights[e] for e in m_card.items())
    print(f"不带权最大匹配：{m_card}，基数 {len(m_card)}，按权重计 {card_weight}")
    print(f"带权最大匹配：{m_opt}，基数 {len(m_opt)}，总权重 {total}")
    print("带权解基数不超过不带权最大匹配基数，但总权重不低于它（本例 "
          f"{total} >= {card_weight}）")
    assert_valid_matching(m_opt, set(edges), left, right)
    print("合法性断言通过")


if __name__ == "__main__":
    main()
