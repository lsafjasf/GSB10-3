"""篡改定位样例：四种典型破坏场景 + 区间校验演示。

运行：python3 demo.py
"""

import copy

import audit_chain
from audit_chain import AuditLog


def fresh_log(n=200):
    log = AuditLog()
    for i in range(n):
        log.append({"op": "transfer", "from": "a%03d" % i, "amount": i * 3})
    return log


def show(title, result):
    print(f"[{title}]")
    print(f"  校验结果: {result}")
    if not result.ok:
        print(f"  定位: 首个无法验证的记录下标 = {result.first_mismatch}")
    print()


def main():
    # 0. 正常日志：全量 + 区间校验
    log = fresh_log()
    trusted = log.commitment()  # 可信锚点，应安全保存（可签名）
    print(f"可信承诺: {trusted.to_json()}\n")
    show("场景0 未篡改：全量校验", log.verify_full(trusted))
    show("场景0 未篡改：区间校验 [50, 60)", log.verify_range(50, 60, trusted))

    # 1. 单条记录被改
    log1 = fresh_log()
    log1._envs[77]["record"]["amount"] = 999999
    show("场景1 第77条被改", log1.verify_full(trusted))
    show("场景1 区间[70,80)复查", log1.verify_range(70, 80, trusted))
    show("场景1 区间[100,150)不含篡改点", log1.verify_range(100, 150, trusted))

    # 2. 连续多条被改
    log2 = fresh_log()
    for i in range(30, 35):
        log2._envs[i]["record"]["amount"] = 0
    show("场景2 第30~34条连续被改", log2.verify_full(trusted))

    # 3. 末尾删除
    log3 = fresh_log()
    del log3._envs[190:]
    del log3._chain[190:]
    show("场景3 末尾10条被删", log3.verify_full(trusted))

    # 4. 中间删除
    log4 = fresh_log()
    del log4._envs[88]
    del log4._chain[88]
    show("场景4 第88条被删", log4.verify_full(trusted))

    # 5. 区间校验不重算全链（哈希计数对比）
    big = fresh_log(10000)
    big_c = big.commitment()
    real_hash = audit_chain._hash
    calls = {"n": 0}

    def counting(data):
        calls["n"] += 1
        return real_hash(data)

    audit_chain._hash = counting
    try:
        calls["n"] = 0
        assert big.verify_range(5000, 5010, big_c).ok
        range_cost = calls["n"]
        calls["n"] = 0
        assert big.verify_full(big_c).ok
        full_cost = calls["n"]
    finally:
        audit_chain._hash = real_hash
    print("[场景5 区间校验代价] 10000 条日志中校验 10 条区间")
    print(f"  区间校验哈希次数: {range_cost}   全量校验哈希次数: {full_cost}")
    print(f"  区间校验仅为全量的 {range_cost / full_cost:.2%}，无需重算全链")


if __name__ == "__main__":
    main()
