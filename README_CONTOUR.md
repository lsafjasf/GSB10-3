# 等值线提取（Marching Squares，纯标准库）

把规则网格上的标量场转成可直接绘图的等值线折线。核心解决两个问题：
鞍点歧义格连接不一致导致的"连错"，以及相邻格线段端点不重合导致的"画断"。

## 文件

- `contour_lib.py` — 库（仅 Python 3 标准库），`python3 contour_lib.py` 运行演示并输出 `contours_demo.svg`
- `test_contour.py` — 自测（unittest，13 个用例），`python3 test_contour.py`

## 用法

```python
from contour_lib import extract_contours

# values[j*nx + i] 为节点 (i, j) 的标量值
res = extract_contours(values, nx, ny, level, dx=1.0, dy=1.0, x0=0.0, y0=0.0)

res.segments          # 原始线段 [((x1,y1),(x2,y2)), ...]
res.closed_contours   # 闭合等值线折线（首尾坐标相同）
res.open_contours     # 开放等值线折线（端点在域边界上）
res.ambiguous_cells   # 全部歧义格 [(i,j), ...]
res.saddle_high_cells # 其中按"鞍点为高"消歧的格
res.saddle_low_cells  # 其中按"鞍点为低"消歧的格
res.breakpoints       # 断点数（恒为 0）
```

## 消歧规则（渐近判定器）

单元格四角的"高/低"（`v >= level` 为高）编码成 0–15 的 case。
其中 case 5 与 case 10（对角两高两低）是歧义格，有两种连接方式，
逐格随意选择会让相邻格线条连错。本库采用**渐近判定器**统一消歧：

1. 把四角值按双线性函数 `f(x,y) = a + b·x + c·y + d·x·y` 插值，
   其鞍点值为 `s = (a·d − b·c) / (a − b − c + d)`（a,b,c,d 为左下/右下/右上/左上四角值）。
2. `s >= level` → 按"鞍点处为高"连接；`s < level` → 按"鞍点处为低"连接。
3. 分母为 0（线性场，无鞍点）时退化为"鞍点处为低"。

该规则只依赖单元格自身的四个角值，对任意相邻格天然一致；
`ContourResult.num_ambiguous / saddle_high_cells / saddle_low_cells`
给出受影响格数与消歧方向统计。

## 线段连接断言（断点数为零）

跨格拼接靠**全局边键**保证：水平边 `("h",i,j)`、垂直边 `("v",i,j)`、
交点恰落在节点上时的节点键 `("n",i,j)`。相邻格引用同一条物理边必产生
相同的键与坐标（交点坐标按边缓存，只计算一次），因此线段端点逐位相等。

断点定义为邻接图中度数的违反（合法端点只允许出现在域边界上）：

| 顶点位置 | 合法度数 | 否则计为断点 |
|---|---|---|
| 内部边键 | == 2 | 是 |
| 边界边键 | 0 或 1 | 是 |
| 内部节点键 | 偶数 | 是 |
| 边界节点键 | ≤ 2 | 是 |

`assert_continuity()`（test_contour.py:14）对每个用例断言
`res.breakpoints == 0`，并校验折线顶点集合与线段端点集合完全一致；
`test_random_fields_property` 用 400 组随机场做性质测试，断点恒为 0。

## 开/闭等值线统计

链追踪把线段拼成折线后按"是否触碰域边界"分类：
触碰边界（含边界节点退化情形）→ 开放等值线，否则 → 闭合等值线。
`len(res.open_contours)` 与 `len(res.closed_contours)` 即各自条数。

## 测试覆盖

- `test_all_above_threshold` / `test_all_below_threshold` — 全部高于/低于阈值：零线段、零断点
- `test_gaussian_closed_contour` / `test_two_hills_two_closed` — 闭合等值线（1 条 / 恰好 2 条）
- `test_linear_plane_open_contours` / `test_boundary_crossing` — 开放等值线、边界多次穿过
- `test_saddle_single_cell_high` / `test_saddle_single_cell_low` — 单格歧义两种消歧方向
- `test_saddle_field_disambiguation_consistency` — 双曲鞍点场，歧义格一致消歧、断点为 0
- `test_exact_level_at_node` / `test_diagonal_exact_level` — 节点值恰等于阈值的退化情形
- `test_shifted_grid_origin` — 非单位间距与原点偏移
- `test_random_fields_property` — 400 组随机场性质测试

## 运行方式

```bash
python3 test_contour.py    # 自测（13 个用例）
python3 contour_lib.py     # 演示：统计信息 + 输出 contours_demo.svg
```
