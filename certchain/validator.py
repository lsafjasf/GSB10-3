"""Path validation for candidate chains.

Implements the checks that matter for the stated policy:

* validity period of every non-anchor certificate (RFC 5280 6.1.3;
  a certificate is invalid AT its notAfter instant),
* signature algorithm sanity (unknown / weak hashes rejected),
* basicConstraints: every issuer must be a CA; pathLenConstraint enforced,
* keyUsage: keyCertSign required on every CA certificate,
* name constraints (RFC 5280 6.1.3(g)/(h) + 4.2.1.10) for dNSName,
  rfc822Name, uniformResourceIdentifier and directoryName, applied to
  every certificate below the constraining CA.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from .builder import BuildResult, TrustStore
from .x509 import Certificate, GeneralName, NameConstraints


# --------------------------------------------------------------------------
# Result model
# --------------------------------------------------------------------------

@dataclass
class ChainError:
    code: str
    message: str
    cert_subject: str = ""

    def __str__(self) -> str:
        where = f" [{self.cert_subject}]" if self.cert_subject else ""
        return f"{self.code}{where}: {self.message}"


@dataclass
class ValidationResult:
    ok: bool
    status: str                       # OK | MISSING_INTERMEDIATE | INVALID
    chain: List[Certificate] = field(default_factory=list)
    errors: List[ChainError] = field(default_factory=list)
    tried_chains: int = 0

    def describe(self) -> str:
        lines = [f"status: {self.status}"]
        if self.chain:
            lines.append("chain:")
            for i, cert in enumerate(self.chain):
                lines.append(f"  [{i}] {cert.label()}")
        for err in self.errors:
            lines.append(f"error: {err}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Name constraint matching (RFC 5280 4.2.1.10)
# --------------------------------------------------------------------------

def _dns_matches(name: str, constraint: str) -> bool:
    name = name.lower().rstrip(".")
    constraint = constraint.lower().rstrip(".")
    if not constraint:
        return False
    if constraint.startswith("."):
        # ".example.com" matches hosts *within* example.com, not the apex
        return name.endswith(constraint) and name != constraint[1:]
    return name == constraint or name.endswith("." + constraint)


def _email_matches(addr: str, constraint: str) -> bool:
    addr = addr.lower()
    constraint = constraint.lower()
    if "@" in constraint:
        return addr == constraint
    if constraint.startswith("."):
        host = addr.rpartition("@")[2]
        return host.endswith(constraint) and host != constraint[1:]
    return addr.rpartition("@")[2] == constraint


def _uri_matches(uri: str, constraint: str) -> bool:
    host = uri.split("://", 1)[-1].split("/", 1)[0].split("@")[-1]
    host = host.split(":")[0].lower()
    constraint = constraint.lower()
    if constraint.startswith("."):
        return host.endswith(constraint) and host != constraint[1:]
    return host == constraint or host.endswith("." + constraint)


def _gn_matches(gn: GeneralName, constraint: GeneralName) -> bool:
    if gn.kind != constraint.kind:
        return False
    if gn.kind == "dns":
        return _dns_matches(str(gn.value), str(constraint.value))
    if gn.kind == "email":
        return _email_matches(str(gn.value), str(constraint.value))
    if gn.kind == "uri":
        return _uri_matches(str(gn.value), str(constraint.value))
    if gn.kind == "dir":
        c = constraint.value
        n = gn.value
        return len(n.rdns) >= len(c.rdns) and n.rdns[:len(c.rdns)] == c.rdns
    return False


def _subject_names(cert: Certificate) -> List[GeneralName]:
    """Names of the certificate subject to name constraints: SANs plus the
    subject DN itself (as a directoryName)."""
    names = list(cert.san)
    if cert.subject.rdns:
        names.append(GeneralName("dir", cert.subject))
    return names


def check_name_constraints(cert: Certificate,
                           constraints: NameConstraints) -> Optional[str]:
    """Return a violation description, or None if *cert* is within the
    permitted/excluded subtrees."""
    if not constraints.present:
        return None
    names = _subject_names(cert)
    for kind in ("dns", "email", "uri", "dir"):
        present = [c for c in constraints.permitted if c.kind == kind]
        excluded = [c for c in constraints.excluded if c.kind == kind]
        if not present and not excluded:
            continue
        of_kind = [n for n in names if n.kind == kind]
        # A constrained name space with no matching name in the cert is OK,
        # except directoryName: an empty subject DN violates a permitted
        # directoryName constraint (RFC 5280 4.2.1.10 final paragraph).
        if kind == "dir" and not cert.subject.rdns and present:
            return ("empty subject DN not within permitted "
                    "directoryName subtree")
        for name in of_kind:
            for ex in excluded:
                if _gn_matches(name, ex):
                    return (f"{kind} name {name.value!r} falls in excluded "
                            f"subtree {ex.value!r}")
            if present and not any(_gn_matches(name, p) for p in present):
                return (f"{kind} name {name.value!r} not within any "
                        f"permitted subtree")
    return None


# --------------------------------------------------------------------------
# Path validation
# --------------------------------------------------------------------------

def _validity_error(cert: Certificate, when: datetime) -> Optional[ChainError]:
    if when < cert.not_before:
        return ChainError(
            "NOT_YET_VALID",
            f"valid from {cert.not_before.isoformat()}, "
            f"validation time {when.isoformat()}",
            cert_subject=str(cert.subject))
    if when >= cert.not_after:  # invalid AT notAfter (RFC 5280 6.1.3(a)(2))
        return ChainError(
            "EXPIRED",
            f"expired at {cert.not_after.isoformat()}, "
            f"validation time {when.isoformat()}",
            cert_subject=str(cert.subject))
    return None


def validate_path(chain: Sequence[Certificate],
                  anchors: TrustStore,
                  when: Optional[datetime] = None) -> List[ChainError]:
    """Validate a single candidate chain leaf..anchor. Returns errors."""
    when = when or datetime.now(timezone.utc)
    errors: List[ChainError] = []
    n = len(chain)

    for i, cert in enumerate(chain):
        is_anchor = i == n - 1 and anchors.is_anchor(cert)
        is_leaf = i == 0
        subject = str(cert.subject)

        # signature algorithm policy (checked for every non-anchor cert)
        if not is_anchor:
            try:
                scheme = cert.sig_scheme
                if scheme[1] in ("md5", "sha1"):
                    errors.append(ChainError(
                        "WEAK_SIGNATURE_ALGORITHM",
                        f"certificate signed with {scheme[1]}", subject))
            except ValueError:
                errors.append(ChainError(
                    "UNSUPPORTED_SIGNATURE_ALGORITHM",
                    f"signature algorithm OID {cert.sig_alg_oid}", subject))

            err = _validity_error(cert, when)
            if err is not None:
                errors.append(err)

        # issuer-side constraints apply to every cert that issues another
        if is_leaf:
            continue
        if not cert.has_basic_constraints or not cert.is_ca:
            errors.append(ChainError(
                "NOT_A_CA",
                "issuer lacks basicConstraints CA=TRUE", subject))
        if cert.key_usage is not None and not cert.key_cert_sign:
            errors.append(ChainError(
                "KEY_USAGE_NO_CERTSIGN",
                "issuer keyUsage lacks keyCertSign", subject))
        if cert.path_len is not None:
            # number of non-self-issued intermediate CA certs that may
            # follow below this certificate (RFC 5280 4.2.1.9)
            ca_below = sum(
                1 for j in range(i - 1, 0, -1)
                if chain[j].subject != chain[j].issuer)
            if ca_below > cert.path_len:
                errors.append(ChainError(
                    "PATH_LEN_EXCEEDED",
                    f"pathLenConstraint={cert.path_len} but {ca_below} "
                    f"intermediate CA certificate(s) below", subject))

    # name constraints accumulate top-down from each CA to everything below
    for i in range(1, n):
        nc = chain[i].name_constraints
        if not nc.present:
            continue
        for j in range(i - 1, -1, -1):
            violation = check_name_constraints(chain[j], nc)
            if violation is not None:
                errors.append(ChainError(
                    "NAME_CONSTRAINT_VIOLATION",
                    f"{violation} (constraint enforced by "
                    f"[{chain[i].subject}])",
                    str(chain[j].subject)))
    return errors


# --------------------------------------------------------------------------
# Top-level entry point
# --------------------------------------------------------------------------

def verify(leaf: Certificate,
           pool: Sequence[Certificate],
           anchors: TrustStore,
           when: Optional[datetime] = None) -> ValidationResult:
    """Build candidate chains from *leaf* to the pinned anchors and return
    the first fully valid one; otherwise report precise diagnostics."""
    when = when or datetime.now(timezone.utc)
    build = anchors.build(leaf, pool)

    best_errors: List[ChainError] = []
    for chain in build.chains:
        errors = validate_path(chain, anchors, when)
        if not errors:
            return ValidationResult(
                True, "OK", chain=chain,
                tried_chains=len(build.chains) + len(build.untrusted))
        if len(errors) < len(best_errors) or not best_errors:
            best_errors = errors

    if not build.chains:
        # construction failed before any chain reached an anchor
        errors: List[ChainError] = []
        if build.untrusted:
            top = build.untrusted[0][-1]
            errors.append(ChainError(
                "UNTRUSTED_ROOT",
                f"chain terminates at self-signed certificate "
                f"subject=[{top.subject}] which is NOT one of the "
                f"configured trust anchors",
                cert_subject=str(top.subject)))
        for dead in build.dead_ends:
            if (dead.reason == "UNTRUSTED_ROOT" and build.untrusted):
                continue  # already reported with the full chain above
            if dead.reason == "MISSING_INTERMEDIATE":
                aki = f", authorityKeyId={dead.needed_aki}" if dead.needed_aki else ""
                errors.append(ChainError(
                    "MISSING_INTERMEDIATE",
                    f"no issuer found for certificate "
                    f"subject=[{dead.cert.subject}]; need an issuer with "
                    f"subject=[{dead.needed_issuer}]{aki} in the "
                    f"intermediate pool or trust store",
                    cert_subject=str(dead.cert.subject)))
            else:
                errors.append(ChainError(
                    dead.reason, f"chain building stopped at "
                    f"subject=[{dead.cert.subject}]",
                    cert_subject=str(dead.cert.subject)))
        status = "MISSING_INTERMEDIATE" if build.missing else "INVALID"
        rejected = build.untrusted[0] if build.untrusted else []
        return ValidationResult(
            False, status, chain=rejected, errors=errors,
            tried_chains=len(build.chains) + len(build.untrusted))

    return ValidationResult(
        False, "INVALID", errors=best_errors,
        tried_chains=len(build.chains) + len(build.untrusted))
