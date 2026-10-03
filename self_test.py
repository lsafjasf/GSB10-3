"""Self-tests and error/compression report for svd.py.

Run:
    python3 self_test.py

The script writes svd_results.csv by default.  Use --no-csv to disable that.
"""

from __future__ import annotations

import argparse
import csv
from math import cos, sin
from typing import Dict, List, Sequence, Tuple

from svd import (
    DEFAULT_MAX_SWEEPS,
    DEFAULT_TOL,
    SVDResult,
    compression_stats,
    direct_residual_norm,
    frobenius_norm,
    identity,
    low_rank_approximation,
    matmul,
    reconstruct,
    shape,
    svd,
    tail_energy_error,
    transpose,
    validate_matrix,
)

Matrix = List[List[float]]


def rotation_matrix(n: int, i: int, j: int, theta: float) -> Matrix:
    result = identity(n)
    c = cos(theta)
    s = sin(theta)
    result[i][i] = c
    result[j][j] = c
    result[i][j] = -s
    result[j][i] = s
    return result


def deterministic_orthogonal(n: int) -> Matrix:
    result = identity(n)
    for i in range(n):
        for j in range(i + 1, n):
            angle = 0.11 * (i + 1) + 0.07 * (j + 1) + 0.03 * n
            result = matmul(result, rotation_matrix(n, i, j, angle))
    return result


def matrix_from_singular_values(
    left: Sequence[Sequence[float]],
    right: Sequence[Sequence[float]],
    singular_values: Sequence[float],
) -> Matrix:
    rows = len(left)
    cols = len(right)
    result = [[0.0 for _ in range(cols)] for _ in range(rows)]
    for component, sigma in enumerate(singular_values):
        for i in range(rows):
            scaled_left = sigma * left[i][component]
            for j in range(cols):
                result[i][j] += scaled_left * right[j][component]
    return result


def orthonormality_error(matrix: Sequence[Sequence[float]]) -> float:
    rows, cols = shape(matrix)
    gram = matmul(transpose(matrix), matrix)
    total = 0.0
    for i in range(cols):
        for j in range(cols):
            expected = 1.0 if i == j else 0.0
            difference = gram[i][j] - expected
            total += difference * difference
    return total ** 0.5


def max_singular_difference(
    actual: Sequence[float], expected: Sequence[float]
) -> float:
    return max(abs(a - e) for a, e in zip(actual, expected))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_result(
    case_name: str,
    matrix: Matrix,
    expected_singular_values: Sequence[float],
    result: SVDResult,
) -> Dict[str, float]:
    norm_a = frobenius_norm(matrix)
    scale = max(1.0, norm_a)
    singular_error = max_singular_difference(
        result.singular_values, expected_singular_values
    )
    u_error = orthonormality_error(result.u)
    v_error = orthonormality_error(result.v)
    full_reconstruction = reconstruct(result)
    reconstruction_error = direct_residual_norm(matrix, full_reconstruction)

    require(
        singular_error <= 1e-9 * scale,
        case_name + ": singular values differ from construction",
    )
    require(u_error <= 1e-9, case_name + ": U columns are not orthonormal")
    require(v_error <= 1e-9, case_name + ": V columns are not orthonormal")
    require(
        reconstruction_error <= 1e-9 * scale,
        case_name + ": full SVD reconstruction residual is too large",
    )
    require(
        result.final_off_diagonal_norm <= DEFAULT_TOL * max(norm_a, 1e-300),
        case_name + ": Jacobi convergence criterion was not met",
    )

    return {
        "norm_a": norm_a,
        "singular_error": singular_error,
        "u_error": u_error,
        "v_error": v_error,
        "reconstruction_error": reconstruction_error,
    }


def make_cases() -> List[Tuple[str, Matrix, List[float], List[int]]]:
    specifications = [
        ("rank_one_4x3", 4, 3, [7.0, 0.0, 0.0], [1, 2, 3]),
        (
            "nearly_singular_5x5",
            5,
            5,
            [8.0, 3.0, 1.0, 1e-6, 1e-10],
            [1, 2, 3, 4, 5],
        ),
        ("tall_full_rank_6x3", 6, 3, [9.0, 4.0, 1.5], [1, 2, 3]),
        ("wide_rank_deficient_3x6", 3, 6, [11.0, 5.0, 2.0], [1, 2, 3]),
        ("all_zero_4x3", 4, 3, [0.0, 0.0, 0.0], [1, 2, 3]),
    ]

    cases = []
    for name, rows, cols, singular_values, ranks in specifications:
        left = deterministic_orthogonal(rows)
        right = deterministic_orthogonal(cols)
        matrix = matrix_from_singular_values(left, right, singular_values)
        cases.append((name, matrix, singular_values, ranks))
    return cases


def run_api_edge_checks() -> None:
    try:
        validate_matrix([[1.0, 2.0], [3.0]])
    except ValueError:
        pass
    else:
        raise AssertionError("ragged matrix was not rejected")

    try:
        low_rank_approximation([[1.0, 2.0], [3.0, 4.0]], 3)
    except ValueError:
        pass
    else:
        raise AssertionError("rank above min(m, n) was not rejected")

    try:
        svd([[1.0, 2.0], [3.0, 4.0]], max_sweeps=0)
    except ValueError:
        pass
    else:
        raise AssertionError("max_sweeps=0 was not rejected")


def format_float(value: float) -> str:
    return "{:.6e}".format(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        default="svd_results.csv",
        help="CSV output path (default: svd_results.csv)",
    )
    parser.add_argument("--no-csv", action="store_true", help="do not write CSV output")
    args = parser.parse_args()

    print("Pure-Python SVD self-test")
    print(
        "Convergence: ||off(B^T B)||_F <= {:.1e} * ||A||_F; max sweeps: {}".format(
            DEFAULT_TOL, DEFAULT_MAX_SWEEPS
        )
    )
    print("Low-rank storage: k*(m+n+1) values for (U_k, Sigma_k, V_k)")
    print()

    csv_rows = []
    total_rank_rows = 0

    for case_name, matrix, expected_singular_values, ranks in make_cases():
        rows, cols = shape(matrix)
        result = svd(matrix)
        quality = check_result(case_name, matrix, expected_singular_values, result)

        print("CASE {} ({}x{})".format(case_name, rows, cols))
        print(
            "  computed singular values: "
            + ", ".join(format_float(value) for value in result.singular_values)
        )
        print(
            "  expected singular values: "
            + ", ".join(format_float(value) for value in expected_singular_values)
        )
        print(
            "  max singular error: {:.3e}; U orth error: {:.3e}; "
            "V orth error: {:.3e}".format(
                quality["singular_error"], quality["u_error"], quality["v_error"]
            )
        )
        print(
            "  full reconstruction residual: {:.3e}; sweeps: {}; "
            "final off-diagonal: {:.3e}".format(
                quality["reconstruction_error"],
                result.sweeps,
                result.final_off_diagonal_norm,
            )
        )
        print(
            "  k  direct_residual  tail_energy  abs_diff  rel_diff  "
            "stored/original  ratio  saving"
        )

        for rank in ranks:
            approximation = reconstruct(result, rank)
            direct = direct_residual_norm(matrix, approximation)
            tail = tail_energy_error(result.singular_values, rank)
            absolute_difference = abs(direct - tail)
            relative_difference = (
                absolute_difference / tail if tail > 0.0 else 0.0
            )
            stats = compression_stats(rows, cols, rank)

            require(
                absolute_difference <= 2e-9 * max(1.0, quality["norm_a"]),
                case_name + ": direct residual and tail-energy error differ",
            )
            if case_name.startswith("rank_one") and rank == 1:
                require(
                    direct <= 1e-10 * quality["norm_a"],
                    "rank-one matrix was not reproduced by rank-1 approximation",
                )
            if case_name.startswith("all_zero"):
                require(
                    direct == 0.0,
                    "all-zero matrix produced a nonzero residual",
                )

            stored = int(stats["stored_values"])
            original = int(stats["original_values"])
            ratio = stats["compression_ratio"]
            saving = stats["space_saving"]
            print(
                "  {}  {:.6e}      {:.6e}   {:.3e}   {:.3e}   "
                "{:>3d}/{:<3d}        {:.3f}  {:.2f}%".format(
                    rank,
                    direct,
                    tail,
                    absolute_difference,
                    relative_difference,
                    stored,
                    original,
                    ratio,
                    100.0 * saving,
                )
            )

            csv_rows.append(
                {
                    "case": case_name,
                    "rows": rows,
                    "cols": cols,
                    "rank": rank,
                    "direct_residual_frobenius": direct,
                    "tail_energy_frobenius": tail,
                    "abs_difference": absolute_difference,
                    "relative_difference": relative_difference,
                    "stored_values": stored,
                    "original_values": original,
                    "compression_ratio": ratio,
                    "space_saving": saving,
                }
            )
            total_rank_rows += 1

        if case_name.startswith("all_zero"):
            require(
                all(value == 0.0 for value in result.singular_values),
                "all-zero matrix produced a nonzero singular value",
            )
        print()

    run_api_edge_checks()

    if not args.no_csv:
        fieldnames = [
            "case",
            "rows",
            "cols",
            "rank",
            "direct_residual_frobenius",
            "tail_energy_frobenius",
            "abs_difference",
            "relative_difference",
            "stored_values",
            "original_values",
            "compression_ratio",
            "space_saving",
        ]
        with open(args.csv, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)
        print("CSV data written to " + args.csv)

    print(
        "ALL CHECKS PASSED: {} cases, {} rank rows".format(
            len(make_cases()), total_rank_rows
        )
    )


if __name__ == "__main__":
    main()
