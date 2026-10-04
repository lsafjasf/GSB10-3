"""
rngqa.selftest — 用固定数据做自测，不使用任何随机源。

固定样本：
  good        : SHA-256(固定种子 || 计数器) 输出，确定性的高质量参照流
  counter     : i & 0xFF 递增字节 —— 均匀性 PASS，但周期性/游程 FAIL
                （专门演示“只查均匀性查不出低熵/周期”）
  low4_fixed  : good 流的低 4 比特强制清零（模拟固定低位）
  period17    : i % 17 循环（短周期）
  all_zero    : 全零
  constant_a5 : 边界：0xA5 重复（位平衡恰好 PASS，但结构是死的）
  tiny / empty: 边界：样本不足，全部 SKIP
"""

from __future__ import annotations

import hashlib

from .quality import assess, Report, CheckResult

N = 65536
FIXED_SEED = b"rngqa-fixed-seed-v1"


def _good(n: int = N) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        out.extend(hashlib.sha256(FIXED_SEED + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(out[:n])


def build_samples() -> dict[str, bytes]:
    good = _good()
    return {
        "good (sha256 counter-mode, fixed seed)": good,
        "counter i&0xff (uniform but low entropy)": bytes(i & 0xFF for i in range(N)),
        "low 4 bits stuck at 0": bytes(b & 0xF0 for b in good),
        "short period (i % 17)": bytes(i % 17 for i in range(N)),
        "all zero": b"\x00" * N,
        "constant 0xA5 (boundary)": b"\xA5" * N,
        "tiny 100 bytes (boundary)": b"\x00" * 100,
        "empty (boundary)": b"",
    }


# 每个样本的硬性期望：(总体判定, {检查名: 判定或判定集合, ...})
EXPECT = {
    "good (sha256 counter-mode, fixed seed)": ("PASS", {}),
    "counter i&0xff (uniform but low entropy)": (
        "FAIL", {"uniformity": "PASS", "periodicity": "FAIL"}),
    "low 4 bits stuck at 0": (
        "FAIL", {"uniformity": "FAIL", "bit_balance": "FAIL"}),
    "short period (i % 17)": (
        "FAIL", {"periodicity": "FAIL"}),
    "all zero": (
        "FAIL", {"uniformity": "FAIL", "bit_balance": "FAIL",
                 "runs": "FAIL", "periodicity": "FAIL"}),
    "constant 0xA5 (boundary)": (
        "FAIL", {"uniformity": "FAIL", "bit_balance": "FAIL",
                 "periodicity": "FAIL"}),
    "tiny 100 bytes (boundary)": ("SKIP", {}),
    "empty (boundary)": ("SKIP", {}),
}


def run() -> tuple[list[Report], list[str]]:
    reports = [assess(data, name) for name, data in build_samples().items()]
    failures: list[str] = []
    for report in reports:
        want_overall, want_checks = EXPECT[report.sample]
        if report.verdict != want_overall:
            failures.append(
                f"{report.sample}: overall {report.verdict} != {want_overall}")
        for check_name, want in want_checks.items():
            got = next(r.verdict for r in report.results if r.name == check_name)
            if got != want:
                failures.append(
                    f"{report.sample}: {check_name} {got} != {want}")
    # 周期样本必须报告精确周期 17
    period_report = next(r for r in reports if r.sample.startswith("short period"))
    period_result = next(r for r in period_report.results if r.name == "periodicity")
    if period_result.detail.get("exact_period") != 17:
        failures.append("short period sample: exact period 17 not detected")
    return reports, failures


def _cell(result: CheckResult) -> str:
    if result.verdict == "SKIP":
        return "SKIP"
    if result.name == "uniformity":
        return f"{result.verdict} (p={result.detail['p']:.2e})"
    if result.name == "bit_balance":
        return f"{result.verdict} (z={result.detail['z_global']:.1f}, worst z={result.detail['z_worst']:.1f}@{result.detail['worst_position']})"
    if result.name == "runs":
        z = result.detail["z"]
        zs = f"{z:.1f}" if z is not None else "n/a"
        return f"{result.verdict} (z={zs}, maxrun={result.detail['longest_run']})"
    z = result.detail["z_worst"]
    return f"{result.verdict} (z={z:.1f}, period={result.detail['exact_period']})"


def render_markdown(reports: list[Report]) -> str:
    header = ("| 样本 | 字节数 | 总体 | 分布均匀性 | 位平衡 | 游程 | 周期性 |\n"
              "|---|---:|---|---|---|---|---|")
    lines = [header]
    for r in reports:
        cells = " | ".join(_cell(c) for c in r.results)
        lines.append(f"| {r.sample} | {r.n_bytes} | **{r.verdict}** | {cells} |")
    return "\n".join(lines)


def render_details(reports: list[Report]) -> str:
    blocks = []
    for r in reports:
        blocks.append(f"### {r.sample}  [n={r.n_bytes}] -> {r.verdict}")
        for c in r.results:
            extra = f" — {c.reason}" if c.reason else ""
            blocks.append(f"- {c.name}: {c.verdict}{extra}\n  {c.statistic}")
    return "\n".join(blocks)
