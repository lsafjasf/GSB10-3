"""delegation 库自测（仅标准库 unittest）。

运行：python3 -m unittest -v   或   python3 test_delegation.py
"""

import unittest

from delegation import (
    DelegationError,
    DelegationRegistry,
    InvalidScopeError,
    Scope,
    ScopeWideningError,
)

T0 = 1_000.0  # 固定时钟，测试全程可控


def new_registry():
    return DelegationRegistry(clock=lambda: T0)


class SingleLevelTest(unittest.TestCase):
    """单级委托：root -> 一环。"""

    def setUp(self):
        self.reg = new_registry()
        self.reg.grant_root(
            "svc-auth", ["doc:read", "doc:write", "cache:*"], expires_at=T0 + 3600
        )

    def test_root_grant_allows_within_scope(self):
        decision = self.reg.can("root", "doc:read")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.effective_expires_at, T0 + 3600)

    def test_root_grant_denies_outside_scope(self):
        self.assertFalse(self.reg.can("root", "doc:delete").allowed)

    def test_wildcard_covers_same_segment_only(self):
        self.assertTrue(self.reg.can("root", "cache:get").allowed)
        self.assertFalse(self.reg.can("root", "cache:get/keys").allowed)

    def test_single_level_delegate_narrower_scope(self):
        self.reg.delegate(
            "root", "svc-report", ["doc:read"], expires_at=T0 + 1800, link_id="d1"
        )
        self.assertTrue(self.reg.can("d1", "doc:read").allowed)
        self.assertFalse(self.reg.can("d1", "doc:write").allowed)

    def test_single_level_delegate_equal_scope_ok(self):
        self.reg.delegate(
            "root",
            "svc-mirror",
            ["doc:read", "doc:write", "cache:*"],
            expires_at=T0 + 3600,
            link_id="d1",
        )
        self.assertTrue(self.reg.can("d1", "cache:flush").allowed)


class MultiLevelTest(unittest.TestCase):
    """多级委托：root -> a -> b -> c，逐级收窄。"""

    def setUp(self):
        self.reg = new_registry()
        self.reg.grant_root("svc-a", ["doc:*", "img:read"], expires_at=T0 + 3600)
        self.reg.delegate("root", "svc-b", ["doc:read", "doc:write"],
                          expires_at=T0 + 3000, link_id="a")
        self.reg.delegate("a", "svc-c", ["doc:read"],
                          expires_at=T0 + 2000, link_id="b")

    def test_chain_scope_is_intersection(self):
        decision = self.reg.inspect("b")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.effective_scope, Scope(["doc:read"]))

    def test_deepest_link_only_has_narrowest_scope(self):
        self.assertTrue(self.reg.can("b", "doc:read").allowed)
        self.assertFalse(self.reg.can("b", "doc:write").allowed)
        self.assertFalse(self.reg.can("b", "img:read").allowed)

    def test_middle_link_keeps_its_own_scope(self):
        self.assertTrue(self.reg.can("a", "doc:write").allowed)
        self.assertFalse(self.reg.can("a", "img:read").allowed)

    def test_actor_must_hold_parent_link(self):
        with self.assertRaises(DelegationError):
            self.reg.delegate("a", "svc-x", ["doc:read"],
                              expires_at=T0 + 100, link_id="bad", actor="svc-c")
        self.reg.delegate("a", "svc-x", ["doc:read"],
                          expires_at=T0 + 100, link_id="ok", actor="svc-b")
        self.assertTrue(self.reg.can("ok", "doc:read").allowed)


class ScopeWideningTest(unittest.TestCase):
    """越权扩大：任何一环试图放大作用域都必须被拒绝。"""

    def setUp(self):
        self.reg = new_registry()
        self.reg.grant_root("svc-a", ["doc:read"], expires_at=T0 + 3600)

    def test_adding_new_permission_rejected(self):
        with self.assertRaises(ScopeWideningError) as ctx:
            self.reg.delegate("root", "svc-b", ["doc:read", "doc:write"],
                              expires_at=T0 + 100, link_id="d1")
        self.assertEqual(ctx.exception.leaked, ("doc:write",))
        self.assertNotIn("d1", self.reg._links)  # 拒绝后不产生任何环

    def test_swapping_permission_rejected(self):
        with self.assertRaises(ScopeWideningError) as ctx:
            self.reg.delegate("root", "svc-b", ["doc:write"],
                              expires_at=T0 + 100, link_id="d1")
        self.assertEqual(ctx.exception.leaked, ("doc:write",))

    def test_widening_wildcard_rejected(self):
        with self.assertRaises(ScopeWideningError) as ctx:
            self.reg.delegate("root", "svc-b", ["doc:*"],
                              expires_at=T0 + 100, link_id="d1")
        self.assertEqual(ctx.exception.leaked, ("doc:*",))

    def test_widening_deep_in_chain_rejected(self):
        self.reg.delegate("root", "svc-b", ["doc:read"],
                          expires_at=T0 + 1800, link_id="a")
        with self.assertRaises(ScopeWideningError) as ctx:
            self.reg.delegate("a", "svc-c", ["doc:read", "img:read"],
                              expires_at=T0 + 100, link_id="b")
        self.assertEqual(ctx.exception.leaked, ("img:read",))

    def test_empty_scope_is_allowed(self):
        self.reg.delegate("root", "svc-b", [], expires_at=T0 + 100, link_id="d1")
        self.assertTrue(self.reg.inspect("d1").allowed)
        self.assertFalse(self.reg.can("d1", "doc:read").allowed)


class ExpiryConvergenceTest(unittest.TestCase):
    """期限收敛：链的有效期取链上最小值。"""

    def setUp(self):
        self.reg = new_registry()
        self.reg.grant_root("svc-a", ["doc:*"], expires_at=T0 + 3600)
        self.reg.delegate("root", "svc-b", ["doc:read"],
                          expires_at=T0 + 1200, link_id="a")  # 瓶颈
        self.reg.delegate("a", "svc-c", ["doc:read"],
                          expires_at=T0 + 3000, link_id="b")

    def test_requested_expiry_is_clipped_to_parent(self):
        link_b = self.reg.get_link("b")
        self.assertEqual(link_b.expires_at, T0 + 1200)  # 3000 被收敛到 1200

    def test_chain_effective_expiry_is_minimum(self):
        decision = self.reg.inspect("b")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.effective_expires_at, T0 + 1200)

    def test_chain_expires_at_bottleneck(self):
        self.assertTrue(self.reg.can("b", "doc:read", at=T0 + 1199).allowed)
        self.assertFalse(self.reg.can("b", "doc:read", at=T0 + 1200).allowed)
        self.assertFalse(self.reg.can("b", "doc:read", at=T0 + 3000).allowed)

    def test_root_expiry_bounds_whole_chain(self):
        self.assertFalse(self.reg.can("b", "doc:read", at=T0 + 3600).allowed)

    def test_exact_expiry_moment_is_denied(self):
        self.assertFalse(self.reg.can("root", "doc:read", at=T0 + 3600).allowed)
        self.assertTrue(self.reg.can("root", "doc:read", at=T0 + 3599).allowed)


class RevocationPropagationTest(unittest.TestCase):
    """撤销传播：撤销一环，该环及以下全部失效，兄弟分支不受影响。"""

    def setUp(self):
        self.reg = new_registry()
        self.reg.grant_root("svc-a", ["doc:*"], expires_at=T0 + 3600)
        self.reg.delegate("root", "svc-b", ["doc:read", "doc:write"],
                          expires_at=T0 + 3000, link_id="a")
        self.reg.delegate("a", "svc-c", ["doc:read"],
                          expires_at=T0 + 2000, link_id="b")
        self.reg.delegate("a", "svc-d", ["doc:write"],
                          expires_at=T0 + 2000, link_id="sibling")

    def test_revoke_middle_link_kills_subtree(self):
        self.reg.revoke("a")
        self.assertFalse(self.reg.can("a", "doc:read").allowed)
        self.assertFalse(self.reg.can("b", "doc:read").allowed)
        self.assertFalse(self.reg.can("sibling", "doc:write").allowed)

    def test_revoke_reports_broken_link(self):
        self.reg.revoke("a")
        decision = self.reg.inspect("b")
        self.assertEqual(decision.broken_link_id, "a")
        self.assertIn("撤销", decision.reason)

    def test_revoke_leaf_keeps_siblings_alive(self):
        self.reg.revoke("b")
        self.assertFalse(self.reg.can("b", "doc:read").allowed)
        self.assertTrue(self.reg.can("a", "doc:read").allowed)
        self.assertTrue(self.reg.can("sibling", "doc:write").allowed)

    def test_revoke_root_kills_everything(self):
        self.reg.revoke("root")
        for link_id in ("root", "a", "b", "sibling"):
            self.assertFalse(self.reg.inspect(link_id).allowed, link_id)

    def test_revoke_is_idempotent(self):
        self.reg.revoke("a")
        self.reg.revoke("a")  # 不抛错
        self.assertFalse(self.reg.inspect("a").allowed)


class EdgeCaseTest(unittest.TestCase):
    """边界用例。"""

    def setUp(self):
        self.reg = new_registry()

    def test_unknown_link_raises(self):
        with self.assertRaises(DelegationError):
            self.reg.can("nope", "doc:read")

    def test_duplicate_link_id_rejected(self):
        self.reg.grant_root("svc-a", ["doc:read"], expires_at=T0 + 100)
        with self.assertRaises(DelegationError):
            self.reg.grant_root("svc-b", ["doc:read"], expires_at=T0 + 100)
        with self.assertRaises(DelegationError):
            self.reg.delegate("root", "svc-b", ["doc:read"],
                              expires_at=T0 + 50, link_id="root")

    def test_expired_root_grant_rejected(self):
        with self.assertRaises(DelegationError):
            self.reg.grant_root("svc-a", ["doc:read"], expires_at=T0 - 1)

    def test_expired_delegate_rejected(self):
        self.reg.grant_root("svc-a", ["doc:read"], expires_at=T0 + 100)
        with self.assertRaises(DelegationError):
            self.reg.delegate("root", "svc-b", ["doc:read"],
                              expires_at=T0 - 1, link_id="d1")

    def test_revoke_unknown_link_raises(self):
        with self.assertRaises(DelegationError):
            self.reg.revoke("ghost")

    def test_invalid_scope_strings_rejected(self):
        for bad in ("doc", ":read", "doc:", "*:read", "doc:re*ad", "doc:**"):
            with self.assertRaises(InvalidScopeError, msg=bad):
                Scope([bad])

    def test_check_raises_permission_error(self):
        self.reg.grant_root("svc-a", ["doc:read"], expires_at=T0 + 100)
        with self.assertRaises(PermissionError):
            self.reg.check("root", "doc:write")
        self.assertTrue(self.reg.check("root", "doc:read").allowed)

    def test_scope_semantics(self):
        scope = Scope(["doc:read*"])
        self.assertTrue(scope.covers_permission("doc:read"))
        self.assertTrue(scope.covers_permission("doc:readonly"))
        self.assertFalse(scope.covers_permission("doc:read/extra"))
        self.assertTrue(Scope(["doc:read"]).is_subset_of(Scope(["doc:*"])))
        self.assertFalse(Scope(["doc:*"]).is_subset_of(Scope(["doc:read"])))
        self.assertEqual(Scope(["doc:read", "doc:read"]), Scope(["doc:read"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
