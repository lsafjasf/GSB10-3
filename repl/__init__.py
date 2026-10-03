"""At-least-once replication log puller with batch checkpoints.

- log_source: in-memory source log supporting purge and fault injection.
- server:     TCP JSON-lines front end for LogSource.
- puller:     client with checkpoint, timeout/retry, dedup and gap report.
- client_main: command line entry point (used by the subprocess restart test).
"""

from .puller import GapError, Puller

__all__ = ["GapError", "Puller"]
