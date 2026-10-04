"""结果报告：对比表、ASCII 残差下降曲线、CSV 导出。"""
import csv
import math


def ascii_residual_curve(history, width=64, height=16, label=""):
    """把相对残差历史画成 semilog ASCII 曲线，返回字符串。"""
    if not history:
        return "(无数据)"
    logs = [math.log10(max(h, 1e-300)) for h in history]
    lo, hi = min(logs), max(logs)
    if hi - lo < 1e-12:
        hi = lo + 1.0
    # 采样到 width 列
    if len(logs) > width:
        step = len(logs) / width
        sampled = [logs[int(k * step)] for k in range(width)]
    else:
        sampled = logs
    w = len(sampled)
    grid = [[" "] * w for _ in range(height)]
    for c, v in enumerate(sampled):
        row = int((hi - v) / (hi - lo) * (height - 1))
        grid[row][c] = "*"
    lines = []
    if label:
        lines.append(label)
    for r in range(height):
        axis = f"1e{int(hi - r * (hi - lo) / (height - 1)):>4d} |"
        lines.append(axis + "".join(grid[r]))
    lines.append("       +" + "-" * w)
    lines.append(f"        迭代 0 -> {len(history) - 1}   (共 {len(history)} 个记录点)")
    return "\n".join(lines)


def print_comparison_table(title, rows):
    """rows: [(方法, 是否收敛, 迭代数, 最终相对残差, 说明)]"""
    print(f"\n=== {title} ===")
    header = f"{'方法':<10}{'收敛':<6}{'迭代数':>8}{'最终相对残差':>16}  说明"
    print(header)
    print("-" * len(header) + "--------")
    for name, conv, it, rel, msg in rows:
        print(f"{name:<10}{('是' if conv else '否'):<6}{it:>8}{rel:>16.3e}  {msg}")


def write_summary_csv(path, all_rows):
    """all_rows: dict 列表，键 scenario/method/converged/iterations/final_rel_residual"""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "scenario", "method", "converged", "iterations",
            "final_rel_residual", "final_abs_residual", "message"])
        w.writeheader()
        w.writerows(all_rows)


def write_curve_csv(path, scenario, curves):
    """curves: {方法名: [相对残差历史]}，按迭代对齐（缺失留空）。"""
    methods = list(curves)
    maxlen = max(len(v) for v in curves.values())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["record_index"] + methods)
        for k in range(maxlen):
            row = [k]
            for m in methods:
                hist = curves[m]
                row.append(f"{hist[k]:.6e}" if k < len(hist) else "")
            w.writerow(row)
