"""Render residual curves from results/residual_curves.csv as ASCII plots.

Run:  python3 plot_curves.py  (writes results/residual_curves_ascii.txt)
"""

import csv
import math
import os

RESULTS = os.path.join(os.path.dirname(__file__), "results")
WIDTH, HEIGHT = 64, 18


def load():
    curves = {}
    with open(os.path.join(RESULTS, "residual_curves.csv")) as f:
        for row in csv.DictReader(f):
            key = (row["case"], row["preconditioner"])
            curves.setdefault(key, []).append(
                (int(row["iteration"]), float(row["relative_residual"])))
    return curves


def render(case, series):
    """series: {precond: [(it, res), ...]}; log10 scale, shared axes."""
    logs = {name: [(it, math.log10(max(r, 1e-16))) for it, r in pts]
            for name, pts in series.items()}
    xmax = max(it for pts in logs.values() for it, _ in pts) or 1
    ymax = max(0.0, math.ceil(max(lr for pts in logs.values()
                                  for _it, lr in pts)))
    ymin = min(-16.0, math.floor(min(lr for pts in logs.values()
                                     for _it, lr in pts)))
    grid = [[" "] * WIDTH for _ in range(HEIGHT)]
    marks = {"none": "o", "jacobi": "+", "ilu0": "*"}
    for name, pts in logs.items():
        ch = marks.get(name, "#")
        for it, lr in pts:
            cx = round(it / xmax * (WIDTH - 1))
            cy = round((lr - ymax) / (ymin - ymax) * (HEIGHT - 1))
            cy = max(0, min(HEIGHT - 1, cy))
            grid[cy][cx] = ch
    lines = [f"case: {case}   (log10 relative residual vs iteration)"]
    for r in range(HEIGHT):
        label = f"{ymax + (ymin - ymax) * r / (HEIGHT - 1):6.1f} |"
        lines.append(label + "".join(grid[r]))
    lines.append("       +" + "-" * WIDTH)
    pad = " " * (WIDTH - len(str(xmax)) - 1)
    lines.append(f"        0{pad}{xmax}  iteration")
    lines.append("  legend: " + "  ".join(f"{m}={n}" for n, m in marks.items()))
    return "\n".join(lines)


def main():
    curves = load()
    cases = []
    for (case, _pc) in curves:
        if case not in cases:
            cases.append(case)
    out = []
    for case in cases:
        series = {pc: pts for (c, pc), pts in curves.items() if c == case}
        out.append(render(case, series))
        out.append("")
    text = "\n".join(out)
    path = os.path.join(RESULTS, "residual_curves_ascii.txt")
    with open(path, "w") as f:
        f.write(text + "\n")
    print(text)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
