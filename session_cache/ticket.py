"""Opaque, tamper-evident session tickets.

Wire format (ASCII)::

    base64url(payload_json) "." base64url(HMAC_SHA256(payload, server_key))

The HMAC covers the *exact* payload bytes, so any flip of a single bit
(version, cipher suite, expiry, session id ...) invalidates the ticket.
Only the standard library is used (hmac / hashlib / base64 / json).
"""

import base64
import hashlib
import hmac
import json


class TicketError(ValueError):
    """Raised when a ticket is malformed or fails authentication."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + pad)
    except (ValueError, TypeError) as exc:
        raise TicketError("ticket payload is not valid base64") from exc


def issue_ticket(
    server_key: bytes,
    session_id: str,
    protocol_version: str,
    cipher_suite: str,
    issued_at: float,
    expires_at: float,
) -> str:
    """Serialize the session binding into an HMAC-signed ticket."""
    if not isinstance(server_key, (bytes, bytearray)) or len(server_key) < 16:
        raise ValueError("server_key must be at least 16 random bytes")
    payload = json.dumps(
        {
            "sid": session_id,
            "ver": protocol_version,
            "cs": cipher_suite,
            "iat": issued_at,
            "exp": expires_at,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    mac = hmac.new(bytes(server_key), payload, hashlib.sha256).digest()
    return _b64e(payload) + "." + _b64e(mac)


def verify_ticket(server_key: bytes, ticket: str) -> dict:
    """Validate a ticket's signature and return its payload.

    Raises TicketError on malformed input, bad encodings or HMAC mismatch.
    """
    if not isinstance(ticket, str):
        raise TicketError("ticket must be a string")
    parts = ticket.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise TicketError("malformed ticket layout")

    payload_b64, mac_b64 = parts
    payload = _b64d(payload_b64)
    provided_mac = _b64d(mac_b64)

    expected_mac = hmac.new(bytes(server_key), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(provided_mac, expected_mac):
        raise TicketError("ticket signature does not match (forged or corrupted)")

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TicketError("ticket payload is not valid JSON") from exc

    required = {"sid", "ver", "cs", "iat", "exp"}
    if not isinstance(data, dict) or not required.issubset(data):
        raise TicketError("ticket payload is missing required fields")
    return data
