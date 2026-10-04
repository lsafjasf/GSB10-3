"""X.509 证书解析（纯标准库）与 RSA PKCS#1 v1.5 验签。

支持范围：
- RSA 公钥（rsaEncryption），签名算法 sha1/sha256/sha384/sha512WithRSAEncryption
- 扩展：BasicConstraints、KeyUsage、SAN、NameConstraints、SKID、AKID
- 时间：UTCTime / GeneralizedTime
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import asn1

OID_RSA_ENCRYPTION = "1.2.840.113549.1.1.1"
SIG_ALGORITHMS = {
    "1.2.840.113549.1.1.5": "sha1",
    "1.2.840.113549.1.1.11": "sha256",
    "1.2.840.113549.1.1.12": "sha384",
    "1.2.840.113549.1.1.13": "sha512",
}
OID_BASIC_CONSTRAINTS = "2.5.29.19"
OID_KEY_USAGE = "2.5.29.15"
OID_SUBJECT_ALT_NAME = "2.5.29.17"
OID_NAME_CONSTRAINTS = "2.5.29.30"
OID_SUBJECT_KEY_ID = "2.5.29.14"
OID_AUTHORITY_KEY_ID = "2.5.29.35"

ATTR_SHORT_NAMES = {
    "2.5.4.3": "CN",
    "2.5.4.6": "C",
    "2.5.4.7": "L",
    "2.5.4.8": "ST",
    "2.5.4.10": "O",
    "2.5.4.11": "OU",
    "0.9.2342.19200300.100.1.25": "DC",
    "1.2.840.113549.1.9.1": "emailAddress",
}

# DigestInfo 前缀（RFC 8017），用于 PKCS#1 v1.5 验签
_DIGEST_INFO_PREFIX = {
    "sha1": bytes.fromhex("3021300906052b0e03021a05000414"),
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}


class CertificateError(ValueError):
    """证书解析失败。"""


class Name:
    """X.501 Distinguished Name。rdns 按 DER 顺序（通常 C 在前、CN 在后）。"""

    def __init__(self, rdns, der: bytes):
        # rdns: list[list[(oid, value)]]
        self.rdns = rdns
        self.der = der

    @classmethod
    def from_node(cls, node) -> "Name":
        rdns = []
        for rdn in node.children or []:
            avas = []
            for ava in rdn.children or []:
                parts = asn1.seq_of(ava)
                oid = asn1.decode_oid(parts[0].content)
                avas.append((oid, asn1.decode_string(parts[1])))
            rdns.append(avas)
        return cls(rdns, node.raw)

    def get(self, short: str):
        for rdn in self.rdns:
            for oid, value in rdn:
                if ATTR_SHORT_NAMES.get(oid) == short:
                    return value
        return None

    def __eq__(self, other):
        return isinstance(other, Name) and self.der == other.der

    def __hash__(self):
        return hash(self.der)

    def __str__(self):
        parts = []
        for rdn in self.rdns:
            for oid, value in rdn:
                parts.append(f"{ATTR_SHORT_NAMES.get(oid, oid)}={value}")
        return ", ".join(parts)


@dataclass
class GeneralNames:
    dns: list = field(default_factory=list)
    ips: list = field(default_factory=list)
    emails: list = field(default_factory=list)
    dirnames: list = field(default_factory=list)  # list[Name]


def _parse_general_names(node) -> GeneralNames:
    names = GeneralNames()
    for gn in asn1.seq_of(node):
        if gn.tag_class != asn1.CLASS_CONTEXT:
            continue
        if gn.tag == 2:  # dNSName
            names.dns.append(gn.content.decode("ascii"))
        elif gn.tag == 1:  # rfc822Name
            names.emails.append(gn.content.decode("ascii"))
        elif gn.tag == 7:  # iPAddress
            names.ips.append(ipaddress.ip_address(gn.content))
        elif gn.tag == 4:  # directoryName（显式）
            inner = gn.children[0] if gn.children else asn1.parse(gn.content)[0]
            names.dirnames.append(Name.from_node(inner))
    return names


def _parse_general_subtrees(node):
    """GeneralSubtrees -> [(GeneralNames,)]，忽略 minimum/maximum（实践中恒为 0/缺省）。"""
    subtrees = []
    for st in asn1.seq_of(node):
        base = st.children[0]
        wrapper = asn1.Node(asn1.CLASS_UNIVERSAL, True, 0x10, base.raw, base.raw,
                            [base])
        subtrees.append(_parse_general_names(wrapper))
    return subtrees


def _parse_time(node) -> datetime:
    text = node.content.decode("ascii")
    if node.tag == asn1.TAG_UTC_TIME:
        dt = datetime.strptime(text, "%y%m%d%H%M%SZ")
        year = dt.year
        year += 100 if year < 1950 else 0  # 50-49 规则
        return dt.replace(year=year, tzinfo=timezone.utc)
    if node.tag == asn1.TAG_GENERALIZED_TIME:
        return datetime.strptime(text, "%Y%m%d%H%M%SZ").replace(tzinfo=timezone.utc)
    raise CertificateError(f"未知时间类型 tag={node.tag}")


class Certificate:
    def __init__(self, der: bytes):
        self.der = der
        try:
            top, end = asn1.parse(der)
            if end != len(der):
                raise CertificateError("证书 DER 后有多余字节")
            tbs, sig_alg, sig_value = asn1.seq_of(top)
            self.tbs_der = tbs.raw
            self.signature_algorithm = asn1.decode_oid(
                asn1.seq_of(sig_alg)[0].content)
            self.signature = asn1.decode_bitstring(sig_value)
            self._parse_tbs(tbs)
        except CertificateError:
            raise
        except (asn1.ASN1Error, IndexError, ValueError) as exc:
            raise CertificateError(f"证书解析失败: {exc}") from exc

    # ---------- 解析 ----------

    def _parse_tbs(self, tbs):
        fields = asn1.seq_of(tbs)
        idx = 0
        self.version = 1
        if (fields[0].tag_class == asn1.CLASS_CONTEXT and fields[0].tag == 0):
            self.version = asn1.decode_int(asn1.context(fields[0], 0)) + 1
            idx = 1
        self.serial = asn1.decode_int(fields[idx]); idx += 1
        idx += 1  # tbs 内的 signature AlgorithmIdentifier，与外层一致，跳过
        self.issuer = Name.from_node(fields[idx]); idx += 1
        validity = asn1.seq_of(fields[idx]); idx += 1
        self.not_before = _parse_time(validity[0])
        self.not_after = _parse_time(validity[1])
        self.subject = Name.from_node(fields[idx]); idx += 1
        self._parse_spki(fields[idx]); idx += 1
        self.extensions = {}
        self.extension_critical = {}
        while idx < len(fields):
            f = fields[idx]
            if f.tag_class == asn1.CLASS_CONTEXT and f.tag == 3:
                for ext in asn1.seq_of(asn1.context(f, 3)):
                    parts = asn1.seq_of(ext)
                    oid = asn1.decode_oid(parts[0].content)
                    critical = False
                    value_node = parts[1]
                    if len(parts) == 3:
                        critical = asn1.decode_bool(parts[1])
                        value_node = parts[2]
                    self.extensions[oid] = value_node.content
                    self.extension_critical[oid] = critical
            idx += 1
        self._parse_extensions()

    def _parse_spki(self, spki):
        alg, bitstr = asn1.seq_of(spki)
        self.spki_algorithm = asn1.decode_oid(asn1.seq_of(alg)[0].content)
        self.spki_der = spki.raw
        self.public_key = None
        if self.spki_algorithm == OID_RSA_ENCRYPTION:
            rsapub, _ = asn1.parse(asn1.decode_bitstring(bitstr))
            n, e = asn1.seq_of(rsapub)
            self.public_key = ("rsa", asn1.decode_int(n), asn1.decode_int(e))

    def _parse_extensions(self):
        self.is_ca = False
        self.path_length = None
        self.key_usage = set()
        self.san = GeneralNames()
        self.san_critical = False
        self.name_constraints = None  # (permitted, excluded)
        self.subject_key_id = None
        self.authority_key_id = None

        if OID_BASIC_CONSTRAINTS in self.extensions:
            node, _ = asn1.parse(self.extensions[OID_BASIC_CONSTRAINTS])
            for item in asn1.seq_of(node):
                if item.tag == asn1.TAG_BOOLEAN:
                    self.is_ca = asn1.decode_bool(item)
                elif item.tag == asn1.TAG_INTEGER:
                    self.path_length = asn1.decode_int(item)
        if OID_KEY_USAGE in self.extensions:
            node, _ = asn1.parse(self.extensions[OID_KEY_USAGE])
            bits = asn1.decode_bitstring(node)
            names = ["digitalSignature", "nonRepudiation", "keyEncipherment",
                     "dataEncipherment", "keyAgreement", "keyCertSign",
                     "cRLSign", "encipherOnly", "decipherOnly"]
            for i, name in enumerate(names):
                byte = i // 8
                if byte < len(bits) and bits[byte] & (0x80 >> (i % 8)):
                    self.key_usage.add(name)
        if OID_SUBJECT_ALT_NAME in self.extensions:
            node, _ = asn1.parse(self.extensions[OID_SUBJECT_ALT_NAME])
            self.san = _parse_general_names(node)
            self.san_critical = self.extension_critical.get(OID_SUBJECT_ALT_NAME, False)
        if OID_NAME_CONSTRAINTS in self.extensions:
            node, _ = asn1.parse(self.extensions[OID_NAME_CONSTRAINTS])
            permitted, excluded = [], []
            for part in asn1.seq_of(node):
                if part.tag_class != asn1.CLASS_CONTEXT:
                    continue
                if part.tag == 0:
                    permitted = _parse_general_subtrees(
                        asn1.Node(asn1.CLASS_UNIVERSAL, True, 0x10,
                                  part.content, part.content, part.children))
                elif part.tag == 1:
                    excluded = _parse_general_subtrees(
                        asn1.Node(asn1.CLASS_UNIVERSAL, True, 0x10,
                                  part.content, part.content, part.children))
            self.name_constraints = (permitted, excluded)
        if OID_SUBJECT_KEY_ID in self.extensions:
            node, _ = asn1.parse(self.extensions[OID_SUBJECT_KEY_ID])
            self.subject_key_id = node.content
        if OID_AUTHORITY_KEY_ID in self.extensions:
            node, _ = asn1.parse(self.extensions[OID_AUTHORITY_KEY_ID])
            for item in asn1.seq_of(node):
                if item.tag_class == asn1.CLASS_CONTEXT and item.tag == 0:
                    self.authority_key_id = item.content

    # ---------- 便捷属性 ----------

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.der).hexdigest()

    @property
    def is_self_issued(self) -> bool:
        return self.subject == self.issuer

    def is_self_signed(self) -> bool:
        return self.is_self_issued and verify_signature(self, self)

    def valid_at(self, moment: datetime) -> bool:
        return self.not_before <= moment <= self.not_after

    def dns_names(self):
        """用于名称约束/主机名匹配的 DNS 名集合：SAN 优先，缺省回退 CN。"""
        if self.san.dns:
            return list(self.san.dns)
        cn = self.subject.get("CN")
        return [cn] if cn else []

    def __str__(self):
        return f"Certificate(subject={self.subject}, issuer={self.issuer})"

    # ---------- 载入 ----------

    @classmethod
    def from_der(cls, der: bytes) -> "Certificate":
        return cls(der)

    @classmethod
    def from_pem(cls, text: str) -> "Certificate":
        certs = cls.load_pem_all(text)
        if len(certs) != 1:
            raise CertificateError(f"PEM 中包含 {len(certs)} 张证书，期望 1 张")
        return certs[0]

    @classmethod
    def load_pem_all(cls, text: str):
        begin = "-----BEGIN CERTIFICATE-----"
        end = "-----END CERTIFICATE-----"
        certs = []
        rest = text
        while begin in rest:
            _, _, rest = rest.partition(begin)
            body, _, rest = rest.partition(end)
            certs.append(cls(base64.b64decode(body)))
        return certs

    @classmethod
    def from_pem_file(cls, path: str) -> "Certificate":
        with open(path, "r", encoding="ascii") as fh:
            return cls.from_pem(fh.read())


# ---------- 签名验证 ----------

def rsa_pkcs1_v1_5_verify(hash_name: str, message: bytes, signature: bytes,
                          n: int, e: int) -> bool:
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    m = pow(int.from_bytes(signature, "big"), e, n)
    em = m.to_bytes(k, "big")
    digest = hashlib.new(hash_name, message).digest()
    expected = (b"\x00\x01" + b"\xff" * (k - len(_DIGEST_INFO_PREFIX[hash_name])
                - len(digest) - 3) + b"\x00"
                + _DIGEST_INFO_PREFIX[hash_name] + digest)
    return em == expected


def verify_signature(cert: Certificate, issuer: Certificate) -> bool:
    """用 issuer 的公钥验证 cert 的签名。算法不支持时返回 False。"""
    hash_name = SIG_ALGORITHMS.get(cert.signature_algorithm)
    if hash_name is None:
        return False
    key = issuer.public_key
    if key is None or key[0] != "rsa":
        return False
    return rsa_pkcs1_v1_5_verify(hash_name, cert.tbs_der, cert.signature,
                                 key[1], key[2])
