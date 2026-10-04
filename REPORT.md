# 三角网格顶点法线重建

## 交付文件

- `mesh_normals.py`：法线重建库，同时提供演示 CLI。
- `test_mesh_normals.py`：基于 Python 标准库 `unittest` 的自测。
- `REPORT.md`：方法说明、实测数据和运行方式。

仅使用 Python 3 标准库，无第三方依赖。

## 加权方式

- 面积加权：直接累加未归一化面法线 `cross(p1-p0, p2-p0)`。其模长为三角形面积的 2 倍，全局系数 2 不影响归一化结果。
- 角度加权：先计算单位面法线，再按每个顶点处的三角形内角累加 `corner_angle * unit_face_normal`。

面积加权更受大三角形影响；角度加权更强调局部连接几何。二者在不规则网格上会产生可测差异。

## 退化面剔除

以下三角形不会参与法线累加：

- 面内出现重复顶点索引。
- 不同索引指向重复坐标。
- 三点共线或零面积。
- 满足相对退化阈值 `||cross(e1, e2)|| <= eps * max_edge_length_squared` 的近零面积三角形。

结果对象返回：

- `degenerate_face_count`：剔除数量。
- `degenerate_face_indices`：被剔除面的原始索引。
- `isolated_vertices`：没有任何有效邻接面的顶点。
- `fallback_vertices`：使用兜底法线的顶点。

孤立顶点没有可定义的曲面法线，因此使用确定性的单位兜底法线 `(0, 0, 1)`，保证输出中的所有法线都是单位向量。

## 模长断言

每个测试结果都检查：

```text
max(abs(length(normal) - 1)) <= 1e-12
```

演示网格的实测模长误差：

```text
max |length(normal)-1| (area): 1.110e-16
max |length(normal)-1| (angle): 1.110e-16
```

## 实测对比数据

演示网格包含 10 个顶点、10 个面，其中 2 个退化面、1 个孤立顶点。

```text
Mesh normal reconstruction demo
================================
vertices: 10
faces: 10
degenerate faces: 2 at indices [8, 9]
isolated vertices: [9]
max |length(normal)-1| (area): 1.110e-16
max |length(normal)-1| (angle): 1.110e-16
area vs angle:
  max angular difference: 8.705927 deg
  mean angular difference: 3.202680 deg
  max Euclidean difference: 0.151801
  mean Euclidean difference: 0.055872
  per-vertex angular difference (deg): [0.522125, 5.780825, 5.165415, 2.357476, 0.197979, 1.420938, 3.940858, 3.935253, 8.705927, 0.000000]
```

## 边界用例覆盖

`test_mesh_normals.py` 覆盖：

- 平面网格：面积加权和角度加权都得到 `(0, 0, 1)`。
- 球面网格：32×16 经纬球，顶点法线与径向方向的点积大于 `0.999`。
- 退化面：重复索引、重复坐标、共线零面积三角形均被剔除并计数。
- 孤立顶点：获得单位兜底法线，并被记录在 `isolated_vertices`。
- 空网格：返回空法线数组，退化计数为 0。
- 非法输入：未知加权方式、错误面长度、越界索引、零兜底法线都会抛出 `ValueError`。

## 运行方式

运行全部自测：

```bash
python3 test_mesh_normals.py
```

运行加权方式对比演示：

```bash
python3 mesh_normals.py
```

在代码中调用：

```python
from mesh_normals import compare_weightings, rebuild_vertex_normals

area_result = rebuild_vertex_normals(vertices, faces, "area")
angle_result = rebuild_vertex_normals(vertices, faces, "angle")
comparison = compare_weightings(vertices, faces)
```
