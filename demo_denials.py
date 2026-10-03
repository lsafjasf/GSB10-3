"""Print one denial record per rejection reason (run: python3 demo_denials.py)."""

from authcode import AuthorizationServer, ExchangeDenied, make_pkce_pair


def show(title, fn):
    print(f"--- {title} ---")
    try:
        fn()
        print("  accepted?!")
    except ExchangeDenied as exc:
        print(f"  reason = {exc.reason}")
        print(f"  detail = {exc.detail}")
    print()


clock_value = 1000.0


def clock():
    return clock_value


def main():
    global clock_value

    # 1. happy path, so the ledger stays in a realistic state
    server = AuthorizationServer(code_ttl=60, clock=clock)
    pair = make_pkce_pair()
    code = server.authorize("client-a", pair["code_challenge"])
    server.exchange(code, pair["code_verifier"])

    show("replay (code already used)", lambda: server.exchange(code, pair["code_verifier"]))
    show("unknown code", lambda: AuthorizationServer(clock=clock).exchange("forged", pair["code_verifier"]))
    show("verifier missing", lambda: _fresh(server, pair, verifier=None))
    show("verifier malformed", lambda: _fresh(server, pair, verifier="short!"))
    show("verifier tampered", lambda: _fresh(server, pair, verifier="z" + pair["code_verifier"][1:]))
    show("algorithm mismatch", _method_mismatch)

    server2 = AuthorizationServer(code_ttl=10, clock=clock)
    pair2 = make_pkce_pair()
    code2 = server2.authorize("client-a", pair2["code_challenge"])
    clock_value += 999
    show("code expired", lambda: server2.exchange(code2, pair2["code_verifier"]))

    print("=== denial ledger (server with replay + verifier failures) ===")
    for d in server.denials:
        print(f"  {d.at:>10.1f}  {d.code_hint:<12} {d.reason:<22} {d.detail}")


def _fresh(server, pair, verifier):
    code = server.authorize("client-a", pair["code_challenge"])
    return server.exchange(code, verifier)


def _method_mismatch():
    global clock_value
    server = AuthorizationServer(code_ttl=60, clock=clock)
    pair = make_pkce_pair()
    code = server.authorize("client-a", pair["code_challenge"])
    server._codes[code].method = "MD5"  # stored record advertises an unsupported alg
    return server.exchange(code, pair["code_verifier"])


if __name__ == "__main__":
    main()
