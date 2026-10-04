# 差分格式稳定性条件推导

模型方程（一维线性对流扩散，$a \ge 0$）：

$$u_t + a\,u_x = \nu\,u_{xx}$$

均匀网格 $x_i = i\,\Delta x$，时间步 $\Delta t$，记

$$c = \frac{a\Delta t}{\Delta x},\qquad r = \frac{\nu\Delta t}{\Delta x^2}$$

## 1. von Neumann 方法

对线性常系数格式，令误差（或解）的傅里叶模态为

$$\epsilon_i^n = G^n\,e^{I i\theta},\qquad \theta = k\Delta x\in[0,\pi],\quad I=\sqrt{-1}$$

代入差分格式得到**单步放大因子** $G(\theta)$。稳定的充要条件（冯·诺依曼判据）：

$$\boxed{\max_{\theta\in[0,\pi]} |G(\theta)| \le 1}$$

## 2. 显式格式的放大因子与稳定条件

### 2.1 纯对流 FTCS（时间前差 + 空间中心差）—— 恒不稳定

$$\frac{u_i^{n+1}-u_i^n}{\Delta t}+a\frac{u_{i+1}^n-u_{i-1}^n}{2\Delta x}=0
\quad\Rightarrow\quad G=1-Ic\sin\theta$$

$$|G|^2 = 1+c^2\sin^2\theta > 1\quad(\forall c>0)$$

**结论：任何 $\Delta t>0$ 都不稳定。** 中心差分对纯一阶方程没有数值耗散。

### 2.2 纯对流一阶迎风（$a>0$）—— $c\le 1$

$$u_i^{n+1}=u_i^n-c(u_i^n-u_{i-1}^n)
\quad\Rightarrow\quad G=1-c(1-e^{-I\theta})$$

$$|G|^2=1-\underbrace{2c(1-c)}_{\ge 0}(1-\cos\theta)$$

要求系数非负：$0\le c\le 1$。临界步长

$$\boxed{\Delta t_c = \frac{\Delta x}{|a|}}\qquad\text{（CFL 条件）}$$

$c=1$ 时 $G=e^{-I\theta}$，幅值恒为 1：格式每步精确平移一格。

### 2.3 纯扩散 FTCS（中心差分）—— $r\le \tfrac12$

$$u_i^{n+1}=u_i^n+r(u_{i+1}^n-2u_i^n+u_{i-1}^n)
\quad\Rightarrow\quad G=1-2r(1-\cos\theta)\in[1-4r,\,1]$$

$|G|\le1\iff -1\le1-4r$，即

$$\boxed{\Delta t_c = \frac{\Delta x^2}{2\nu}}$$

最危险模态是 $\theta=\pi$ 的棋盘格（2Δx 波长）。

### 2.4 对流（迎风）+ 扩散（中心）显式 —— $c+2r\le 1$

$$G=1-c(1-e^{-I\theta})-2r(1-\cos\theta)$$

令 $s=1-\cos\theta\in[0,2]$，直接展开：

$$|G|^2=1-2(c+2r-c^2)s+\big[(c+2r)^2-c^2\big]s^2$$

这是 $s$ 上开口向上的二次式（$r>0$ 时），最大值在端点；$s=0$ 时为 1，$s=2$ 时为 $(1-2(c+2r))^2$。故

$$\boxed{c+2r\le1
\quad\Longleftrightarrow\quad
\Delta t_c=\frac{1}{\dfrac{|a|}{\Delta x}+\dfrac{2\nu}{\Delta x^2}}}$$

物理含义：对流贡献 $|a|/\Delta x$ 与扩散贡献 $2\nu/\Delta x^2$ 对时间步的限制**取调和叠加**。

### 2.5 对流（中心）+ 扩散（中心）显式 FTCS —— $r\le\tfrac12,\ c^2\le2r$

$$G=1-2r(1-\cos\theta)-Ic\sin\theta$$

$$|G|^2=1+\big(2c^2-4r\big)(1-\cos\theta)+4r^2(1-\cos\theta)^2$$

令 $s=1-\cos\theta$：$|G|^2\le1$ 对所有 $s\in[0,2]$ 成立，当且仅当二次项根的范围覆盖 $[0,2]$，即

$$r\le\frac12,\qquad c^2\le 2r$$

$$\boxed{\Delta t_c=\min\!\left(\frac{\Delta x^2}{2\nu},\ \frac{2\nu}{a^2}\right)}$$

注意第二个条件：扩散太弱时该格式仍可能不稳定（中心对流缺耗散）；且固定 $\nu$ 时 $\Delta t\le 2\nu/a^2$ 是一个与 $\Delta x$ 无关的上限。

## 3. 隐式格式 —— 无条件稳定

### 3.1 向后 Euler（迎风对流 + 中心扩散）

$$-r\,u_{i-1}^{n+1}+(1+c+2r)u_i^{n+1}-r\,u_{i+1}^{n+1}
=c\,u_{i-1}^{n+1}+u_i^n
\quad\Rightarrow\quad
G=\frac{1}{1+c(1-e^{-I\theta})+2r(1-\cos\theta)}$$

分母实部 $=1+(c+2r)(1-\cos\theta)\ge1$，虚部 $=c\sin\theta$，故

$$|G|=\frac{1}{\sqrt{(1+(c+2r)(1-\cos\theta))^2+c^2\sin^2\theta}}\le1
\quad(\forall c,r\ge0)$$

**无条件稳定**：步长只受精度约束。每步需解三对角线性方程组（周期边界为循环三对角），本库分别用 Thomas 算法与 Sherman–Morrison 公式求解。

### 3.2 Crank–Nicolson（纯扩散）

$$G=\frac{1-r(1-\cos\theta)}{1+r(1-\cos\theta)},\qquad |G|\le1\;\;\forall r\ge0$$

同样无条件稳定，时间精度为二阶。

## 4. 临界步长汇总

| 格式 | 放大因子 $G(\theta)$ | 稳定条件 | $\Delta t_c$ |
|---|---|---|---|
| 对流 FTCS 中心 | $1-Ic\sin\theta$ | 恒不稳定 | — |
| 对流一阶迎风 | $1-c(1-e^{-I\theta})$ | $c\le1$ | $\Delta x/\lvert a\rvert$ |
| 扩散 FTCS | $1-2r(1-\cos\theta)$ | $r\le\frac12$ | $\Delta x^2/(2\nu)$ |
| 迎风对流+中心扩散 | 上两式之和 | $c+2r\le1$ | $1/(\lvert a\rvert/\Delta x+2\nu/\Delta x^2)$ |
| 中心对流+中心扩散 | $1-2r(1-\cos\theta)-Ic\sin\theta$ | $r\le\frac12,\ c^2\le2r$ | $\min(\Delta x^2/(2\nu),\,2\nu/a^2)$ |
| 隐式 BE 迎风+扩散 | 上式分母倒数 | 无条件 | — |
| Crank–Nicolson 扩散 | 有理式 | 无条件 | — |

## 5. 数值验证方法

1. **放大因子扫描**：在 $\theta\in[0,\pi]$ 上数值求 $\max|G|$，比较 $0.99\Delta t_c$（应 $\le1$）与 $1.01\Delta t_c$（应 $>1$）。
2. **临界点二分**：对 $\max|G|=1$ 关于 $\Delta t$ 二分求根，与解析公式核对。
3. **扰动增长实验**：在初值中注入幅度 $\varepsilon=10^{-6}$ 的最危险傅里叶模态（$\theta^*=\pi$，FTCS 对流为 $\pi/2$），推进 $n$ 步后度量放大率，应与 $|G(\theta^*)|^n$ 完全一致；$\Delta t<\Delta t_c$ 时衰减，$\Delta t>\Delta t_c$ 时指数发散。
4. **物理解误差**：周期边界下与精确解 $\sin(k(x-at))e^{-\nu k^2t}$ 对比 L2 误差。
5. **边界一致性**：Dirichlet 入流、出流零梯度、Neumann 零通量下重复实验，确认内部格式的临界步长不依赖边界类型（见实验 4d）。

> 说明：$\theta=\pi$ 时三个显式格式的 $|G|$ 都等于 $|1-2\rho|$（$\rho=\Delta t/\Delta t_c$），所以实验中它们在临界点附近的扰动放大率数字相同——这是最危险模态上不同判据退化一致的体现。
