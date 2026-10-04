"""权限委托与作用域检查 —— 自测（python3 -m unittest -v）。

覆盖：单级委托、多级委托、越权扩大、有效期收敛、中途撤销传播、边界用例。
"""

import unittest

from delegation import (
    DelegationAuthority,
    Scope,
    ScopeExpansionError,
    RevokedError,
    ExpiredError,
    UnknownPrincipalError,
    PermissionDeniedError,
)


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class SingleLevelDelegationTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.auth = DelegationAuthority(clock=self.clock)
        self.auth.anchor(
            "gateway",
            Scope("read:*", "write:*"),
            ttl=100,
        )

    def test_subset_delegation_succeeds(self):
        edge = self.auth.delegate("gateway", "billing", Scope("read:doc"), ttl=50)
        self.assertEqual(edge.delegatee, "billing")
        self.assertTrue(self.auth.can_access("billing", Scope("read:doc")))
        self.assertFalse(self.auth.can_access("billing", Scope("write:doc")))

    def test_equal_scope_is_not_expansion(self):
        same = Scope("read:*", "write:*")
        self.auth.delegate("gateway", "clone", same, ttl=10)
        self.assertTrue(self.auth.can_access("clone", Scope("read:doc")))
        self.assertTrue(self.auth.can_access("clone", Scope("write:doc")))

    def test_check_returns_resolution(self):
        self.auth.delegate("gateway", "billing", Scope("read:*"), ttl=50)
        resolution = self.auth.check("billing", Scope("read:doc"))
        self.assertEqual(resolution.principal, "billing")
        self.assertTrue(resolution.effective_scope.covers(Scope("read:doc")))

    def test_root_still_has_full_scope(self):
        self.auth.delegate("gateway", "billing", Scope("read:doc"), ttl=50)
        self.assertTrue(self.auth.can_access("gateway", Scope("write:doc")))

    def test_denied_delegation_sample(self):
        """被拒的委托样例：billing 只有 read:doc，却想下发 write:*。"""
        self.auth.delegate("gateway", "billing", Scope("read:doc"), ttl=50)
        with self.assertRaises(ScopeExpansionError):
            self.auth.delegate("billing", "evil", Scope("write:*"), ttl=10)
        with self.assertRaises(ScopeExpansionError):
            self.auth.delegate(
                "billing", "evil2", Scope("read:doc", "read:sheet"), ttl=10
            )
        self.assertNotIn("evil", self.auth.resolve.__self__._by_delegatee)


class MultiLevelDelegationTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.auth = DelegationAuthority(clock=self.clock)
        self.auth.anchor("A", Scope("read:*", "write:*"), ttl=1000)

    def test_chain_narrows_at_each_hop(self):
        # A(read:*,write:*) -> B(read:*) -> C(read:doc)
        self.auth.delegate("A", "B", Scope("read:*"), ttl=500)
        self.auth.delegate("B", "C", Scope("read:doc"), ttl=300)

        self.assertTrue(self.auth.can_access("C", Scope("read:doc")))
        # C 只有 read:doc，read:sheet 已在 B->C 这环被收窄掉
        self.assertFalse(self.auth.can_access("C", Scope("read:sheet")))
        # write 早在 A->B 环就被去掉
        self.assertFalse(self.auth.can_access("C", Scope("write:doc")))
        # 中间环 B 保留 read:*
        self.assertTrue(self.auth.can_access("B", Scope("read:sheet")))
        self.assertFalse(self.auth.can_access("B", Scope("write:doc")))

    def test_chain_path_recorded(self):
        ab = self.auth.delegate("A", "B", Scope("read:*"), ttl=500)
        bc = self.auth.delegate("B", "C", Scope("read:doc"), ttl=300)
        resolution = self.auth.resolve("C")
        self.assertEqual(resolution.path, (ab.id, bc.id))

    def test_expansion_at_later_hop_rejected(self):
        self.auth.delegate("A", "B", Scope("read:*"), ttl=500)
        with self.assertRaises(ScopeExpansionError):
            self.auth.delegate("B", "C", Scope("read:*", "write:*"), ttl=100)


class ExpiryConvergenceTest(unittest.TestCase):
    """作用域/期限收敛断言：链上有效期始终取最小值。"""

    def setUp(self):
        self.clock = FakeClock()
        self.auth = DelegationAuthority(clock=self.clock)
        self.auth.anchor("A", Scope("*"), ttl=100)  # A 到 1100

    def test_requested_ttl_clamped_to_parent_expiry(self):
        edge_b = self.auth.delegate("A", "B", Scope("read:*"), ttl=1000)
        # 请求 1000s，但 A 在 100s 后到期 -> 收敛到 A 的到期时刻
        self.assertAlmostEqual(edge_b.expires_at, 1100.0)

        edge_c = self.auth.delegate("B", "C", Scope("read:doc"), ttl=1000)
        self.assertAlmostEqual(edge_c.expires_at, 1100.0)

    def test_minimum_expiry_anywhere_on_chain_wins(self):
        self.auth.delegate("A", "B", Scope("read:*"), ttl=40)   # B 到 1040
        edge_c = self.auth.delegate("B", "C", Scope("read:doc"), ttl=1000)
        # 链上最小值是 B 的 1040，而不是 A 的 1100
        self.assertAlmostEqual(edge_c.expires_at, 1040.0)
        self.assertAlmostEqual(self.auth.resolve("C").expires_at, 1040.0)

    def test_shorter_ttl_dominates_downstream(self):
        self.auth.delegate("A", "B", Scope("read:*"), ttl=80)
        edge_c = self.auth.delegate("B", "C", Scope("read:doc"), ttl=10)
        self.assertAlmostEqual(edge_c.expires_at, 1010.0)
        edge_d = self.auth.delegate("C", "D", Scope("read:doc"), ttl=1000)
        self.assertAlmostEqual(edge_d.expires_at, 1010.0)

    def test_chain_expires_at_minimum_time(self):
        self.auth.delegate("A", "B", Scope("read:*"), ttl=40)
        self.auth.delegate("B", "C", Scope("read:doc"), ttl=40)
        self.assertTrue(self.auth.can_access("C", Scope("read:doc")))

        self.clock.advance(41)  # 超过 B/C 的 1040，A(1100) 仍有效
        with self.assertRaises(ExpiredError):
            self.auth.resolve("C")
        with self.assertRaises(ExpiredError):
            self.auth.delegate("C", "D", Scope("read:doc"), ttl=5)
        # 上游 A、B 中 A 仍有效；B 也已过期
        self.assertTrue(self.auth.can_access("A", Scope("read:doc")))

    def test_non_expiring_root_allows_finite_delegation(self):
        auth = DelegationAuthority(clock=self.clock)
        auth.anchor("root", Scope("*"))  # 无期限
        edge = auth.delegate("root", "svc", Scope("read:*"), ttl=30)
        self.assertAlmostEqual(edge.expires_at, 1030.0)

    def test_non_positive_ttl_rejected(self):
        from delegation import DelegationError
        with self.assertRaises(DelegationError):
            self.auth.delegate("A", "B", Scope("read:*"), ttl=0)
        with self.assertRaises(DelegationError):
            self.auth.delegate("A", "B", Scope("read:*"), ttl=-5)


class RevocationPropagationTest(unittest.TestCase):
    """撤销传播：撤销某一环后，该环以下全部失效。"""

    def setUp(self):
        self.clock = FakeClock()
        self.auth = DelegationAuthority(clock=self.clock)
        self.auth.anchor("A", Scope("*"), ttl=1000)
        self.ab = self.auth.delegate("A", "B", Scope("read:*"), ttl=500)
        self.bc = self.auth.delegate("B", "C", Scope("read:doc"), ttl=300)
        self.cd = self.auth.delegate("C", "D", Scope("read:doc"), ttl=200)

    def test_revoke_middle_kills_downstream_only(self):
        self.auth.revoke(self.bc.id)  # 撤销 B->C

        with self.assertRaises(RevokedError):
            self.auth.resolve("C")
        with self.assertRaises(RevokedError):
            self.auth.resolve("D")
        self.assertFalse(self.auth.can_access("C", Scope("read:doc")))
        self.assertFalse(self.auth.can_access("D", Scope("read:doc")))

        # 被撤环节之上不受影响
        self.assertTrue(self.auth.can_access("A", Scope("write:doc")))
        self.assertTrue(self.auth.can_access("B", Scope("read:doc")))

    def test_revoked_node_cannot_delegate_further(self):
        self.auth.revoke(self.bc.id)
        with self.assertRaises(RevokedError):
            self.auth.delegate("C", "E", Scope("read:doc"), ttl=10)

    def test_revoke_leaf_only(self):
        self.auth.revoke(self.cd.id)  # 只撤销 C->D
        self.assertFalse(self.auth.can_access("D", Scope("read:doc")))
        self.assertTrue(self.auth.can_access("C", Scope("read:doc")))
        self.assertTrue(self.auth.can_access("B", Scope("read:doc")))

    def test_revoke_root_edge_disables_entire_chain(self):
        self.auth.revoke(self.ab.id)
        for principal in ("B", "C", "D"):
            with self.assertRaises(RevokedError):
                self.auth.resolve(principal)
        self.assertTrue(self.auth.can_access("A", Scope("read:doc")))

    def test_revoke_unknown_edge(self):
        with self.assertRaises(UnknownPrincipalError):
            self.auth.revoke("nope")


class BoundaryCaseTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.auth = DelegationAuthority(clock=self.clock)

    def test_wildcard_scope_matching(self):
        self.auth.anchor("A", Scope("*"))
        self.assertTrue(self.auth.can_access("A", Scope("read:doc")))
        self.assertTrue(self.auth.can_access("A", Scope("anything:at:all")))

        self.auth.delegate("A", "B", Scope("read:*"), ttl=100)
        self.assertTrue(self.auth.can_access("B", Scope("read:doc")))
        self.assertTrue(self.auth.can_access("B", Scope("read:doc:page1")))
        self.assertFalse(self.auth.can_access("B", Scope("write:doc")))
        self.assertFalse(self.auth.can_access("B", Scope("*")))

    def test_empty_scope_is_subset_of_everything(self):
        self.auth.anchor("A", Scope("read:*"))
        self.auth.delegate("A", "B", Scope(), ttl=100)
        # 空作用域访问任何具体动作都被拒绝
        self.assertFalse(self.auth.can_access("B", Scope("read:doc")))
        self.assertEqual(self.auth.resolve("B").effective_scope, Scope())

    def test_unknown_principal(self):
        with self.assertRaises(UnknownPrincipalError):
            self.auth.resolve("ghost")
        with self.assertRaises(UnknownPrincipalError):
            self.auth.delegate("ghost", "B", Scope("read:*"), ttl=100)

    def test_blank_token_rejected(self):
        with self.assertRaises(ValueError):
            Scope("read:doc", "  ")

    def test_permission_denied_error_type(self):
        self.auth.anchor("A", Scope("read:doc"))
        with self.assertRaises(PermissionDeniedError):
            self.auth.check("A", Scope("write:doc"))

    def test_delegation_from_expired_principal_rejected(self):
        self.auth.anchor("A", Scope("read:*"), ttl=10)
        self.clock.advance(11)
        with self.assertRaises(ExpiredError):
            self.auth.delegate("A", "B", Scope("read:doc"), ttl=5)

    def test_wildcard_prefix_is_not_suffix(self):
        self.auth.anchor("A", Scope("read:*"))
        # 前缀匹配不能误判为后缀/包含匹配
        self.assertFalse(self.auth.can_access("A", Scope("prefix:read:doc")))

    def test_revoke_is_idempotent(self):
        self.auth.anchor("A", Scope("*"))
        edge = self.auth.delegate("A", "B", Scope("read:*"), ttl=100)
        self.auth.revoke(edge.id)
        self.auth.revoke(edge.id)  # 重复撤销不报错
        self.assertFalse(self.auth.can_access("B", Scope("read:doc")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
