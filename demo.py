"""演示：正常兑换 + 各类拒绝场景，打印拒绝原因样例。"""

from pkce import (
    AuthorizationCodeStore,
    begin_authorization,
    compute_challenge,
    generate_code_verifier,
)

clock = [1_000_000.0]
store = AuthorizationCodeStore(ttl=60, clock=lambda: clock[0])


def show(title, result):
    status = "允许" if result.ok else "拒绝"
    print(f"[{status}] {title}")
    print(f"       reason={result.reason}  message={result.message}")


# 1. 正常流程
code, verifier = begin_authorization(store, "S256")
show("正常兑换（S256）", store.exchange(code, verifier))

# 2. 重复兑换（一次性断言）
show("重复兑换同一个授权码", store.exchange(code, verifier))

# 3. 校验值被改动
code2, verifier2 = begin_authorization(store, "S256")
tampered = verifier2[:-1] + ("A" if verifier2[-1] != "A" else "B")
show("code_verifier 被改动一个字符", store.exchange(code2, tampered))

# 4. 缺失校验值
code3, _ = begin_authorization(store, "S256")
show("未携带 code_verifier", store.exchange(code3, None))

# 5. 授权码过期
code4, verifier4 = begin_authorization(store, "S256")
clock[0] += 61
show("授权码过期后兑换", store.exchange(code4, verifier4))

# 6. 算法不一致（授权用 plain，兑换时服务端只接受 S256）
store2 = AuthorizationCodeStore(ttl=60, clock=lambda: clock[0])
code5, verifier5 = begin_authorization(store2, "plain")
store2.allowed_methods = ("S256",)
show("challenge 算法不在允许列表", store2.exchange(code5, verifier5))

# 7. 授权码不存在
show("不存在的授权码", store.exchange("forged-code", generate_code_verifier()))

print("\n--- 被拒记录（审计日志） ---")
for rec in store.rejections + store2.rejections:
    print(f"reason={rec.reason:<28} code={rec.code_prefix}...  detail={rec.detail}")
