"""自测：词族断言、边界用例、归并数量度量。

运行：
    python3 -m morpho.selftest          # 打印报告并把数据写入 merge_metrics.json
    python3 morpho/selftest.py          # 直接运行等价
退出码为 0 表示全部断言通过。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from morpho import gold, tables
from morpho.lemmatizer import Lemmatizer, NaiveStemmer


# ---------------------------------------------------------------------------
# 度量方法
# ---------------------------------------------------------------------------
def build_labels():
    """同一词族内的所有形态共享一个标签；单例各自独立。"""
    labels: dict[str, str] = {}
    for key, forms in gold.FAMILIES.items():
        for form in forms:
            labels[form] = key
    for word in gold.SINGLETONS:
        labels[word] = f"#{word}"
    return labels


def pair_metrics(lemmatizer, labels):
    """枚举全部无序词对，按“是否同标签 / 是否同归并键”划分为四格。

    TP=正确归并, FP=错误归并(过度), TN=正确分开, FN=错误分开(欠归并)。
    同时返回词面级准确率（与黄金标签精确匹配）。
    """
    counts = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    words = list(labels)
    keys = {w: lemmatizer.lemmatize(w) for w in words}
    for a, b in combinations(words, 2):
        same_gold = labels[a] == labels[b]
        same_pred = keys[a] == keys[b]
        if same_gold and same_pred:
            counts["TP"] += 1
        elif not same_gold and same_pred:
            counts["FP"] += 1
        elif not same_gold and not same_pred:
            counts["TN"] += 1
        else:
            counts["FN"] += 1
    correct_surface = sum(1 for w in words if keys[w] == labels[w].lstrip("#"))
    return counts, correct_surface, len(words), keys


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------
def run_assertions(engine: Lemmatizer):
    # 1) 词族断言：同一词族的全部形态必须落到同一个键，且键就是词族键
    for key, forms in gold.FAMILIES.items():
        keys = {engine.lemmatize(form) for form in forms}
        assert keys == {key}, f"词族 {key!r} 未归一: {[(f, engine.lemmatize(f)) for f in forms]}"

    # 2) 单例断言：不得落到任何词族键上
    family_keys = set(gold.FAMILIES)
    for word in gold.SINGLETONS:
        k = engine.lemmatize(word)
        assert k not in family_keys, f"单例 {word!r} 被错误归并到 {k!r}"

    # 3) 陷阱对断言
    for a, b in gold.TRAP_PAIRS:
        ka, kb = engine.lemmatize(a), engine.lemmatize(b)
        assert ka != kb, f"陷阱对 {a!r}/{b!r} 被归并到 {ka!r}"

    # 4) 边界用例断言
    for raw, expected in gold.EDGE_CASES.items():
        got = engine.lemmatize(raw)
        assert got == expected, f"边界用例 {raw!r}: 期望 {expected!r}, 实际 {got!r}"


def assert_table_consistency():
    """规则表/例外表与数据集的一致性（更新表时防止漏改）。"""
    # 每个词族键都必须在已知原型词表中，否则规则没有落点
    missing = set(gold.FAMILIES) - tables.KNOWN_LEMMAS
    assert not missing, f"词族键不在 KNOWN_LEMMAS 中: {sorted(missing)}"
    # 不规则表的目标必须是已知原型
    bad = {k: v for k, v in tables.IRREGULAR.items() if v not in tables.KNOWN_LEMMAS}
    assert not bad, f"不规则表目标未知: {bad}"


def assert_updatable():
    """表可更新：临时 JSON override 验证优先级与运行时更新。"""
    engine = Lemmatizer()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "overrides.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "irregular": {"zorked": "zork"},
                "known": ["zork"],
            }, fh)
        engine.load_overrides(path)
    assert engine.lemmatize("zorked") == "zork", "不规则表更新未生效"
    assert engine.lemmatize("zorks") == "zork", "known 词表更新未生效"

    # 规则级例外更新：把 cat 加入 -s 规则的 blocked 后，cats 不再走该规则
    engine.update_rule("s", blocked=frozenset({"cats"}))
    assert engine.lemmatize("cats") == "cats", "规则 blocked 更新未生效"


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def report(name, counts, surface_ok, total):
    pairs = sum(counts.values())
    tp, fp, tn, fn = counts["TP"], counts["FP"], counts["TN"], counts["FN"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    print(f"\n[{name}] 词面 {total} 个，词对 {pairs} 对")
    print(f"  正确归并 TP = {tp}")
    print(f"  错误归并 FP = {fp}   <- 过度归并（规则加过头）")
    print(f"  正确分开 TN = {tn}")
    print(f"  错误分开 FN = {fn}   <- 欠归并（规则不足）")
    print(f"  精确率 P  = {precision:.4f}")
    print(f"  召回率 R  = {recall:.4f}")
    print(f"  词面命中率 = {surface_ok}/{total} = {surface_ok/total:.4f}")
    return {"name": name, "pairs": pairs, **counts,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "surfaces": total, "surface_correct": surface_ok,
            "surface_accuracy": round(surface_ok / total, 4)}


def main():
    labels = build_labels()
    engine = Lemmatizer()
    naive = NaiveStemmer()

    assert_table_consistency()
    run_assertions(engine)
    assert_updatable()
    print("全部断言通过：词族、单例、陷阱对、边界用例、表更新。")

    # 打印若干错误归并实例（朴素基线为什么失败）
    e_counts, e_ok, total, e_keys = pair_metrics(engine, labels)
    n_counts, n_ok, _, n_keys = pair_metrics(naive, labels)

    if n_counts["FP"]:
        print("\n朴素基线的错误归并示例（FP，最多 10 对）：")
        shown = 0
        for a, b in combinations(list(labels), 2):
            if labels[a] != labels[b] and n_keys[a] == n_keys[b]:
                print(f"  {a!r}({labels[a]})  <->  {b!r}({labels[b]})  => {n_keys[a]!r}")
                shown += 1
                if shown == 10:
                    break

    results = [report("Lemmatizer（不规则表+规则+词表门控）", e_counts, e_ok, total),
               report("NaiveStemmer（规则加过头的基线）", n_counts, n_ok, total)]

    # 检索示例：同键命中
    docs = {
        "d1": "Cats are running and cats were seen",
        "d2": "a cat organization meeting",
        "d3": "organizations organize",
    }
    index = engine.build_index(docs)
    print(f"\n检索倒排示例：cat -> {index.get('cat')}（同一键命中 d1/d2）")
    assert set(index["cat"]) == {"d1", "d2"}
    assert "organization" not in index.get("organ", [])

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "merge_metrics.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"surfaces": total, "systems": results}, fh,
                  ensure_ascii=False, indent=2)
    print(f"\n归并数量数据已写入 {out}")


if __name__ == "__main__":
    main()
