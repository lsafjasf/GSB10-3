"""Runnable parse/decision samples.

Run:  python3 demo.py
"""

from forwarded import TrustedProxies, parse_chain, resolve_client

TRUST = TrustedProxies(["10.0.0.0/8", "192.168.10.1/32", "2001:db8::/48"])

CASES = [
    # (title, direct peer, X-Forwarded-For raw value)
    ("1. direct client, no proxy",
     "203.0.113.50", None),
    ("2. direct client with spoofed header",
     "203.0.113.50", "10.0.0.1, 198.51.100.7"),
    ("3. single trusted proxy",
     "192.168.10.1", "203.0.113.50"),
    ("4. single proxy + client-prepended forgery",
     "192.168.10.1", "1.2.3.4, 203.0.113.50"),
    ("5. two trusted proxies",
     "10.0.0.2", "203.0.113.50, 192.168.10.1"),
    ("6. forged admin prefix through two proxies",
     "10.0.0.2", "10.0.0.99, 169.254.0.1, 203.0.113.50, 192.168.10.1"),
    ("7. invalid item where client should be",
     "10.0.0.2", "203.0.113.50, garbage, 192.168.10.1"),
    ("8. invalid item to the left of real client",
     "10.0.0.2", "garbage, 203.0.113.50, 192.168.10.1"),
    ("9. unknown marker and empty slot",
     "10.0.0.2", "unknown, 203.0.113.50, , 192.168.10.1"),
]


def main() -> None:
    for title, peer, header in CASES:
        print(f"== {title}")
        print(f"   socket peer      : {peer}")
        print(f"   X-Forwarded-For  : {header!r}")
        report = parse_chain(header)
        for entry in report.entries:
            mark = "ok " if entry.usable else "BAD"
            print(f"     [{mark}] #{entry.index} raw={entry.raw!r} "
                  f"kind={entry.kind}")
        result = resolve_client(peer, header, TRUST)
        print(f"   -> {result.summary()}")
        print()


if __name__ == "__main__":
    main()
