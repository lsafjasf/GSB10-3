# 状态转移样例

由 `python3 examples/demo.py` 自动生成；时间与对端行为均为注入的确定值。

## 场景一：正常投递（明文，EHLO 多行能力通告）

CONNECT→EHLO→MAIL→RCPT→DATA→CONTENT→QUIT→DONE，一次成功。

| 尝试 | 阶段 | 对端应答 | 备注 |
| --- | --- | --- | --- |
| 1 | CONNECT |  |  |
| 1 | CONNECT | 220 mx.example.net ESMTP ready |  |
| 1 | EHLO |  |  |
| 1 | EHLO | 250 mx.example.net / SIZE 10485760 / 8BITMIME |  |
| 1 | MAIL |  |  |
| 1 | MAIL | 250 2.1.0 sender ok [增强码 2.1.0] |  |
| 1 | RCPT |  |  |
| 1 | RCPT | 250 2.1.5 recipient ok [增强码 2.1.5] |  |
| 1 | DATA |  |  |
| 1 | DATA | 354 end data with <CR><LF>.<CR><LF> |  |
| 1 | CONTENT |  |  |
| 1 | CONTENT | 250 2.0.0 queued as 9F3A [增强码 2.0.0] |  |
| 1 | QUIT |  |  |
| 1 | QUIT | 221 2.0.0 bye [增强码 2.0.0] |  |
| 1 | DONE |  |  |

结果：成功；尝试 1 次；总耗时 0s；连接释放：True

## 场景二：会话中途被要求换用加密通道（530 → STARTTLS）

PREFER 策略下首次 STARTTLS 被 454 临时拒绝，退回明文；MAIL FROM 收到 530「必须先 STARTTLS」后，状态机回退到 STARTTLS 状态重新升级，TLS 后重新 EHLO，最终投递成功。

| 尝试 | 阶段 | 对端应答 | 备注 |
| --- | --- | --- | --- |
| 1 | CONNECT |  |  |
| 1 | CONNECT | 220 mx.example.net ESMTP ready |  |
| 1 | EHLO |  |  |
| 1 | EHLO | 250 mx.example.net / STARTTLS |  |
| 1 | STARTTLS |  |  |
| 1 | STARTTLS | 454 4.7.0 TLS temporarily unavailable [增强码 4.7.0] |  |
| 1 | STARTTLS |  | STARTTLS 被拒（454 4.7.0 TLS temporarily unavailable [增强码 4.7.0]），按 prefer 策略退回明文 |
| 1 | MAIL |  |  |
| 1 | MAIL | 530 5.7.0 Must issue a STARTTLS command first [增强码 5.7.0] |  |
| 1 | MAIL |  | 对端要求加密（530），在会话中途升级通道 |
| 1 | STARTTLS |  |  |
| 1 | STARTTLS | 220 2.0.0 ready to start TLS [增强码 2.0.0] |  |
| 1 | STARTTLS |  | 通道已升级为 TLS |
| 1 | EHLO |  |  |
| 1 | EHLO | 250 mx.example.net / STARTTLS |  |
| 1 | MAIL |  |  |
| 1 | MAIL | 250 2.1.0 sender ok [增强码 2.1.0] |  |
| 1 | RCPT |  |  |
| 1 | RCPT | 250 2.1.5 recipient ok [增强码 2.1.5] |  |
| 1 | DATA |  |  |
| 1 | DATA | 354 end data with <CR><LF>.<CR><LF> |  |
| 1 | CONTENT |  |  |
| 1 | CONTENT | 250 2.0.0 queued as 9F3A [增强码 2.0.0] |  |
| 1 | QUIT |  |  |
| 1 | QUIT | 221 2.0.0 bye [增强码 2.0.0] |  |
| 1 | DONE |  |  |

结果：成功；尝试 1 次；总耗时 0s；连接释放：True

## 场景三：临时拒绝（450 灰名单）后退避重试成功

第一次尝试在 RCPT 阶段被 450 临时拒绝，按退避策略等待 30s 后重新建连，第二次尝试完整走通。

| 尝试 | 阶段 | 对端应答 | 备注 |
| --- | --- | --- | --- |
| 1 | CONNECT |  |  |
| 1 | CONNECT | 220 mx.example.net ESMTP ready |  |
| 1 | EHLO |  |  |
| 1 | EHLO | 250 mx.example.net / SIZE 10485760 |  |
| 1 | MAIL |  |  |
| 1 | MAIL | 250 2.1.0 sender ok [增强码 2.1.0] |  |
| 1 | RCPT |  |  |
| 1 | RCPT | 450 4.7.1 greylisted, try again later [增强码 4.7.1] |  |
| 1 | ABORTED |  | 会话中止: 收件人 <bob@example.net> 被临时拒绝: 450 4.7.1 greylisted, try again later [增强码 4.7.1] |
| 2 | CONNECT |  |  |
| 2 | CONNECT | 220 mx.example.net ESMTP ready |  |
| 2 | EHLO |  |  |
| 2 | EHLO | 250 mx.example.net / SIZE 10485760 |  |
| 2 | MAIL |  |  |
| 2 | MAIL | 250 2.1.0 sender ok [增强码 2.1.0] |  |
| 2 | RCPT |  |  |
| 2 | RCPT | 250 2.1.5 recipient ok [增强码 2.1.5] |  |
| 2 | DATA |  |  |
| 2 | DATA | 354 end data with <CR><LF>.<CR><LF> |  |
| 2 | CONTENT |  |  |
| 2 | CONTENT | 250 2.0.0 queued as 9F3A [增强码 2.0.0] |  |
| 2 | QUIT |  |  |
| 2 | QUIT | 221 2.0.0 bye [增强码 2.0.0] |  |
| 2 | DONE |  |  |

结果：成功；尝试 2 次；总耗时 30s；连接释放：True

## 场景四：永久拒绝（550 5.1.1 用户不存在）立刻中止

RCPT 阶段收到 550 5.1.1，属于永久性拒绝：不等待、不重试，立即中止并给出应答码与增强码作为依据。

| 尝试 | 阶段 | 对端应答 | 备注 |
| --- | --- | --- | --- |
| 1 | CONNECT |  |  |
| 1 | CONNECT | 220 mx.example.net ESMTP ready |  |
| 1 | EHLO |  |  |
| 1 | EHLO | 250 mx.example.net / SIZE 10485760 |  |
| 1 | MAIL |  |  |
| 1 | MAIL | 250 2.1.0 sender ok [增强码 2.1.0] |  |
| 1 | RCPT |  |  |
| 1 | RCPT | 550 5.1.1 no such user here [增强码 5.1.1] |  |
| 1 | ABORTED |  | 会话中止: 收件人 <bob@example.net> 被永久拒绝，立刻中止: 550 5.1.1 no such user here [增强码 5.1.1] |

结果：失败；尝试 1 次；总耗时 0s；连接释放：True；最终错误：永久性拒绝，立刻中止且不再重试: 收件人 <bob@example.net> 被永久拒绝，立刻中止: 550 5.1.1 no such user here [增强码 5.1.1]

## 场景五：对端卡顿触发会话超时与整体超时

单次会话预算 10s、整体预算 25s。对端每次应答都卡顿：第一次会话超时（t=12s），退避 8s 后第二次会话被整体预算截短（t=25s 处超时），预算耗尽后停止重试，连接全部释放。

| 尝试 | 阶段 | 对端应答 | 备注 |
| --- | --- | --- | --- |
| 1 | CONNECT |  |  |
| 1 | CONNECT | 220 mx.example.net |  |
| 1 | EHLO |  |  |
| 1 | ABORTED |  | 会话中止: 等待对端应答超时 |
| 2 | CONNECT |  |  |
| 2 | CONNECT | 220 mx.example.net |  |
| 2 | EHLO |  |  |
| 2 | ABORTED |  | 会话中止: 等待对端应答超时 |

结果：失败；尝试 3 次；总耗时 44s；连接释放：True；最终错误：整体超时，投递未完成（会话资源均已释放）
