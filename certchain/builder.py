"""Chain building against a pinned set of trust anchors.

The builder explores every issuer candidate (certificate pool + anchors),
verifying each signature link as it goes, so cross-signed hierarchies are
handled naturally: an intermediate signed by two roots simply yields two
candidate parents, and only paths that terminate at a *configured* anchor
(identity = subject name AND public key) are returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .crypto import verify_signature
from .x509 import Certificate


@dataclass
class DeadEnd:
    """Why chain construction stopped at a particular certificate."""
    cert: Certificate
    reason: str
    needed_issuer: Optional[str] = None   # subject DN we were looking for
    needed_aki: Optional[str] = None      # authority key id, hex, if present


@dataclass
class BuildResult:
    chains: List[List[Certificate]] = field(default_factory=list)
    # complete paths that terminate at a self-signed certificate which is
    # NOT a configured trust anchor (kept for diagnostics / rejection proof)
    untrusted: List[List[Certificate]] = field(default_factory=list)
    dead_ends: List[DeadEnd] = field(default_factory=list)

    @property
    def missing(self) -> List[DeadEnd]:
        return [d for d in self.dead_ends if d.reason == "MISSING_INTERMEDIATE"]


def _key_id(cert: Certificate) -> bytes:
    """Stable identity of a certificate's public key."""
    return cert.spki.raw


class TrustStore:
    """A fixed set of trust anchors. Only chains terminating at one of
    these anchors (matched by subject *and* public key) are accepted."""

    def __init__(self, anchors: Sequence[Certificate]):
        if not anchors:
            raise ValueError("trust store must contain at least one anchor")
        self.anchors: List[Certificate] = list(anchors)
        self._by_subject = {}
        for anchor in self.anchors:
            self._by_subject.setdefault(anchor.subject, []).append(anchor)

    # -- anchor identity --------------------------------------------------

    def is_anchor(self, cert: Certificate) -> bool:
        """True iff *cert* is exactly one of the configured anchors
        (same subject name and same public key)."""
        for anchor in self._by_subject.get(cert.subject, []):
            if anchor.spki.raw == cert.spki.raw:
                return True
        return False

    def matching_anchor(self, cert: Certificate) -> Optional[Certificate]:
        """Return the configured anchor that issued *cert*, if any."""
        for anchor in self._by_subject.get(cert.issuer, []):
            ok, _ = verify_signature(anchor.spki, cert.sig_alg_oid,
                                     cert.tbs, cert.signature)
            if ok:
                return anchor
        return None

    # -- chain construction ------------------------------------------------

    def build(self, leaf: Certificate, pool: Sequence[Certificate],
              max_depth: int = 8) -> BuildResult:
        """Find every chain leaf..anchor. *pool* holds untrusted
        intermediates (and possibly extra certs, which are ignored)."""
        result = BuildResult()
        index = {}
        for cert in pool:
            index.setdefault(cert.subject, []).append(cert)

        def link_ok(child: Certificate, parent: Certificate) -> bool:
            if child.aki is not None and parent.ski is not None \
                    and child.aki != parent.ski:
                return False
            ok, _ = verify_signature(parent.spki, child.sig_alg_oid,
                                     child.tbs, child.signature)
            return ok

        def walk(cert: Certificate, path: List[Certificate],
                 seen: set, depth: int) -> None:
            # 1. cert itself is a configured anchor -> done
            if self.is_anchor(cert):
                result.chains.append(list(path))
                return
            # 2. cert was issued directly by a configured anchor -> done
            anchor = self.matching_anchor(cert)
            if anchor is not None:
                result.chains.append(path + [anchor])
                return
            if depth >= max_depth:
                result.dead_ends.append(DeadEnd(cert, "MAX_DEPTH_EXCEEDED"))
                return
            # 3. try every candidate issuer from the untrusted pool
            candidates = index.get(cert.issuer, [])
            progressed = False
            for parent in candidates:
                if _key_id(parent) in seen:
                    continue
                if not link_ok(cert, parent):
                    continue
                progressed = True
                walk(parent, path + [parent], seen | {_key_id(parent)}, depth + 1)
            if not progressed:
                if cert.issuer == cert.subject:
                    ok, _ = verify_signature(cert.spki, cert.sig_alg_oid,
                                             cert.tbs, cert.signature)
                    if ok:
                        result.untrusted.append(list(path))
                        result.dead_ends.append(DeadEnd(
                            cert, "UNTRUSTED_ROOT",
                            needed_issuer=str(cert.issuer)))
                        return
                aki = cert.aki.hex() if cert.aki is not None else None
                result.dead_ends.append(DeadEnd(
                    cert, "MISSING_INTERMEDIATE",
                    needed_issuer=str(cert.issuer), needed_aki=aki))

        walk(leaf, [leaf], {_key_id(leaf)}, 0)
        return result
