"""实验：不同初始步长 alpha0 下的迭代次数与最终误差。

对三类问题（强凸 / 平坦区域 / 边界受限）分别用回溯法与插值法运行
梯度下降，统计外层迭代轮数、目标函数评估总次数和最终误差。

运行：python3 experiments.py
"""

import math

from linesearch import (backtracking_line_search, gradient_descent,
                        interpolation_line_search)


class Counted:
    """包装目标函数，统计评估次数。"""

    def __init__(self, f):
        self.f = f
        self.n = 0

    def __call__(self, x):
        self.n += 1
        return self.f(x)


def norm(a, b):
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


PROBLEMS = {
    "强凸二次(条件数100)": dict(
        f=lambda x: 0.5 * (x[0] ** 2 + 100.0 * x[1] ** 2),
        g=lambda x: [x[0], 100.0 * x[1]],
        x0=[1.0, 1.0], xstar=[0.0, 0.0], project=None, tol=1e-8),
    "平坦区域(四次)": dict(
        f=lambda x: x[0] ** 4 + x[1] ** 4,
        g=lambda x: [4.0 * x[0] ** 3, 4.0 * x[1] ** 3],
        x0=[1.0, -1.0], xstar=[0.0, 0.0], project=None, tol=1e-6),
    "边界受限(盒约束)": dict(
        f=lambda x: (x[0] - 3.0) ** 2 + (x[1] + 2.0) ** 2,
        g=lambda x: [2.0 * (x[0] - 3.0), 2.0 * (x[1] + 2.0)],
        x0=[0.0, 0.0], xstar=[1.0, -1.0],
        project=lambda x: [min(1.0, max(-1.0, x[0])),
                           min(1.0, max(-1.0, x[1]))],
        tol=1e-9),
}

ALPHA0S = [1e-8, 1e-4, 1.0, 1e4, 1e10]
METHODS = [("回溯", backtracking_line_search),
           ("插值", interpolation_line_search)]


def main():
    for pname, p in PROBLEMS.items():
        print("=" * 78)
        print("问题：%s   初值 x0=%s   最优解 x*=%s"
              % (pname, p["x0"], p["xstar"]))
        print("-" * 78)
        print("%-6s %-10s %10s %12s %14s %s"
              % ("方法", "alpha0", "迭代轮数", "f评估次数", "最终误差‖x-x*‖",
                 "单调下降"))
        for mname, ls in METHODS:
            for a0 in ALPHA0S:
                fc = Counted(p["f"])
                x, fx, iters, hist = gradient_descent(
                    fc, p["g"], p["x0"], ls, alpha0=a0,
                    project=p["project"], tol=p["tol"], max_iter=200000)
                mono = all(hist[i] <= hist[i - 1] + 1e-12
                           for i in range(1, len(hist)))
                print("%-6s %-10.0e %10d %12d %14.3e %s"
                      % (mname, a0, iters, fc.n,
                         norm(x, p["xstar"]), "是" if mono else "否"))
        print()


if __name__ == "__main__":
    main()
