"""Tiny user database that stores self-describing password records."""

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .core import (
    hash_password,
    needs_upgrade,
    normalize_record,
    verify_and_upgrade,
)


@dataclass(frozen=True)
class LoginResult:
    ok: bool
    upgraded: bool = False
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None


class UserDB:
    """In-memory username -> normalized record mapping.

    Persistence (load/save) is JSON on disk.  Everything needed to verify a
    password lives inside the record itself; there is no global parameter
    state consulted during verification.
    """

    def __init__(self) -> None:
        self._users: Dict[str, Dict[str, Any]] = {}

    def create(self, username: str, password: str) -> Dict[str, Any]:
        """Create a user with current parameters. Fails if the user exists."""
        if username in self._users:
            raise ValueError(f"user already exists: {username!r}")
        record = normalize_record(hash_password(password))
        self._users[username] = record
        return record

    def get_record(self, username: str) -> Dict[str, Any]:
        return self._users[username]

    def set_raw_record(self, username: str, record: Mapping[str, Any]) -> None:
        """Install a (possibly legacy or corrupt) record directly.

        Intended for migrating records from elsewhere or for tests.
        """
        self._users[username] = dict(record) if isinstance(record, Mapping) else record

    def corrupt(self, username: str) -> None:
        """Replace a user's record with garbage (simulates storage damage)."""
        self._users[username] = {"v": 1, "params": {}, "salt": None}

    def login(self, username: str, password: str) -> LoginResult:
        """Verify credentials and upgrade legacy records in place on success."""
        try:
            before = self._users[username]
        except KeyError:
            return LoginResult(ok=False)

        try:
            normalized_before = normalize_record(before)
        except Exception:
            # A corrupt record must never authenticate.
            return LoginResult(
                ok=False,
                before=dict(before) if isinstance(before, Mapping) else before,
            )

        ok, upgraded = verify_and_upgrade(password, normalized_before)
        if not ok:
            return LoginResult(ok=False, before=normalized_before)
        if upgraded is None:
            return LoginResult(ok=True, before=normalized_before, after=normalized_before)

        self._users[username] = upgraded
        return LoginResult(
            ok=True,
            upgraded=True,
            before=normalized_before,
            after=upgraded,
        )

    def needs_upgrade(self, username: str) -> bool:
        return needs_upgrade(self._users[username])

    def save(self, path: str) -> None:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._users, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "UserDB":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("user database file must contain a JSON object")
        db = cls()
        for username, record in data.items():
            db._users[username] = normalize_record(record)
        return db
