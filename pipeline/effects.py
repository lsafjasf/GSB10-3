"""幂等副作用存储。

所有副作用（写文件、追加日志等）都必须通过 EffectStore.apply 执行：
- 每个副作用带一个稳定的幂等键；
- 已应用过的键（持久化在 journal 文件中）不会重复执行；
- 阶段重试 / 进程重启后重跑，副作用最多生效一次。
"""
import os


class EffectStore:
    def __init__(self, journal_path=None):
        self.journal_path = journal_path
        self._applied = set()
        if journal_path and os.path.exists(journal_path):
            with open(journal_path, encoding="utf-8") as fh:
                for line in fh:
                    key = line.strip()
                    if key:
                        self._applied.add(key)

    def apply(self, key, effect):
        """执行副作用；若 key 已应用过则跳过。返回是否真正执行。"""
        if key in self._applied:
            return False
        effect()
        if self.journal_path:
            with open(self.journal_path, "a", encoding="utf-8") as fh:
                fh.write(key + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        self._applied.add(key)
        return True

    def applied(self, key):
        return key in self._applied

    def assert_idempotent(self):
        """幂等断言：journal 中不允许出现重复的键。"""
        if not self.journal_path or not os.path.exists(self.journal_path):
            return
        with open(self.journal_path, encoding="utf-8") as fh:
            keys = [line.strip() for line in fh if line.strip()]
        seen = set()
        duplicates = set()
        for key in keys:
            if key in seen:
                duplicates.add(key)
            seen.add(key)
        assert not duplicates, "duplicate side effects detected: %s" % sorted(duplicates)
