"""
run_all.py —— 一键运行全部分析实验。

用法：在仓库根目录执行  python3 analysis/run_all.py
依次运行：静态扫描 → 动态探测 → 文档对拍 → 非法事件矩阵。
所有结果写入 analysis/out/*.json，控制台输出即实验观察记录。
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = ['static_scan.py', 'probe_dynamic.py', 'doc_diff.py',
           'illegal_events.py']


def main():
    for name in SCRIPTS:
        print('=' * 78)
        print('运行 analysis/%s' % name)
        print('=' * 78)
        completed = subprocess.run(
            [sys.executable, os.path.join(HERE, name)], check=False)
        if completed.returncode != 0:
            print('!! %s 退出码 %d，中止' % (name, completed.returncode))
            return completed.returncode
        print()
    print('全部实验完成，结构化结果见 analysis/out/。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
