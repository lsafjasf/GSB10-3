# 自测结果表（python3 -m rngqa 输出）

样本均为固定数据（确定性构造，不依赖任何随机源），每个样本 65536 字节（边界样本除外）。

| 样本 | 字节数 | 总体 | 分布均匀性 | 位平衡 | 游程 | 周期性 |
|---|---:|---|---|---|---|---|
| good (sha256 counter-mode, fixed seed) | 65536 | **PASS** | PASS (p=1.86e-01) | PASS (z=1.1, worst z=2.3@3) | PASS (z=-0.4, maxrun=17) | PASS (z=-3.6, period=None) |
| counter i&0xff (uniform but low entropy) | 65536 | **FAIL** | PASS (p=1.00e+00) | PASS (z=0.0, worst z=0.0@0) | PASS (z=-0.0, maxrun=15) | FAIL (z=542.5, period=256) |
| low 4 bits stuck at 0 | 65536 | **FAIL** | FAIL (p=0.00e+00) | FAIL (z=-361.9, worst z=-256.0@0) | FAIL (z=-241.3, maxrun=38) | FAIL (z=362.4, period=None) |
| short period (i % 17) | 65536 | **FAIL** | FAIL (p=0.00e+00) | FAIL (z=-372.7, worst z=-256.0@5) | PASS (z=0.2, maxrun=19) | FAIL (z=383.3, period=17) |
| all zero | 65536 | **FAIL** | FAIL (p=0.00e+00) | FAIL (z=-724.1, worst z=-256.0@0) | FAIL (z=n/a, maxrun=524288) | FAIL (z=724.1, period=1) |
| constant 0xA5 (boundary) | 65536 | **FAIL** | FAIL (p=0.00e+00) | FAIL (z=0.0, worst z=256.0@0) | FAIL (z=362.0, maxrun=2) | FAIL (z=-724.1, period=1) |
| tiny 100 bytes (boundary) | 100 | **SKIP** | SKIP | SKIP | SKIP | SKIP |
| empty (boundary) | 0 | **SKIP** | SKIP | SKIP | SKIP | SKIP |

## 各检查明细

### good (sha256 counter-mode, fixed seed)  [n=65536] -> PASS
- uniformity: PASS
  chi2=274.96 df=255 p=0.1864 (256 buckets, expect 256.0/bucket)
- bit_balance: PASS
  global ones=262547/524288 z=1.11; worst bit pos=3 z=2.26
- runs: PASS
  runs=261986 expect~262144 z=-0.44; longest_run=17
- periodicity: PASS
  worst lag=5 z=-3.63; exact_period=None; notable [lag5:z=-3.6]
### counter i&0xff (uniform but low entropy)  [n=65536] -> FAIL
- uniformity: PASS
  chi2=0.00 df=255 p=1 (256 buckets, expect 256.0/bucket)
- bit_balance: PASS
  global ones=262144/524288 z=0.00; worst bit pos=0 z=0.00
- runs: PASS
  runs=262144 expect~262145 z=-0.00; longest_run=15
- periodicity: FAIL — autocorr lag=1024 |z|=542.5; exact byte period p=256
  worst lag=1024 z=542.53; exact_period=256; notable [lag8:z=363.5, lag16:z=364.9, lag32:z=367.7, lag64:z=373.4, lag128:z=384.7, lag256:z=407.3, lag512:z=452.4, lag1024:z=542.5]
### low 4 bits stuck at 0  [n=65536] -> FAIL
- uniformity: FAIL — p < 0.001
  chi2=983242.91 df=255 p=0 (256 buckets, expect 256.0/bucket)
- bit_balance: FAIL — global |z|=361.9
  global ones=131126/524288 z=-361.89; worst bit pos=0 z=-256.00
- runs: FAIL — runs |z|=241.3; longest run 38 > 34
  runs=131130 expect~196663 z=-241.28; longest_run=38
- periodicity: FAIL — autocorr lag=32 |z|=362.4
  worst lag=32 z=362.36; exact_period=None; notable [lag1:z=271.5, lag2:z=181.6, lag3:z=90.9, lag5:z=90.5, lag7:z=270.4, lag8:z=362.0, lag16:z=361.9, lag32:z=362.4, lag64:z=361.7, lag128:z=361.9, lag256:z=361.2, lag512:z=361.7, lag1024:z=362.0]
### short period (i % 17)  [n=65536] -> FAIL
- uniformity: FAIL — p < 0.001
  chi2=921359.06 df=255 p=0 (256 buckets, expect 256.0/bucket)
- bit_balance: FAIL — global |z|=372.7
  global ones=127215/524288 z=-372.69; worst bit pos=5 z=-256.00
- runs: PASS
  runs=192751 expect~192695 z=0.21; longest_run=19
- periodicity: FAIL — autocorr lag=32 |z|=383.3; exact byte period p=17
  worst lag=32 z=383.34; exact_period=17; notable [lag1:z=276.8, lag2:z=191.7, lag3:z=106.5, lag4:z=42.6, lag5:z=127.8, lag7:z=298.1, lag8:z=383.3, lag16:z=383.3, lag32:z=383.3, lag64:z=383.3, lag128:z=383.3, lag256:z=383.2, lag512:z=383.1, lag1024:z=382.9]
### all zero  [n=65536] -> FAIL
- uniformity: FAIL — p < 0.001
  chi2=16711680.00 df=255 p=0 (256 buckets, expect 256.0/bucket)
- bit_balance: FAIL — global |z|=724.1
  global ones=0/524288 z=-724.08; worst bit pos=0 z=-256.00
- runs: FAIL — constant bit sequence, 0 transitions
  runs=1 (constant bits: all 0)
- periodicity: FAIL — autocorr lag=8 |z|=724.1; exact byte period p=1
  worst lag=8 z=724.07; exact_period=1; notable [lag1:z=724.1, lag2:z=724.1, lag3:z=724.1, lag4:z=724.1, lag5:z=724.1, lag7:z=724.1, lag8:z=724.1, lag16:z=724.1, lag32:z=724.1, lag64:z=724.0, lag128:z=724.0, lag256:z=723.9, lag512:z=723.7, lag1024:z=723.4]
### constant 0xA5 (boundary)  [n=65536] -> FAIL
- uniformity: FAIL — p < 0.001
  chi2=16711680.00 df=255 p=0 (256 buckets, expect 256.0/bucket)
- bit_balance: FAIL — bit position 0 |z|=256.0 (fixed/stuck bit)
  global ones=262144/524288 z=0.00; worst bit pos=0 z=256.00
- runs: FAIL — runs |z|=362.0
  runs=393217 expect~262145 z=362.04; longest_run=2
- periodicity: FAIL — autocorr lag=4 |z|=724.1; exact byte period p=1
  worst lag=4 z=-724.07; exact_period=1; notable [lag1:z=-362.0, lag3:z=362.0, lag4:z=-724.1, lag5:z=362.0, lag7:z=-362.0, lag8:z=724.1, lag16:z=724.1, lag32:z=724.1, lag64:z=724.0, lag128:z=724.0, lag256:z=723.9, lag512:z=723.7, lag1024:z=723.4]
### tiny 100 bytes (boundary)  [n=100] -> SKIP
- uniformity: SKIP — n=100 < 1024
  
- bit_balance: SKIP — n=100 < 1024
  
- runs: SKIP — n=100 < 1024
  
- periodicity: SKIP — n=100 < 1024
  
### empty (boundary)  [n=0] -> SKIP
- uniformity: SKIP — n=0 < 1024
  
- bit_balance: SKIP — n=0 < 1024
  
- runs: SKIP — n=0 < 1024
  
- periodicity: SKIP — n=0 < 1024
  
