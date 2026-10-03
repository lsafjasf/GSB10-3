"""演示 CLI：

    python3 -m forwarded_trust --direct 10.0.0.2 \
        --trusted 10.0.0.0/8 --xff "203.0.113.7, 10.0.0.1"
"""

import argparse
import json
import sys

from .core import resolve_client


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="forwarded_trust")
    parser.add_argument("--direct", required=True, help="直连对端地址（TCP peer）")
    parser.add_argument("--trusted", nargs="+", default=["10.0.0.0/8", "127.0.0.0/8"],
                        help="可信代理网段（CIDR），可多个")
    parser.add_argument("--xff", default=None, help="X-Forwarded-For 头部值")
    parser.add_argument("--forwarded", default=None, help="Forwarded 头部值")
    args = parser.parse_args(argv)

    result = resolve_client(
        args.direct,
        args.trusted,
        x_forwarded_for=args.xff,
        forwarded=args.forwarded,
    )
    out = {
        "client_ip": str(result.client_ip),
        "source": result.source,
        "direct_ip": str(result.direct_ip),
        "direct_trusted": result.trusted,
        "trusted_hops": [str(h) for h in result.hops],
        "blocked_index": result.blocked_index,
        "spoofable": result.spoofable,
        "reason": result.reason,
    }
    if result.chain is not None:
        out["chain"] = [
            {"index": e.index, "raw": e.raw,
             "ip": str(e.ip) if e.ip else None, "error": e.error}
            for e in result.chain.entries
        ]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
