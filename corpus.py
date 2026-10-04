"""corpus.py — 覆盖引导的模糊测试语料库维护库（仅 Python 3 标准库）。

核心机制：
- 覆盖采集：用 sys.settrace 记录目标函数所在文件被执行到的行号集合。
- 入库决策：输入必须 (1) 被目标接受（有效），(2) 相对当前语料库总覆盖有新增益，
  否则丢弃（完全重复 / 无增益 / 无效分别计数）。
- 最小化：入库前对输入做贪心块删除（ddmin 简化版），保持“本次增益行集合”不丢。
- 容量上界：条目数达到 capacity 后触发淘汰。
  淘汰策略（冗余优先 + 满则拒收，redundancy-first / reject-when-no-redundant）：
    1. 计算每条目的“独占行”（全库中只有它覆盖到的行）；
    2. 只淘汰独占行数为 0 的条目（完全冗余，淘汰不损失总覆盖）；
       并列时淘汰体积更大者；再并列淘汰更早入库者；
    3. 若所有条目都有独占行（淘汰必然损失覆盖），则拒绝本次新输入，
       计入 n_full —— 库总覆盖只增不减，避免“新增益->挤掉旧覆盖->
       旧覆盖又成新增益”的抖动（churn）。
  每次淘汰记录 (淘汰前覆盖, 淘汰后覆盖)，本策略下二者恒等（无损）。
"""

import hashlib
import sys
from collections import Counter


def trace_coverage(func, data):
    """运行 func(data)，返回 (accepted, coverage)。

    coverage 为 func 所在源文件内被执行到的行号集合。
    func 抛出异常视为“无效输入”，accepted=False。
    """
    target_file = func.__code__.co_filename
    cov = set()

    def tracer(frame, event, arg):
        if event == "line" and frame.f_code.co_filename == target_file:
            cov.add(frame.f_lineno)
        return tracer

    old_trace = sys.gettrace()
    sys.settrace(tracer)
    try:
        accepted = bool(func(data))
    except Exception:
        accepted = False
    finally:
        sys.settrace(old_trace)
    return accepted, cov


def minimize(data, is_interesting):
    """贪心块删除最小化：反复尝试删除连续块，只要 is_interesting 仍成立。

    is_interesting(candidate) -> bool 由调用方定义（通常是“仍有效且增益行不丢”）。
    """
    data = bytes(data)
    if not is_interesting(data):
        return data
    changed = True
    while changed:
        changed = False
        size = max(1, len(data) // 2)
        while size >= 1:
            i = 0
            while i < len(data):
                cand = data[:i] + data[i + size:]
                if cand and is_interesting(cand):
                    data = cand
                    changed = True
                else:
                    i += size
            size //= 2
    return data


def _sha1(data):
    return hashlib.sha1(data).hexdigest()


class Corpus:
    """有容量上界、按覆盖增益入库的语料库。"""

    def __init__(self, target, capacity=100):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.target = target
        self.capacity = capacity
        self.entries = []          # [(data, coverage), ...] 按入库顺序
        self.total_cov = set()     # 全库覆盖并集
        self._hashes = set()       # 见过的输入哈希（完全重复快速判重）
        # 统计
        self.n_seen = 0            # 收到的输入总数
        self.n_admitted = 0        # 入库数
        self.n_dup = 0             # 完全重复（哈希命中）
        self.n_nogain = 0          # 有效但无覆盖增益
        self.n_invalid = 0         # 无效输入（目标拒绝/异常）
        self.n_full = 0            # 容量满且无冗余条目可淘汰
        self.bytes_before_min = 0  # 入库输入最小化前总字节
        self.bytes_after_min = 0   # 入库输入最小化后总字节
        self.evictions = []        # [(淘汰前覆盖数, 淘汰后覆盖数), ...]

    def add(self, data):
        """尝试入库，返回是否入库。"""
        data = bytes(data)
        self.n_seen += 1

        digest = _sha1(data)
        if digest in self._hashes:
            self.n_dup += 1
            return False
        self._hashes.add(digest)

        accepted, cov = trace_coverage(self.target, data)
        if not accepted:
            self.n_invalid += 1
            return False

        gain = cov - self.total_cov
        if not gain:
            self.n_nogain += 1
            return False

        # 最小化：保持“本次增益行”不丢即可（库总覆盖不变式得以维持）
        def still_interesting(cand):
            ok, c = trace_coverage(self.target, cand)
            return ok and gain <= c

        min_data = minimize(data, still_interesting)
        _, min_cov = trace_coverage(self.target, min_data)
        self._hashes.add(_sha1(min_data))

        self.entries.append((min_data, min_cov))
        if len(self.entries) > self.capacity and not self._evict():
            # 容量满且无冗余条目：撤销本次入库，覆盖与容量保持不变
            self.entries.pop()
            self.n_full += 1
            return False

        self.total_cov |= min_cov
        self.n_admitted += 1
        self.bytes_before_min += len(data)
        self.bytes_after_min += len(min_data)
        return True

    def _evict(self):
        """冗余优先淘汰一条目。有冗余条目则淘汰并返回 True，否则返回 False。

        只淘汰“独占行数为 0”的条目，因此淘汰前后总覆盖恒等（无损）。
        """
        line_count = Counter()
        for _, c in self.entries:
            for line in c:
                line_count[line] += 1

        def unique_lines(idx):
            return sum(1 for line in self.entries[idx][1] if line_count[line] == 1)

        redundant = [i for i in range(len(self.entries)) if unique_lines(i) == 0]
        if not redundant:
            return False

        before = set()
        for _, c in self.entries:
            before |= c

        # 并列时淘汰体积更大者，再并列淘汰更早入库者
        victim = min(redundant, key=lambda i: (-len(self.entries[i][0]), i))
        del self.entries[victim]

        after = set()
        for _, c in self.entries:
            after |= c
        self.total_cov = after
        self.evictions.append((len(before), len(after)))
        return True

    @property
    def admission_rate(self):
        return self.n_admitted / self.n_seen if self.n_seen else 0.0

    def report(self):
        lines = [
            "=== 语料库报告 ===",
            "输入总数:            %d" % self.n_seen,
            "入库:                %d" % self.n_admitted,
            "丢弃-完全重复:       %d" % self.n_dup,
            "丢弃-无覆盖增益:     %d" % self.n_nogain,
            "丢弃-无效输入:       %d" % self.n_invalid,
            "丢弃-容量满:         %d" % self.n_full,
            "入库率:              %.2f%%" % (self.admission_rate * 100),
            "当前条目数/容量:     %d/%d" % (len(self.entries), self.capacity),
            "总覆盖行数:          %d" % len(self.total_cov),
            "最小化前总字节:      %d" % self.bytes_before_min,
            "最小化后总字节:      %d" % self.bytes_after_min,
        ]
        if self.bytes_before_min:
            saved = 1 - self.bytes_after_min / self.bytes_before_min
            lines.append("最小化体积压缩:      %.2f%%" % (saved * 100))
        if self.evictions:
            lines.append("淘汰事件(前->后覆盖):")
            for before, after in self.evictions:
                tag = "无损" if after == before else "损失 %d 行" % (before - after)
                lines.append("  %d -> %d (%s)" % (before, after, tag))
        return "\n".join(lines)
