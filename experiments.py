"""experiments.py — 不同初始步长下的迭代次数与最终误差对比

对四个问题 × 两种线搜索 × 五种初始步长运行梯度下降，
输出迭代次数、函数评估次数、最终误差 |f - f*| 与 ||x - x*||，
并验证每条运行历史满足目标值单调下降。

运行: python3 experiments.py
"""

from math import sqrt

from linesearch import gradient_descent, assert_monotone_decreasing

# ---------------------------------------------------------------------------
# 问题定义（f* 与 x* 均已知，用于误差计算）
# ---------------------------------------------------------------------------

def quad_f(x):
    return 0.5 * (x[0] ** 2 + 100.0 * x[1] ** 2)

def quad_g(x):
    return [x[0], 100.0 * x[1]]

def rosen_f(x):
    return (1.0 - x[0]) ** 2 + 100.0 * (x[1] - x[0] ** 2) ** 2

def rosen_g(x):
    return [-2.0 * (1.0 - x[0]) - 400.0 * x[0] * (x[1] - x[0] ** 2),
            200.0 * (x[1] - x[0] ** 2)]

def quartic_f(x):
    r2 = x[0] ** 2 + x[1] ** 2
    return r2 * r2

def quartic_g(x):
    r2 = x[0] ** 2 + x[1] ** 2
    return [4.0 * x[0] * r2, 4.0 * x[1] * r2]

C = [3.0, -2.0]   # 耦合病态二次型 A=[[10,9],[9,10]]，解为边界点 (1,0), f*=4

def bounded_f(x):
    d0, d1 = x[0] - C[0], x[1] - C[1]
    return 0.5 * (10.0 * d0 * d0 + 18.0 * d0 * d1 + 10.0 * d1 * d1)

def bounded_g(x):
    d0, d1 = x[0] - C[0], x[1] - C[1]
    return [10.0 * d0 + 9.0 * d1, 9.0 * d0 + 10.0 * d1]

PROBLEMS = [
    {"name": "强凸二次 (cond=100)", "f": quad_f, "g": quad_g,
     "x0": [1.0, 1.0], "x_star": [0.0, 0.0], "f_star": 0.0,
     "bounds": None, "tol": 1e-9, "max_iter": 20000},
    {"name": "Rosenbrock", "f": rosen_f, "g": rosen_g,
     "x0": [-1.2, 1.0], "x_star": [1.0, 1.0], "f_star": 0.0,
     "bounds": None, "tol": 1e-8, "max_iter": 50000},
    {"name": "平坦四次 (x^2+y^2)^2", "f": quartic_f, "g": quartic_g,
     "x0": [1.0, 1.0], "x_star": [0.0, 0.0], "f_star": 0.0,
     "bounds": None, "tol": 1e-8, "max_iter": 20000},
    {"name": "边界受限耦合二次 (盒[0,1]^2)", "f": bounded_f, "g": bounded_g,
     "x0": [0.2, 0.5], "x_star": [1.0, 0.0], "f_star": 4.0,
     "bounds": ([0.0, 0.0], [1.0, 1.0]), "tol": 1e-10, "max_iter": 20000},
]

ALPHA0_GRID = [1e-6, 1e-3, 1.0, 1e3, 1e8]
METHODS = ["backtracking", "interpolation"]


def main():
    print("## 首轮线搜索成本（强凸二次，从 (1,1) 出发，精确最优步长约 0.01）")
    print(f"{'方法':<14}{'alpha0':>10}{'本轮函数评估':>14}{'接受步长':>14}")
    from linesearch import backtracking_line_search, interpolation_line_search
    x, d = [1.0, 1.0], [-1.0, -100.0]
    phi = lambda a: quad_f([x[0] + a * d[0], x[1] + a * d[1]])
    phi0, dphi0 = phi(0.0), -10001.0
    for name, ls in [("backtracking", backtracking_line_search),
                     ("interpolation", interpolation_line_search)]:
        for alpha0 in ALPHA0_GRID:
            r = ls(phi, phi0, dphi0, alpha0=alpha0)
            print(f"{name:<14}{alpha0:>10.0e}{r.n_fevals:>14}{r.alpha:>14.6f}")

    for prob in PROBLEMS:
        print(f"\n=== {prob['name']} ===")
        print(f"{'方法':<14}{'alpha0':>10}{'迭代':>8}{'函数评估':>10}"
              f"{'|f-f*|':>14}{'||x-x*||':>14}  收敛")
        for method in METHODS:
            for alpha0 in ALPHA0_GRID:
                res = gradient_descent(
                    prob["f"], prob["g"], prob["x0"],
                    method=method, alpha0=alpha0,
                    tol=prob["tol"], max_iter=prob["max_iter"],
                    bounds=prob["bounds"])
                # 单调下降断言：每条实验轨迹都验证
                assert_monotone_decreasing(res.f_history)
                f_err = abs(res.f - prob["f_star"])
                x_err = sqrt(sum((xi - si) ** 2 for xi, si
                                 in zip(res.x, prob["x_star"])))
                tag = "是" if res.converged else f"否({res.message})"
                print(f"{method:<14}{alpha0:>10.0e}{res.n_iter:>8}"
                      f"{res.n_fevals:>10}{f_err:>14.3e}{x_err:>14.3e}  {tag}")
    print("\n所有轨迹均通过单调下降断言 (assert_monotone_decreasing)")


if __name__ == "__main__":
    main()
