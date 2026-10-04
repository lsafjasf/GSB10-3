"""违规注入验证：证明每条断言都是"承重"的。

做法（变异测试）：对 pipeline.py 的源码逐一注入违规改动（删掉/架空一条
断言），把变异后的模块跑完整回归测试套件。若套件仍然全绿，说明该断言
没有被任何测试守住 -> 脚本以非零码退出并报告漏网变异。

运行：python3 inject_violations.py
"""

import importlib.util
import io
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE_PATH = os.path.join(HERE, "pipeline.py")
TEST_PATH = os.path.join(HERE, "test_pipeline.py")

# (变异编号, 针对约定, 说明, 被替换的源码片段, 替换后)
MUTATIONS = [
    ("M1", "C1", "删除 job 缺键检查",
     '    _require(not missing, "C1", "job missing keys: {0}".format(sorted(missing)))\n',
     ""),
    ("M2", "C2", "架空 result 键集合校验",
     '_require(set(result) == _RESULT_KEYS,',
     '_require(True or set(result) == _RESULT_KEYS,'),
    ("M3", "C3", "submit 不再检查生命周期状态",
     '        self._check_open("submit")\n',
     ""),
    ("M4", "C4", "允许空管线 run()",
     '        _require(len(self._jobs) > 0,\n',
     '        _require(True or len(self._jobs) > 0,\n'),
    ("M5", "C5", "priority 越界不再报错",
     '_require(PRIORITY_MIN <= job["priority"] <= PRIORITY_MAX,',
     '_require(True or PRIORITY_MIN <= job["priority"] <= PRIORITY_MAX,'),
    ("M6", "C6", "max_retries 越界不再报错",
     '_require(RETRIES_MIN <= max_retries <= RETRIES_MAX,',
     '_require(True or RETRIES_MIN <= max_retries <= RETRIES_MAX,'),
    ("M7", "C7", "允许重复 job id",
     '        _require(job["id"] not in self._seen_ids,\n',
     '        _require(True or job["id"] not in self._seen_ids,\n'),
    ("M8", "C8", "线程亲和检查失效",
     "_require(threading.get_ident() == self._owner_thread,",
     "_require(True or threading.get_ident() == self._owner_thread,"),
    ("M9", "C9", "允许 run() 重入",
     '        _require(not self._running,\n',
     '        _require(True or not self._running,\n'),
]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_suite_against(mutated_module):
    """把变异模块塞进 sys.modules['pipeline'] 后跑完整测试套件。"""
    saved = sys.modules.get("pipeline")
    sys.modules["pipeline"] = mutated_module
    try:
        test_module = load_module("test_pipeline_mutant", TEST_PATH)
        suite = unittest.defaultTestLoader.loadTestsFromModule(test_module)
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
        return result, stream.getvalue()
    finally:
        if saved is None:
            del sys.modules["pipeline"]
        else:
            sys.modules["pipeline"] = saved


def main():
    original = open(SOURCE_PATH, encoding="utf-8").read()
    uncaught = []
    print("=" * 70)
    print("违规注入验证：%d 个变异 x 完整回归套件" % len(MUTATIONS))
    print("=" * 70)
    for mut_id, code, desc, old, new in MUTATIONS:
        assert old in original, "变异锚点不存在: %s" % mut_id
        mutated = original.replace(old, new, 1)
        with tempfile.TemporaryDirectory() as tmp:
            mut_path = os.path.join(tmp, "pipeline.py")
            with open(mut_path, "w", encoding="utf-8") as fh:
                fh.write(mutated)
            module = load_module("pipeline_mutant", mut_path)
            result, output = run_suite_against(module)
        failures = len(result.failures) + len(result.errors)
        caught = failures > 0
        status = "CAUGHT " if caught else "ESCAPED"
        print("%s %-7s %s (%s): 套件失败 %d 项" % (status, mut_id, code, desc, failures))
        if not caught:
            uncaught.append(mut_id)
        else:
            # 打印第一条失败详情作为"失败输出"样例
            first = (result.failures + result.errors)[0]
            header = str(first[0]).split(" ")[0]
            for line in output.splitlines():
                if "ContractViolation" in line or "AssertionError" in line:
                    print("         %s -> %s" % (header, line.strip()))
                    break
    print("=" * 70)
    if uncaught:
        print("失败：以下变异未被任何测试捕获: %s" % ", ".join(uncaught))
        return 1
    print("通过：全部 %d 个变异均被回归套件捕获，断言均为承重断言。" % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
