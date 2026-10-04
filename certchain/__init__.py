"""certchain -- certificate chain building & validation, stdlib only.

Public API:

    from certchain import TrustStore, verify, load_certificates

    anchors = TrustStore(load_certificates("root-a.pem"))
    leaf, = load_certificates("leaf.pem")
    pool = load_certificates("intermediates.pem")
    result = verify(leaf, pool, anchors)
"""

from .builder import BuildResult, DeadEnd, TrustStore
from .validator import ChainError, ValidationResult, validate_path, verify
from .x509 import Certificate, load_certificates, parse_certificate

__all__ = [
    "BuildResult", "Certificate", "ChainError", "DeadEnd", "TrustStore",
    "ValidationResult", "load_certificates", "parse_certificate",
    "validate_path", "verify",
]
