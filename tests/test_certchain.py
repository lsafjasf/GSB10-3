"""证书链校验自测：覆盖正常链、交叉签名、过期、缺失、名称约束、自签、边界值等。"""

import ipaddress
import os
import unittest
from datetime import timedelta

from certchain import Certificate, ChainValidator, Code
from certchain.chain import dns_matches, dirname_within, hostname_matches, ip_matches

FX = os.path.join(os.path.dirname(__file__), "fixtures")


def cert(name):
    return Certificate.from_pem_file(os.path.join(FX, name))


class ChainValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rootA = cert("rootA.crt.pem")
        cls.rootB = cert("rootB.crt.pem")
        cls.interA = cert("interA.crt.pem")
        cls.interExp = cert("interExp.crt.pem")
        cls.interNC = cert("interNC.crt.pem")
        cls.ixA = cert("interX_by_rootA.crt.pem")
        cls.ixB = cert("interX_by_rootB.crt.pem")
        cls.interP = cert("interP.crt.pem")
        cls.subP = cert("subP.crt.pem")
        cls.leaf = cert("leaf.crt.pem")
        cls.leafX = cert("leafX.crt.pem")
        cls.leafExp = cert("leafExp.crt.pem")
        cls.leafNcOK = cert("leafNcOK.crt.pem")
        cls.leafNcEx = cert("leafNcEx.crt.pem")
        cls.leafNcNp = cert("leafNcNp.crt.pem")
        cls.leafNcDir = cert("leafNcDir.crt.pem")
        cls.leafP = cert("leafP.crt.pem")
        cls.selfsigned = cert("selfsigned.crt.pem")

    def codes(self, result):
        return {f.code for f in result.failures}

    # ---------- 正常链 ----------

    def test_01_normal_chain_ok(self):
        v = ChainValidator([self.rootA], [self.interA])
        r = v.validate(self.leaf)
        self.assertTrue(r.ok, r.failure_text())
        self.assertEqual([c.fingerprint for c in r.chain],
                         [self.leaf.fingerprint, self.interA.fingerprint,
                          self.rootA.fingerprint])

    def test_02_hostname_ok_and_mismatch(self):
        v = ChainValidator([self.rootA], [self.interA],
                           expected_hostname="www.example.com")
        self.assertTrue(v.validate(self.leaf).ok)
        bad = ChainValidator([self.rootA], [self.interA],
                             expected_hostname="evil.example.com").validate(self.leaf)
        self.assertFalse(bad.ok)
        self.assertIn(Code.HOSTNAME_MISMATCH, self.codes(bad))

    # ---------- 交叉签名：只接受指定的根 ----------

    def test_03_cross_signed_picks_configured_root(self):
        # 中间证书有 A/B 两个根的交叉签名；只信任 RootA，必须选出 RootA 路径，
        # 同时诊断中保留指向 RootB 的被拒链。
        v = ChainValidator([self.rootA], [self.ixA, self.ixB, self.rootB])
        r = v.validate(self.leafX)
        self.assertTrue(r.ok, r.failure_text())
        self.assertEqual(r.chain[1].fingerprint, self.ixA.fingerprint)
        self.assertEqual(r.chain[-1].fingerprint, self.rootA.fingerprint)
        rejected = [f for f in r.failures if f.code == Code.UNTRUSTED_ROOT]
        self.assertTrue(rejected)
        rejected_subjects = [str(c.subject) for c in rejected[0].chain]
        self.assertEqual(rejected_subjects[-1], "O=Other PKI, CN=Root B CA (untrusted)")

    def test_04_cross_signed_chain_to_untrusted_root_rejected(self):
        # 只提供 RootB 签名版本、只信任 RootA：完整链能拼出但终点是别的根，必须拒绝。
        v = ChainValidator([self.rootA], [self.ixB, self.rootB])
        r = v.validate(self.leafX)
        self.assertFalse(r.ok)
        self.assertIn(Code.UNTRUSTED_ROOT, self.codes(r))
        rejected = next(f for f in r.failures if f.code == Code.UNTRUSTED_ROOT)
        self.assertEqual([c.fingerprint for c in rejected.chain],
                         [self.leafX.fingerprint, self.ixB.fingerprint,
                          self.rootB.fingerprint])

    def test_05_trust_b_reverses_result(self):
        # 同一个叶子，信任锚换成 RootB 即应通过（走另一张交叉证书）。
        v = ChainValidator([self.rootB], [self.ixA, self.ixB])
        r = v.validate(self.leafX)
        self.assertTrue(r.ok, r.failure_text())
        self.assertEqual(r.chain[1].fingerprint, self.ixB.fingerprint)
        self.assertEqual(r.chain[-1].fingerprint, self.rootB.fingerprint)

    # ---------- 中间证书缺失：必须指明主体与颁发者 ----------

    def test_06_missing_intermediate_reports_names(self):
        r = ChainValidator([self.rootA], []).validate(self.leaf)
        self.assertFalse(r.ok)
        f = next(f for f in r.failures if f.code == Code.MISSING_ISSUER)
        self.assertIn(str(self.leaf.subject), f.message)
        self.assertIn(str(self.leaf.issuer), f.message)
        self.assertEqual(f.chain[-1].fingerprint, self.leaf.fingerprint)

    def test_07_partial_chain_then_missing(self):
        # leafX -> ixB 已提供，但 RootB 不在池/锚中：报告缺失的是 ixB 的颁发者。
        r = ChainValidator([], [self.ixB]).validate(self.leafX)
        self.assertFalse(r.ok)
        f = next(f for f in r.failures if f.code == Code.MISSING_ISSUER)
        self.assertIn("Intermediate X (cross-signed)", f.message)
        self.assertIn("Root B CA", f.message)
        self.assertEqual(len(f.chain), 2)

    # ---------- 过期 / 未生效 ----------

    def test_08_expired_intermediate_rejected(self):
        r = ChainValidator([self.rootA], [self.interExp]).validate(self.leafExp)
        self.assertFalse(r.ok)
        f = next(f for f in r.failures if f.code == Code.CERT_EXPIRED)
        self.assertIn("Intermediate Expired", f.message)
        self.assertIn("notAfter=2021-01-01", f.message)

    def test_09_validity_boundaries_leaf(self):
        base = ChainValidator([self.rootA], [self.interA])
        for delta, expect in ((timedelta(seconds=-1), Code.CERT_NOT_YET_VALID),
                              (timedelta(0), Code.OK),
                              (timedelta(days=825), Code.OK),
                              (timedelta(days=825, seconds=1), Code.CERT_EXPIRED)):
            v = ChainValidator([self.rootA], [self.interA],
                               at_time=self.leaf.not_before + delta)
            r = v.validate(self.leaf)
            if expect == Code.OK:
                self.assertTrue(r.ok, f"delta={delta}: {r.failure_text()}")
            else:
                self.assertFalse(r.ok, f"delta={delta} 应失败")
                self.assertIn(expect, self.codes(r))

    # ---------- 名称约束 ----------

    def test_10_name_constraints(self):
        v = ChainValidator([self.rootA], [self.interNC])
        self.assertTrue(v.validate(self.leafNcOK).ok)
        ex = v.validate(self.leafNcEx)
        self.assertIn(Code.NAME_EXCLUDED_VIOLATION, self.codes(ex))
        np = v.validate(self.leafNcNp)
        self.assertIn(Code.NAME_PERMITTED_VIOLATION, self.codes(np))
        dr = v.validate(self.leafNcDir)
        self.assertIn(Code.NAME_PERMITTED_VIOLATION, self.codes(dr))
        self.assertIn("Evil Corp", next(
            f.message for f in dr.failures
            if f.code == Code.NAME_PERMITTED_VIOLATION))

    def test_11_dns_subtree_matching(self):
        self.assertTrue(dns_matches("a.example.com", ".example.com"))
        self.assertFalse(dns_matches("example.com", ".example.com"))
        self.assertTrue(dns_matches("example.com", "example.com"))
        self.assertTrue(dns_matches("a.b.example.com", "example.com"))
        self.assertFalse(dns_matches("notexample.com", "example.com"))

    def test_12_ip_subtree_matching(self):
        v4 = b"\x0a\x00\x00\x00" + b"\xff\x00\x00\x00"  # 10.0.0.0/8
        self.assertTrue(ip_matches(ipaddress.ip_address("10.9.9.9"), v4))
        self.assertFalse(ip_matches(ipaddress.ip_address("11.0.0.1"), v4))
        v6 = (bytes(15) + b"\x01") + b"\xff" * 16  # ::1/128
        self.assertTrue(ip_matches(ipaddress.ip_address("::1"), v6))
        self.assertFalse(ip_matches(ipaddress.ip_address("::2"), v6))

    def test_13_wildcard_hostname(self):
        self.assertTrue(hostname_matches("a.example.com", "*.example.com"))
        self.assertFalse(hostname_matches("a.b.example.com", "*.example.com"))
        self.assertFalse(hostname_matches("example.com", "*.example.com"))

    def test_14_dirname_subtree(self):
        self.assertTrue(dirname_within(self.leafNcOK.subject,
                                       self.interNC.subject.__class__(
                                           [[("2.5.4.10", "Demo PKI")]],
                                           b"")))

    # ---------- 自签证书 ----------

    def test_15_self_signed_not_anchor_rejected(self):
        r = ChainValidator([]).validate(self.selfsigned)
        self.assertFalse(r.ok)
        self.assertIn(Code.UNTRUSTED_ROOT, self.codes(r))

    def test_16_self_signed_as_anchor_ok(self):
        r = ChainValidator([self.selfsigned]).validate(self.selfsigned)
        self.assertTrue(r.ok, r.failure_text())

    def test_17_trust_anchor_as_leaf_ok(self):
        r = ChainValidator([self.rootA]).validate(self.rootA)
        self.assertTrue(r.ok)

    # ---------- pathLenConstraint ----------

    def test_18_pathlen_exceeded(self):
        r = ChainValidator([self.rootA], [self.interP, self.subP]).validate(self.leafP)
        self.assertFalse(r.ok)
        f = next(f for f in r.failures if f.code == Code.PATHLEN_EXCEEDED)
        self.assertIn("pathLenConstraint=0", f.message)

    # ---------- 签名篡改 ----------

    def test_19_tampered_intermediate_bad_signature(self):
        der = bytearray(self.interA.der)
        # 把 not_after 年份的一位数字改掉（不破坏 DER 结构），其对 RootA 的签名即失效
        idx = der.rfind(b"203")  # not_after 在后半张证书中
        der[idx] = ord("9")
        tampered = Certificate(bytes(der))
        r = ChainValidator([self.rootA], [tampered]).validate(self.leaf)
        self.assertFalse(r.ok)
        self.assertIn(Code.BAD_SIGNATURE, self.codes(r))

    # ---------- 其他根的链，锚里没有该根 ----------

    def test_20_other_root_chain_rejected_sample(self):
        # leafX -> ixB -> rootB 是一条密码学上完全合法的链，
        # 但配置只信任 RootA：拒绝且诊断里给出完整被拒链。
        v = ChainValidator([self.rootA], [self.ixB, self.rootB])
        r = v.validate(self.leafX)
        self.assertFalse(r.ok)
        untrusted = [f for f in r.failures if f.code == Code.UNTRUSTED_ROOT]
        self.assertTrue(untrusted)
        self.assertIn("Root B CA (untrusted)", str(untrusted[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
