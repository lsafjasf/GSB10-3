"""Minimal semver implementation (stdlib only).

Supports:
  - Versions: MAJOR.MINOR.PATCH with optional -prerelease and +build.
  - Ranges: comma/space separated comparators (AND), '||' for OR.
  - Comparators: <, <=, >, >=, = (or bare), ^ (caret), ~ (tilde).
  - Partial versions in ranges are zero-padded ("1.2" -> "1.2.0").
"""

import re

_VERSION_RE = re.compile(
    r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?\s*$"
)


class VersionError(ValueError):
    pass


class Version:
    __slots__ = ("major", "minor", "patch", "prerelease")

    def __init__(self, major, minor, patch, prerelease=()):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = tuple(prerelease)

    @classmethod
    def parse(cls, text):
        m = _VERSION_RE.match(text)
        if not m:
            raise VersionError("invalid version: %r" % text)
        major, minor, patch, pre = m.groups()
        pre_parts = tuple(pre.split(".")) if pre else ()
        for part in pre_parts:
            if not part:
                raise VersionError("invalid prerelease: %r" % text)
        return cls(int(major), int(minor or 0), int(patch or 0), pre_parts)

    def _key(self):
        # Prerelease ordering: a version without prerelease > with prerelease;
        # numeric identifiers compare numerically and are lower than strings.
        if not self.prerelease:
            pre_key = (1,)
        else:
            pre_key = (0,) + tuple(
                (0, int(p)) if p.isdigit() else (1, p) for p in self.prerelease
            )
        return (self.major, self.minor, self.patch, pre_key)

    def __eq__(self, other):
        return self._key() == other._key()

    def __lt__(self, other):
        return self._key() < other._key()

    def __le__(self, other):
        return self._key() <= other._key()

    def __gt__(self, other):
        return self._key() > other._key()

    def __ge__(self, other):
        return self._key() >= other._key()

    def __hash__(self):
        return hash(self._key())

    def __str__(self):
        s = "%d.%d.%d" % (self.major, self.minor, self.patch)
        if self.prerelease:
            s += "-" + ".".join(self.prerelease)
        return s

    def __repr__(self):
        return "Version(%s)" % self


def _satisfies_comparator(version, op, bound):
    if op in ("", "="):
        return version == bound
    if op == "<":
        return version < bound
    if op == "<=":
        return version <= bound
    if op == ">":
        return version > bound
    if op == ">=":
        return version >= bound
    if op == "^":
        if bound.major > 0:
            upper = Version(bound.major + 1, 0, 0)
        elif bound.minor > 0:
            upper = Version(0, bound.minor + 1, 0)
        else:
            upper = Version(0, 0, bound.patch + 1)
        return version >= bound and version < upper
    if op == "~":
        upper = Version(bound.major, bound.minor + 1, 0)
        return version >= bound and version < upper
    raise VersionError("unknown operator: %r" % op)


_COMPARATOR_RE = re.compile(r"^\s*(\^|~|>=|<=|>|<|=)?\s*(\S+)\s*$")


def satisfies(version_text, range_text):
    """Return True if version_text satisfies range_text."""
    version = Version.parse(version_text)
    range_text = range_text.strip()
    if range_text in ("", "*"):
        return True
    for alternative in range_text.split("||"):
        comparators = [c for c in re.split(r"[,\s]+", alternative.strip()) if c]
        ok = True
        for comp in comparators:
            m = _COMPARATOR_RE.match(comp)
            if not m:
                raise VersionError("invalid comparator: %r" % comp)
            op, ver_text = m.groups()
            bound = Version.parse(ver_text)
            if not _satisfies_comparator(version, op or "", bound):
                ok = False
                break
        if ok:
            return True
    return False
