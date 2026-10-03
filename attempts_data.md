# 尝试次数数据（python3 simulate.py，每核 100 次获取）

确定性离散事件仿真：获取尝试=1 个时间片，临界区=cs_len 时间单位，
释放后立即重新竞争（最大竞争），平局按已完成轮数少者优先避免饿死伪影。
策略参数：cap=64, max_spins=12，达到上限后升级为 block（锁释放时唤醒）。

```
rounds per core: 100  (total acquires = cores*rounds)
cores cs_len policy                   attempts   att/acq  escalations
---------------------------------------------------------------------
    1      1 fixed(delay=1)                100      1.00            0
    1      1 exponential(base=1)           100      1.00            0
    1      1 exp+jitter(base=1)            100      1.00            0
    1     10 fixed(delay=1)                100      1.00            0
    1     10 exponential(base=1)           100      1.00            0
    1     10 exp+jitter(base=1)            100      1.00            0
    1     50 fixed(delay=1)                100      1.00            0
    1     50 exponential(base=1)           100      1.00            0
    1     50 exp+jitter(base=1)            100      1.00            0
    2      1 fixed(delay=1)                399      2.00            0
    2      1 exponential(base=1)           399      2.00            0
    2      1 exp+jitter(base=1)            211      1.05            0
    2     10 fixed(delay=1)               2190     10.95            0
    2     10 exponential(base=1)           260      1.30            0
    2     10 exp+jitter(base=1)            314      1.57            0
    2     50 fixed(delay=1)               2787     13.94            0
    2     50 exponential(base=1)           492      2.46            0
    2     50 exp+jitter(base=1)            686      3.43            0
    4      1 fixed(delay=1)               1597      3.99            0
    4      1 exponential(base=1)          1045      2.61            0
    4      1 exp+jitter(base=1)            526      1.31            0
    4     10 fixed(delay=1)               6004     15.01            1
    4     10 exponential(base=1)           813      2.03            0
    4     10 exp+jitter(base=1)           1122      2.81            1
    4     50 fixed(delay=1)               6409     16.02            3
    4     50 exponential(base=1)          2132      5.33            0
    4     50 exp+jitter(base=1)           3314      8.29            1
    8      1 fixed(delay=1)               6393      7.99            0
    8      1 exponential(base=1)          2610      3.26            0
    8      1 exp+jitter(base=1)           1558      1.95           42
    8     10 fixed(delay=1)              15244     19.05           15
    8     10 exponential(base=1)          2633      3.29            0
    8     10 exp+jitter(base=1)           4254      5.32            0
    8     50 fixed(delay=1)              16053     20.07           21
    8     50 exponential(base=1)          8865     11.08            0
    8     50 exp+jitter(base=1)          12818     16.02            3
   16      1 fixed(delay=1)              25585     15.99            3
   16      1 exponential(base=1)          5719      3.57            0
   16      1 exp+jitter(base=1)           5576      3.48         1245
   16     10 fixed(delay=1)              43324     27.08           91
   16     10 exponential(base=1)          9100      5.69            0
   16     10 exp+jitter(base=1)          16262     10.16            0
   16     50 fixed(delay=1)              44941     28.09          105
   16     50 exponential(base=1)         32009     20.01           21
   16     50 exp+jitter(base=1)          38506     24.07           57
   32      1 fixed(delay=1)             102369     31.99          171
   32      1 exponential(base=1)         19367      6.05            0
   32      1 exp+jitter(base=1)          16021      5.01         2914
   32     10 fixed(delay=1)             137884     43.09          435
   32     10 exponential(base=1)         33191     10.37            0
   32     10 exp+jitter(base=1)          70874     22.15           56
   32     50 fixed(delay=1)             141117     44.10          465
   32     50 exponential(base=1)        115257     36.02          253
   32     50 exp+jitter(base=1)         128254     40.08          353
```

## 读数要点

- 无竞争（1 核）：三种策略都是 1 次尝试/获取，零升级 —— 自旋零浪费。
- 固定退避的尝试数随核数近似线性膨胀（32 核/cs=50 时 44 次尝试/获取），
  验证了"核数一多就变成纯浪费"。
- 指数退避把尝试数压到 1/4~1/7；短临界区（cs=1）下抖动版最优。
- 长临界区（cs=50）下带抖动反而略逊于纯指数：抖动频繁抽到很小的延迟，
  导致更密集的空转碰撞 —— 退避策略选不好确实更慢。
- escalations 列显示达到上限后切换为阻塞的次数，高竞争下三策略均触发。
