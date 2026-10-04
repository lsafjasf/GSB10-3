"""X.509 certificate parsing -- standard library only.

Parses the parts of RFC 5280 needed for chain building and validation:
names, validity, SPKI, basicConstraints, keyUsage, SAN, SKI/AKI and
nameConstraints. Equality of names is byte-wise DER equality, which is
stricter (and safer) than RFC 5280 string preparation.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from . import der

# --------------------------------------------------------------------------
# OIDs
# --------------------------------------------------------------------------

OID_NAMES: Dict[str, str] = {
    "2.5.4.3": "CN",
    "2.5.4.6": "C",
    "2.5.4.7": "L",
    "2.5.4.8": "ST",
    "2.5.4.10": "O",
    "2.5.4.11": "OU",
    "0.9.2342.19200300.100.1.25": "DC",
    "1.2.840.113549.1.9.1": "emailAddress",
    "2.5.4.5": "serialNumber",
}

OID_BASIC_CONSTRAINTS = "2.5.29.19"
OID_KEY_USAGE = "2.5.29.15"
OID_SUBJECT_ALT_NAME = "2.5.29.17"
OID_NAME_CONSTRAINTS = "2.5.29.30"
OID_SUBJECT_KEY_ID = "2.5.29.14"
OID_AUTHORITY_KEY_ID = "2.5.29.35"

SIG_ALG_HASH: Dict[str, Tuple[str, str]] = {
    "1.2.840.113549.1.1.5": ("rsa", "sha1"),
    "1.2.840.113549.1.1.11": ("rsa", "sha256"),
    "1.2.840.113549.1.1.12": ("rsa", "sha384"),
    "1.2.840.113549.1.1.13": ("rsa", "sha512"),
    "1.2.840.10045.4.3.2": ("ecdsa", "sha256"),
    "1.2.840.10045.4.3.3": ("ecdsa", "sha384"),
    "1.2.840.10045.4.3.4": ("ecdsa", "sha512"),
}

KEY_ALG_NAMES = {
    "1.2.840.113549.1.1.1": "rsa",
    "1.2.840.10045.2.1": "ec",
}

EC_CURVE_NAMES = {
    "1.2.840.10045.3.1.7": "P-256",
    "1.3.132.0.34": "P-384",
}

WEAK_HASHES = {"md5", "sha1"}


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Name:
    """An X.501 Name; equality is DER byte equality (canonical, case sensitive)."""
    raw: bytes
    rdns: Tuple[Tuple[Tuple[str, str], ...], ...]  # ((oid, value), ...) per RDN

    @staticmethod
    def from_der(node: der.Node) -> "Name":
        rdns: List[Tuple[Tuple[str, str], ...]] = []
        for rdn in node.children:  # RDNSequence -> SET OF AttributeTypeAndValue
            attrs = []
            for atv in rdn.children:
                oid = der.to_oid(atv.children[0])
                value = der.to_text(atv.children[1])
                attrs.append((oid, value))
            rdns.append(tuple(attrs))
        return Name(raw=node.raw, rdns=tuple(rdns))

    def short(self) -> str:
        parts = []
        for rdn in self.rdns:
            for oid, value in rdn:
                parts.append(f"{OID_NAMES.get(oid, oid)}={value}")
        return ",".join(parts) if parts else "(empty)"

    def get(self, oid: str) -> List[str]:
        return [v for rdn in self.rdns for (o, v) in rdn if o == oid]

    def __str__(self) -> str:
        return self.short()


EMPTY_NAME = Name(raw=b"0\x00", rdns=())


# --------------------------------------------------------------------------
# General names / name constraints
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class GeneralName:
    kind: str            # 'dns' | 'email' | 'uri' | 'dir' | 'ip' | 'other'
    value: object        # str for dns/email/uri, Name for dir, bytes for ip


def parse_general_name(node: der.Node) -> Optional[GeneralName]:
    if node.tag_class != 2:
        return None
    if node.tag == 1:
        return GeneralName("email", node.content.decode("ascii", "replace"))
    if node.tag == 2:
        return GeneralName("dns", node.content.decode("ascii", "replace"))
    if node.tag == 4:
        inner, _ = der.parse_one(node.content, 0)
        return GeneralName("dir", Name.from_der(inner))
    if node.tag == 6:
        return GeneralName("uri", node.content.decode("ascii", "replace"))
    if node.tag == 7:
        return GeneralName("ip", node.content)
    return None


@dataclass(frozen=True)
class NameConstraints:
    permitted: Tuple[GeneralName, ...] = ()
    excluded: Tuple[GeneralName, ...] = ()

    @property
    def present(self) -> bool:
        return bool(self.permitted or self.excluded)


def _parse_general_subtrees(node: der.Node) -> Tuple[GeneralName, ...]:
    names = []
    for subtree in node.children:
        base = subtree.children[0]
        gn = parse_general_name(base)
        if gn is not None:
            names.append(gn)
    return tuple(names)


def parse_name_constraints(data: bytes) -> NameConstraints:
    top = der.parse(data)
    permitted: Tuple[GeneralName, ...] = ()
    excluded: Tuple[GeneralName, ...] = ()
    for child in top.children:
        if child.tag_class != 2:
            continue
        if child.tag == 0:
            permitted = _parse_general_subtrees(child)
        elif child.tag == 1:
            excluded = _parse_general_subtrees(child)
    return NameConstraints(permitted=permitted, excluded=excluded)


# --------------------------------------------------------------------------
# Certificate
# --------------------------------------------------------------------------

@dataclass
class Spki:
    algorithm: str                 # 'rsa' | 'ec' | oid string
    key_bytes: bytes               # BIT STRING payload
    params_oid: Optional[str]      # EC curve OID, else None
    raw: bytes                     # full SPKI DER (identity anchor matching)


@dataclass
class Certificate:
    der: bytes
    tbs: bytes
    serial: int
    sig_alg_oid: str
    issuer: Name
    subject: Name
    not_before: datetime
    not_after: datetime
    spki: Spki
    signature: bytes
    # extensions
    is_ca: bool = False
    path_len: Optional[int] = None
    has_basic_constraints: bool = False
    key_usage: Optional[bytes] = None
    key_cert_sign: bool = False
    san: Tuple[GeneralName, ...] = ()
    ski: Optional[bytes] = None
    aki: Optional[bytes] = None
    name_constraints: NameConstraints = NameConstraints()

    # -- convenience ------------------------------------------------------

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.der).hexdigest()

    @property
    def sig_scheme(self) -> Tuple[str, str]:
        """(algorithm, hash) e.g. ('rsa', 'sha256'); raises for unknown."""
        if self.sig_alg_oid not in SIG_ALG_HASH:
            raise ValueError(f"unsupported signature algorithm OID {self.sig_alg_oid}")
        return SIG_ALG_HASH[self.sig_alg_oid]

    @property
    def weak_signature(self) -> bool:
        try:
            return self.sig_scheme[1] in WEAK_HASHES
        except ValueError:
            return False

    def label(self) -> str:
        return f"subject=[{self.subject}] issuer=[{self.issuer}] serial={self.serial:#x}"


def _parse_extensions(cert: Certificate, seq: der.Node) -> None:
    for ext in seq.children:
        oid = der.to_oid(ext.children[0])
        idx = 1
        critical = False
        if ext.children[idx].tag == 1:  # BOOLEAN
            critical = ext.children[idx].content != b"\x00"
            idx += 1
        value = ext.children[idx].content  # OCTET STRING payload
        try:
            if oid == OID_BASIC_CONSTRAINTS:
                cert.has_basic_constraints = True
                node = der.parse(value)
                cert.is_ca = False
                for ch in node.children:
                    if ch.tag == 1:
                        cert.is_ca = ch.content != b"\x00"
                    elif ch.tag == 2:
                        cert.path_len = der.to_uint(ch)
            elif oid == OID_KEY_USAGE:
                bits = der.parse(value)  # BIT STRING, may have unused bits
                if bits.tag != 3 or not bits.content:
                    raise der.DerError("bad keyUsage")
                cert.key_usage = bits.content[1:]
                cert.key_cert_sign = bool(cert.key_usage) and \
                    bool(cert.key_usage[0] & 0x04)
            elif oid == OID_SUBJECT_ALT_NAME:
                node = der.parse(value)
                cert.san = tuple(g for g in
                                 (parse_general_name(c) for c in node.children)
                                 if g is not None)
            elif oid == OID_SUBJECT_KEY_ID:
                cert.ski = der.parse(value).content
            elif oid == OID_AUTHORITY_KEY_ID:
                node = der.parse(value)
                for ch in node.children:
                    if ch.tag_class == 2 and ch.tag == 0:
                        cert.aki = ch.content
            elif oid == OID_NAME_CONSTRAINTS:
                cert.name_constraints = parse_name_constraints(value)
        except der.DerError:
            if critical:
                raise


def parse_certificate(data: bytes) -> Certificate:
    top = der.parse(data)
    if top.tag != 16 or len(top.children) != 3:
        raise der.DerError("not a certificate SEQUENCE")
    tbs_node, sig_alg_node, sig_node = top.children
    tbs = tbs_node.children

    idx = 0
    if tbs[0].tag_class == 2 and tbs[0].tag == 0:  # [0] EXPLICIT version
        idx = 1

    serial = der.to_uint(tbs_node.child(idx)); idx += 1
    sig_alg_oid = der.to_oid(tbs_node.child(idx).children[0]); idx += 1
    issuer = Name.from_der(tbs_node.child(idx)); idx += 1
    validity = tbs_node.child(idx); idx += 1
    not_before = der.to_time(validity.children[0])
    not_after = der.to_time(validity.children[1])
    subject = Name.from_der(tbs_node.child(idx)); idx += 1

    spki_node = tbs_node.child(idx); idx += 1
    key_alg_oid = der.to_oid(spki_node.children[0].children[0])
    params_oid = None
    if len(spki_node.children[0].children) > 1 and spki_node.children[0].children[1].tag == 6:
        params_oid = der.to_oid(spki_node.children[0].children[1])
    spki = Spki(
        algorithm=KEY_ALG_NAMES.get(key_alg_oid, key_alg_oid),
        key_bytes=der.to_bit_string(spki_node.children[1]),
        params_oid=params_oid,
        raw=spki_node.raw,
    )

    # optional [1]/[2] unique identifiers, then [3] extensions
    extensions_node = None
    while idx < len(tbs_node.children):
        node = tbs_node.children[idx]
        if node.tag_class == 2 and node.tag == 3:
            extensions_node = node
        idx += 1

    outer_sig_oid = der.to_oid(sig_alg_node.children[0])
    if outer_sig_oid != sig_alg_oid:
        raise der.DerError("signature algorithm mismatch between TBS and outer")
    signature = der.to_bit_string(sig_node)

    cert = Certificate(
        der=data, tbs=tbs_node.raw, serial=serial, sig_alg_oid=sig_alg_oid,
        issuer=issuer, subject=subject, not_before=not_before,
        not_after=not_after, spki=spki, signature=signature,
    )
    if extensions_node is not None and extensions_node.children:
        _parse_extensions(cert, extensions_node.children[0])
    return cert


# --------------------------------------------------------------------------
# PEM helpers
# --------------------------------------------------------------------------

def pem_decode_many(text: str) -> List[bytes]:
    blocks = []
    marker_b = "-----BEGIN CERTIFICATE-----"
    marker_e = "-----END CERTIFICATE-----"
    pos = 0
    while True:
        start = text.find(marker_b, pos)
        if start < 0:
            break
        end = text.find(marker_e, start)
        if end < 0:
            raise der.DerError("unterminated PEM block")
        b64 = text[start + len(marker_b):end]
        blocks.append(base64.b64decode("".join(b64.split())))
        pos = end + len(marker_e)
    return blocks


def load_certificates(path: str) -> List[Certificate]:
    with open(path, "rb") as fh:
        blob = fh.read()
    if b"-----BEGIN" in blob:
        ders = pem_decode_many(blob.decode("ascii"))
    else:
        ders = [blob]
    return [parse_certificate(d) for d in ders]
