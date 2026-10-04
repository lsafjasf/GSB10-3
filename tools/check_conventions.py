#!/usr/bin/env python3
"""违规注入验证（变异测试 / mutation testing）。

对每一条约定，在源码里故意做一处违规改动（把该约定的断言条件
替换成 True / 或删掉约束），然后运行整个回归测试套件：

- 原始代码：测试必须全部通过；
- 注入违规后：测试必须失败，且失败用例必须指向该约定编号。

若某条约定被注入违规后测试居然还通过，说明断言没被测试保护，
本脚本以非零状态码退出。
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
REPORT = ROOT / "reports" / "mutation_report.md"

# (约定编号, 源码文件, 被替换片段, 替换成, 预期出现次数)
MUTATIONS = [
    ("S1", "events.py",
     "frozenset(event.keys()) == REQUIRED_KEYS", "True", 1),
    ("S2", "events.py",
     "not (event_type == \"quote\" and symbol is not None)", "True", 1),
    ("S3", "processor.py",
     'type(receipt["committed_at"]) is int', "True", 1),
    ("O1", "processor.py",
     "self._state in allowed", "True", 1),
    ("O2", "processor.py",
     "not self._events or event[\"occurred_at\"] >= previous", "True", 1),
    ("O3", "processor.py",
     "self._state != STATE_DONE", "True", 1),
    ("R1", "events.py",
     "isinstance(event_id, str) and len(event_id) > 0", "True", 1),
    ("R2", "events.py",
     "event_type in EVENT_TYPES", "True", 1),
    ("R3", "events.py",
     "type(amount) is int and MIN_AMOUNT <= amount <= MAX_AMOUNT", "True", 1),
    ("R4", "events.py",
     "type(occurred_at) is int and occurred_at >= 0", "True", 1),
    ("R5", "events.py",
     "all(type(tag) is str for tag in tags)", "True", 1),
    ("R6", "events.py",
     "symbol is None or symbol in ALLOWED_SYMBOLS", "True", 1),
    ("R7", "processor.py",
     "len(self._events) < self._max_batch", "True", 1),
    ("T1", "processor.py",
     "threading.get_ident() == self._owner", "True", 1),
    ("T2", "processor.py",
     "not self._lock._is_owned()", "True", 1),
    # T3 断言在两个 *_locked 方法里各有一处，两处一起破坏。
    ("T3", "processor.py",
     'check(self._lock._is_owned(), "T3"', 'check(True, "T3"', 2),
]

FAIL_LINE = re.compile(rb"^(?:FAIL|ERROR): (.+)$", re.MULTILINE)


def run_tests():
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT, capture_output=True,
    )
    output = proc.stderr + proc.stdout
    return proc.returncode, output


def main():
    code, output = run_tests()
    if code != 0:
        print("基线测试未通过，请先修复：")
        print(output.decode(errors="replace"))
        return 2
    print(f"基线：全部测试通过（{len(FAIL_LINE.findall(b'')) or '?'}）。")

    rows = []
    all_killed = True
    for cid, filename, anchor, replacement, expected in MUTATIONS:
        path = SRC / filename
        original = path.read_text(encoding="utf-8")
        count = original.count(anchor)
        if count != expected:
            print(f"[{cid}] 锚点在 {filename} 出现 {count} 次（期望 {expected}），中止。")
            path.write_text(original, encoding="utf-8")
            return 3
        mutated = original.replace(anchor, replacement)
        path.write_text(mutated, encoding="utf-8")
        try:
            code, output = run_tests()
        finally:
            path.write_text(original, encoding="utf-8")

        text = output.decode(errors="replace")
        failed = [m.decode() for m in FAIL_LINE.findall(output)]
        mentions = cid in text
        killed = code != 0 and mentions and any(cid in name for name in failed)
        all_killed = all_killed and killed
        status = "KILLED（测试失败，违规被抓住）" if killed else "SURVIVED（违规逃逸！）"
        rows.append((cid, filename, anchor, failed, status, killed))
        print(f"[{cid}] {status}  失败用例 {failed[:3]}")

    REPORT.write_text(render_report(rows), encoding="utf-8")
    print(f"\n报告已写入 {REPORT.relative_to(ROOT)}")
    return 0 if all_killed else 1


def render_report(rows):
    lines = [
        "# 违规注入验证结果",
        "",
        "对每条约定的断言做一处源码变异（断言条件 -> True），",
        "运行 `python3 -m unittest discover -s tests`，期望全部变异被测试杀死。",
        "",
        "| 约定 | 变异文件 | 注入的违规改动 | 结果 | 触发的失败用例 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for cid, filename, anchor, failed, status, killed in rows:
        names = "<br>".join(failed) if failed else "（无）"
        mark = "✅" if killed else "❌"
        lines.append(
            f"| {cid} | {filename} | `{anchor}` -> `True` | {mark} {status} | {names} |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
