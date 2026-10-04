# 纹理环绕与采样（Python 3，仅标准库）

贴图坐标可能超出取值范围，本库实现三种环绕方式与最近邻/双线性采样，
并对边界取值给出明确规定与测试。

## 文件

- `texture_sampling.py` — 库：`wrap_index`、`Texture`（最近邻/双线性）、`magnify_coordinates`
- `test_texture.py` — 自测（26 个用例，unittest）
- `generate_data.py` — 生成采样结果数据到 `data/`
- `data/wrap_index_table.csv` — 整数下标环绕对照表（n=4，x ∈ [-9, 12]）
- `data/magnification_results.csv` — 1x/2x/3x/4x 放大、两种过滤、三种环绕的逐像素结果
- `data/edge_cases.csv` — 边界用例采样结果（整数/负坐标/恰好边界/极大坐标）

## 坐标与取值规则（明确规定）

1. 采样坐标 `(u, v)` 为连续浮点坐标，单位 texel：texel `(i, j)` 中心在
   `(i+0.5, j+0.5)`，覆盖 `[i, i+1) x [j, j+1)`。
   因此 **u 恰好等于整数 k 时落在两个 texel 的公共边界上，双线性权重各 0.5**。
2. 环绕只作用于整数 texel 下标：先取 `floor(u-0.5)` 与其 `+1` 两个邻居下标，
   再分别按环绕规则映射到 `[0, n)`，**绝不越界读取**。
3. 三种环绕方式（`w = wrap(x, n)`，与 GPU 惯例一致）：
   - **repeat（重复）**：`w = x mod n`。负坐标按数学取模回绕，如 n=4：`w(-1)=3`。
   - **clamp（钳制）**：`w = min(max(x,0), n-1)`。`w(-1)=0`，`w(n)=n-1`。
   - **mirror（镜像）**：同 OpenGL `GL_MIRRORED_REPEAT`，周期 `2n`，
     边界 texel 在反射点重复一次：`r = x mod 2n`，`r >= n` 时 `w = 2n-1-r`。
     n=4 时下标序列 `... 0 1 2 3 | 3 2 1 0 | 0 1 2 3 ...`，
     即 `w(-1)=0`、`w(3)=w(4)=3`、`w(5)=2`，反射轴在 `u = k*n`。
4. 双线性权重：`s = u - 0.5`，`fx = s - floor(s)`（fy 同理），标准四texel混合。
   推论：texel 中心处采样值精确等于该 texel。
5. 越界检测：`Texture.texel()` 读取前断言下标范围，越界即计数并抛
   `IndexError`；测试断言任意采样后 `out_of_bounds_reads == 0`。

## 运行方式

```bash
cd texture_sampling
python3 test_texture.py        # 或 python3 -m unittest -v
python3 generate_data.py       # 重新生成 data/*.csv 并打印 2x 放大摘要
```

## 测试覆盖

- 环绕规则表：三种方式在 `x ∈ [-9, 12]` 及 `±10^15` 的逐点取值（`WrapIndexTest`）
- 坐标恰好为整数：接缝处权重各 0.5（`test_integer_coordinate_is_midpoint` 等）
- 负坐标：`-0.5 / -0.25 / -1.0 / -12.75` 在三种环绕下的最近邻与双线性
- 恰好等于边界：`u = 0, n, 2n`（repeat 跨接缝、clamp 拉伸边缘、mirror 反射）
- 极大坐标：`±10^9`（repeat 周期性、mirror 按 2n 折叠、clamp 退化为角点 texel）
- 越界检测为零：`BoundsGuardTest` 对 17×17 坐标 × 3 环绕 × 2 种过滤采样后
  断言 `out_of_bounds_reads == 0`，并验证守卫本身能抓到直接越界访问
- 接缝差异：`test_modes_give_different_results_at_seam` 断言同一接缝点
  三种环绕结果互异（repeat 0.2 / clamp 3.0 / mirror 2.8）

## 采样结果数据说明

源纹理 4x4，`T(i,j) = i + 10j`。放大坐标映射：输出像素 `p` 的中心映射到
`(p+0.5) * in/out`（mag=1 时与 texel 中心对齐）。注意在纹理内部
clamp 与 mirror 结果相同，差异只出现在 `[0, n]` 之外的接缝区，
见 `data/edge_cases.csv` 中 `exact_boundary_*`、`negative_*`、`huge_*` 各行。
