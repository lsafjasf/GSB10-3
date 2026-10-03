# -*- coding: utf-8 -*-
"""自测与评测脚本：边界用例 + 算法对比 + 阈值/误用率扫描。

运行：python3 selftest.py
"""
# -*- coding: utf-8 -*-
from fuzzy_match import (
    DEFAULT_THRESHOLD,
    HistoryEntry,
    diff_segments,
    find_best_match,
    render_diff,
    similarity,
    simple_overlap,
)

# ---------------------------------------------------------------- 演示用历史句库

HISTORY = [
    HistoryEntry("The file was uploaded successfully.", "文件上传成功。",
                 "TM:project-alpha", "A-001"),
    HistoryEntry("The user uploaded the files.", "用户已上传这些文件。",
                 "TM:project-alpha", "A-002"),
    HistoryEntry("The cat chases the dog.", "猫追狗。",
                 "TM:demo", "D-001"),
    HistoryEntry("Do you want to delete the selected files?", "确定要删除选中的文件吗？",
                 "TM:project-beta", "B-001"),
    HistoryEntry("Your order has been shipped.", "您的订单已发货。",
                 "TM:shop", "S-001"),
    HistoryEntry("用户已成功登录系统。", "User signed in successfully.",
                 "TM:project-alpha", "A-003"),
]

# ---------------------------------------------------------------- 一、边界用例断言

def test_edge_cases():
    # 完全相同 -> 满分，可复用
    r = find_best_match("The file was uploaded successfully.", HISTORY)
    assert r.score == 1.0 and r.reusable and r.entry.entry_id == "A-001", r.describe()

    # 仅标点不同 -> 高分（>=0.9），可复用
    r = find_best_match("The file was uploaded successfully!", HISTORY)
    assert r.score >= 0.9 and r.reusable, r.describe()

    # 全角/半角标点差异 -> 归一化后等价
    assert similarity("确定要删除吗？", "确定要删除吗?") >= 0.99

    # 大小写差异 -> 满分
    assert similarity("Save Changes", "save changes") == 1.0

    # 变格（复数/时态）-> 高分
    s = similarity("The users uploaded the files", "The user uploaded the file")
    assert s >= 0.9, s

    # 词序颠倒（语义改变！）-> 本算法必须显著掉分，而简单重合度=满分
    ours = similarity("The dog chases the cat.", "The cat chases the dog.")
    base = simple_overlap("The dog chases the cat.", "The cat chases the dog.")
    assert ours < DEFAULT_THRESHOLD, ours
    assert base == 1.0, base

    # 完全不同 -> 低分，不可复用
    r = find_best_match("Completely unrelated sentence about quantum physics.", HISTORY)
    assert not r.reusable, r.describe()

    # 空历史库 -> 无命中
    r = find_best_match("anything", [])
    assert r.entry is None and not r.reusable

    # 空查询 -> 不崩溃
    assert 0.0 <= similarity("", "abc") <= 1.0
    assert similarity("", "") == 1.0

    # 差异高亮包含删除/新增标记
    diff = render_diff(diff_segments("The user uploaded the files.",
                                     "The user uploaded 3 files."))
    assert "[-" in diff and "{+" in diff, diff

    print("[PASS] 边界用例全部通过（完全相同/仅标点/变格/词序颠倒/完全不同/空库/空串/全半角）")


# ---------------------------------------------------------------- 二、算法对比数据

COMPARE_CASES = [
    ("完全相同",        "The file was uploaded successfully.", "The file was uploaded successfully."),
    ("仅标点不同",      "The file was uploaded successfully!", "The file was uploaded successfully."),
    ("变格(复数/时态)", "The users uploaded 2 files",          "The user uploaded the file"),
    ("词序颠倒(语义变)","The dog chases the cat.",              "The cat chases the dog."),
    ("中文词序颠倒",    "狗追猫",                               "猫追狗"),
    ("完全不同",        "Quantum entanglement is spooky.",      "The file was uploaded successfully."),
]

def print_comparison():
    print("\n=== 相似度算法对比（本算法 vs 简单重合度基线）===")
    print(f"{'情形':<16}{'本算法':>8}{'简单重合度':>12}  结论")
    for name, a, b in COMPARE_CASES:
        ours, base = similarity(a, b), simple_overlap(a, b)
        note = ""
        if name.startswith("词序颠倒") or name.startswith("中文词序"):
            note = "基线误判为可复用！" if base >= DEFAULT_THRESHOLD else ""
        print(f"{name:<16}{ours:>8.2f}{base:>12.2f}  {note}")


# ---------------------------------------------------------------- 三、差异高亮样例

def print_diff_samples():
    print("\n=== 命中差异高亮样例 ===")
    queries = [
        "The file was uploaded successfully!",
        "The user uploaded 3 files.",
        "Do you want to delete the selected file?",
        "用户已成功登录系统!",
    ]
    for q in queries:
        r = find_best_match(q, HISTORY)
        print(f"\n待译句: {q}")
        print(r.describe())


# ---------------------------------------------------------------- 四、阈值 vs 误用率

# (待译句, 历史句, 是否应复用)
EVAL_PAIRS = [
    # ---- 正例：应当复用（语义不变的安全差异）----
    ("The file was uploaded successfully!",  "The file was uploaded successfully.",  True),
    ("File uploaded successfully",           "The file was uploaded successfully.",  True),
    ("The users uploaded the files",         "The user uploaded the file",           True),
    ("Save changes",                         "save changes",                         True),
    ("确定要删除吗？",                        "确定要删除吗?",                        True),
    ("用户已成功登录系统!",                   "用户已成功登录系统。",                  True),
    ("The files were uploaded successfully", "The file was uploaded successfully",   True),
    ("Do you want to delete these files?",   "Do you want to delete the files?",     True),
    # ---- 负例：不得复用（语义改变）----
    ("The dog chases the cat.",              "The cat chases the dog.",              False),
    ("狗追猫",                               "猫追狗",                               False),
    ("You cannot delete the file",           "You can delete the file",              False),
    ("Transfer 200 USD to Bob",              "Transfer 100 USD to Alice",            False),
    ("恢复选中的文件",                        "删除选中的文件",                       False),
    ("关闭自动保存",                          "开启自动保存",                         False),
    ("Your order has been cancelled.",       "Your order has been shipped.",         False),
    ("Quantum entanglement is spooky.",      "The file was uploaded successfully.",  False),
]

def sweep_thresholds():
    print("\n=== 阈值 vs 自动复用率 / 误用率 ===")
    print("（复用率=被自动复用的正例占比；误用率=被自动复用的负例占比）")
    print(f"{'阈值':>6} | {'本算法 复用率':>12}{'误用率':>8} | {'简单重合度 复用率':>14}{'误用率':>8}")
    print("-" * 64)
    thresholds = [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.99]
    pos = [p for p in EVAL_PAIRS if p[2]]
    neg = [p for p in EVAL_PAIRS if not p[2]]
    for th in thresholds:
        row = []
        for scorer in (similarity, simple_overlap):
            reused_pos = sum(1 for q, h, _ in pos if scorer(q, h) >= th)
            reused_neg = sum(1 for q, h, _ in neg if scorer(q, h) >= th)
            row.append((reused_pos / len(pos), reused_neg / len(neg)))
        print(f"{th:>6.2f} | {row[0][0]:>12.0%}{row[0][1]:>8.0%} | "
              f"{row[1][0]:>14.0%}{row[1][1]:>8.0%}")
    print("\n结论：简单重合度把“词序颠倒/否定词”等负例判为满分，任何阈值下都会误用；")
    print("本算法在阈值 0.85 附近复用率最高且误用率为 0，故默认阈值取 DEFAULT_THRESHOLD=0.85。")


if __name__ == "__main__":
    test_edge_cases()
    print_comparison()
    print_diff_samples()
    sweep_thresholds()
