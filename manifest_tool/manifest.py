"""Manifest generation and verification for release directories.

A manifest is a JSON Lines file with three kinds of lines:

* ``{"kind": "header", ...}`` — format version, hash algorithm, creation time.
* ``{"kind": "entry", ...}`` — one per file/symlink, with normalized relative
  path, length and digest.
* ``{"kind": "signature", ...}`` — digest of the canonical serialization of
  all entries, so the manifest itself can be self-verified.

Only the Python standard library is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple

FORMAT_VERSION = 1
DEFAULT_ALGORITHM = "sha256"
CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming reads, safe for very large files

FILE_TYPE = "file"
SYMLINK_TYPE = "symlink"


class UnsafePathError(ValueError):
    """Raised when a path is absolute or would escape the root directory."""


class ManifestFormatError(ValueError):
    """Raised when a manifest file is malformed."""


def normalize_relpath(path: str) -> str:
    """Normalize a relative path to canonical POSIX form.

    Rejects absolute paths and any path that would escape the root
    directory (e.g. ``../x``). Redundant ``.`` components, duplicate
    separators and ``a/../b`` sequences are collapsed.
    """
    if not isinstance(path, str) or not path:
        raise UnsafePathError("path must be a non-empty string")
    path = path.replace("\\", "/")
    if path.startswith("/") or (len(path) >= 2 and path[1] == ":"):
        raise UnsafePathError(f"absolute path not allowed: {path!r}")
    parts: List[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise UnsafePathError(f"path escapes root directory: {path!r}")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise UnsafePathError(f"path does not name a file: {path!r}")
    return "/".join(parts)


def _hash_file(path: str, algorithm: str, chunk_size: int = CHUNK_SIZE) -> Tuple[str, int]:
    """Stream-hash a file; returns (hex digest, size in bytes)."""
    digest = hashlib.new(algorithm)
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _hash_symlink(target: str, algorithm: str) -> Tuple[str, int]:
    """Hash a symlink by hashing its (raw) target string."""
    raw = os.fsencode(target)
    digest = hashlib.new(algorithm)
    digest.update(raw)
    return digest.hexdigest(), len(raw)


@dataclass(frozen=True)
class Entry:
    """One manifest entry: normalized relative path, type, length, digest."""

    path: str
    type: str  # FILE_TYPE or SYMLINK_TYPE
    size: int
    digest: str
    link_target: Optional[str] = None

    def to_dict(self) -> dict:
        data = {
            "kind": "entry",
            "path": self.path,
            "type": self.type,
            "size": self.size,
            "digest": self.digest,
        }
        if self.link_target is not None:
            data["link_target"] = self.link_target
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Entry":
        path = normalize_relpath(data["path"])
        entry_type = data["type"]
        if entry_type not in (FILE_TYPE, SYMLINK_TYPE):
            raise ManifestFormatError(f"unknown entry type: {entry_type!r}")
        return cls(
            path=path,
            type=entry_type,
            size=int(data["size"]),
            digest=str(data["digest"]),
            link_target=data.get("link_target"),
        )


def _canonical_json(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _scan_tree(root: str, algorithm: str, exclude: Iterable[str]) -> List[Entry]:
    """Walk ``root`` without following symlinks and build sorted entries."""
    excluded = set()
    for item in exclude:
        excluded.add(normalize_relpath(item))

    entries: List[Entry] = []
    # Iterative DFS so arbitrarily deep trees do not hit recursion limits.
    stack: List[Tuple[str, str]] = [(root, "")]
    while stack:
        abs_dir, rel_dir = stack.pop()
        try:
            children = sorted(os.scandir(abs_dir), key=lambda c: c.name)
        except OSError:
            continue
        for child in children:
            rel = f"{rel_dir}/{child.name}" if rel_dir else child.name
            if rel in excluded:
                continue
            try:
                st = child.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(child.path)
                digest, size = _hash_symlink(target, algorithm)
                entries.append(
                    Entry(
                        path=rel,
                        type=SYMLINK_TYPE,
                        size=size,
                        digest=digest,
                        link_target=target,
                    )
                )
            elif stat.S_ISDIR(st.st_mode):
                stack.append((child.path, rel))
            elif stat.S_ISREG(st.st_mode):
                digest, size = _hash_file(child.path, algorithm)
                entries.append(Entry(path=rel, type=FILE_TYPE, size=size, digest=digest))
            # Other types (fifo, socket, device) are not part of a release
            # payload and are skipped.
    entries.sort(key=lambda e: e.path)
    return entries


def _case_collisions(paths: Iterable[str]) -> List[str]:
    """Return groups of paths that differ only by letter case."""
    by_lower: Dict[str, List[str]] = {}
    for path in paths:
        by_lower.setdefault(path.lower(), []).append(path)
    return sorted(
        "/".join(sorted(group)) for group in by_lower.values() if len(group) > 1
    )


@dataclass
class Manifest:
    """A parsed or freshly generated manifest."""

    entries: List[Entry]
    algorithm: str = DEFAULT_ALGORITHM
    created_utc: str = ""
    warnings: List[str] = field(default_factory=list)

    def signature_payload(self) -> str:
        """Canonical string whose digest is the manifest signature."""
        doc = {
            "algorithm": self.algorithm,
            "entries": [e.to_dict() for e in self.entries],
            "format": FORMAT_VERSION,
            "warnings": sorted(self.warnings),
        }
        return _canonical_json(doc)

    def compute_signature(self) -> str:
        digest = hashlib.new(self.algorithm)
        digest.update(self.signature_payload().encode("utf-8"))
        return digest.hexdigest()

    def to_text(self) -> str:
        lines = [
            _canonical_json(
                {
                    "kind": "header",
                    "format": FORMAT_VERSION,
                    "algorithm": self.algorithm,
                    "created_utc": self.created_utc,
                    "warnings": sorted(self.warnings),
                }
            )
        ]
        lines.extend(_canonical_json(e.to_dict()) for e in self.entries)
        lines.append(
            _canonical_json(
                {
                    "kind": "signature",
                    "algorithm": self.algorithm,
                    "digest": self.compute_signature(),
                }
            )
        )
        return "\n".join(lines) + "\n"

    @classmethod
    def parse(cls, text: str) -> Tuple["Manifest", str]:
        """Parse manifest text; returns (manifest, recorded signature)."""
        header = None
        signature = None
        entries: List[Entry] = []
        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ManifestFormatError(f"line {lineno}: invalid JSON: {exc}")
            kind = obj.get("kind")
            if kind == "header":
                if header is not None:
                    raise ManifestFormatError("duplicate header line")
                header = obj
            elif kind == "entry":
                entries.append(Entry.from_dict(obj))
            elif kind == "signature":
                if signature is not None:
                    raise ManifestFormatError("duplicate signature line")
                signature = obj
            else:
                raise ManifestFormatError(f"line {lineno}: unknown kind {kind!r}")
        if header is None:
            raise ManifestFormatError("missing header line")
        if signature is None:
            raise ManifestFormatError("missing signature line")
        algorithm = str(header.get("algorithm", DEFAULT_ALGORITHM))
        manifest = cls(
            entries=sorted(entries, key=lambda e: e.path),
            algorithm=algorithm,
            created_utc=str(header.get("created_utc", "")),
            warnings=list(header.get("warnings", [])),
        )
        return manifest, str(signature.get("digest", ""))

    @classmethod
    def load(cls, path: str) -> Tuple["Manifest", str]:
        with open(path, "r", encoding="utf-8") as handle:
            return cls.parse(handle.read())


def generate_manifest(
    root: str,
    algorithm: str = DEFAULT_ALGORITHM,
    exclude: Iterable[str] = (),
) -> Manifest:
    """Scan ``root`` and build a manifest of every file and symlink."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise NotADirectoryError(root)
    entries = _scan_tree(root, algorithm, exclude)
    warnings = [
        f"case-collision: {group}" for group in _case_collisions(e.path for e in entries)
    ]
    return Manifest(
        entries=entries,
        algorithm=algorithm,
        created_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        warnings=warnings,
    )


def write_manifest(manifest: Manifest, path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(manifest.to_text())


def verify_manifest_self(manifest_path: str) -> Tuple[bool, str, Optional[Manifest]]:
    """Check the manifest's own signature.

    Returns ``(ok, detail, manifest)``. ``manifest`` is ``None`` when the
    file cannot even be parsed.
    """
    try:
        manifest, recorded = Manifest.load(manifest_path)
    except (OSError, ManifestFormatError, UnsafePathError, KeyError, ValueError) as exc:
        return False, f"manifest is malformed: {exc}", None
    actual = manifest.compute_signature()
    if actual != recorded:
        return (
            False,
            f"manifest signature mismatch: recorded {recorded}, computed {actual}",
            manifest,
        )
    return True, "manifest signature ok", manifest


@dataclass
class VerificationReport:
    """Structured result of verifying a directory against a manifest."""

    manifest_ok: bool
    manifest_detail: str
    modified: List[Tuple[str, str]] = field(default_factory=list)  # (path, reason)
    deleted: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.manifest_ok
            and not self.modified
            and not self.deleted
            and not self.added
        )

    def to_dict(self) -> dict:
        return {
            "manifest_ok": self.manifest_ok,
            "manifest_detail": self.manifest_detail,
            "ok": self.ok,
            "modified": [{"path": p, "reason": r} for p, r in self.modified],
            "deleted": list(self.deleted),
            "added": list(self.added),
            "unchanged": list(self.unchanged),
            "warnings": list(self.warnings),
        }

    def to_text(self) -> str:
        lines = [
            f"manifest self-check: {'OK' if self.manifest_ok else 'FAILED'}"
            f" ({self.manifest_detail})"
        ]
        if not self.manifest_ok:
            lines.append(
                "verification aborted: the manifest itself was tampered with,"
                " so its entries cannot be trusted"
            )
            return "\n".join(lines)
        lines.append(f"modified: {len(self.modified)}")
        lines.extend(f"  M {path}  ({reason})" for path, reason in self.modified)
        lines.append(f"deleted: {len(self.deleted)}")
        lines.extend(f"  D {path}" for path in self.deleted)
        lines.append(f"added: {len(self.added)}")
        lines.extend(f"  A {path}" for path in self.added)
        lines.append(f"unchanged: {len(self.unchanged)}")
        for warning in self.warnings:
            lines.append(f"warning: {warning}")
        lines.append(f"result: {'PASS' if self.ok else 'FAIL'}")
        return "\n".join(lines)


def _entry_mismatch(expected: Entry, actual: Entry) -> Optional[str]:
    """Return a human-readable reason if ``actual`` differs from ``expected``."""
    if expected.type != actual.type:
        return f"type changed: {expected.type} -> {actual.type}"
    if expected.digest != actual.digest:
        if expected.size != actual.size:
            return f"content changed (size {expected.size} -> {actual.size})"
        return "content changed (same size)"
    return None


def verify_manifest(root: str, manifest_path: str) -> VerificationReport:
    """Verify directory ``root`` against the manifest at ``manifest_path``.

    The manifest's own signature is checked first; if it fails, directory
    verification is aborted because the entries cannot be trusted.
    """
    manifest_ok, detail, manifest = verify_manifest_self(manifest_path)
    if not manifest_ok or manifest is None:
        return VerificationReport(manifest_ok=False, manifest_detail=detail)

    manifest_name = normalize_relpath(os.path.basename(os.path.abspath(manifest_path)))
    actual_entries = _scan_tree(
        os.path.abspath(root), manifest.algorithm, exclude=[manifest_name]
    )
    actual_by_path = {e.path: e for e in actual_entries}

    report = VerificationReport(manifest_ok=True, manifest_detail=detail)
    report.warnings.extend(manifest.warnings)

    for expected in manifest.entries:
        actual = actual_by_path.pop(expected.path, None)
        if actual is None:
            report.deleted.append(expected.path)
            continue
        reason = _entry_mismatch(expected, actual)
        if reason is None:
            report.unchanged.append(expected.path)
        else:
            report.modified.append((expected.path, reason))

    report.added.extend(sorted(actual_by_path))
    return report
