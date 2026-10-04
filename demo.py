"""演示：越权扩大被拒、期限收敛、撤销传播。运行：python3 demo.py"""

from delegation import DelegationRegistry, ScopeWideningError

T0 = 1_000.0
reg = DelegationRegistry(clock=lambda: T0)

# 源头：svc-auth 拥有 doc 读写和 cache 全部动作，1 小时有效
reg.grant_root("svc-auth", ["doc:read", "doc:write", "cache:*"], expires_at=T0 + 3600)

print("== 1. 正常逐级收窄 ==")
reg.delegate("root", "svc-report", ["doc:read"], expires_at=T0 + 1800, link_id="d1")
reg.delegate("d1", "svc-export", ["doc:read"], expires_at=T0 + 9999, link_id="d2")
d = reg.inspect("d2")
print(f"d2 有效作用域: {d.effective_scope.render()}")
print(f"d2 请求 9999 到期，被收敛到父链最小值: {d.effective_expires_at:g} (= T0+1800)")

print("\n== 2. 越权扩大被拒 ==")
for link_id, perms in [
    ("evil-1", ["doc:read", "doc:delete"]),   # 多出 doc:delete
    ("evil-2", ["doc:*"]),                    # 通配放大
    ("evil-3", ["billing:read"]),             # 换资源域
]:
    try:
        reg.delegate("d1", "svc-evil", perms, expires_at=T0 + 100, link_id=link_id)
        print(f"{link_id}: 意外通过！")
    except ScopeWideningError as exc:
        print(f"{link_id}: 拒绝 -> 越权权限 {list(exc.leaked)}")

print("\n== 3. 撤销传播 ==")
print(f"撤销前 d2 可用: {reg.can('d2', 'doc:read').allowed}")
reg.revoke("d1")
for link_id in ("root", "d1", "d2"):
    d = reg.inspect(link_id)
    print(f"{link_id}: allowed={d.allowed}  reason={d.reason}")
