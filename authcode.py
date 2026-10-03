"""Authorization-code exchange with a one-time PKCE-style verifier binding.

The authorization request carries a *code challenge* derived from a secret
*code verifier*; the token request must present the original verifier.  The
server recomputes the challenge and compares it in constant time, which binds
the two requests together and defeats authorization-code injection.

Standard library only.  Python 3.8+.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

VERIFIER_MIN_LEN = 43
VERIFIER_MAX_LEN = 128
VERIFIER_ENTROPY_BYTES = 32  # 256 bits of CSPRNG output per verifier
SUPPORTED_METHODS = ("S256", "plain")
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


class Reason:
    """Machine-readable denial reasons, one per distinct failure mode."""

    UNKNOWN_CODE = "unknown_code"
    CODE_EXPIRED = "code_expired"
    CODE_ALREADY_USED = "code_already_used"
    VERIFIER_MISSING = "verifier_missing"
    VERIFIER_MALFORMED = "verifier_malformed"
    VERIFIER_MISMATCH = "verifier_mismatch"
    METHOD_MISMATCH = "method_mismatch"


@dataclass(frozen=True)
class Denial:
    """One rejected exchange attempt, kept in the server's denial ledger."""

    reason: str
    detail: str
    code_hint: str
    at: float


class ExchangeDenied(Exception):
    """Raised by ``exchange`` when a token request is rejected."""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def generate_verifier(entropy_bytes: int = VERIFIER_ENTROPY_BYTES) -> str:
    """Return a fresh code verifier from the OS CSPRNG (``secrets``).

    32 random bytes -> 43 base64url characters (no padding), the RFC 7636
    minimum length, drawn from the unreserved URI character set.
    """
    if entropy_bytes < 32:
        raise ValueError("verifier entropy must be at least 32 bytes (256 bits)")
    return base64.urlsafe_b64encode(secrets.token_bytes(entropy_bytes)).rstrip(b"=").decode("ascii")


def compute_challenge(verifier: str, method: str = "S256") -> str:
    """Derive the code challenge stored with the authorization request."""
    if method == "S256":
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if method == "plain":
        return verifier
    raise ValueError(f"unsupported challenge method: {method!r}")


def make_pkce_pair(method: str = "S256") -> Dict[str, str]:
    """Convenience helper for clients: one (verifier, challenge, method) triple."""
    verifier = generate_verifier()
    return {
        "code_verifier": verifier,
        "code_challenge": compute_challenge(verifier, method),
        "code_challenge_method": method,
    }


@dataclass
class _CodeRecord:
    client_id: str
    challenge: str
    method: str
    expires_at: float
    used: bool = False


class AuthorizationServer:
    """Issues single-use authorization codes bound to a code challenge."""

    def __init__(
        self,
        code_ttl: float = 600.0,
        clock: Callable[[], float] = time.time,
    ):
        self._code_ttl = float(code_ttl)
        self._clock = clock
        self._codes: Dict[str, _CodeRecord] = {}
        self._denials: List[Denial] = []
        self._lock = threading.Lock()

    @property
    def denials(self) -> List[Denial]:
        """Every rejected exchange attempt, in order (audit trail)."""
        return list(self._denials)

    def authorize(
        self,
        client_id: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
    ) -> str:
        """Issue an authorization code bound to ``code_challenge``.

        The code itself is 32 bytes (256 bits) of CSPRNG output, base64url
        encoded; it is unguessable and independent of the verifier.
        """
        if code_challenge_method not in SUPPORTED_METHODS:
            raise ValueError(
                f"unsupported code_challenge_method: {code_challenge_method!r}"
            )
        if not code_challenge:
            raise ValueError("code_challenge must not be empty")
        code = secrets.token_urlsafe(32)
        self._codes[code] = _CodeRecord(
            client_id=client_id,
            challenge=code_challenge,
            method=code_challenge_method,
            expires_at=self._clock() + self._code_ttl,
        )
        return code

    def exchange(self, code: Optional[str], code_verifier: Optional[str]) -> Dict[str, str]:
        """Redeem an authorization code for a token, or raise ``ExchangeDenied``.

        Checks, in order: code known -> not expired -> not already used ->
        verifier present -> verifier well-formed -> method supported ->
        challenge matches (constant-time).  Any verifier-side failure also
        consumes the code, so a leaked code cannot be brute-forced.
        """
        with self._lock:
            record = self._codes.get(code) if code else None
            if record is None:
                self._deny(Reason.UNKNOWN_CODE, "authorization code is not recognized", code)

            if self._clock() >= record.expires_at:
                self._deny(Reason.CODE_EXPIRED, "authorization code has expired", code)

            if record.used:
                self._deny(
                    Reason.CODE_ALREADY_USED,
                    "authorization code was already redeemed (possible replay)",
                    code,
                )

            if not code_verifier:
                record.used = True
                self._deny(Reason.VERIFIER_MISSING, "code_verifier parameter is missing", code)

            if (
                not isinstance(code_verifier, str)
                or not VERIFIER_MIN_LEN <= len(code_verifier) <= VERIFIER_MAX_LEN
                or any(ch not in _UNRESERVED for ch in code_verifier)
            ):
                record.used = True
                self._deny(
                    Reason.VERIFIER_MALFORMED,
                    "code_verifier must be 43-128 unreserved characters",
                    code,
                )

            if record.method not in SUPPORTED_METHODS:
                record.used = True
                self._deny(
                    Reason.METHOD_MISMATCH,
                    f"stored challenge method {record.method!r} is not supported",
                    code,
                )

            expected = compute_challenge(code_verifier, record.method)
            if not hmac.compare_digest(expected, record.challenge):
                record.used = True
                self._deny(
                    Reason.VERIFIER_MISMATCH,
                    "code_verifier does not match the stored code_challenge",
                    code,
                )

            record.used = True
            return {
                "access_token": secrets.token_urlsafe(32),
                "token_type": "Bearer",
                "client_id": record.client_id,
            }

    def _deny(self, reason: str, detail: str, code: Optional[str]):
        hint = (code[:8] + "...") if isinstance(code, str) and code else "<none>"
        self._denials.append(Denial(reason=reason, detail=detail, code_hint=hint, at=self._clock()))
        raise ExchangeDenied(reason, detail)
