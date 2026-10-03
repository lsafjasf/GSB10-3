"""Scripted, deterministic per-round packet-loss patterns.

A "loss script" is a sequence aligned with simulated RTT rounds. Each entry
is a :class:`LossSpec`:

  * ``0`` / ``None`` / ``{"lost": 0}`` -> no loss in that round.
  * a positive int ``k``, or ``{"lost": k}`` -> exactly ``k`` MSS are lost
    in that round; the reaction is *inferred*:
        - at least one ACK for later data is observed
          (acks_seen >= cwnd - lost >= 3) -> three duplicate ACKs,
        - otherwise -> timeout.
  * ``{"lost": k, "reaction": "fast"|"rto"}`` forces the reaction.
  * the strings ``"fast"`` / ``"rto"`` are shorthand for exactly one loss
    with that reaction forced.

Because scripts are plain data (plus an explicit seed for the policy-based
path), the same inputs always produce the same back-to-back loss behavior.
"""

from types import MappingProxyType

FORCE_FAST = "fast"
FORCE_RTO = "rto"


class LossSpec:
    __slots__ = ("lost", "reaction")

    def __init__(self, lost=0, reaction=None):
        lost = int(lost)
        if lost < 0:
            raise ValueError("lost must be non-negative")
        if reaction not in (None, FORCE_FAST, FORCE_RTO):
            raise ValueError("reaction must be None, %r or %r"
                             % (FORCE_FAST, FORCE_RTO))
        self.lost = lost
        self.reaction = reaction if lost > 0 else None

    @property
    def has_loss(self):
        return self.lost > 0

    def resolved_reaction(self, cwnd):
        """Return the concrete reaction ('fast' or 'rto') for a loss round."""
        if self.reaction is not None:
            return self.reaction
        acks_seen = cwnd - self.lost
        return FORCE_FAST if acks_seen >= 3 else FORCE_RTO

    def __eq__(self, other):
        return (isinstance(other, LossSpec)
                and self.lost == other.lost
                and self.reaction == other.reaction)

    def __repr__(self):
        return "LossSpec(lost=%d, reaction=%r)" % (self.lost, self.reaction)


_NO_LOSS = LossSpec(0)


def _parse_entry(entry):
    if isinstance(entry, str):
        if entry not in (FORCE_FAST, FORCE_RTO):
            raise ValueError("unknown loss shorthand %r" % entry)
        return LossSpec(1, entry)
    if isinstance(entry, LossSpec):
        return entry
    if entry is None:
        return _NO_LOSS
    if isinstance(entry, int) and not isinstance(entry, bool):
        return LossSpec(entry)
    if isinstance(entry, MappingProxyType) or isinstance(entry, dict):
        return LossSpec(entry.get("lost", 0), entry.get("reaction"))
    if isinstance(entry, (list, tuple)):
        if len(entry) not in (1, 2):
            raise ValueError("loss tuple must be [lost] or [lost, reaction]")
        return LossSpec(entry[0], entry[1] if len(entry) == 2 else None)
    raise TypeError("unsupported loss entry: %r" % (entry,))


def build_script(entries):
    """Normalize a user-provided script into a tuple of :class:`LossSpec`."""
    if entries is None:
        return ()
    if callable(entries):
        raise TypeError("callable loss policies are handled by the Simulator")
    return tuple(_parse_entry(e) for e in entries)


def coerce_spec(round_index, cwnd, source):
    """Resolve one round's source entry (data or policy) to a LossSpec."""
    if callable(source):
        return _parse_entry(source(round_index, cwnd))
    return _parse_entry(source)
