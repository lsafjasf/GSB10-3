"""主演示：四种情形 × {无预处理, Jacobi, ILU(0)}，输出迭代对比与残差曲线。

运行：python3 src/demo.py
结果写入 results/（CSV 数据 + 完整文本报告）。
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matrices import (laplacian_2d, nearly_singular, convection_diffusion,
                      exact_solution, parabola_solution)
from preconditioner import JacobiPreconditioner, ILU0Preconditioner
from gmres import gmres
from sparse_matrix import norm2
from report import (ascii_residual_curve, print_comparison_table,
                    write_summary_csv, write_curve_csv)

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "results")
CURVE_DIR = os.path.join(RESULTS_DIR, "residual_curves")

TOL = 1e-8


def build_methods():
    return [
        ("无预处理", None),
        ("Jacobi", JacobiPreconditioner),
        ("ILU(0)", ILU0Preconditioner),
    ]


def run_scenario(title, A, b, x_star, x0=None, max_iter=200, restart=50):
    print("\n" + "#" * 72)
    print(f"# {title}   (n={A.nrows}, nnz={A.nnz}, "
          f"tol={TOL:g}, max_iter={max_iter}, restart={restart})")
    print("#" * 72)

    info = A.storage_report()
    print(f"存储：CSR {info['csr_bytes']} B vs 稠密 {info['dense_bytes']} B，"
          f"节省 {info['ratio']:.1f} 倍（每行平均 {info['avg_nnz_per_row']:.1f} 个非零元）")

    table_rows = []
    curves = {}
    summary = []
    for name, factory in build_methods():
        M = factory(A) if factory is not None else None
        res = gmres(A, b, M, x0=x0, tol=TOL, max_iter=max_iter, restart=restart)
        err = norm2([a - c for a, c in zip(res["x"], x_star)])
        curves[name] = res["residual_history"]
        table_rows.append((name, res["converged"], res["iterations"],
                           res["final_relative_residual"], res["message"]))
        summary.append({
            "scenario": title, "method": name,
            "converged": res["converged"], "iterations": res["iterations"],
            "final_rel_residual": f"{res['final_relative_residual']:.6e}",
            "final_abs_residual": f"{res['final_absolute_residual']:.6e}",
            "message": res["message"],
        })
        print(ascii_residual_curve(
            res["residual_history"], label=f"[{name}] 相对残差下降曲线"))
        print(f"    解的绝对误差 ||x-x*||_2 = {err:.3e}")
        if not res["converged"]:
            print(f"    !! 未收敛报告：达到最大迭代 {max_iter}，"
                  f"最终相对残差 {res['final_relative_residual']:.3e}，"
                  f"绝对残差 {res['final_absolute_residual']:.3e}")

    print_comparison_table(title, table_rows)
    return summary, curves


def main():
    os.makedirs(CURVE_DIR, exist_ok=True)
    all_summary = []

    # 1. 对角占优：2D 五点 Laplacian（严格对角占优、SPD，非均匀对角）
    A1 = laplacian_2d(16, diag_variation=lambda i: (i % 7) * 0.5)
    n1 = A1.nrows
    x1 = exact_solution(n1)
    b1 = A1.matvec(x1)
    s1, c1 = run_scenario("情形1 对角占优(2D Laplacian)", A1, b1, x1)
    all_summary += s1
    write_curve_csv(os.path.join(CURVE_DIR, "case1_diag_dominant.csv"),
                    "case1", c1)

    # 2. 接近奇异：1D Laplacian + 微小平移，条件数 ~ 1/eps
    A2 = nearly_singular(200, eps=1e-6)
    n2 = A2.nrows
    # 注意：不能用 sin 精确解——它是该三对角矩阵的特征向量，
    # 会使 Krylov 子空间退化为一维，失去测试意义
    x2 = parabola_solution(n2)
    b2 = A2.matvec(x2)
    s2, c2 = run_scenario("情形2 接近奇异(eps=1e-6)", A2, b2, x2,
                          max_iter=500, restart=50)
    all_summary += s2
    write_curve_csv(os.path.join(CURVE_DIR, "case2_near_singular.csv"),
                    "case2", c2)

    # 3. 非对称：对流扩散（中心差分，强对流）
    A3 = convection_diffusion(16, conv=10.0)
    n3 = A3.nrows
    x3 = exact_solution(n3)
    b3 = A3.matvec(x3)
    s3, c3 = run_scenario("情形3 非对称(对流扩散 conv=10)", A3, b3, x3,
                          max_iter=300, restart=50)
    all_summary += s3
    write_curve_csv(os.path.join(CURVE_DIR, "case3_nonsymmetric.csv"),
                    "case3", c3)

    # 4. 初值很差：同一对角占优矩阵，三种初值
    print("\n" + "#" * 72)
    print("# 情形4 初值很差（矩阵同情形1，对比不同初值）")
    print("#" * 72)
    import math
    x0_list = [
        ("零初值", [0.0] * n1),
        ("偏置初值1e6*1", [1e6] * n1),
        ("高频振荡1e8",
         [1e8 * math.sin(7 * (i + 1)) for i in range(n1)]),
    ]
    c4 = {}
    for label, x0 in x0_list:
        r0 = norm2([bi - xi for bi, xi in zip(b1, A1.matvec(x0))])
        print(f"\n--- 初值：{label}，初始相对残差 ||r0||/||b|| = "
              f"{r0 / norm2(b1):.3e} ---")
        for name, factory in build_methods():
            M = factory(A1) if factory is not None else None
            res = gmres(A1, b1, M, x0=x0, tol=TOL, max_iter=200, restart=50)
            all_summary.append({
                "scenario": f"情形4 初值很差[{label}]", "method": name,
                "converged": res["converged"],
                "iterations": res["iterations"],
                "final_rel_residual": f"{res['final_relative_residual']:.6e}",
                "final_abs_residual": f"{res['final_absolute_residual']:.6e}",
                "message": res["message"],
            })
            c4[f"{label}|{name}"] = res["residual_history"]
            print(f"    {name:<10} 收敛={res['converged']!s:<5} "
                  f"迭代={res['iterations']:>4d}  "
                  f"最终相对残差={res['final_relative_residual']:.3e}")
    write_curve_csv(os.path.join(CURVE_DIR, "case4_bad_x0.csv"), "case4", c4)

    # 存储占用汇总
    storage_path = os.path.join(RESULTS_DIR, "storage_comparison.csv")
    with open(storage_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["matrix", "n", "nnz", "csr_bytes",
                    "dense_bytes", "saving_factor"])
        for label, A in [("laplacian_2d_16x16", A1),
                         ("nearly_singular_n200", A2),
                         ("convection_diffusion_16x16", A3)]:
            info = A.storage_report()
            w.writerow([label, info["n"], info["nnz"], info["csr_bytes"],
                        info["dense_bytes"], f"{info['ratio']:.2f}"])

    write_summary_csv(os.path.join(RESULTS_DIR, "iterations_summary.csv"),
                      all_summary)
    print("\n数据已写入 results/ ：iterations_summary.csv, "
          "storage_comparison.csv, residual_curves/*.csv")


if __name__ == "__main__":
    main()
