"""Shared deterministic scenario builders for the regression suite."""


def fake_clock(start=0.0):
    state = {"now": float(start)}

    def clock():
        return state["now"]

    clock.set = lambda value: state.__setitem__("now", float(value))
    clock.advance = lambda delta: state.__setitem__("now", state["now"] + delta)
    return clock


def scripted_elapsed(script, default=0.0):
    """elapsed() returning scripted values in order, then `default`."""
    values = iter(script)
    return lambda: next(values, default)


def ok_fetcher(value="v"):
    return lambda attempt: value


def flaky_fetcher(failures, exc_cls, value="v"):
    """Fails with exc_cls on the first `failures` attempts, then succeeds."""

    def fetch(attempt):
        if attempt < failures:
            raise exc_cls("attempt %d" % attempt)
        return value

    return fetch


def always_failing_fetcher(exc_cls):
    def fetch(attempt):
        raise exc_cls("attempt %d" % attempt)

    return fetch
