"""Weighted fair scheduler (stride scheduling), pure standard library.

Model
-----
- Work is organized into named *classes* (e.g. priority levels). Each class
  has a non-negative integer weight and a FIFO queue of items.
- The scheduler hands out *processing opportunities* one at a time via
  ``dispatch()``. Every opportunity has equal cost, so weights translate
  directly into long-run shares of service.

Guarantees
----------
- Proportional fairness: over any window in which a set of positive-weight
  classes is continuously backlogged, class ``c`` receives a share of
  service that converges to ``weight_c / sum(weights)``, with per-class
  discrepancy bounded by a small constant (stride scheduling bound).
- No starvation: any positive-weight class that has pending items is
  guaranteed to be dispatched within a bounded number of opportunities,
  regardless of how much higher-weight traffic exists.
- Determinism: identical sequences of ``add_class`` / ``enqueue`` /
  ``dispatch`` calls always produce identical dispatch sequences. All
  ties are broken by a fixed class order (order of ``add_class`` calls);
  within a class, items are served FIFO.
- Zero weight: a class with weight 0 is *best-effort*. It is served only
  when no positive-weight class is backlogged, in which case zero-weight
  classes share opportunities in round-robin (by class order). A
  zero-weight class may therefore starve under sustained positive-weight
  load; this is the documented, tested semantics.
"""

from collections import deque
from fractions import Fraction

__all__ = ["WeightedFairScheduler"]


class WeightedFairScheduler:
    """Stride scheduler over named FIFO classes with integer weights."""

    def __init__(self):
        self._classes = {}          # name -> state dict
        self._order = []            # class names in add_class order (tie-break order)
        self._zero_cursor = 0       # round-robin cursor over zero-weight classes
        self._served = {}           # name -> number of dispatched items
        # Monotonic lower bound on the pass of any backlogged positive-weight
        # class ("system virtual time"). Never decreases while the system is
        # continuously busy; this is what makes the schedule starvation-free
        # even when classes oscillate between idle and backlogged.
        self._virtual_min = Fraction(0)

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #
    def add_class(self, name, weight):
        """Register a class. ``weight`` must be a non-negative int.

        The order of ``add_class`` calls defines the deterministic
        tie-breaking order between classes.
        """
        if name in self._classes:
            raise ValueError(f"class {name!r} already exists")
        if isinstance(weight, bool) or not isinstance(weight, int):
            raise TypeError("weight must be an int")
        if weight < 0:
            raise ValueError("weight must be >= 0")
        self._classes[name] = {
            "weight": weight,
            "stride": Fraction(1, weight) if weight > 0 else None,
            "pass": Fraction(0),
            "queue": deque(),
        }
        self._order.append(name)
        self._served[name] = 0

    # ------------------------------------------------------------------ #
    # Producing work
    # ------------------------------------------------------------------ #
    def enqueue(self, name, item):
        """Append ``item`` to the FIFO queue of class ``name``."""
        state = self._classes.get(name)
        if state is None:
            raise KeyError(f"unknown class {name!r}")
        was_inactive = not state["queue"]
        state["queue"].append(item)
        if was_inactive and state["weight"] > 0:
            if not self._any_other_positive_backlogged(name):
                # The system was idle of positive-weight work: start a new
                # scheduling epoch from zero. Pass values of idle classes
                # are irrelevant until they reactivate, so resetting them
                # keeps the schedule deterministic and the numbers small.
                for st in self._classes.values():
                    st["pass"] = Fraction(0)
                self._virtual_min = Fraction(0)
            else:
                # Reactivation rule: rejoin at the current virtual time, but
                # never move a class's pass backwards. Clamping (instead of
                # resetting) keeps pass values monotonic, which is exactly
                # what prevents a frequently oscillating class from
                # repeatedly resetting itself into winning every tie and
                # starving lower-weight classes.
                if state["pass"] < self._virtual_min:
                    state["pass"] = self._virtual_min

    # ------------------------------------------------------------------ #
    # Consuming work
    # ------------------------------------------------------------------ #
    def dispatch(self):
        """Select the next item to process.

        Returns ``(class_name, item)`` or ``None`` when nothing is pending.
        """
        name = self._select_class()
        if name is None:
            return None
        state = self._classes[name]
        item = state["queue"].popleft()
        if state["weight"] > 0:
            # The selected class held the minimum pass among backlogged
            # classes, so virtual time advances to it (monotonically).
            self._virtual_min = state["pass"]
            state["pass"] += state["stride"]
        self._served[name] += 1
        return name, item

    def pending(self, name=None):
        """Number of queued items, overall or for one class."""
        if name is not None:
            return len(self._classes[name]["queue"])
        return sum(len(s["queue"]) for s in self._classes.values())

    def served_counts(self):
        """Mapping of class name -> number of dispatched items so far."""
        return dict(self._served)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _select_class(self):
        best_name = None
        best_pass = None
        for name in self._order:  # fixed order => deterministic ties
            state = self._classes[name]
            if state["weight"] > 0 and state["queue"]:
                if best_pass is None or state["pass"] < best_pass:
                    best_pass = state["pass"]
                    best_name = name
        if best_name is not None:
            return best_name
        # Best-effort tier: only zero-weight classes remain. Serve them in
        # deterministic round-robin following the class registration order.
        zero_names = [n for n in self._order if self._classes[n]["weight"] == 0]
        if not zero_names:
            return None
        for offset in range(len(zero_names)):
            idx = (self._zero_cursor + offset) % len(zero_names)
            name = zero_names[idx]
            if self._classes[name]["queue"]:
                self._zero_cursor = (idx + 1) % len(zero_names)
                return name
        return None

    def _any_other_positive_backlogged(self, exclude):
        return any(
            s["weight"] > 0 and s["queue"]
            for n, s in self._classes.items()
            if n != exclude
        )
