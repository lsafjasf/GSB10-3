# 估算误差数据与误差来源分析

数据由 `python3 experiments.py` 生成（固定随机种子 20261004，可复现）。
域 [0, 10000]，装载 20000 行，2000 个随机范围查询与真实多重集逐一对比。

- `MAE(sel)`：|估计选择率 − 真实选择率| 的均值（选择率 = 命中数 / 总元组数）
- `P95(sel)`：上述绝对误差的 95 分位
- `meanRelErr`：|估计 − 真实| / max(真实, 1) 的均值

## 静态负载：误差 vs 桶数容量 M

```
uniform       M=4   buckets=4    MAE(sel)=0.00228  P95=0.00543  meanRelErr=0.0158
uniform       M=8   buckets=8    MAE(sel)=0.00125  P95=0.00305  meanRelErr=0.0115
uniform       M=16  buckets=16   MAE(sel)=0.00086  P95=0.00222  meanRelErr=0.0089
uniform       M=32  buckets=32   MAE(sel)=0.00056  P95=0.00143  meanRelErr=0.0071
uniform       M=64  buckets=64   MAE(sel)=0.00039  P95=0.00102  meanRelErr=0.0059

concentrated  M=4   buckets=3    MAE(sel)=0.23593  P95=0.54670  meanRelErr=906.60
concentrated  M=8   buckets=8    MAE(sel)=0.06047  P95=0.15127  meanRelErr=239.46
concentrated  M=16  buckets=16   MAE(sel)=0.02953  P95=0.06974  meanRelErr=118.87
concentrated  M=32  buckets=32   MAE(sel)=0.01658  P95=0.03910  meanRelErr= 67.32
concentrated  M=64  buckets=64   MAE(sel)=0.00904  P95=0.02149  meanRelErr= 36.94

skewed(zipf)  M=4   buckets=4    MAE(sel)=0.03096  P95=0.07504  meanRelErr=0.5387
skewed(zipf)  M=8   buckets=8    MAE(sel)=0.00944  P95=0.02406  meanRelErr=0.1688
skewed(zipf)  M=16  buckets=16   MAE(sel)=0.00359  P95=0.00909  meanRelErr=0.0797
skewed(zipf)  M=32  buckets=22   MAE(sel)=0.00260  P95=0.00614  meanRelErr=0.0640
skewed(zipf)  M=64  buckets=22   MAE(sel)=0.00260  P95=0.00614  meanRelErr=0.0640
```

## 频繁删改（装载后再交错执行 40000 次插入/删除）

```
uniform+churn       M=8   MAE(sel)=0.00123  P95=0.00302  meanRelErr=0.0110
uniform+churn       M=16  MAE(sel)=0.00072  P95=0.00188  meanRelErr=0.0080
uniform+churn       M=32  MAE(sel)=0.00053  P95=0.00136  meanRelErr=0.0069
concentrated+churn  M=8   MAE(sel)=0.08528  P95=0.19912  meanRelErr=0.8381
concentrated+churn  M=16  MAE(sel)=0.02448  P95=0.05789  meanRelErr=0.2479
concentrated+churn  M=32  MAE(sel)=0.00340  P95=0.01539  meanRelErr=0.0792
skewed+churn        M=8   MAE(sel)=0.01227  P95=0.03245  meanRelErr=0.1216
skewed+churn        M=16  MAE(sel)=0.00275  P95=0.00852  meanRelErr=0.0347
skewed+churn        M=32  MAE(sel)=0.00113  P95=0.00283  meanRelErr=0.0204
```

结论：删改不会累积误差——churn 后的误差与静态负载同量级，
因为分裂/合并都用桶内真实计数计算，维护本身不引入近似。

## 误差从哪来

1. **桶内均匀分布假设（唯一来源）**。范围查询对未完全覆盖的桶按
   宽度比例插值。桶内数据越不均匀（集中分布的斜坡、Zipf 的头部），
   插值偏差越大。桶内计数本身是精确的：插入/删除只改所属桶，
   分裂/合并都用桶内保存的真实 value→count 重新分配，不做近似。
2. **容量压力**。M 太小而数据集中时（concentrated, M=4），少数宽桶
   覆盖整个高密度区，均匀假设被严重违反，MAE(sel) 高达 0.24。
   M 翻倍误差近似减半，符合“桶宽减半 → 插值误差减半”的直觉。
3. **单值桶不可再分**。Zipf 头部单个值就超过重桶阈值，但该桶只有
   一个不同值，无法分裂（M=32/64 时只用了 22 个桶）。这是等深
   直方图的固有粒度下限，误差集中在这个热值附近。
4. **相对误差在小基数查询上爆炸**。concentrated 的 meanRelErr 达数百，
   是因为域尾部真实计数接近 0，分母极小；这类查询应看绝对选择率
   误差（P95 仍有界）。工程上应对小基数结果加下限或使用绝对误差。

## 桶变化样例（concentrated, M=8，装载 4000 行后删除 90%）

装载期的分裂（等深切分，边界固定）：

```
split  [0, 10000) (2) -> [0, 5000) (1) + [5000, 10000) (1)
split  [0, 5000) (2) -> [0, 4927.5) (1) + [4927.5, 5000) (1)
split  [5000, 10000) (2) -> [5000, 5243) (1) + [5243, 10000) (1)
...
```

装载后桶自动向高密度区聚拢（中间窄、两端宽）：

```
[     0.00,   4496.00)  count=215
[  4496.00,   4767.00)  count=653
[  4767.00,   4914.00)  count=660
[  4914.00,   5000.00)  count=437
[  5000.00,   5122.50)  count=640
[  5122.50,   5243.00)  count=574
[  5243.00,   5358.50)  count=370
[  5358.50,  10000.00)  count=451
```

删除 90% 行后触发合并，桶数回落：

```
merge  [4914, 4934) (2) + [4934, 5000) (2) -> [4914, 5000) (4)
merge  [5000, 5100.5) (32) + [5100.5, 5122.5) (10) -> [5000, 5122.5) (42)
merge  [0, 4496) (215) + [4496, 4767) (653) -> [0, 4767) (868)
```
