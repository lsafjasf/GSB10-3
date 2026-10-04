"""Core password hashing routines: hashing, verification, upgrade checks.

Only the Python standard library is used.
"""

import base64
import hashlib
import hmac
import secrets
from copy import deepcopy
from typing import Any, Dict, Mapping, Tuple

ALG_VERSION = 1
CURRENT_ALG = "pbkdf2-sha256"

# 2023 OWASP guidance for PBKDF2-HMAC-SHA256 is 600000 iterations.
# Adjust this (or DEFAULT_PARAMS) when hardware makes it cheap; every old
# record keeps its own parameters and is upgraded on next successful login.
DEFAULT_ITERATIONS = 600_000

SALT_BYTES = 16
_HASH_BYTES = 32

#: Parameters considered "current" for freshly created records.
CURRENT_PARAMS: Dict[str, Any] = {"iterations": DEFAULT_ITERATIONS}


class HashError(Exception):
    """Base class for password-hashing errors."""


class UnsupportedAlgorithmError(HashError):
    """The record names an algorithm this build does not know."""


class InvalidRecordError(HashError):
    """The stored record is missing fields or holds invalid values."""


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: Any) -> bytes:
    if not isinstance(text, str):
        raise InvalidRecordError("base64 field must be a string")
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except Exception as exc:  # binascii.Error / ValueError
        raise InvalidRecordError("invalid base64 data") from exc


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=_HASH_BYTES,
    )


def hash_password(password: str, *, iterations: int | None = None) -> Dict[str, Any]:
    """Hash *password* with a fresh per-user random salt.

    Returns a self-describing record containing the algorithm identifier
    and the parameters used.
    """
    if not isinstance(password, str):
        raise TypeError("password must be a str")
    iters = DEFAULT_ITERATIONS if iterations is None else iterations
    if not isinstance(iters, int) or isinstance(iters, bool) or iters <= 0:
        raise ValueError("iterations must be a positive integer")

    salt = secrets.token_bytes(SALT_BYTES)
    digest = _derive(password, salt, iters)
    return {
        "v": ALG_VERSION,
        "alg": CURRENT_ALG,
        "params": {"iterations": iters},
        "salt": _b64e(salt),
        "hash": _b64e(digest),
    }


def normalize_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate a stored record and return a normalized deep copy of it.

    Raises InvalidRecordError if the record is structurally unusable and
    UnsupportedAlgorithmError if its algorithm is unknown.
    """
    if not isinstance(record, Mapping):
        raise InvalidRecordError("record must be a mapping")

    rec = dict(record)
    if not isinstance(rec.get("v"), int) or isinstance(rec.get("v"), bool):
        raise InvalidRecordError("record missing integer 'v'")
    if rec["v"] != ALG_VERSION:
        raise InvalidRecordError(f"unsupported record version: {rec['v']!r}")

    alg = rec.get("alg")
    if alg != CURRENT_ALG:
        if not isinstance(alg, str):
            raise InvalidRecordError("record missing string 'alg'")
        raise UnsupportedAlgorithmError(f"unsupported algorithm: {alg!r}")

    params = rec.get("params")
    if not isinstance(params, Mapping):
        raise InvalidRecordError("record missing mapping 'params'")
    iters = params.get("iterations")
    if not isinstance(iters, int) or isinstance(iters, bool) or iters <= 0:
        raise InvalidRecordError("params.iterations must be a positive integer")

    salt = _b64d(rec.get("salt"))
    digest = _b64d(rec.get("hash"))
    if not salt:
        raise InvalidRecordError("salt must not be empty")
    if len(digest) != _HASH_BYTES:
        raise InvalidRecordError(f"hash must be {_HASH_BYTES} bytes")

    return {
        "v": rec["v"],
        "alg": alg,
        "params": {"iterations": iters},
        "salt": _b64e(salt),
        "hash": _b64e(digest),
    }


def _extract(record: Mapping[str, Any]) -> Tuple[bytes, int, bytes]:
    rec = normalize_record(record)
    return (
        _b64d(rec["salt"]),
        rec["params"]["iterations"],
        _b64d(rec["hash"]),
    )


def verify_password(password: str, record: Mapping[str, Any]) -> bool:
    """Return True iff *password* matches *record*.

    The hash is always re-derived with the parameters stored in the record.
    A structurally invalid record raises InvalidRecordError rather than
    silently failing.
    """
    if not isinstance(password, str):
        raise TypeError("password must be a str")
    salt, iterations, expected = _extract(record)
    actual = _derive(password, salt, iterations)
    return hmac.compare_digest(actual, expected)


def needs_upgrade(record: Mapping[str, Any]) -> bool:
    """True when the record's algorithm/parameters differ from current ones."""
    rec = normalize_record(record)
    if rec["alg"] != CURRENT_ALG:
        return True
    return rec["params"] != CURRENT_PARAMS


def verify_and_upgrade(
    password: str, record: Mapping[str, Any]
) -> Tuple[bool, Dict[str, Any] | None]:
    """Verify *password* against *record* and upgrade it on success if needed.

    Returns ``(ok, upgraded_record)``.  *upgraded_record* is None when the
    password is wrong or the record already uses current parameters; it is
    never written back by this function -- callers persist it themselves
    (UserDB.login does this in place).
    """
    if not verify_password(password, record):
        return False, None
    if not needs_upgrade(record):
        return True, None

    salt, _old_iters, _expected = _extract(record)
    upgraded: Dict[str, Any] = {
        "v": ALG_VERSION,
        "alg": CURRENT_ALG,
        "params": dict(CURRENT_PARAMS),
        "salt": _b64e(salt),
        "hash": _b64e(_derive(password, salt, CURRENT_PARAMS["iterations"])),
    }
    return True, normalize_record(upgraded)
