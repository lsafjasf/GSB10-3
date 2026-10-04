"""一键运行全部实验：python3 experiments/run_all.py

依次执行：
  01_static_scan.py    状态/事件清单 + 可达性 BFS
  02_doc_diff.py       文档 vs 实现 差异场景复现
  03_illegal_events.py 非法事件穷举矩阵
并校验被测文件未被改动（sha256 指纹）。
"""
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, '..', 'legacy', 'legacy_order_machine.py')
# 从 git 分支 origin/230-A 提取时的原始指纹
EXPECTED_SHA256 = 'fb84e95f64fd291924515084f5d0ec41cc6ade9b7cf966b30423f03218463560'


def sha256(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    before = sha256(TARGET)
    for script in ('01_static_scan.py', '02_doc_diff.py', '03_illegal_events.py'):
        print('=' * 70)
        print('运行 %s' % script)
        print('=' * 70)
        r = subprocess.run([sys.executable, os.path.join(HERE, script)],
                           cwd=HERE)
        if r.returncode != 0:
            sys.exit('%s 运行失败' % script)
        print()
    after = sha256(TARGET)
    print('=' * 70)
    if before == after == EXPECTED_SHA256:
        print('被测文件完整性校验通过（sha256 未变）：%s' % after)
    else:
        sys.exit('警告：被测文件指纹变化！before=%s after=%s' % (before, after))
    print('全部实验完成，结果见 experiments/results/*.json')


if __name__ == '__main__':
    main()
