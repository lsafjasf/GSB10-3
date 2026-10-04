# 对拍报告（由 build_decision_table.py 自动生成）

## 概览

- legacy 最大 if 嵌套深度：**11 层**
- 条件组合总数：**864**（4 amount 段 × 3 qty 段 × 3 vip × 2 channel × 2 region × 3 coupon × 2 birthday）
- 其中抛错组合：288，正常返回组合：576
- 边界扫描用例：29952（每组合扫 amount/qty 段界 ±1 及中点）
- 随机模糊用例：5000（种子 20261004）
- 非法输入用例：15（类型错误 / 越界 / 未知枚举 / 券门槛不足）
- 不一致用例：**0**

## 逐组合对拍

| # | 组合 (a_band,q_band,vip,channel,region,coupon,birthday) | 代表值 | 结果 |
|---|----------------------------------------------------------|--------|------|
| 1 | (0, 0, 'normal', 'app', 'mainland', 'none', False) | (2499, 5) | OK MATCH |
| 2 | (0, 0, 'normal', 'app', 'mainland', 'none', True) | (2499, 5) | OK MATCH |
| 3 | (0, 0, 'normal', 'app', 'mainland', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 4 | (0, 0, 'normal', 'app', 'mainland', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 5 | (0, 0, 'normal', 'app', 'mainland', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 6 | (0, 0, 'normal', 'app', 'mainland', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 7 | (0, 0, 'normal', 'app', 'remote', 'none', False) | (2499, 5) | OK MATCH |
| 8 | (0, 0, 'normal', 'app', 'remote', 'none', True) | (2499, 5) | OK MATCH |
| 9 | (0, 0, 'normal', 'app', 'remote', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 10 | (0, 0, 'normal', 'app', 'remote', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 11 | (0, 0, 'normal', 'app', 'remote', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 12 | (0, 0, 'normal', 'app', 'remote', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 13 | (0, 0, 'normal', 'web', 'mainland', 'none', False) | (2499, 5) | OK MATCH |
| 14 | (0, 0, 'normal', 'web', 'mainland', 'none', True) | (2499, 5) | OK MATCH |
| 15 | (0, 0, 'normal', 'web', 'mainland', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 16 | (0, 0, 'normal', 'web', 'mainland', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 17 | (0, 0, 'normal', 'web', 'mainland', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 18 | (0, 0, 'normal', 'web', 'mainland', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 19 | (0, 0, 'normal', 'web', 'remote', 'none', False) | (2499, 5) | OK MATCH |
| 20 | (0, 0, 'normal', 'web', 'remote', 'none', True) | (2499, 5) | OK MATCH |
| 21 | (0, 0, 'normal', 'web', 'remote', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 22 | (0, 0, 'normal', 'web', 'remote', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 23 | (0, 0, 'normal', 'web', 'remote', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 24 | (0, 0, 'normal', 'web', 'remote', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 25 | (0, 0, 'silver', 'app', 'mainland', 'none', False) | (2499, 5) | OK MATCH |
| 26 | (0, 0, 'silver', 'app', 'mainland', 'none', True) | (2499, 5) | OK MATCH |
| 27 | (0, 0, 'silver', 'app', 'mainland', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 28 | (0, 0, 'silver', 'app', 'mainland', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 29 | (0, 0, 'silver', 'app', 'mainland', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 30 | (0, 0, 'silver', 'app', 'mainland', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 31 | (0, 0, 'silver', 'app', 'remote', 'none', False) | (2499, 5) | OK MATCH |
| 32 | (0, 0, 'silver', 'app', 'remote', 'none', True) | (2499, 5) | OK MATCH |
| 33 | (0, 0, 'silver', 'app', 'remote', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 34 | (0, 0, 'silver', 'app', 'remote', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 35 | (0, 0, 'silver', 'app', 'remote', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 36 | (0, 0, 'silver', 'app', 'remote', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 37 | (0, 0, 'silver', 'web', 'mainland', 'none', False) | (2499, 5) | OK MATCH |
| 38 | (0, 0, 'silver', 'web', 'mainland', 'none', True) | (2499, 5) | OK MATCH |
| 39 | (0, 0, 'silver', 'web', 'mainland', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 40 | (0, 0, 'silver', 'web', 'mainland', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 41 | (0, 0, 'silver', 'web', 'mainland', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 42 | (0, 0, 'silver', 'web', 'mainland', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 43 | (0, 0, 'silver', 'web', 'remote', 'none', False) | (2499, 5) | OK MATCH |
| 44 | (0, 0, 'silver', 'web', 'remote', 'none', True) | (2499, 5) | OK MATCH |
| 45 | (0, 0, 'silver', 'web', 'remote', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 46 | (0, 0, 'silver', 'web', 'remote', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 47 | (0, 0, 'silver', 'web', 'remote', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 48 | (0, 0, 'silver', 'web', 'remote', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 49 | (0, 0, 'gold', 'app', 'mainland', 'none', False) | (2499, 5) | OK MATCH |
| 50 | (0, 0, 'gold', 'app', 'mainland', 'none', True) | (2499, 5) | OK MATCH |
| 51 | (0, 0, 'gold', 'app', 'mainland', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 52 | (0, 0, 'gold', 'app', 'mainland', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 53 | (0, 0, 'gold', 'app', 'mainland', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 54 | (0, 0, 'gold', 'app', 'mainland', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 55 | (0, 0, 'gold', 'app', 'remote', 'none', False) | (2499, 5) | OK MATCH |
| 56 | (0, 0, 'gold', 'app', 'remote', 'none', True) | (2499, 5) | OK MATCH |
| 57 | (0, 0, 'gold', 'app', 'remote', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 58 | (0, 0, 'gold', 'app', 'remote', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 59 | (0, 0, 'gold', 'app', 'remote', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 60 | (0, 0, 'gold', 'app', 'remote', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 61 | (0, 0, 'gold', 'web', 'mainland', 'none', False) | (2499, 5) | OK MATCH |
| 62 | (0, 0, 'gold', 'web', 'mainland', 'none', True) | (2499, 5) | OK MATCH |
| 63 | (0, 0, 'gold', 'web', 'mainland', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 64 | (0, 0, 'gold', 'web', 'mainland', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 65 | (0, 0, 'gold', 'web', 'mainland', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 66 | (0, 0, 'gold', 'web', 'mainland', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 67 | (0, 0, 'gold', 'web', 'remote', 'none', False) | (2499, 5) | OK MATCH |
| 68 | (0, 0, 'gold', 'web', 'remote', 'none', True) | (2499, 5) | OK MATCH |
| 69 | (0, 0, 'gold', 'web', 'remote', 'cash', False) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 70 | (0, 0, 'gold', 'web', 'remote', 'cash', True) | (2499, 5) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 71 | (0, 0, 'gold', 'web', 'remote', 'gift', False) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 72 | (0, 0, 'gold', 'web', 'remote', 'gift', True) | (2499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 73 | (0, 1, 'normal', 'app', 'mainland', 'none', False) | (2499, 14) | OK MATCH |
| 74 | (0, 1, 'normal', 'app', 'mainland', 'none', True) | (2499, 14) | OK MATCH |
| 75 | (0, 1, 'normal', 'app', 'mainland', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 76 | (0, 1, 'normal', 'app', 'mainland', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 77 | (0, 1, 'normal', 'app', 'mainland', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 78 | (0, 1, 'normal', 'app', 'mainland', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 79 | (0, 1, 'normal', 'app', 'remote', 'none', False) | (2499, 14) | OK MATCH |
| 80 | (0, 1, 'normal', 'app', 'remote', 'none', True) | (2499, 14) | OK MATCH |
| 81 | (0, 1, 'normal', 'app', 'remote', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 82 | (0, 1, 'normal', 'app', 'remote', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 83 | (0, 1, 'normal', 'app', 'remote', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 84 | (0, 1, 'normal', 'app', 'remote', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 85 | (0, 1, 'normal', 'web', 'mainland', 'none', False) | (2499, 14) | OK MATCH |
| 86 | (0, 1, 'normal', 'web', 'mainland', 'none', True) | (2499, 14) | OK MATCH |
| 87 | (0, 1, 'normal', 'web', 'mainland', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 88 | (0, 1, 'normal', 'web', 'mainland', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 89 | (0, 1, 'normal', 'web', 'mainland', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 90 | (0, 1, 'normal', 'web', 'mainland', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 91 | (0, 1, 'normal', 'web', 'remote', 'none', False) | (2499, 14) | OK MATCH |
| 92 | (0, 1, 'normal', 'web', 'remote', 'none', True) | (2499, 14) | OK MATCH |
| 93 | (0, 1, 'normal', 'web', 'remote', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 94 | (0, 1, 'normal', 'web', 'remote', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 95 | (0, 1, 'normal', 'web', 'remote', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 96 | (0, 1, 'normal', 'web', 'remote', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 97 | (0, 1, 'silver', 'app', 'mainland', 'none', False) | (2499, 14) | OK MATCH |
| 98 | (0, 1, 'silver', 'app', 'mainland', 'none', True) | (2499, 14) | OK MATCH |
| 99 | (0, 1, 'silver', 'app', 'mainland', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 100 | (0, 1, 'silver', 'app', 'mainland', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 101 | (0, 1, 'silver', 'app', 'mainland', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 102 | (0, 1, 'silver', 'app', 'mainland', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 103 | (0, 1, 'silver', 'app', 'remote', 'none', False) | (2499, 14) | OK MATCH |
| 104 | (0, 1, 'silver', 'app', 'remote', 'none', True) | (2499, 14) | OK MATCH |
| 105 | (0, 1, 'silver', 'app', 'remote', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 106 | (0, 1, 'silver', 'app', 'remote', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 107 | (0, 1, 'silver', 'app', 'remote', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 108 | (0, 1, 'silver', 'app', 'remote', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 109 | (0, 1, 'silver', 'web', 'mainland', 'none', False) | (2499, 14) | OK MATCH |
| 110 | (0, 1, 'silver', 'web', 'mainland', 'none', True) | (2499, 14) | OK MATCH |
| 111 | (0, 1, 'silver', 'web', 'mainland', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 112 | (0, 1, 'silver', 'web', 'mainland', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 113 | (0, 1, 'silver', 'web', 'mainland', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 114 | (0, 1, 'silver', 'web', 'mainland', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 115 | (0, 1, 'silver', 'web', 'remote', 'none', False) | (2499, 14) | OK MATCH |
| 116 | (0, 1, 'silver', 'web', 'remote', 'none', True) | (2499, 14) | OK MATCH |
| 117 | (0, 1, 'silver', 'web', 'remote', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 118 | (0, 1, 'silver', 'web', 'remote', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 119 | (0, 1, 'silver', 'web', 'remote', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 120 | (0, 1, 'silver', 'web', 'remote', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 121 | (0, 1, 'gold', 'app', 'mainland', 'none', False) | (2499, 14) | OK MATCH |
| 122 | (0, 1, 'gold', 'app', 'mainland', 'none', True) | (2499, 14) | OK MATCH |
| 123 | (0, 1, 'gold', 'app', 'mainland', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 124 | (0, 1, 'gold', 'app', 'mainland', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 125 | (0, 1, 'gold', 'app', 'mainland', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 126 | (0, 1, 'gold', 'app', 'mainland', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 127 | (0, 1, 'gold', 'app', 'remote', 'none', False) | (2499, 14) | OK MATCH |
| 128 | (0, 1, 'gold', 'app', 'remote', 'none', True) | (2499, 14) | OK MATCH |
| 129 | (0, 1, 'gold', 'app', 'remote', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 130 | (0, 1, 'gold', 'app', 'remote', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 131 | (0, 1, 'gold', 'app', 'remote', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 132 | (0, 1, 'gold', 'app', 'remote', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 133 | (0, 1, 'gold', 'web', 'mainland', 'none', False) | (2499, 14) | OK MATCH |
| 134 | (0, 1, 'gold', 'web', 'mainland', 'none', True) | (2499, 14) | OK MATCH |
| 135 | (0, 1, 'gold', 'web', 'mainland', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 136 | (0, 1, 'gold', 'web', 'mainland', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 137 | (0, 1, 'gold', 'web', 'mainland', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 138 | (0, 1, 'gold', 'web', 'mainland', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 139 | (0, 1, 'gold', 'web', 'remote', 'none', False) | (2499, 14) | OK MATCH |
| 140 | (0, 1, 'gold', 'web', 'remote', 'none', True) | (2499, 14) | OK MATCH |
| 141 | (0, 1, 'gold', 'web', 'remote', 'cash', False) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 142 | (0, 1, 'gold', 'web', 'remote', 'cash', True) | (2499, 14) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 143 | (0, 1, 'gold', 'web', 'remote', 'gift', False) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 144 | (0, 1, 'gold', 'web', 'remote', 'gift', True) | (2499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 145 | (0, 2, 'normal', 'app', 'mainland', 'none', False) | (2499, 21) | OK MATCH |
| 146 | (0, 2, 'normal', 'app', 'mainland', 'none', True) | (2499, 21) | OK MATCH |
| 147 | (0, 2, 'normal', 'app', 'mainland', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 148 | (0, 2, 'normal', 'app', 'mainland', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 149 | (0, 2, 'normal', 'app', 'mainland', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 150 | (0, 2, 'normal', 'app', 'mainland', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 151 | (0, 2, 'normal', 'app', 'remote', 'none', False) | (2499, 21) | OK MATCH |
| 152 | (0, 2, 'normal', 'app', 'remote', 'none', True) | (2499, 21) | OK MATCH |
| 153 | (0, 2, 'normal', 'app', 'remote', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 154 | (0, 2, 'normal', 'app', 'remote', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 155 | (0, 2, 'normal', 'app', 'remote', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 156 | (0, 2, 'normal', 'app', 'remote', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 157 | (0, 2, 'normal', 'web', 'mainland', 'none', False) | (2499, 21) | OK MATCH |
| 158 | (0, 2, 'normal', 'web', 'mainland', 'none', True) | (2499, 21) | OK MATCH |
| 159 | (0, 2, 'normal', 'web', 'mainland', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 160 | (0, 2, 'normal', 'web', 'mainland', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 161 | (0, 2, 'normal', 'web', 'mainland', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 162 | (0, 2, 'normal', 'web', 'mainland', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 163 | (0, 2, 'normal', 'web', 'remote', 'none', False) | (2499, 21) | OK MATCH |
| 164 | (0, 2, 'normal', 'web', 'remote', 'none', True) | (2499, 21) | OK MATCH |
| 165 | (0, 2, 'normal', 'web', 'remote', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 166 | (0, 2, 'normal', 'web', 'remote', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 167 | (0, 2, 'normal', 'web', 'remote', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 168 | (0, 2, 'normal', 'web', 'remote', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 169 | (0, 2, 'silver', 'app', 'mainland', 'none', False) | (2499, 21) | OK MATCH |
| 170 | (0, 2, 'silver', 'app', 'mainland', 'none', True) | (2499, 21) | OK MATCH |
| 171 | (0, 2, 'silver', 'app', 'mainland', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 172 | (0, 2, 'silver', 'app', 'mainland', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 173 | (0, 2, 'silver', 'app', 'mainland', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 174 | (0, 2, 'silver', 'app', 'mainland', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 175 | (0, 2, 'silver', 'app', 'remote', 'none', False) | (2499, 21) | OK MATCH |
| 176 | (0, 2, 'silver', 'app', 'remote', 'none', True) | (2499, 21) | OK MATCH |
| 177 | (0, 2, 'silver', 'app', 'remote', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 178 | (0, 2, 'silver', 'app', 'remote', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 179 | (0, 2, 'silver', 'app', 'remote', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 180 | (0, 2, 'silver', 'app', 'remote', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 181 | (0, 2, 'silver', 'web', 'mainland', 'none', False) | (2499, 21) | OK MATCH |
| 182 | (0, 2, 'silver', 'web', 'mainland', 'none', True) | (2499, 21) | OK MATCH |
| 183 | (0, 2, 'silver', 'web', 'mainland', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 184 | (0, 2, 'silver', 'web', 'mainland', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 185 | (0, 2, 'silver', 'web', 'mainland', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 186 | (0, 2, 'silver', 'web', 'mainland', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 187 | (0, 2, 'silver', 'web', 'remote', 'none', False) | (2499, 21) | OK MATCH |
| 188 | (0, 2, 'silver', 'web', 'remote', 'none', True) | (2499, 21) | OK MATCH |
| 189 | (0, 2, 'silver', 'web', 'remote', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 190 | (0, 2, 'silver', 'web', 'remote', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 191 | (0, 2, 'silver', 'web', 'remote', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 192 | (0, 2, 'silver', 'web', 'remote', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 193 | (0, 2, 'gold', 'app', 'mainland', 'none', False) | (2499, 21) | OK MATCH |
| 194 | (0, 2, 'gold', 'app', 'mainland', 'none', True) | (2499, 21) | OK MATCH |
| 195 | (0, 2, 'gold', 'app', 'mainland', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 196 | (0, 2, 'gold', 'app', 'mainland', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 197 | (0, 2, 'gold', 'app', 'mainland', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 198 | (0, 2, 'gold', 'app', 'mainland', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 199 | (0, 2, 'gold', 'app', 'remote', 'none', False) | (2499, 21) | OK MATCH |
| 200 | (0, 2, 'gold', 'app', 'remote', 'none', True) | (2499, 21) | OK MATCH |
| 201 | (0, 2, 'gold', 'app', 'remote', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 202 | (0, 2, 'gold', 'app', 'remote', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 203 | (0, 2, 'gold', 'app', 'remote', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 204 | (0, 2, 'gold', 'app', 'remote', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 205 | (0, 2, 'gold', 'web', 'mainland', 'none', False) | (2499, 21) | OK MATCH |
| 206 | (0, 2, 'gold', 'web', 'mainland', 'none', True) | (2499, 21) | OK MATCH |
| 207 | (0, 2, 'gold', 'web', 'mainland', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 208 | (0, 2, 'gold', 'web', 'mainland', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 209 | (0, 2, 'gold', 'web', 'mainland', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 210 | (0, 2, 'gold', 'web', 'mainland', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 211 | (0, 2, 'gold', 'web', 'remote', 'none', False) | (2499, 21) | OK MATCH |
| 212 | (0, 2, 'gold', 'web', 'remote', 'none', True) | (2499, 21) | OK MATCH |
| 213 | (0, 2, 'gold', 'web', 'remote', 'cash', False) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 214 | (0, 2, 'gold', 'web', 'remote', 'cash', True) | (2499, 21) | ERROR ValueError: cash coupon requires amount >= 5000 MATCH |
| 215 | (0, 2, 'gold', 'web', 'remote', 'gift', False) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 216 | (0, 2, 'gold', 'web', 'remote', 'gift', True) | (2499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 217 | (1, 0, 'normal', 'app', 'mainland', 'none', False) | (7499, 5) | OK MATCH |
| 218 | (1, 0, 'normal', 'app', 'mainland', 'none', True) | (7499, 5) | OK MATCH |
| 219 | (1, 0, 'normal', 'app', 'mainland', 'cash', False) | (7499, 5) | OK MATCH |
| 220 | (1, 0, 'normal', 'app', 'mainland', 'cash', True) | (7499, 5) | OK MATCH |
| 221 | (1, 0, 'normal', 'app', 'mainland', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 222 | (1, 0, 'normal', 'app', 'mainland', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 223 | (1, 0, 'normal', 'app', 'remote', 'none', False) | (7499, 5) | OK MATCH |
| 224 | (1, 0, 'normal', 'app', 'remote', 'none', True) | (7499, 5) | OK MATCH |
| 225 | (1, 0, 'normal', 'app', 'remote', 'cash', False) | (7499, 5) | OK MATCH |
| 226 | (1, 0, 'normal', 'app', 'remote', 'cash', True) | (7499, 5) | OK MATCH |
| 227 | (1, 0, 'normal', 'app', 'remote', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 228 | (1, 0, 'normal', 'app', 'remote', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 229 | (1, 0, 'normal', 'web', 'mainland', 'none', False) | (7499, 5) | OK MATCH |
| 230 | (1, 0, 'normal', 'web', 'mainland', 'none', True) | (7499, 5) | OK MATCH |
| 231 | (1, 0, 'normal', 'web', 'mainland', 'cash', False) | (7499, 5) | OK MATCH |
| 232 | (1, 0, 'normal', 'web', 'mainland', 'cash', True) | (7499, 5) | OK MATCH |
| 233 | (1, 0, 'normal', 'web', 'mainland', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 234 | (1, 0, 'normal', 'web', 'mainland', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 235 | (1, 0, 'normal', 'web', 'remote', 'none', False) | (7499, 5) | OK MATCH |
| 236 | (1, 0, 'normal', 'web', 'remote', 'none', True) | (7499, 5) | OK MATCH |
| 237 | (1, 0, 'normal', 'web', 'remote', 'cash', False) | (7499, 5) | OK MATCH |
| 238 | (1, 0, 'normal', 'web', 'remote', 'cash', True) | (7499, 5) | OK MATCH |
| 239 | (1, 0, 'normal', 'web', 'remote', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 240 | (1, 0, 'normal', 'web', 'remote', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 241 | (1, 0, 'silver', 'app', 'mainland', 'none', False) | (7499, 5) | OK MATCH |
| 242 | (1, 0, 'silver', 'app', 'mainland', 'none', True) | (7499, 5) | OK MATCH |
| 243 | (1, 0, 'silver', 'app', 'mainland', 'cash', False) | (7499, 5) | OK MATCH |
| 244 | (1, 0, 'silver', 'app', 'mainland', 'cash', True) | (7499, 5) | OK MATCH |
| 245 | (1, 0, 'silver', 'app', 'mainland', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 246 | (1, 0, 'silver', 'app', 'mainland', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 247 | (1, 0, 'silver', 'app', 'remote', 'none', False) | (7499, 5) | OK MATCH |
| 248 | (1, 0, 'silver', 'app', 'remote', 'none', True) | (7499, 5) | OK MATCH |
| 249 | (1, 0, 'silver', 'app', 'remote', 'cash', False) | (7499, 5) | OK MATCH |
| 250 | (1, 0, 'silver', 'app', 'remote', 'cash', True) | (7499, 5) | OK MATCH |
| 251 | (1, 0, 'silver', 'app', 'remote', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 252 | (1, 0, 'silver', 'app', 'remote', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 253 | (1, 0, 'silver', 'web', 'mainland', 'none', False) | (7499, 5) | OK MATCH |
| 254 | (1, 0, 'silver', 'web', 'mainland', 'none', True) | (7499, 5) | OK MATCH |
| 255 | (1, 0, 'silver', 'web', 'mainland', 'cash', False) | (7499, 5) | OK MATCH |
| 256 | (1, 0, 'silver', 'web', 'mainland', 'cash', True) | (7499, 5) | OK MATCH |
| 257 | (1, 0, 'silver', 'web', 'mainland', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 258 | (1, 0, 'silver', 'web', 'mainland', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 259 | (1, 0, 'silver', 'web', 'remote', 'none', False) | (7499, 5) | OK MATCH |
| 260 | (1, 0, 'silver', 'web', 'remote', 'none', True) | (7499, 5) | OK MATCH |
| 261 | (1, 0, 'silver', 'web', 'remote', 'cash', False) | (7499, 5) | OK MATCH |
| 262 | (1, 0, 'silver', 'web', 'remote', 'cash', True) | (7499, 5) | OK MATCH |
| 263 | (1, 0, 'silver', 'web', 'remote', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 264 | (1, 0, 'silver', 'web', 'remote', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 265 | (1, 0, 'gold', 'app', 'mainland', 'none', False) | (7499, 5) | OK MATCH |
| 266 | (1, 0, 'gold', 'app', 'mainland', 'none', True) | (7499, 5) | OK MATCH |
| 267 | (1, 0, 'gold', 'app', 'mainland', 'cash', False) | (7499, 5) | OK MATCH |
| 268 | (1, 0, 'gold', 'app', 'mainland', 'cash', True) | (7499, 5) | OK MATCH |
| 269 | (1, 0, 'gold', 'app', 'mainland', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 270 | (1, 0, 'gold', 'app', 'mainland', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 271 | (1, 0, 'gold', 'app', 'remote', 'none', False) | (7499, 5) | OK MATCH |
| 272 | (1, 0, 'gold', 'app', 'remote', 'none', True) | (7499, 5) | OK MATCH |
| 273 | (1, 0, 'gold', 'app', 'remote', 'cash', False) | (7499, 5) | OK MATCH |
| 274 | (1, 0, 'gold', 'app', 'remote', 'cash', True) | (7499, 5) | OK MATCH |
| 275 | (1, 0, 'gold', 'app', 'remote', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 276 | (1, 0, 'gold', 'app', 'remote', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 277 | (1, 0, 'gold', 'web', 'mainland', 'none', False) | (7499, 5) | OK MATCH |
| 278 | (1, 0, 'gold', 'web', 'mainland', 'none', True) | (7499, 5) | OK MATCH |
| 279 | (1, 0, 'gold', 'web', 'mainland', 'cash', False) | (7499, 5) | OK MATCH |
| 280 | (1, 0, 'gold', 'web', 'mainland', 'cash', True) | (7499, 5) | OK MATCH |
| 281 | (1, 0, 'gold', 'web', 'mainland', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 282 | (1, 0, 'gold', 'web', 'mainland', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 283 | (1, 0, 'gold', 'web', 'remote', 'none', False) | (7499, 5) | OK MATCH |
| 284 | (1, 0, 'gold', 'web', 'remote', 'none', True) | (7499, 5) | OK MATCH |
| 285 | (1, 0, 'gold', 'web', 'remote', 'cash', False) | (7499, 5) | OK MATCH |
| 286 | (1, 0, 'gold', 'web', 'remote', 'cash', True) | (7499, 5) | OK MATCH |
| 287 | (1, 0, 'gold', 'web', 'remote', 'gift', False) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 288 | (1, 0, 'gold', 'web', 'remote', 'gift', True) | (7499, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 289 | (1, 1, 'normal', 'app', 'mainland', 'none', False) | (7499, 14) | OK MATCH |
| 290 | (1, 1, 'normal', 'app', 'mainland', 'none', True) | (7499, 14) | OK MATCH |
| 291 | (1, 1, 'normal', 'app', 'mainland', 'cash', False) | (7499, 14) | OK MATCH |
| 292 | (1, 1, 'normal', 'app', 'mainland', 'cash', True) | (7499, 14) | OK MATCH |
| 293 | (1, 1, 'normal', 'app', 'mainland', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 294 | (1, 1, 'normal', 'app', 'mainland', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 295 | (1, 1, 'normal', 'app', 'remote', 'none', False) | (7499, 14) | OK MATCH |
| 296 | (1, 1, 'normal', 'app', 'remote', 'none', True) | (7499, 14) | OK MATCH |
| 297 | (1, 1, 'normal', 'app', 'remote', 'cash', False) | (7499, 14) | OK MATCH |
| 298 | (1, 1, 'normal', 'app', 'remote', 'cash', True) | (7499, 14) | OK MATCH |
| 299 | (1, 1, 'normal', 'app', 'remote', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 300 | (1, 1, 'normal', 'app', 'remote', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 301 | (1, 1, 'normal', 'web', 'mainland', 'none', False) | (7499, 14) | OK MATCH |
| 302 | (1, 1, 'normal', 'web', 'mainland', 'none', True) | (7499, 14) | OK MATCH |
| 303 | (1, 1, 'normal', 'web', 'mainland', 'cash', False) | (7499, 14) | OK MATCH |
| 304 | (1, 1, 'normal', 'web', 'mainland', 'cash', True) | (7499, 14) | OK MATCH |
| 305 | (1, 1, 'normal', 'web', 'mainland', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 306 | (1, 1, 'normal', 'web', 'mainland', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 307 | (1, 1, 'normal', 'web', 'remote', 'none', False) | (7499, 14) | OK MATCH |
| 308 | (1, 1, 'normal', 'web', 'remote', 'none', True) | (7499, 14) | OK MATCH |
| 309 | (1, 1, 'normal', 'web', 'remote', 'cash', False) | (7499, 14) | OK MATCH |
| 310 | (1, 1, 'normal', 'web', 'remote', 'cash', True) | (7499, 14) | OK MATCH |
| 311 | (1, 1, 'normal', 'web', 'remote', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 312 | (1, 1, 'normal', 'web', 'remote', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 313 | (1, 1, 'silver', 'app', 'mainland', 'none', False) | (7499, 14) | OK MATCH |
| 314 | (1, 1, 'silver', 'app', 'mainland', 'none', True) | (7499, 14) | OK MATCH |
| 315 | (1, 1, 'silver', 'app', 'mainland', 'cash', False) | (7499, 14) | OK MATCH |
| 316 | (1, 1, 'silver', 'app', 'mainland', 'cash', True) | (7499, 14) | OK MATCH |
| 317 | (1, 1, 'silver', 'app', 'mainland', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 318 | (1, 1, 'silver', 'app', 'mainland', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 319 | (1, 1, 'silver', 'app', 'remote', 'none', False) | (7499, 14) | OK MATCH |
| 320 | (1, 1, 'silver', 'app', 'remote', 'none', True) | (7499, 14) | OK MATCH |
| 321 | (1, 1, 'silver', 'app', 'remote', 'cash', False) | (7499, 14) | OK MATCH |
| 322 | (1, 1, 'silver', 'app', 'remote', 'cash', True) | (7499, 14) | OK MATCH |
| 323 | (1, 1, 'silver', 'app', 'remote', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 324 | (1, 1, 'silver', 'app', 'remote', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 325 | (1, 1, 'silver', 'web', 'mainland', 'none', False) | (7499, 14) | OK MATCH |
| 326 | (1, 1, 'silver', 'web', 'mainland', 'none', True) | (7499, 14) | OK MATCH |
| 327 | (1, 1, 'silver', 'web', 'mainland', 'cash', False) | (7499, 14) | OK MATCH |
| 328 | (1, 1, 'silver', 'web', 'mainland', 'cash', True) | (7499, 14) | OK MATCH |
| 329 | (1, 1, 'silver', 'web', 'mainland', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 330 | (1, 1, 'silver', 'web', 'mainland', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 331 | (1, 1, 'silver', 'web', 'remote', 'none', False) | (7499, 14) | OK MATCH |
| 332 | (1, 1, 'silver', 'web', 'remote', 'none', True) | (7499, 14) | OK MATCH |
| 333 | (1, 1, 'silver', 'web', 'remote', 'cash', False) | (7499, 14) | OK MATCH |
| 334 | (1, 1, 'silver', 'web', 'remote', 'cash', True) | (7499, 14) | OK MATCH |
| 335 | (1, 1, 'silver', 'web', 'remote', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 336 | (1, 1, 'silver', 'web', 'remote', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 337 | (1, 1, 'gold', 'app', 'mainland', 'none', False) | (7499, 14) | OK MATCH |
| 338 | (1, 1, 'gold', 'app', 'mainland', 'none', True) | (7499, 14) | OK MATCH |
| 339 | (1, 1, 'gold', 'app', 'mainland', 'cash', False) | (7499, 14) | OK MATCH |
| 340 | (1, 1, 'gold', 'app', 'mainland', 'cash', True) | (7499, 14) | OK MATCH |
| 341 | (1, 1, 'gold', 'app', 'mainland', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 342 | (1, 1, 'gold', 'app', 'mainland', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 343 | (1, 1, 'gold', 'app', 'remote', 'none', False) | (7499, 14) | OK MATCH |
| 344 | (1, 1, 'gold', 'app', 'remote', 'none', True) | (7499, 14) | OK MATCH |
| 345 | (1, 1, 'gold', 'app', 'remote', 'cash', False) | (7499, 14) | OK MATCH |
| 346 | (1, 1, 'gold', 'app', 'remote', 'cash', True) | (7499, 14) | OK MATCH |
| 347 | (1, 1, 'gold', 'app', 'remote', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 348 | (1, 1, 'gold', 'app', 'remote', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 349 | (1, 1, 'gold', 'web', 'mainland', 'none', False) | (7499, 14) | OK MATCH |
| 350 | (1, 1, 'gold', 'web', 'mainland', 'none', True) | (7499, 14) | OK MATCH |
| 351 | (1, 1, 'gold', 'web', 'mainland', 'cash', False) | (7499, 14) | OK MATCH |
| 352 | (1, 1, 'gold', 'web', 'mainland', 'cash', True) | (7499, 14) | OK MATCH |
| 353 | (1, 1, 'gold', 'web', 'mainland', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 354 | (1, 1, 'gold', 'web', 'mainland', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 355 | (1, 1, 'gold', 'web', 'remote', 'none', False) | (7499, 14) | OK MATCH |
| 356 | (1, 1, 'gold', 'web', 'remote', 'none', True) | (7499, 14) | OK MATCH |
| 357 | (1, 1, 'gold', 'web', 'remote', 'cash', False) | (7499, 14) | OK MATCH |
| 358 | (1, 1, 'gold', 'web', 'remote', 'cash', True) | (7499, 14) | OK MATCH |
| 359 | (1, 1, 'gold', 'web', 'remote', 'gift', False) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 360 | (1, 1, 'gold', 'web', 'remote', 'gift', True) | (7499, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 361 | (1, 2, 'normal', 'app', 'mainland', 'none', False) | (7499, 21) | OK MATCH |
| 362 | (1, 2, 'normal', 'app', 'mainland', 'none', True) | (7499, 21) | OK MATCH |
| 363 | (1, 2, 'normal', 'app', 'mainland', 'cash', False) | (7499, 21) | OK MATCH |
| 364 | (1, 2, 'normal', 'app', 'mainland', 'cash', True) | (7499, 21) | OK MATCH |
| 365 | (1, 2, 'normal', 'app', 'mainland', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 366 | (1, 2, 'normal', 'app', 'mainland', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 367 | (1, 2, 'normal', 'app', 'remote', 'none', False) | (7499, 21) | OK MATCH |
| 368 | (1, 2, 'normal', 'app', 'remote', 'none', True) | (7499, 21) | OK MATCH |
| 369 | (1, 2, 'normal', 'app', 'remote', 'cash', False) | (7499, 21) | OK MATCH |
| 370 | (1, 2, 'normal', 'app', 'remote', 'cash', True) | (7499, 21) | OK MATCH |
| 371 | (1, 2, 'normal', 'app', 'remote', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 372 | (1, 2, 'normal', 'app', 'remote', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 373 | (1, 2, 'normal', 'web', 'mainland', 'none', False) | (7499, 21) | OK MATCH |
| 374 | (1, 2, 'normal', 'web', 'mainland', 'none', True) | (7499, 21) | OK MATCH |
| 375 | (1, 2, 'normal', 'web', 'mainland', 'cash', False) | (7499, 21) | OK MATCH |
| 376 | (1, 2, 'normal', 'web', 'mainland', 'cash', True) | (7499, 21) | OK MATCH |
| 377 | (1, 2, 'normal', 'web', 'mainland', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 378 | (1, 2, 'normal', 'web', 'mainland', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 379 | (1, 2, 'normal', 'web', 'remote', 'none', False) | (7499, 21) | OK MATCH |
| 380 | (1, 2, 'normal', 'web', 'remote', 'none', True) | (7499, 21) | OK MATCH |
| 381 | (1, 2, 'normal', 'web', 'remote', 'cash', False) | (7499, 21) | OK MATCH |
| 382 | (1, 2, 'normal', 'web', 'remote', 'cash', True) | (7499, 21) | OK MATCH |
| 383 | (1, 2, 'normal', 'web', 'remote', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 384 | (1, 2, 'normal', 'web', 'remote', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 385 | (1, 2, 'silver', 'app', 'mainland', 'none', False) | (7499, 21) | OK MATCH |
| 386 | (1, 2, 'silver', 'app', 'mainland', 'none', True) | (7499, 21) | OK MATCH |
| 387 | (1, 2, 'silver', 'app', 'mainland', 'cash', False) | (7499, 21) | OK MATCH |
| 388 | (1, 2, 'silver', 'app', 'mainland', 'cash', True) | (7499, 21) | OK MATCH |
| 389 | (1, 2, 'silver', 'app', 'mainland', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 390 | (1, 2, 'silver', 'app', 'mainland', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 391 | (1, 2, 'silver', 'app', 'remote', 'none', False) | (7499, 21) | OK MATCH |
| 392 | (1, 2, 'silver', 'app', 'remote', 'none', True) | (7499, 21) | OK MATCH |
| 393 | (1, 2, 'silver', 'app', 'remote', 'cash', False) | (7499, 21) | OK MATCH |
| 394 | (1, 2, 'silver', 'app', 'remote', 'cash', True) | (7499, 21) | OK MATCH |
| 395 | (1, 2, 'silver', 'app', 'remote', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 396 | (1, 2, 'silver', 'app', 'remote', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 397 | (1, 2, 'silver', 'web', 'mainland', 'none', False) | (7499, 21) | OK MATCH |
| 398 | (1, 2, 'silver', 'web', 'mainland', 'none', True) | (7499, 21) | OK MATCH |
| 399 | (1, 2, 'silver', 'web', 'mainland', 'cash', False) | (7499, 21) | OK MATCH |
| 400 | (1, 2, 'silver', 'web', 'mainland', 'cash', True) | (7499, 21) | OK MATCH |
| 401 | (1, 2, 'silver', 'web', 'mainland', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 402 | (1, 2, 'silver', 'web', 'mainland', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 403 | (1, 2, 'silver', 'web', 'remote', 'none', False) | (7499, 21) | OK MATCH |
| 404 | (1, 2, 'silver', 'web', 'remote', 'none', True) | (7499, 21) | OK MATCH |
| 405 | (1, 2, 'silver', 'web', 'remote', 'cash', False) | (7499, 21) | OK MATCH |
| 406 | (1, 2, 'silver', 'web', 'remote', 'cash', True) | (7499, 21) | OK MATCH |
| 407 | (1, 2, 'silver', 'web', 'remote', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 408 | (1, 2, 'silver', 'web', 'remote', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 409 | (1, 2, 'gold', 'app', 'mainland', 'none', False) | (7499, 21) | OK MATCH |
| 410 | (1, 2, 'gold', 'app', 'mainland', 'none', True) | (7499, 21) | OK MATCH |
| 411 | (1, 2, 'gold', 'app', 'mainland', 'cash', False) | (7499, 21) | OK MATCH |
| 412 | (1, 2, 'gold', 'app', 'mainland', 'cash', True) | (7499, 21) | OK MATCH |
| 413 | (1, 2, 'gold', 'app', 'mainland', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 414 | (1, 2, 'gold', 'app', 'mainland', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 415 | (1, 2, 'gold', 'app', 'remote', 'none', False) | (7499, 21) | OK MATCH |
| 416 | (1, 2, 'gold', 'app', 'remote', 'none', True) | (7499, 21) | OK MATCH |
| 417 | (1, 2, 'gold', 'app', 'remote', 'cash', False) | (7499, 21) | OK MATCH |
| 418 | (1, 2, 'gold', 'app', 'remote', 'cash', True) | (7499, 21) | OK MATCH |
| 419 | (1, 2, 'gold', 'app', 'remote', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 420 | (1, 2, 'gold', 'app', 'remote', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 421 | (1, 2, 'gold', 'web', 'mainland', 'none', False) | (7499, 21) | OK MATCH |
| 422 | (1, 2, 'gold', 'web', 'mainland', 'none', True) | (7499, 21) | OK MATCH |
| 423 | (1, 2, 'gold', 'web', 'mainland', 'cash', False) | (7499, 21) | OK MATCH |
| 424 | (1, 2, 'gold', 'web', 'mainland', 'cash', True) | (7499, 21) | OK MATCH |
| 425 | (1, 2, 'gold', 'web', 'mainland', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 426 | (1, 2, 'gold', 'web', 'mainland', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 427 | (1, 2, 'gold', 'web', 'remote', 'none', False) | (7499, 21) | OK MATCH |
| 428 | (1, 2, 'gold', 'web', 'remote', 'none', True) | (7499, 21) | OK MATCH |
| 429 | (1, 2, 'gold', 'web', 'remote', 'cash', False) | (7499, 21) | OK MATCH |
| 430 | (1, 2, 'gold', 'web', 'remote', 'cash', True) | (7499, 21) | OK MATCH |
| 431 | (1, 2, 'gold', 'web', 'remote', 'gift', False) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 432 | (1, 2, 'gold', 'web', 'remote', 'gift', True) | (7499, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 433 | (2, 0, 'normal', 'app', 'mainland', 'none', False) | (14999, 5) | OK MATCH |
| 434 | (2, 0, 'normal', 'app', 'mainland', 'none', True) | (14999, 5) | OK MATCH |
| 435 | (2, 0, 'normal', 'app', 'mainland', 'cash', False) | (14999, 5) | OK MATCH |
| 436 | (2, 0, 'normal', 'app', 'mainland', 'cash', True) | (14999, 5) | OK MATCH |
| 437 | (2, 0, 'normal', 'app', 'mainland', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 438 | (2, 0, 'normal', 'app', 'mainland', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 439 | (2, 0, 'normal', 'app', 'remote', 'none', False) | (14999, 5) | OK MATCH |
| 440 | (2, 0, 'normal', 'app', 'remote', 'none', True) | (14999, 5) | OK MATCH |
| 441 | (2, 0, 'normal', 'app', 'remote', 'cash', False) | (14999, 5) | OK MATCH |
| 442 | (2, 0, 'normal', 'app', 'remote', 'cash', True) | (14999, 5) | OK MATCH |
| 443 | (2, 0, 'normal', 'app', 'remote', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 444 | (2, 0, 'normal', 'app', 'remote', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 445 | (2, 0, 'normal', 'web', 'mainland', 'none', False) | (14999, 5) | OK MATCH |
| 446 | (2, 0, 'normal', 'web', 'mainland', 'none', True) | (14999, 5) | OK MATCH |
| 447 | (2, 0, 'normal', 'web', 'mainland', 'cash', False) | (14999, 5) | OK MATCH |
| 448 | (2, 0, 'normal', 'web', 'mainland', 'cash', True) | (14999, 5) | OK MATCH |
| 449 | (2, 0, 'normal', 'web', 'mainland', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 450 | (2, 0, 'normal', 'web', 'mainland', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 451 | (2, 0, 'normal', 'web', 'remote', 'none', False) | (14999, 5) | OK MATCH |
| 452 | (2, 0, 'normal', 'web', 'remote', 'none', True) | (14999, 5) | OK MATCH |
| 453 | (2, 0, 'normal', 'web', 'remote', 'cash', False) | (14999, 5) | OK MATCH |
| 454 | (2, 0, 'normal', 'web', 'remote', 'cash', True) | (14999, 5) | OK MATCH |
| 455 | (2, 0, 'normal', 'web', 'remote', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 456 | (2, 0, 'normal', 'web', 'remote', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 457 | (2, 0, 'silver', 'app', 'mainland', 'none', False) | (14999, 5) | OK MATCH |
| 458 | (2, 0, 'silver', 'app', 'mainland', 'none', True) | (14999, 5) | OK MATCH |
| 459 | (2, 0, 'silver', 'app', 'mainland', 'cash', False) | (14999, 5) | OK MATCH |
| 460 | (2, 0, 'silver', 'app', 'mainland', 'cash', True) | (14999, 5) | OK MATCH |
| 461 | (2, 0, 'silver', 'app', 'mainland', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 462 | (2, 0, 'silver', 'app', 'mainland', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 463 | (2, 0, 'silver', 'app', 'remote', 'none', False) | (14999, 5) | OK MATCH |
| 464 | (2, 0, 'silver', 'app', 'remote', 'none', True) | (14999, 5) | OK MATCH |
| 465 | (2, 0, 'silver', 'app', 'remote', 'cash', False) | (14999, 5) | OK MATCH |
| 466 | (2, 0, 'silver', 'app', 'remote', 'cash', True) | (14999, 5) | OK MATCH |
| 467 | (2, 0, 'silver', 'app', 'remote', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 468 | (2, 0, 'silver', 'app', 'remote', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 469 | (2, 0, 'silver', 'web', 'mainland', 'none', False) | (14999, 5) | OK MATCH |
| 470 | (2, 0, 'silver', 'web', 'mainland', 'none', True) | (14999, 5) | OK MATCH |
| 471 | (2, 0, 'silver', 'web', 'mainland', 'cash', False) | (14999, 5) | OK MATCH |
| 472 | (2, 0, 'silver', 'web', 'mainland', 'cash', True) | (14999, 5) | OK MATCH |
| 473 | (2, 0, 'silver', 'web', 'mainland', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 474 | (2, 0, 'silver', 'web', 'mainland', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 475 | (2, 0, 'silver', 'web', 'remote', 'none', False) | (14999, 5) | OK MATCH |
| 476 | (2, 0, 'silver', 'web', 'remote', 'none', True) | (14999, 5) | OK MATCH |
| 477 | (2, 0, 'silver', 'web', 'remote', 'cash', False) | (14999, 5) | OK MATCH |
| 478 | (2, 0, 'silver', 'web', 'remote', 'cash', True) | (14999, 5) | OK MATCH |
| 479 | (2, 0, 'silver', 'web', 'remote', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 480 | (2, 0, 'silver', 'web', 'remote', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 481 | (2, 0, 'gold', 'app', 'mainland', 'none', False) | (14999, 5) | OK MATCH |
| 482 | (2, 0, 'gold', 'app', 'mainland', 'none', True) | (14999, 5) | OK MATCH |
| 483 | (2, 0, 'gold', 'app', 'mainland', 'cash', False) | (14999, 5) | OK MATCH |
| 484 | (2, 0, 'gold', 'app', 'mainland', 'cash', True) | (14999, 5) | OK MATCH |
| 485 | (2, 0, 'gold', 'app', 'mainland', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 486 | (2, 0, 'gold', 'app', 'mainland', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 487 | (2, 0, 'gold', 'app', 'remote', 'none', False) | (14999, 5) | OK MATCH |
| 488 | (2, 0, 'gold', 'app', 'remote', 'none', True) | (14999, 5) | OK MATCH |
| 489 | (2, 0, 'gold', 'app', 'remote', 'cash', False) | (14999, 5) | OK MATCH |
| 490 | (2, 0, 'gold', 'app', 'remote', 'cash', True) | (14999, 5) | OK MATCH |
| 491 | (2, 0, 'gold', 'app', 'remote', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 492 | (2, 0, 'gold', 'app', 'remote', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 493 | (2, 0, 'gold', 'web', 'mainland', 'none', False) | (14999, 5) | OK MATCH |
| 494 | (2, 0, 'gold', 'web', 'mainland', 'none', True) | (14999, 5) | OK MATCH |
| 495 | (2, 0, 'gold', 'web', 'mainland', 'cash', False) | (14999, 5) | OK MATCH |
| 496 | (2, 0, 'gold', 'web', 'mainland', 'cash', True) | (14999, 5) | OK MATCH |
| 497 | (2, 0, 'gold', 'web', 'mainland', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 498 | (2, 0, 'gold', 'web', 'mainland', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 499 | (2, 0, 'gold', 'web', 'remote', 'none', False) | (14999, 5) | OK MATCH |
| 500 | (2, 0, 'gold', 'web', 'remote', 'none', True) | (14999, 5) | OK MATCH |
| 501 | (2, 0, 'gold', 'web', 'remote', 'cash', False) | (14999, 5) | OK MATCH |
| 502 | (2, 0, 'gold', 'web', 'remote', 'cash', True) | (14999, 5) | OK MATCH |
| 503 | (2, 0, 'gold', 'web', 'remote', 'gift', False) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 504 | (2, 0, 'gold', 'web', 'remote', 'gift', True) | (14999, 5) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 505 | (2, 1, 'normal', 'app', 'mainland', 'none', False) | (14999, 14) | OK MATCH |
| 506 | (2, 1, 'normal', 'app', 'mainland', 'none', True) | (14999, 14) | OK MATCH |
| 507 | (2, 1, 'normal', 'app', 'mainland', 'cash', False) | (14999, 14) | OK MATCH |
| 508 | (2, 1, 'normal', 'app', 'mainland', 'cash', True) | (14999, 14) | OK MATCH |
| 509 | (2, 1, 'normal', 'app', 'mainland', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 510 | (2, 1, 'normal', 'app', 'mainland', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 511 | (2, 1, 'normal', 'app', 'remote', 'none', False) | (14999, 14) | OK MATCH |
| 512 | (2, 1, 'normal', 'app', 'remote', 'none', True) | (14999, 14) | OK MATCH |
| 513 | (2, 1, 'normal', 'app', 'remote', 'cash', False) | (14999, 14) | OK MATCH |
| 514 | (2, 1, 'normal', 'app', 'remote', 'cash', True) | (14999, 14) | OK MATCH |
| 515 | (2, 1, 'normal', 'app', 'remote', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 516 | (2, 1, 'normal', 'app', 'remote', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 517 | (2, 1, 'normal', 'web', 'mainland', 'none', False) | (14999, 14) | OK MATCH |
| 518 | (2, 1, 'normal', 'web', 'mainland', 'none', True) | (14999, 14) | OK MATCH |
| 519 | (2, 1, 'normal', 'web', 'mainland', 'cash', False) | (14999, 14) | OK MATCH |
| 520 | (2, 1, 'normal', 'web', 'mainland', 'cash', True) | (14999, 14) | OK MATCH |
| 521 | (2, 1, 'normal', 'web', 'mainland', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 522 | (2, 1, 'normal', 'web', 'mainland', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 523 | (2, 1, 'normal', 'web', 'remote', 'none', False) | (14999, 14) | OK MATCH |
| 524 | (2, 1, 'normal', 'web', 'remote', 'none', True) | (14999, 14) | OK MATCH |
| 525 | (2, 1, 'normal', 'web', 'remote', 'cash', False) | (14999, 14) | OK MATCH |
| 526 | (2, 1, 'normal', 'web', 'remote', 'cash', True) | (14999, 14) | OK MATCH |
| 527 | (2, 1, 'normal', 'web', 'remote', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 528 | (2, 1, 'normal', 'web', 'remote', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 529 | (2, 1, 'silver', 'app', 'mainland', 'none', False) | (14999, 14) | OK MATCH |
| 530 | (2, 1, 'silver', 'app', 'mainland', 'none', True) | (14999, 14) | OK MATCH |
| 531 | (2, 1, 'silver', 'app', 'mainland', 'cash', False) | (14999, 14) | OK MATCH |
| 532 | (2, 1, 'silver', 'app', 'mainland', 'cash', True) | (14999, 14) | OK MATCH |
| 533 | (2, 1, 'silver', 'app', 'mainland', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 534 | (2, 1, 'silver', 'app', 'mainland', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 535 | (2, 1, 'silver', 'app', 'remote', 'none', False) | (14999, 14) | OK MATCH |
| 536 | (2, 1, 'silver', 'app', 'remote', 'none', True) | (14999, 14) | OK MATCH |
| 537 | (2, 1, 'silver', 'app', 'remote', 'cash', False) | (14999, 14) | OK MATCH |
| 538 | (2, 1, 'silver', 'app', 'remote', 'cash', True) | (14999, 14) | OK MATCH |
| 539 | (2, 1, 'silver', 'app', 'remote', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 540 | (2, 1, 'silver', 'app', 'remote', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 541 | (2, 1, 'silver', 'web', 'mainland', 'none', False) | (14999, 14) | OK MATCH |
| 542 | (2, 1, 'silver', 'web', 'mainland', 'none', True) | (14999, 14) | OK MATCH |
| 543 | (2, 1, 'silver', 'web', 'mainland', 'cash', False) | (14999, 14) | OK MATCH |
| 544 | (2, 1, 'silver', 'web', 'mainland', 'cash', True) | (14999, 14) | OK MATCH |
| 545 | (2, 1, 'silver', 'web', 'mainland', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 546 | (2, 1, 'silver', 'web', 'mainland', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 547 | (2, 1, 'silver', 'web', 'remote', 'none', False) | (14999, 14) | OK MATCH |
| 548 | (2, 1, 'silver', 'web', 'remote', 'none', True) | (14999, 14) | OK MATCH |
| 549 | (2, 1, 'silver', 'web', 'remote', 'cash', False) | (14999, 14) | OK MATCH |
| 550 | (2, 1, 'silver', 'web', 'remote', 'cash', True) | (14999, 14) | OK MATCH |
| 551 | (2, 1, 'silver', 'web', 'remote', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 552 | (2, 1, 'silver', 'web', 'remote', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 553 | (2, 1, 'gold', 'app', 'mainland', 'none', False) | (14999, 14) | OK MATCH |
| 554 | (2, 1, 'gold', 'app', 'mainland', 'none', True) | (14999, 14) | OK MATCH |
| 555 | (2, 1, 'gold', 'app', 'mainland', 'cash', False) | (14999, 14) | OK MATCH |
| 556 | (2, 1, 'gold', 'app', 'mainland', 'cash', True) | (14999, 14) | OK MATCH |
| 557 | (2, 1, 'gold', 'app', 'mainland', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 558 | (2, 1, 'gold', 'app', 'mainland', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 559 | (2, 1, 'gold', 'app', 'remote', 'none', False) | (14999, 14) | OK MATCH |
| 560 | (2, 1, 'gold', 'app', 'remote', 'none', True) | (14999, 14) | OK MATCH |
| 561 | (2, 1, 'gold', 'app', 'remote', 'cash', False) | (14999, 14) | OK MATCH |
| 562 | (2, 1, 'gold', 'app', 'remote', 'cash', True) | (14999, 14) | OK MATCH |
| 563 | (2, 1, 'gold', 'app', 'remote', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 564 | (2, 1, 'gold', 'app', 'remote', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 565 | (2, 1, 'gold', 'web', 'mainland', 'none', False) | (14999, 14) | OK MATCH |
| 566 | (2, 1, 'gold', 'web', 'mainland', 'none', True) | (14999, 14) | OK MATCH |
| 567 | (2, 1, 'gold', 'web', 'mainland', 'cash', False) | (14999, 14) | OK MATCH |
| 568 | (2, 1, 'gold', 'web', 'mainland', 'cash', True) | (14999, 14) | OK MATCH |
| 569 | (2, 1, 'gold', 'web', 'mainland', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 570 | (2, 1, 'gold', 'web', 'mainland', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 571 | (2, 1, 'gold', 'web', 'remote', 'none', False) | (14999, 14) | OK MATCH |
| 572 | (2, 1, 'gold', 'web', 'remote', 'none', True) | (14999, 14) | OK MATCH |
| 573 | (2, 1, 'gold', 'web', 'remote', 'cash', False) | (14999, 14) | OK MATCH |
| 574 | (2, 1, 'gold', 'web', 'remote', 'cash', True) | (14999, 14) | OK MATCH |
| 575 | (2, 1, 'gold', 'web', 'remote', 'gift', False) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 576 | (2, 1, 'gold', 'web', 'remote', 'gift', True) | (14999, 14) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 577 | (2, 2, 'normal', 'app', 'mainland', 'none', False) | (14999, 21) | OK MATCH |
| 578 | (2, 2, 'normal', 'app', 'mainland', 'none', True) | (14999, 21) | OK MATCH |
| 579 | (2, 2, 'normal', 'app', 'mainland', 'cash', False) | (14999, 21) | OK MATCH |
| 580 | (2, 2, 'normal', 'app', 'mainland', 'cash', True) | (14999, 21) | OK MATCH |
| 581 | (2, 2, 'normal', 'app', 'mainland', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 582 | (2, 2, 'normal', 'app', 'mainland', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 583 | (2, 2, 'normal', 'app', 'remote', 'none', False) | (14999, 21) | OK MATCH |
| 584 | (2, 2, 'normal', 'app', 'remote', 'none', True) | (14999, 21) | OK MATCH |
| 585 | (2, 2, 'normal', 'app', 'remote', 'cash', False) | (14999, 21) | OK MATCH |
| 586 | (2, 2, 'normal', 'app', 'remote', 'cash', True) | (14999, 21) | OK MATCH |
| 587 | (2, 2, 'normal', 'app', 'remote', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 588 | (2, 2, 'normal', 'app', 'remote', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 589 | (2, 2, 'normal', 'web', 'mainland', 'none', False) | (14999, 21) | OK MATCH |
| 590 | (2, 2, 'normal', 'web', 'mainland', 'none', True) | (14999, 21) | OK MATCH |
| 591 | (2, 2, 'normal', 'web', 'mainland', 'cash', False) | (14999, 21) | OK MATCH |
| 592 | (2, 2, 'normal', 'web', 'mainland', 'cash', True) | (14999, 21) | OK MATCH |
| 593 | (2, 2, 'normal', 'web', 'mainland', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 594 | (2, 2, 'normal', 'web', 'mainland', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 595 | (2, 2, 'normal', 'web', 'remote', 'none', False) | (14999, 21) | OK MATCH |
| 596 | (2, 2, 'normal', 'web', 'remote', 'none', True) | (14999, 21) | OK MATCH |
| 597 | (2, 2, 'normal', 'web', 'remote', 'cash', False) | (14999, 21) | OK MATCH |
| 598 | (2, 2, 'normal', 'web', 'remote', 'cash', True) | (14999, 21) | OK MATCH |
| 599 | (2, 2, 'normal', 'web', 'remote', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 600 | (2, 2, 'normal', 'web', 'remote', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 601 | (2, 2, 'silver', 'app', 'mainland', 'none', False) | (14999, 21) | OK MATCH |
| 602 | (2, 2, 'silver', 'app', 'mainland', 'none', True) | (14999, 21) | OK MATCH |
| 603 | (2, 2, 'silver', 'app', 'mainland', 'cash', False) | (14999, 21) | OK MATCH |
| 604 | (2, 2, 'silver', 'app', 'mainland', 'cash', True) | (14999, 21) | OK MATCH |
| 605 | (2, 2, 'silver', 'app', 'mainland', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 606 | (2, 2, 'silver', 'app', 'mainland', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 607 | (2, 2, 'silver', 'app', 'remote', 'none', False) | (14999, 21) | OK MATCH |
| 608 | (2, 2, 'silver', 'app', 'remote', 'none', True) | (14999, 21) | OK MATCH |
| 609 | (2, 2, 'silver', 'app', 'remote', 'cash', False) | (14999, 21) | OK MATCH |
| 610 | (2, 2, 'silver', 'app', 'remote', 'cash', True) | (14999, 21) | OK MATCH |
| 611 | (2, 2, 'silver', 'app', 'remote', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 612 | (2, 2, 'silver', 'app', 'remote', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 613 | (2, 2, 'silver', 'web', 'mainland', 'none', False) | (14999, 21) | OK MATCH |
| 614 | (2, 2, 'silver', 'web', 'mainland', 'none', True) | (14999, 21) | OK MATCH |
| 615 | (2, 2, 'silver', 'web', 'mainland', 'cash', False) | (14999, 21) | OK MATCH |
| 616 | (2, 2, 'silver', 'web', 'mainland', 'cash', True) | (14999, 21) | OK MATCH |
| 617 | (2, 2, 'silver', 'web', 'mainland', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 618 | (2, 2, 'silver', 'web', 'mainland', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 619 | (2, 2, 'silver', 'web', 'remote', 'none', False) | (14999, 21) | OK MATCH |
| 620 | (2, 2, 'silver', 'web', 'remote', 'none', True) | (14999, 21) | OK MATCH |
| 621 | (2, 2, 'silver', 'web', 'remote', 'cash', False) | (14999, 21) | OK MATCH |
| 622 | (2, 2, 'silver', 'web', 'remote', 'cash', True) | (14999, 21) | OK MATCH |
| 623 | (2, 2, 'silver', 'web', 'remote', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 624 | (2, 2, 'silver', 'web', 'remote', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 625 | (2, 2, 'gold', 'app', 'mainland', 'none', False) | (14999, 21) | OK MATCH |
| 626 | (2, 2, 'gold', 'app', 'mainland', 'none', True) | (14999, 21) | OK MATCH |
| 627 | (2, 2, 'gold', 'app', 'mainland', 'cash', False) | (14999, 21) | OK MATCH |
| 628 | (2, 2, 'gold', 'app', 'mainland', 'cash', True) | (14999, 21) | OK MATCH |
| 629 | (2, 2, 'gold', 'app', 'mainland', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 630 | (2, 2, 'gold', 'app', 'mainland', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 631 | (2, 2, 'gold', 'app', 'remote', 'none', False) | (14999, 21) | OK MATCH |
| 632 | (2, 2, 'gold', 'app', 'remote', 'none', True) | (14999, 21) | OK MATCH |
| 633 | (2, 2, 'gold', 'app', 'remote', 'cash', False) | (14999, 21) | OK MATCH |
| 634 | (2, 2, 'gold', 'app', 'remote', 'cash', True) | (14999, 21) | OK MATCH |
| 635 | (2, 2, 'gold', 'app', 'remote', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 636 | (2, 2, 'gold', 'app', 'remote', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 637 | (2, 2, 'gold', 'web', 'mainland', 'none', False) | (14999, 21) | OK MATCH |
| 638 | (2, 2, 'gold', 'web', 'mainland', 'none', True) | (14999, 21) | OK MATCH |
| 639 | (2, 2, 'gold', 'web', 'mainland', 'cash', False) | (14999, 21) | OK MATCH |
| 640 | (2, 2, 'gold', 'web', 'mainland', 'cash', True) | (14999, 21) | OK MATCH |
| 641 | (2, 2, 'gold', 'web', 'mainland', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 642 | (2, 2, 'gold', 'web', 'mainland', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 643 | (2, 2, 'gold', 'web', 'remote', 'none', False) | (14999, 21) | OK MATCH |
| 644 | (2, 2, 'gold', 'web', 'remote', 'none', True) | (14999, 21) | OK MATCH |
| 645 | (2, 2, 'gold', 'web', 'remote', 'cash', False) | (14999, 21) | OK MATCH |
| 646 | (2, 2, 'gold', 'web', 'remote', 'cash', True) | (14999, 21) | OK MATCH |
| 647 | (2, 2, 'gold', 'web', 'remote', 'gift', False) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 648 | (2, 2, 'gold', 'web', 'remote', 'gift', True) | (14999, 21) | ERROR ValueError: gift coupon requires amount >= 20000 MATCH |
| 649 | (3, 0, 'normal', 'app', 'mainland', 'none', False) | (20001, 5) | OK MATCH |
| 650 | (3, 0, 'normal', 'app', 'mainland', 'none', True) | (20001, 5) | OK MATCH |
| 651 | (3, 0, 'normal', 'app', 'mainland', 'cash', False) | (20001, 5) | OK MATCH |
| 652 | (3, 0, 'normal', 'app', 'mainland', 'cash', True) | (20001, 5) | OK MATCH |
| 653 | (3, 0, 'normal', 'app', 'mainland', 'gift', False) | (20001, 5) | OK MATCH |
| 654 | (3, 0, 'normal', 'app', 'mainland', 'gift', True) | (20001, 5) | OK MATCH |
| 655 | (3, 0, 'normal', 'app', 'remote', 'none', False) | (20001, 5) | OK MATCH |
| 656 | (3, 0, 'normal', 'app', 'remote', 'none', True) | (20001, 5) | OK MATCH |
| 657 | (3, 0, 'normal', 'app', 'remote', 'cash', False) | (20001, 5) | OK MATCH |
| 658 | (3, 0, 'normal', 'app', 'remote', 'cash', True) | (20001, 5) | OK MATCH |
| 659 | (3, 0, 'normal', 'app', 'remote', 'gift', False) | (20001, 5) | OK MATCH |
| 660 | (3, 0, 'normal', 'app', 'remote', 'gift', True) | (20001, 5) | OK MATCH |
| 661 | (3, 0, 'normal', 'web', 'mainland', 'none', False) | (20001, 5) | OK MATCH |
| 662 | (3, 0, 'normal', 'web', 'mainland', 'none', True) | (20001, 5) | OK MATCH |
| 663 | (3, 0, 'normal', 'web', 'mainland', 'cash', False) | (20001, 5) | OK MATCH |
| 664 | (3, 0, 'normal', 'web', 'mainland', 'cash', True) | (20001, 5) | OK MATCH |
| 665 | (3, 0, 'normal', 'web', 'mainland', 'gift', False) | (20001, 5) | OK MATCH |
| 666 | (3, 0, 'normal', 'web', 'mainland', 'gift', True) | (20001, 5) | OK MATCH |
| 667 | (3, 0, 'normal', 'web', 'remote', 'none', False) | (20001, 5) | OK MATCH |
| 668 | (3, 0, 'normal', 'web', 'remote', 'none', True) | (20001, 5) | OK MATCH |
| 669 | (3, 0, 'normal', 'web', 'remote', 'cash', False) | (20001, 5) | OK MATCH |
| 670 | (3, 0, 'normal', 'web', 'remote', 'cash', True) | (20001, 5) | OK MATCH |
| 671 | (3, 0, 'normal', 'web', 'remote', 'gift', False) | (20001, 5) | OK MATCH |
| 672 | (3, 0, 'normal', 'web', 'remote', 'gift', True) | (20001, 5) | OK MATCH |
| 673 | (3, 0, 'silver', 'app', 'mainland', 'none', False) | (20001, 5) | OK MATCH |
| 674 | (3, 0, 'silver', 'app', 'mainland', 'none', True) | (20001, 5) | OK MATCH |
| 675 | (3, 0, 'silver', 'app', 'mainland', 'cash', False) | (20001, 5) | OK MATCH |
| 676 | (3, 0, 'silver', 'app', 'mainland', 'cash', True) | (20001, 5) | OK MATCH |
| 677 | (3, 0, 'silver', 'app', 'mainland', 'gift', False) | (20001, 5) | OK MATCH |
| 678 | (3, 0, 'silver', 'app', 'mainland', 'gift', True) | (20001, 5) | OK MATCH |
| 679 | (3, 0, 'silver', 'app', 'remote', 'none', False) | (20001, 5) | OK MATCH |
| 680 | (3, 0, 'silver', 'app', 'remote', 'none', True) | (20001, 5) | OK MATCH |
| 681 | (3, 0, 'silver', 'app', 'remote', 'cash', False) | (20001, 5) | OK MATCH |
| 682 | (3, 0, 'silver', 'app', 'remote', 'cash', True) | (20001, 5) | OK MATCH |
| 683 | (3, 0, 'silver', 'app', 'remote', 'gift', False) | (20001, 5) | OK MATCH |
| 684 | (3, 0, 'silver', 'app', 'remote', 'gift', True) | (20001, 5) | OK MATCH |
| 685 | (3, 0, 'silver', 'web', 'mainland', 'none', False) | (20001, 5) | OK MATCH |
| 686 | (3, 0, 'silver', 'web', 'mainland', 'none', True) | (20001, 5) | OK MATCH |
| 687 | (3, 0, 'silver', 'web', 'mainland', 'cash', False) | (20001, 5) | OK MATCH |
| 688 | (3, 0, 'silver', 'web', 'mainland', 'cash', True) | (20001, 5) | OK MATCH |
| 689 | (3, 0, 'silver', 'web', 'mainland', 'gift', False) | (20001, 5) | OK MATCH |
| 690 | (3, 0, 'silver', 'web', 'mainland', 'gift', True) | (20001, 5) | OK MATCH |
| 691 | (3, 0, 'silver', 'web', 'remote', 'none', False) | (20001, 5) | OK MATCH |
| 692 | (3, 0, 'silver', 'web', 'remote', 'none', True) | (20001, 5) | OK MATCH |
| 693 | (3, 0, 'silver', 'web', 'remote', 'cash', False) | (20001, 5) | OK MATCH |
| 694 | (3, 0, 'silver', 'web', 'remote', 'cash', True) | (20001, 5) | OK MATCH |
| 695 | (3, 0, 'silver', 'web', 'remote', 'gift', False) | (20001, 5) | OK MATCH |
| 696 | (3, 0, 'silver', 'web', 'remote', 'gift', True) | (20001, 5) | OK MATCH |
| 697 | (3, 0, 'gold', 'app', 'mainland', 'none', False) | (20001, 5) | OK MATCH |
| 698 | (3, 0, 'gold', 'app', 'mainland', 'none', True) | (20001, 5) | OK MATCH |
| 699 | (3, 0, 'gold', 'app', 'mainland', 'cash', False) | (20001, 5) | OK MATCH |
| 700 | (3, 0, 'gold', 'app', 'mainland', 'cash', True) | (20001, 5) | OK MATCH |
| 701 | (3, 0, 'gold', 'app', 'mainland', 'gift', False) | (20001, 5) | OK MATCH |
| 702 | (3, 0, 'gold', 'app', 'mainland', 'gift', True) | (20001, 5) | OK MATCH |
| 703 | (3, 0, 'gold', 'app', 'remote', 'none', False) | (20001, 5) | OK MATCH |
| 704 | (3, 0, 'gold', 'app', 'remote', 'none', True) | (20001, 5) | OK MATCH |
| 705 | (3, 0, 'gold', 'app', 'remote', 'cash', False) | (20001, 5) | OK MATCH |
| 706 | (3, 0, 'gold', 'app', 'remote', 'cash', True) | (20001, 5) | OK MATCH |
| 707 | (3, 0, 'gold', 'app', 'remote', 'gift', False) | (20001, 5) | OK MATCH |
| 708 | (3, 0, 'gold', 'app', 'remote', 'gift', True) | (20001, 5) | OK MATCH |
| 709 | (3, 0, 'gold', 'web', 'mainland', 'none', False) | (20001, 5) | OK MATCH |
| 710 | (3, 0, 'gold', 'web', 'mainland', 'none', True) | (20001, 5) | OK MATCH |
| 711 | (3, 0, 'gold', 'web', 'mainland', 'cash', False) | (20001, 5) | OK MATCH |
| 712 | (3, 0, 'gold', 'web', 'mainland', 'cash', True) | (20001, 5) | OK MATCH |
| 713 | (3, 0, 'gold', 'web', 'mainland', 'gift', False) | (20001, 5) | OK MATCH |
| 714 | (3, 0, 'gold', 'web', 'mainland', 'gift', True) | (20001, 5) | OK MATCH |
| 715 | (3, 0, 'gold', 'web', 'remote', 'none', False) | (20001, 5) | OK MATCH |
| 716 | (3, 0, 'gold', 'web', 'remote', 'none', True) | (20001, 5) | OK MATCH |
| 717 | (3, 0, 'gold', 'web', 'remote', 'cash', False) | (20001, 5) | OK MATCH |
| 718 | (3, 0, 'gold', 'web', 'remote', 'cash', True) | (20001, 5) | OK MATCH |
| 719 | (3, 0, 'gold', 'web', 'remote', 'gift', False) | (20001, 5) | OK MATCH |
| 720 | (3, 0, 'gold', 'web', 'remote', 'gift', True) | (20001, 5) | OK MATCH |
| 721 | (3, 1, 'normal', 'app', 'mainland', 'none', False) | (20001, 14) | OK MATCH |
| 722 | (3, 1, 'normal', 'app', 'mainland', 'none', True) | (20001, 14) | OK MATCH |
| 723 | (3, 1, 'normal', 'app', 'mainland', 'cash', False) | (20001, 14) | OK MATCH |
| 724 | (3, 1, 'normal', 'app', 'mainland', 'cash', True) | (20001, 14) | OK MATCH |
| 725 | (3, 1, 'normal', 'app', 'mainland', 'gift', False) | (20001, 14) | OK MATCH |
| 726 | (3, 1, 'normal', 'app', 'mainland', 'gift', True) | (20001, 14) | OK MATCH |
| 727 | (3, 1, 'normal', 'app', 'remote', 'none', False) | (20001, 14) | OK MATCH |
| 728 | (3, 1, 'normal', 'app', 'remote', 'none', True) | (20001, 14) | OK MATCH |
| 729 | (3, 1, 'normal', 'app', 'remote', 'cash', False) | (20001, 14) | OK MATCH |
| 730 | (3, 1, 'normal', 'app', 'remote', 'cash', True) | (20001, 14) | OK MATCH |
| 731 | (3, 1, 'normal', 'app', 'remote', 'gift', False) | (20001, 14) | OK MATCH |
| 732 | (3, 1, 'normal', 'app', 'remote', 'gift', True) | (20001, 14) | OK MATCH |
| 733 | (3, 1, 'normal', 'web', 'mainland', 'none', False) | (20001, 14) | OK MATCH |
| 734 | (3, 1, 'normal', 'web', 'mainland', 'none', True) | (20001, 14) | OK MATCH |
| 735 | (3, 1, 'normal', 'web', 'mainland', 'cash', False) | (20001, 14) | OK MATCH |
| 736 | (3, 1, 'normal', 'web', 'mainland', 'cash', True) | (20001, 14) | OK MATCH |
| 737 | (3, 1, 'normal', 'web', 'mainland', 'gift', False) | (20001, 14) | OK MATCH |
| 738 | (3, 1, 'normal', 'web', 'mainland', 'gift', True) | (20001, 14) | OK MATCH |
| 739 | (3, 1, 'normal', 'web', 'remote', 'none', False) | (20001, 14) | OK MATCH |
| 740 | (3, 1, 'normal', 'web', 'remote', 'none', True) | (20001, 14) | OK MATCH |
| 741 | (3, 1, 'normal', 'web', 'remote', 'cash', False) | (20001, 14) | OK MATCH |
| 742 | (3, 1, 'normal', 'web', 'remote', 'cash', True) | (20001, 14) | OK MATCH |
| 743 | (3, 1, 'normal', 'web', 'remote', 'gift', False) | (20001, 14) | OK MATCH |
| 744 | (3, 1, 'normal', 'web', 'remote', 'gift', True) | (20001, 14) | OK MATCH |
| 745 | (3, 1, 'silver', 'app', 'mainland', 'none', False) | (20001, 14) | OK MATCH |
| 746 | (3, 1, 'silver', 'app', 'mainland', 'none', True) | (20001, 14) | OK MATCH |
| 747 | (3, 1, 'silver', 'app', 'mainland', 'cash', False) | (20001, 14) | OK MATCH |
| 748 | (3, 1, 'silver', 'app', 'mainland', 'cash', True) | (20001, 14) | OK MATCH |
| 749 | (3, 1, 'silver', 'app', 'mainland', 'gift', False) | (20001, 14) | OK MATCH |
| 750 | (3, 1, 'silver', 'app', 'mainland', 'gift', True) | (20001, 14) | OK MATCH |
| 751 | (3, 1, 'silver', 'app', 'remote', 'none', False) | (20001, 14) | OK MATCH |
| 752 | (3, 1, 'silver', 'app', 'remote', 'none', True) | (20001, 14) | OK MATCH |
| 753 | (3, 1, 'silver', 'app', 'remote', 'cash', False) | (20001, 14) | OK MATCH |
| 754 | (3, 1, 'silver', 'app', 'remote', 'cash', True) | (20001, 14) | OK MATCH |
| 755 | (3, 1, 'silver', 'app', 'remote', 'gift', False) | (20001, 14) | OK MATCH |
| 756 | (3, 1, 'silver', 'app', 'remote', 'gift', True) | (20001, 14) | OK MATCH |
| 757 | (3, 1, 'silver', 'web', 'mainland', 'none', False) | (20001, 14) | OK MATCH |
| 758 | (3, 1, 'silver', 'web', 'mainland', 'none', True) | (20001, 14) | OK MATCH |
| 759 | (3, 1, 'silver', 'web', 'mainland', 'cash', False) | (20001, 14) | OK MATCH |
| 760 | (3, 1, 'silver', 'web', 'mainland', 'cash', True) | (20001, 14) | OK MATCH |
| 761 | (3, 1, 'silver', 'web', 'mainland', 'gift', False) | (20001, 14) | OK MATCH |
| 762 | (3, 1, 'silver', 'web', 'mainland', 'gift', True) | (20001, 14) | OK MATCH |
| 763 | (3, 1, 'silver', 'web', 'remote', 'none', False) | (20001, 14) | OK MATCH |
| 764 | (3, 1, 'silver', 'web', 'remote', 'none', True) | (20001, 14) | OK MATCH |
| 765 | (3, 1, 'silver', 'web', 'remote', 'cash', False) | (20001, 14) | OK MATCH |
| 766 | (3, 1, 'silver', 'web', 'remote', 'cash', True) | (20001, 14) | OK MATCH |
| 767 | (3, 1, 'silver', 'web', 'remote', 'gift', False) | (20001, 14) | OK MATCH |
| 768 | (3, 1, 'silver', 'web', 'remote', 'gift', True) | (20001, 14) | OK MATCH |
| 769 | (3, 1, 'gold', 'app', 'mainland', 'none', False) | (20001, 14) | OK MATCH |
| 770 | (3, 1, 'gold', 'app', 'mainland', 'none', True) | (20001, 14) | OK MATCH |
| 771 | (3, 1, 'gold', 'app', 'mainland', 'cash', False) | (20001, 14) | OK MATCH |
| 772 | (3, 1, 'gold', 'app', 'mainland', 'cash', True) | (20001, 14) | OK MATCH |
| 773 | (3, 1, 'gold', 'app', 'mainland', 'gift', False) | (20001, 14) | OK MATCH |
| 774 | (3, 1, 'gold', 'app', 'mainland', 'gift', True) | (20001, 14) | OK MATCH |
| 775 | (3, 1, 'gold', 'app', 'remote', 'none', False) | (20001, 14) | OK MATCH |
| 776 | (3, 1, 'gold', 'app', 'remote', 'none', True) | (20001, 14) | OK MATCH |
| 777 | (3, 1, 'gold', 'app', 'remote', 'cash', False) | (20001, 14) | OK MATCH |
| 778 | (3, 1, 'gold', 'app', 'remote', 'cash', True) | (20001, 14) | OK MATCH |
| 779 | (3, 1, 'gold', 'app', 'remote', 'gift', False) | (20001, 14) | OK MATCH |
| 780 | (3, 1, 'gold', 'app', 'remote', 'gift', True) | (20001, 14) | OK MATCH |
| 781 | (3, 1, 'gold', 'web', 'mainland', 'none', False) | (20001, 14) | OK MATCH |
| 782 | (3, 1, 'gold', 'web', 'mainland', 'none', True) | (20001, 14) | OK MATCH |
| 783 | (3, 1, 'gold', 'web', 'mainland', 'cash', False) | (20001, 14) | OK MATCH |
| 784 | (3, 1, 'gold', 'web', 'mainland', 'cash', True) | (20001, 14) | OK MATCH |
| 785 | (3, 1, 'gold', 'web', 'mainland', 'gift', False) | (20001, 14) | OK MATCH |
| 786 | (3, 1, 'gold', 'web', 'mainland', 'gift', True) | (20001, 14) | OK MATCH |
| 787 | (3, 1, 'gold', 'web', 'remote', 'none', False) | (20001, 14) | OK MATCH |
| 788 | (3, 1, 'gold', 'web', 'remote', 'none', True) | (20001, 14) | OK MATCH |
| 789 | (3, 1, 'gold', 'web', 'remote', 'cash', False) | (20001, 14) | OK MATCH |
| 790 | (3, 1, 'gold', 'web', 'remote', 'cash', True) | (20001, 14) | OK MATCH |
| 791 | (3, 1, 'gold', 'web', 'remote', 'gift', False) | (20001, 14) | OK MATCH |
| 792 | (3, 1, 'gold', 'web', 'remote', 'gift', True) | (20001, 14) | OK MATCH |
| 793 | (3, 2, 'normal', 'app', 'mainland', 'none', False) | (20001, 21) | OK MATCH |
| 794 | (3, 2, 'normal', 'app', 'mainland', 'none', True) | (20001, 21) | OK MATCH |
| 795 | (3, 2, 'normal', 'app', 'mainland', 'cash', False) | (20001, 21) | OK MATCH |
| 796 | (3, 2, 'normal', 'app', 'mainland', 'cash', True) | (20001, 21) | OK MATCH |
| 797 | (3, 2, 'normal', 'app', 'mainland', 'gift', False) | (20001, 21) | OK MATCH |
| 798 | (3, 2, 'normal', 'app', 'mainland', 'gift', True) | (20001, 21) | OK MATCH |
| 799 | (3, 2, 'normal', 'app', 'remote', 'none', False) | (20001, 21) | OK MATCH |
| 800 | (3, 2, 'normal', 'app', 'remote', 'none', True) | (20001, 21) | OK MATCH |
| 801 | (3, 2, 'normal', 'app', 'remote', 'cash', False) | (20001, 21) | OK MATCH |
| 802 | (3, 2, 'normal', 'app', 'remote', 'cash', True) | (20001, 21) | OK MATCH |
| 803 | (3, 2, 'normal', 'app', 'remote', 'gift', False) | (20001, 21) | OK MATCH |
| 804 | (3, 2, 'normal', 'app', 'remote', 'gift', True) | (20001, 21) | OK MATCH |
| 805 | (3, 2, 'normal', 'web', 'mainland', 'none', False) | (20001, 21) | OK MATCH |
| 806 | (3, 2, 'normal', 'web', 'mainland', 'none', True) | (20001, 21) | OK MATCH |
| 807 | (3, 2, 'normal', 'web', 'mainland', 'cash', False) | (20001, 21) | OK MATCH |
| 808 | (3, 2, 'normal', 'web', 'mainland', 'cash', True) | (20001, 21) | OK MATCH |
| 809 | (3, 2, 'normal', 'web', 'mainland', 'gift', False) | (20001, 21) | OK MATCH |
| 810 | (3, 2, 'normal', 'web', 'mainland', 'gift', True) | (20001, 21) | OK MATCH |
| 811 | (3, 2, 'normal', 'web', 'remote', 'none', False) | (20001, 21) | OK MATCH |
| 812 | (3, 2, 'normal', 'web', 'remote', 'none', True) | (20001, 21) | OK MATCH |
| 813 | (3, 2, 'normal', 'web', 'remote', 'cash', False) | (20001, 21) | OK MATCH |
| 814 | (3, 2, 'normal', 'web', 'remote', 'cash', True) | (20001, 21) | OK MATCH |
| 815 | (3, 2, 'normal', 'web', 'remote', 'gift', False) | (20001, 21) | OK MATCH |
| 816 | (3, 2, 'normal', 'web', 'remote', 'gift', True) | (20001, 21) | OK MATCH |
| 817 | (3, 2, 'silver', 'app', 'mainland', 'none', False) | (20001, 21) | OK MATCH |
| 818 | (3, 2, 'silver', 'app', 'mainland', 'none', True) | (20001, 21) | OK MATCH |
| 819 | (3, 2, 'silver', 'app', 'mainland', 'cash', False) | (20001, 21) | OK MATCH |
| 820 | (3, 2, 'silver', 'app', 'mainland', 'cash', True) | (20001, 21) | OK MATCH |
| 821 | (3, 2, 'silver', 'app', 'mainland', 'gift', False) | (20001, 21) | OK MATCH |
| 822 | (3, 2, 'silver', 'app', 'mainland', 'gift', True) | (20001, 21) | OK MATCH |
| 823 | (3, 2, 'silver', 'app', 'remote', 'none', False) | (20001, 21) | OK MATCH |
| 824 | (3, 2, 'silver', 'app', 'remote', 'none', True) | (20001, 21) | OK MATCH |
| 825 | (3, 2, 'silver', 'app', 'remote', 'cash', False) | (20001, 21) | OK MATCH |
| 826 | (3, 2, 'silver', 'app', 'remote', 'cash', True) | (20001, 21) | OK MATCH |
| 827 | (3, 2, 'silver', 'app', 'remote', 'gift', False) | (20001, 21) | OK MATCH |
| 828 | (3, 2, 'silver', 'app', 'remote', 'gift', True) | (20001, 21) | OK MATCH |
| 829 | (3, 2, 'silver', 'web', 'mainland', 'none', False) | (20001, 21) | OK MATCH |
| 830 | (3, 2, 'silver', 'web', 'mainland', 'none', True) | (20001, 21) | OK MATCH |
| 831 | (3, 2, 'silver', 'web', 'mainland', 'cash', False) | (20001, 21) | OK MATCH |
| 832 | (3, 2, 'silver', 'web', 'mainland', 'cash', True) | (20001, 21) | OK MATCH |
| 833 | (3, 2, 'silver', 'web', 'mainland', 'gift', False) | (20001, 21) | OK MATCH |
| 834 | (3, 2, 'silver', 'web', 'mainland', 'gift', True) | (20001, 21) | OK MATCH |
| 835 | (3, 2, 'silver', 'web', 'remote', 'none', False) | (20001, 21) | OK MATCH |
| 836 | (3, 2, 'silver', 'web', 'remote', 'none', True) | (20001, 21) | OK MATCH |
| 837 | (3, 2, 'silver', 'web', 'remote', 'cash', False) | (20001, 21) | OK MATCH |
| 838 | (3, 2, 'silver', 'web', 'remote', 'cash', True) | (20001, 21) | OK MATCH |
| 839 | (3, 2, 'silver', 'web', 'remote', 'gift', False) | (20001, 21) | OK MATCH |
| 840 | (3, 2, 'silver', 'web', 'remote', 'gift', True) | (20001, 21) | OK MATCH |
| 841 | (3, 2, 'gold', 'app', 'mainland', 'none', False) | (20001, 21) | OK MATCH |
| 842 | (3, 2, 'gold', 'app', 'mainland', 'none', True) | (20001, 21) | OK MATCH |
| 843 | (3, 2, 'gold', 'app', 'mainland', 'cash', False) | (20001, 21) | OK MATCH |
| 844 | (3, 2, 'gold', 'app', 'mainland', 'cash', True) | (20001, 21) | OK MATCH |
| 845 | (3, 2, 'gold', 'app', 'mainland', 'gift', False) | (20001, 21) | OK MATCH |
| 846 | (3, 2, 'gold', 'app', 'mainland', 'gift', True) | (20001, 21) | OK MATCH |
| 847 | (3, 2, 'gold', 'app', 'remote', 'none', False) | (20001, 21) | OK MATCH |
| 848 | (3, 2, 'gold', 'app', 'remote', 'none', True) | (20001, 21) | OK MATCH |
| 849 | (3, 2, 'gold', 'app', 'remote', 'cash', False) | (20001, 21) | OK MATCH |
| 850 | (3, 2, 'gold', 'app', 'remote', 'cash', True) | (20001, 21) | OK MATCH |
| 851 | (3, 2, 'gold', 'app', 'remote', 'gift', False) | (20001, 21) | OK MATCH |
| 852 | (3, 2, 'gold', 'app', 'remote', 'gift', True) | (20001, 21) | OK MATCH |
| 853 | (3, 2, 'gold', 'web', 'mainland', 'none', False) | (20001, 21) | OK MATCH |
| 854 | (3, 2, 'gold', 'web', 'mainland', 'none', True) | (20001, 21) | OK MATCH |
| 855 | (3, 2, 'gold', 'web', 'mainland', 'cash', False) | (20001, 21) | OK MATCH |
| 856 | (3, 2, 'gold', 'web', 'mainland', 'cash', True) | (20001, 21) | OK MATCH |
| 857 | (3, 2, 'gold', 'web', 'mainland', 'gift', False) | (20001, 21) | OK MATCH |
| 858 | (3, 2, 'gold', 'web', 'mainland', 'gift', True) | (20001, 21) | OK MATCH |
| 859 | (3, 2, 'gold', 'web', 'remote', 'none', False) | (20001, 21) | OK MATCH |
| 860 | (3, 2, 'gold', 'web', 'remote', 'none', True) | (20001, 21) | OK MATCH |
| 861 | (3, 2, 'gold', 'web', 'remote', 'cash', False) | (20001, 21) | OK MATCH |
| 862 | (3, 2, 'gold', 'web', 'remote', 'cash', True) | (20001, 21) | OK MATCH |
| 863 | (3, 2, 'gold', 'web', 'remote', 'gift', False) | (20001, 21) | OK MATCH |
| 864 | (3, 2, 'gold', 'web', 'remote', 'gift', True) | (20001, 21) | OK MATCH |

## 覆盖验证（legacy_pricing.settle）

- 函数体语句行：122 行，对拍期间全部被执行（遗漏 0 行）
- UNREACHABLE(redundant) 死语句：6 处（L72, 75, 78, 103, 145, 169），对拍期间命中 0 处（符合预期）
- UNREACHABLE(omission) 遗漏：1 处（注释位于 L194）

详细分类说明见 docs/unreachable_analysis.md。
