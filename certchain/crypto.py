"""Signature verification -- standard library only.

Supports RSA PKCS#1 v1.5 (RFC 8017 9.2, EMSA-PKCS1-v1_5) and ECDSA
(RFC 5754 / SEC 1) on P-256 and P-384. No bigint acceleration is needed:
certificate signatures are tiny.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from . import der
from .x509 import Spki

# --------------------------------------------------------------------------
# DigestInfo DER prefixes (algorithm identifiers + OCTET STRING header)
# --------------------------------------------------------------------------

_PKCS1_PREFIX: Dict[str, bytes] = {
    "sha1": bytes.fromhex(
        "3021300906052b0e03021a05000414"),
    "sha256": bytes.fromhex(
        "3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex(
        "3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex(
        "3051300d060960864801650304020305000440"),
}


def _verify_rsa_pkcs1(spki: Spki, tbs: bytes, signature: bytes,
                      hash_name: str) -> bool:
    pub = der.parse(spki.key_bytes)
    if pub.tag != 16 or len(pub.children) != 2:
        return False
    n = der.to_uint(pub.children[0])
    e = der.to_uint(pub.children[1])
    if n <= 1 or e <= 1:
        return False
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")

    digest = hashlib.new(hash_name, tbs).digest()
    encoded = _PKCS1_PREFIX[hash_name] + digest
    t_len = len(encoded)
    if k < t_len + 11:
        return False
    padding = b"\xff" * (k - t_len - 3)
    expected = b"\x00\x01" + padding + b"\x00" + encoded
    if em != expected:
        return False
    return True


# --------------------------------------------------------------------------
# Elliptic curves (NIST P-256 / P-384), Jacobian coordinates
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Curve:
    p: int
    a: int
    b: int
    gx: int
    gy: int
    n: int
    coord_len: int


_P256 = Curve(
    p=0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF,
    a=0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFC,
    b=0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B,
    gx=0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    gy=0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
    n=0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551,
    coord_len=32,
)

_P384 = Curve(
    p=0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFF0000000000000000FFFFFFFF,
    a=0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFF0000000000000000FFFFFFFC,
    b=0xB3312FA7E23EE7E4988E056BE3F82D19181D9C6EFE8141120314088F5013875AC656398D8A2ED19D2A85C8EDD3EC2AEF,
    gx=0xAA87CA22BE8B05378EB1C71EF320AD746E1D3B628BA79B9859F741E082542A385502F25DBF55296C3A545E3872760AB7,
    gy=0x3617DE4A96262C6F5D9E98BF9292DC29F8F41DBD289A147CE9DA3113B5F0B8C00A60B1CE1D7E819D7A431D7C90EA0E5F,
    n=0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFC7634D81F4372DDF581A0DB248B0A77AECEC196ACCC52973,
    coord_len=48,
)

_CURVES: Dict[str, Curve] = {"P-256": _P256, "P-384": _P384}

# point at infinity represented as None; otherwise (x, y, z) Jacobian


def _jac_double(p, curve: Curve):
    x, y, z = p
    if y == 0:
        return None
    p_mod = curve.p
    yy = (y * y) % p_mod
    s = (4 * x * yy) % p_mod
    m = (3 * x * x + curve.a * z * z * z * z) % p_mod
    x3 = (m * m - 2 * s) % p_mod
    y3 = (m * (s - x3) - 8 * yy * yy) % p_mod
    z3 = (2 * y * z) % p_mod
    return (x3, y3, z3)


def _jac_add(p, q, curve: Curve):
    if p is None:
        return q
    if q is None:
        return p
    x1, y1, z1 = p
    x2, y2, z2 = q
    p_mod = curve.p
    z1z1 = z1 * z1 % p_mod
    z2z2 = z2 * z2 % p_mod
    u1 = x1 * z2z2 % p_mod
    u2 = x2 * z1z1 % p_mod
    s1 = y1 * z2 * z2z2 % p_mod
    s2 = y2 * z1 * z1z1 % p_mod
    if u1 == u2:
        if s1 != s2:
            return None
        return _jac_double(p, curve)
    h = (u2 - u1) % p_mod
    r = (s2 - s1) % p_mod
    hh = h * h % p_mod
    hhh = h * hh % p_mod
    v = u1 * hh % p_mod
    x3 = (r * r - hhh - 2 * v) % p_mod
    y3 = (r * (v - x3) - s1 * hhh) % p_mod
    z3 = (z1 * z2 * h) % p_mod
    return (x3, y3, z3)


def _scalar_mult(k: int, point, curve: Curve):
    result = None
    addend = point
    while k:
        if k & 1:
            result = _jac_add(result, addend, curve)
        addend = _jac_double(addend, curve)
        k >>= 1
    return result


def _to_affine(point, curve: Curve) -> Optional[Tuple[int, int]]:
    if point is None:
        return None
    x, y, z = point
    if z == 0:
        return None
    p_mod = curve.p
    zinv = pow(z, p_mod - 2, p_mod)
    zinv2 = zinv * zinv % p_mod
    zinv3 = zinv2 * zinv % p_mod
    return x * zinv2 % p_mod, y * zinv3 % p_mod


def _on_curve(curve: Curve, x: int, y: int) -> bool:
    return (y * y - x * x * x - curve.a * x - curve.b) % curve.p == 0


def _verify_ecdsa(spki: Spki, tbs: bytes, signature: bytes,
                  hash_name: str) -> bool:
    curve_name = None
    from .x509 import EC_CURVE_NAMES
    curve_name = EC_CURVE_NAMES.get(spki.params_oid or "")
    if curve_name is None:
        return False
    curve = _CURVES[curve_name]

    key = spki.key_bytes
    if len(key) != 1 + 2 * curve.coord_len or key[0] != 0x04:
        return False
    qx = int.from_bytes(key[1:1 + curve.coord_len], "big")
    qy = int.from_bytes(key[1 + curve.coord_len:], "big")
    if qx >= curve.p or qy >= curve.p or not _on_curve(curve, qx, qy):
        return False

    try:
        sig = der.parse(signature)
        r = der.to_uint(sig.children[0])
        s = der.to_uint(sig.children[1])
    except (der.DerError, IndexError):
        return False
    if not (1 <= r < curve.n and 1 <= s < curve.n):
        return False

    digest = hashlib.new(hash_name, tbs).digest()
    z = int.from_bytes(digest, "big")
    order_bits = curve.n.bit_length()
    if len(digest) * 8 > order_bits:
        z >>= len(digest) * 8 - order_bits

    sinv = pow(s, curve.n - 2, curve.n)
    u1 = z * sinv % curve.n
    u2 = r * sinv % curve.n

    g = (curve.gx, curve.gy, 1)
    q = (qx, qy, 1)
    point = _jac_add(_scalar_mult(u1, g, curve), _scalar_mult(u2, q, curve), curve)
    affine = _to_affine(point, curve)
    if affine is None:
        return False
    x1, _ = affine
    return x1 % curve.n == r


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def verify_signature(child_spki: Spki, sig_alg_oid: str,
                     tbs: bytes, signature: bytes) -> Tuple[bool, str]:
    """Return (ok, reason). reason is '' on success or a short code."""
    from .x509 import SIG_ALG_HASH, WEAK_HASHES
    scheme = SIG_ALG_HASH.get(sig_alg_oid)
    if scheme is None:
        return False, "UNSUPPORTED_SIGNATURE_ALGORITHM"
    algorithm, hash_name = scheme
    if hash_name in WEAK_HASHES:
        return False, "WEAK_SIGNATURE_ALGORITHM"
    if algorithm == "rsa" and child_spki.algorithm == "rsa":
        ok = _verify_rsa_pkcs1(child_spki, tbs, signature, hash_name)
    elif algorithm == "ecdsa" and child_spki.algorithm == "ec":
        ok = _verify_ecdsa(child_spki, tbs, signature, hash_name)
    else:
        return False, "SIGNATURE_KEY_ALGORITHM_MISMATCH"
    if not ok:
        return False, "BAD_SIGNATURE"
    return True, ""
