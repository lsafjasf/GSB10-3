"""证书链构建与校验。

路径方向统一为「叶子在前、根锚最后」。

关键策略：
- 只以传入的 trust_anchors 为终点；任何无法连接到信任锚的链都拒绝
  （自签但不在锚集合中同样拒绝）。
- 路径构建时枚举所有同主体候选（含交叉签名产生的多张候选证书），
  逐张验签后 DFS；最终给出全部尝试过的失败诊断。
- 校验项：签名、有效期、BasicConstraints/pathLenConstraint、KeyUsage、
  NameConstraints（DNS / directoryName / IP）、可选主机名。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .x509 import Certificate, Name, verify_signature


class Code:
    OK = "OK"
    MISSING_ISSUER = "MISSING_ISSUER"               # 找不到签发者证书
    UNTRUSTED_ROOT = "UNTRUSTED_ROOT"               # 链顶根不在信任锚中
    BAD_SIGNATURE = "BAD_SIGNATURE"                 # 签名验证失败
    UNSUPPORTED_ALGORITHM = "UNSUPPORTED_ALGORITHM"
    CERT_EXPIRED = "CERT_EXPIRED"
    CERT_NOT_YET_VALID = "CERT_NOT_YET_VALID"
    ISSUER_NOT_CA = "ISSUER_NOT_CA"                 # 颁发者无 CA:TRUE
    KEY_CERTSIGN_MISSING = "KEY_CERTSIGN_MISSING"   # KeyUsage 不含 keyCertSign
    PATHLEN_EXCEEDED = "PATHLEN_EXCEEDED"
    NAME_PERMITTED_VIOLATION = "NAME_PERMITTED_VIOLATION"
    NAME_EXCLUDED_VIOLATION = "NAME_EXCLUDED_VIOLATION"
    HOSTNAME_MISMATCH = "HOSTNAME_MISMATCH"


@dataclass
class Failure:
    code: str
    message: str
    chain: list = field(default_factory=list)  # 诊断发生时的部分链（叶子在前）


@dataclass
class ChainResult:
    ok: bool
    chain: list | None = None
    failures: list[Failure] = field(default_factory=list)

    def chain_text(self):
        if self.chain is None:
            return None
        return " -> ".join(str(c.subject) for c in self.chain)

    def failure_text(self):
        lines = []
        for f in self.failures:
            path = " -> ".join(str(c.subject) for c in f.chain) if f.chain else "-"
            lines.append(f"[{f.code}] {f.message}\n    路径: {path}")
        return "\n".join(lines)


def dns_matches(name: str, constraint: str) -> bool:
    """RFC 5280 DNS 子树匹配。

    "example.com" 匹配 example.com 及其任意下级；".example.com" 只匹配下级。
    """
    name, constraint = name.lower().rstrip("."), constraint.lower().rstrip(".")
    if not name or not constraint:
        return False
    if constraint.startswith("."):
        return name.endswith(constraint)
    return name == constraint or name.endswith("." + constraint)


def dirname_within(subject: Name, constraint: Name) -> bool:
    """directoryName 子树：subject 的 RDN 序列以 constraint 为前缀。"""
    if len(constraint.rdns) > len(subject.rdns):
        return False
    for i, rdn in enumerate(constraint.rdns):
        if rdn != subject.rdns[i]:
            return False
    return True


def ip_matches(addr: ipaddress._BaseAddress, raw_constraint: bytes) -> bool:
    """raw_constraint 为 RFC 5280 的 addr+mask 拼接（IPv4 8 字节 / IPv6 32 字节）。"""
    half = len(raw_constraint) // 2
    if len(raw_constraint) not in (8, 32):
        return False
    prefixlen = bin(int.from_bytes(raw_constraint[half:], "big")).count("1")
    net = ipaddress.ip_network((raw_constraint[:half], prefixlen), strict=False)
    return addr in net


def hostname_matches(hostname: str, cert_name: str) -> bool:
    hostname, cert_name = hostname.lower(), cert_name.lower()
    if cert_name.startswith("*."):
        suffix = cert_name[1:]  # ".example.com"
        return (hostname.endswith(suffix)
                and hostname.count(".") == suffix.count("."))
    return hostname == cert_name


class ChainValidator:
    def __init__(self, trust_anchors, intermediates=(), at_time: datetime | None = None,
                 expected_hostname: str | None = None, check_anchor_validity: bool = True):
        self.anchors = list(trust_anchors)
        self.anchors_by_name: dict[bytes, list[Certificate]] = {}
        for a in self.anchors:
            self.anchors_by_name.setdefault(a.subject.der, []).append(a)
        self.inters_by_name: dict[bytes, list[Certificate]] = {}
        for c in intermediates:
            self.inters_by_name.setdefault(c.subject.der, []).append(c)
        self.at_time = at_time or datetime.now(timezone.utc)
        if self.at_time.tzinfo is None:
            self.at_time = self.at_time.replace(tzinfo=timezone.utc)
        self.expected_hostname = expected_hostname
        self.check_anchor_validity = check_anchor_validity
        self._anchor_fps = {a.fingerprint for a in self.anchors}

    # ---------- 入口 ----------

    def validate(self, leaf: Certificate) -> ChainResult:
        failures: list[Failure] = []
        valid_chains: list[list[Certificate]] = []
        self._build([leaf], failures, valid_chains)
        if valid_chains:
            # 多条可用链时优先选锚在最前的（都同样可信），取第一条即可
            return ChainResult(True, chain=valid_chains[0], failures=failures)
        return ChainResult(False, chain=None, failures=failures)

    # ---------- 路径构建 ----------

    def _is_anchor(self, cert: Certificate) -> bool:
        return cert.fingerprint in self._anchor_fps

    def _candidates(self, cert: Certificate):
        """返回候选签发者 (cert, is_anchor)，并按 AKID/SKID 与去重过滤。"""
        result = []
        seen = set()
        for group, is_anchor in ((self.anchors_by_name.get(cert.issuer.der, []), True),
                                 (self.inters_by_name.get(cert.issuer.der, []), False)):
            for cand in group:
                if cand.fingerprint in seen:
                    continue
                if (cert.authority_key_id and cand.subject_key_id
                        and cert.authority_key_id != cand.subject_key_id):
                    continue
                seen.add(cand.fingerprint)
                result.append((cand, is_anchor))
        return result

    def _build(self, path, failures, valid_chains):
        top = path[-1]

        if self._is_anchor(top):
            self._finish(path, failures, valid_chains)
            return

        # 自签但不在信任锚里：链到此终止，根不可信
        if top.is_self_issued:
            if verify_signature(top, top):
                failures.append(Failure(
                    Code.UNTRUSTED_ROOT,
                    f"自签证书「{top.subject}」不在信任锚集合中，拒绝接受其为根",
                    list(path)))
                return

        candidates = self._candidates(top)
        if not candidates:
            failures.append(Failure(
                Code.MISSING_ISSUER,
                "缺少签发者证书：主体「{s}」由「{i}」签发，但该颁发者既不在中间证书池中，"
                "也不在信任锚集合中".format(s=top.subject, i=top.issuer),
                list(path)))
            return

        for cand, _ in candidates:
            if any(cand.fingerprint == p.fingerprint for p in path):
                continue
            if not verify_signature(top, cand):
                failures.append(Failure(
                    Code.BAD_SIGNATURE,
                    f"证书「{top.subject}」无法通过候选签发者「{cand.subject}」的签名验证",
                    list(path) + [cand]))
                continue
            self._build(path + [cand], failures, valid_chains)

    def _finish(self, path, failures, valid_chains):
        problems = self._validate_path(path)
        if problems:
            failures.extend(problems)
        else:
            valid_chains.append(list(path))

    # ---------- 链级校验 ----------

    def _validate_path(self, path) -> list[Failure]:
        problems: list[Failure] = []

        # 1) 有效期（逐张，含根锚，可配置）
        last = len(path) if self.check_anchor_validity else len(path) - 1
        for cert in path[:last]:
            if self.at_time > cert.not_after:
                problems.append(Failure(
                    Code.CERT_EXPIRED,
                    f"证书「{cert.subject}」已过期：notAfter={cert.not_after:%Y-%m-%d %H:%M:%SZ}，"
                    f"校验时刻={self.at_time:%Y-%m-%d %H:%M:%SZ}", list(path)))
            elif self.at_time < cert.not_before:
                problems.append(Failure(
                    Code.CERT_NOT_YET_VALID,
                    f"证书「{cert.subject}」尚未生效：notBefore={cert.not_before:%Y-%m-%d %H:%M:%SZ}，"
                    f"校验时刻={self.at_time:%Y-%m-%d %H:%M:%SZ}", list(path)))

        # 2) 颁发者能力：CA:TRUE、KeyUsage.keyCertSign、pathLen
        for i in range(1, len(path)):
            issuer, child = path[i], path[i - 1]
            if not issuer.is_ca:
                problems.append(Failure(
                    Code.ISSUER_NOT_CA,
                    f"颁发者「{issuer.subject}」缺少 BasicConstraints CA:TRUE，不能签发证书",
                    list(path)))
            if issuer.key_usage and "keyCertSign" not in issuer.key_usage:
                problems.append(Failure(
                    Code.KEY_CERTSIGN_MISSING,
                    f"颁发者「{issuer.subject}」的 KeyUsage 不含 keyCertSign", list(path)))
            if issuer.path_length is not None:
                # RFC 5280：其下方（更靠近叶子、不含叶子本身）的非自签发 CA 证书数
                ca_below = sum(1 for c in path[:i] if c.is_ca)
                if ca_below > issuer.path_length:
                    problems.append(Failure(
                        Code.PATHLEN_EXCEEDED,
                        f"颁发者「{issuer.subject}」pathLenConstraint={issuer.path_length}，"
                        f"但其下方有 {ca_below} 张中间 CA 证书", list(path)))

        # 3) 名称约束：每个 CA 的约束作用于其下方全部证书
        for i in range(1, len(path)):
            nc = path[i].name_constraints
            if nc is None:
                continue
            permitted, excluded = nc
            for j in range(i):
                problems.extend(self._check_name_constraints(path[j], path[i],
                                                             permitted, excluded, path))

        # 4) 主机名（仅叶子）
        if self.expected_hostname is not None:
            names = path[0].dns_names()
            if not any(hostname_matches(self.expected_hostname, n) for n in names):
                problems.append(Failure(
                    Code.HOSTNAME_MISMATCH,
                    f"叶子证书「{path[0].subject}」的名称 {names} 与期望主机名 "
                    f"{self.expected_hostname!r} 不匹配", list(path)))

        return problems

    def _check_name_constraints(self, cert, issuer_ca, permitted, excluded, path):
        out = []

        def dns_set():
            return cert.dns_names()

        for gn in excluded:
            for name in dns_set():
                for subtree in gn.dns:
                    if dns_matches(name, subtree):
                        out.append(Failure(
                            Code.NAME_EXCLUDED_VIOLATION,
                            f"证书「{cert.subject}」的 DNS 名 {name!r} 命中 CA「{issuer_ca.subject}」"
                            f"的排除子树 {subtree!r}", list(path)))
            for dn in gn.dirnames:
                if dirname_within(cert.subject, dn):
                    out.append(Failure(
                        Code.NAME_EXCLUDED_VIOLATION,
                        f"证书主体「{cert.subject}」命中 CA「{issuer_ca.subject}」的排除 "
                        f"DirName 子树「{dn}」", list(path)))
            for ip in gn.ips:
                for cert_ip in cert.san.ips:
                    if ip_matches(cert_ip, ip.packed):
                        out.append(Failure(
                            Code.NAME_EXCLUDED_VIOLATION,
                            f"证书 IP {cert_ip} 命中 CA「{issuer_ca.subject}」的排除 IP 子树",
                            list(path)))

        for gn in permitted:
            for name in dns_set():
                if gn.dns and not any(dns_matches(name, s) for s in gn.dns):
                    out.append(Failure(
                        Code.NAME_PERMITTED_VIOLATION,
                        f"证书「{cert.subject}」的 DNS 名 {name!r} 不在 CA「{issuer_ca.subject}」"
                        f"的允许子树 {gn.dns} 内", list(path)))
            if gn.dirnames and not any(dirname_within(cert.subject, d) for d in gn.dirnames):
                out.append(Failure(
                    Code.NAME_PERMITTED_VIOLATION,
                    f"证书主体「{cert.subject}」不在 CA「{issuer_ca.subject}」的允许 DirName "
                    f"子树「{gn.dirnames[0]}」内", list(path)))
            if gn.ips:
                for cert_ip in cert.san.ips:
                    if not any(ip_matches(cert_ip, ip.packed) for ip in gn.ips):
                        out.append(Failure(
                            Code.NAME_PERMITTED_VIOLATION,
                            f"证书 IP {cert_ip} 不在 CA「{issuer_ca.subject}」的允许 IP 子树内",
                            list(path)))
        return out
