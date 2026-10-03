"""Pure-standard-library sparse preconditioned iterative solvers."""

from .csr_matrix import CSRMatrix
from .preconditioners import (
    IdentityPreconditioner,
    JacobiPreconditioner,
    ILU0Preconditioner,
    PreconditionerError,
)
from .krylov import GMRESResult, gmres, residual_norm, norm

__all__ = [
    "CSRMatrix",
    "IdentityPreconditioner",
    "JacobiPreconditioner",
    "ILU0Preconditioner",
    "PreconditionerError",
    "GMRESResult",
    "gmres",
    "residual_norm",
    "norm",
]
