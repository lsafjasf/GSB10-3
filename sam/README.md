# 后缀自动机（Suffix Automaton）

纯标准库 Python 3 实现，构建 O(n)，节点数 ≤ 2n−1（n≥2）。

## 文件
- `sam.py` — 库：`SuffixAutomaton`
- `test_sam.py` — 自测 + 暴力对拍 + 规模基准

## 运行
```bash
cd sam
python3 test_sam.py          # 自测（unittest，含暴力对拍断言）
SAM_BENCH=1 python3 test_sam.py   # 打印节点规模与对拍数据表
```

## API
- `SuffixAutomaton(text)` / `sam.build(text)` — 线性构建
- `sam.contains(p)` — 子串是否出现
- `sam.count_occurrences(p)` — 出现次数（允许重叠）
- `sam.num_distinct_substrings()` — 不同非空子串数量
- `sam.longest_common_substring(other)` — 与 other 的最长公共子串
- `sam.stats()` — 长度/节点数/克隆数/转移数

## 覆盖情形
单字符、空串、全部相同、周期/重复块、随机（二元/小字母表/DNA/大字母表）、
噪声中嵌重复模式，以及 200 组随机小样本压力对拍。
