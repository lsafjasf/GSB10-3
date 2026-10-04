"""Per-record password hashing with in-place parameter upgrades.

Standard library only (hashlib, hmac, secrets, json).

Each stored record is a plain dict (JSON-serializable) carrying its own
algorithm identifier and parameters, e.g.::

    {
        "v": 1,
        "alg": "pbkdf2-sha256",
        "params": {"iterations": 600000},
        "salt": "<base64>",
        "hash": "<base64>",
    }

Verification always re-derives using the parameters stored *in the record*,
never global defaults.  When a record's parameters differ from the current
defaults, a successful login upgrades the record in place.
"""

from .core import (
    ALG_VERSION,
    CURRENT_ALG,
    CURRENT_PARAMS,
    DEFAULT_ITERATIONS,
    SALT_BYTES,
    HashError,
    InvalidRecordError,
    UnsupportedAlgorithmError,
    hash_password,
    needs_upgrade,
    normalize_record,
    verify_and_upgrade,
    verify_password,
)
from .userdb import LoginResult, UserDB

__all__ = [
    "ALG_VERSION",
    "CURRENT_ALG",
    "CURRENT_PARAMS",
    "DEFAULT_ITERATIONS",
    "SALT_BYTES",
    "HashError",
    "InvalidRecordError",
    "UnsupportedAlgorithmError",
    "hash_password",
    "needs_upgrade",
    "normalize_record",
    "verify_and_upgrade",
    "verify_password",
    "LoginResult",
    "UserDB",
]
