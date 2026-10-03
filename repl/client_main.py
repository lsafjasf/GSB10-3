"""CLI entry point: python3 -m repl.client_main [options]

Appends every consumed record as one line "position record" to the consumed
file, so an external process can assert the consumption sequence across
restarts.
"""

import argparse

from .puller import GapError, Puller


def main(argv=None):
    parser = argparse.ArgumentParser(description="replication log puller")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--consumed", required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--start-position", type=int, default=0)
    parser.add_argument("--max-batches", type=int, default=10000)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--max-retries", type=int, default=5)
    args = parser.parse_args(argv)

    def consume(position, record):
        with open(args.consumed, "a", encoding="utf-8") as fh:
            fh.write("%d %s\n" % (position, record))

    puller = Puller(
        args.host, args.port, args.checkpoint, consume,
        batch_size=args.batch_size, start_position=args.start_position,
        timeout=args.timeout, max_retries=args.max_retries,
    )
    try:
        batches = puller.run_until_caught_up(max_batches=args.max_batches)
    except GapError as exc:
        print("GAP: %s" % exc)
        return 2
    print("confirmed=%d batches=%d duplicates=%d retries=%d"
          % (puller.confirmed, batches, puller.duplicate_batches, puller.retries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
