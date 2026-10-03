"""评测脚本：生成对比数据、阈值-误用率数据、差异高亮样例。

运行：python3 benchmark.py
输出：终端表格 + data/case_scores.csv + data/threshold_sweep.csv
"""

import csv
import os

from fuzzy_tm import (TranslationMemory, TMEntry, render_diff_plain,
                      similarity, similarity_overlap)

# 人工标注的评测对：label=1 表示语义相同、可安全复用译文；0 表示不可复用。
PAIRS = [
    # ---- 正例：语义相同，应当复用 ----
    ("The file was saved successfully.",
     "The file was saved successfully!", 1, "仅标点不同"),
    ("Click the Save button to continue.",
     "Click the save button to continue.", 1, "仅大小写不同"),
    ("All items were updated.",
     "All item were updated.", 1, "变格：单复数"),
    ("The user is running the backup task.",
     "The user is run the backup task.", 1, "变格：时态/词形"),
    ("She deleted three files from the folder.",
     "She deleted three file from the folder.", 1, "变格：复数"),
    ("Please restart the application to apply changes.",
     "Please restart the application to apply the changes.", 1, "虚词增减"),
    ("把文件保存到本地磁盘。",
     "把文件保存到本地磁盘", 1, "中文：仅标点不同"),
    ("用户已成功登录系统。",
     "用户已成功登陆系统。", 1, "中文：近义字"),
    ("No changes were made to the document.",
     "No changes were made to the document!", 1, "仅标点不同"),
    ("The report contains five charts.",
     "The report contain five charts.", 1, "变格：动词"),
    # ---- 反例：语义不同，不得复用 ----
    ("The file was saved successfully.",
     "The file was deleted successfully.", 0, "关键词相反：save/delete"),
    ("Click the Save button to continue.",
     "Click the Cancel button to continue.", 0, "关键词相反：Save/Cancel"),
    ("She deleted three files from the folder.",
     "She deleted three files from the server.", 0, "关键名词不同"),
    ("Please restart the application to apply changes.",
     "Please restart the server to apply changes.", 0, "关键名词不同"),
    ("The quick brown fox jumps over the lazy dog.",
     "Quantum chromodynamics describes strong interactions.", 0, "完全不同"),
    ("用户已成功登录系统。",
     "用户登录系统失败。", 0, "中文：语义相反"),
    ("把文件保存到本地磁盘。",
     "从本地磁盘删除文件。", 0, "中文：语义相反"),
    ("The report contains five charts.",
     "The report contains five tables.", 0, "关键名词不同"),
    ("No changes were made to the document.",
     "Many changes were made to the document.", 0, "数量词相反"),
    ("All items were updated.",
     "All items were deleted.", 0, "关键词相反"),
]

# 边界情形专项（含词序颠倒：考察词序敏感性）
EDGE_CASES = [
    ("完全相同", "The file was saved successfully.",
     "The file was saved successfully."),
    ("仅标点不同", "The file was saved successfully.",
     "The file was saved successfully!"),
    ("词序颠倒", "The cat chased the dog.",
     "The dog chased the cat."),
    ("完全不同", "The quick brown fox jumps over the lazy dog.",
     "Quantum chromodynamics describes strong interactions."),
    ("变格", "All items were updated.",
     "All item was update."),
    ("空串", "", ""),
]


def fmt(v: float) -> str:
    return f"{v:.3f}"


def case_table() -> list[str]:
    lines = []
    header = f"{'情形':<14}{'基线重合度':>10}{'本算法':>8}  查询 / 候选"
    lines.append(header)
    lines.append("-" * 78)
    for name, a, b in EDGE_CASES:
        base = similarity_overlap(a, b)
        ours = similarity(a, b).score
        lines.append(f"{name:<14}{fmt(base):>10}{fmt(ours):>8}  {a!r} / {b!r}")
    return lines


def sweep() -> tuple[list[str], list[tuple]]:
    """阈值扫描：误用率=被自动复用的反例占比；召回=被复用的正例占比。"""
    scored = []
    for a, b, label, note in PAIRS:
        s = similarity(a, b).score
        scored.append((s, label, note, a, b))

    rows = []
    lines = [f"{'阈值':>6}{'误用率(反例被复用)':>18}{'正例召回':>10}{'自动复用总数':>14}"]
    lines.append("-" * 52)
    t = 0.50
    while t <= 0.999:
        reused_pos = sum(1 for s, l, *_ in scored if l == 1 and s >= t)
        reused_neg = sum(1 for s, l, *_ in scored if l == 0 and s >= t)
        total_pos = sum(1 for _, l, *_ in scored if l == 1)
        total_neg = sum(1 for _, l, *_ in scored if l == 0)
        misuse = reused_neg / total_neg
        recall = reused_pos / total_pos
        rows.append((round(t, 2), misuse, recall, reused_pos + reused_neg))
        lines.append(f"{t:>6.2f}{misuse:>18.0%}{recall:>10.0%}"
                     f"{reused_pos + reused_neg:>14}")
        t += 0.05
    return lines, rows


def diff_samples() -> list[str]:
    lines = []
    samples = [
        ("仅标点不同", "The file was saved successfully!",
         "The file was saved successfully."),
        ("关键词替换", "The file was deleted successfully.",
         "The file was saved successfully."),
        ("变格", "All item were updated.",
         "All items were updated."),
        ("词序颠倒", "The dog chased the cat.",
         "The cat chased the dog."),
        ("中文", "把文件保存到本地磁盘",
         "把文件保存到本地磁盘。"),
    ]
    for name, query, cand in samples:
        lines.append(f"[{name}]")
        lines.append(f"  查询: {query}")
        lines.append(f"  历史: {cand}")
        lines.append(f"  差异: {render_diff_plain(query, cand)}")
    return lines


def reuse_demo() -> list[str]:
    tm = TranslationMemory([
        TMEntry("The file was saved successfully.", "文件保存成功。", "tm://en2zh/0001"),
        TMEntry("Click the Save button to continue.", "点击“保存”按钮继续。", "tm://en2zh/0002"),
        TMEntry("All items were updated.", "所有条目已更新。", "tm://en2zh/0003"),
        TMEntry("The cat chased the dog.", "猫追狗。", "tm://en2zh/0004"),
    ], threshold=0.90)
    lines = []
    for q in ["The file was saved successfully!",
              "All item were updated.",
              "The dog chased the cat.",
              "Something entirely unrelated happened."]:
        r = tm.lookup(q)
        lines.append(f"查询: {q}")
        lines.append(f"  相似度={r['similarity']}  决策={r['decision']}")
        if r.get("source"):
            lines.append(f"  来源: {r['source']['sid']}  历史: {r['source']['text']}")
            lines.append(f"  差异: {r['diff']}")
        if r["reused"]:
            lines.append(f"  复用译文: {r['reuse_target']}")
    return lines


def main() -> None:
    os.makedirs("data", exist_ok=True)

    print("== 边界情形：基线重合度 vs 本算法（词序+变格敏感）==")
    for line in case_table():
        print(line)

    print("\n== 阈值 vs 误用率（20 组人工标注句对）==")
    sweep_lines, rows = sweep()
    for line in sweep_lines:
        print(line)

    print("\n== 差异高亮样例 ==")
    for line in diff_samples():
        print(line)

    print("\n== 复用决策演示（自动复用阈值 0.90，人工确认带 0.70~0.90）==")
    for line in reuse_demo():
        print(line)

    with open("data/case_scores.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case", "query", "candidate", "label",
                    "baseline_overlap", "ours"])
        for a, b, label, note in PAIRS:
            w.writerow([note, a, b, label,
                        f"{similarity_overlap(a, b):.4f}",
                        f"{similarity(a, b).score:.4f}"])
    with open("data/threshold_sweep.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["threshold", "misuse_rate", "recall", "auto_reused"])
        w.writerows(rows)
    print("\n已写出 data/case_scores.csv 与 data/threshold_sweep.csv")


if __name__ == "__main__":
    main()
