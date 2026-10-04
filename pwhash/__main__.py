"""Demo: first setup, legacy verification, in-place upgrade, corrupt record.

Run:  python -m pwhash
"""

import json

from .core import CURRENT_PARAMS, hash_password, needs_upgrade, verify_password
from .userdb import UserDB

# A "legacy" parameter set, as if recorded by an older build of the library.
LEGACY_ITERATIONS = 10_000


def show(title, record):
    print(f"  {title}:")
    print("    " + json.dumps(record, ensure_ascii=False, sort_keys=True))


def main():
    print(f"current parameters: alg=pbkdf2-sha256 params={CURRENT_PARAMS}")
    print(f"legacy parameters : alg=pbkdf2-sha256 params={{'iterations': {LEGACY_ITERATIONS}}}")

    db = UserDB()

    print("\n[1] first setup (current parameters, per-user random salt)")
    rec_alice = db.create("alice", "correct horse battery staple")
    show("alice record", rec_alice)
    r = db.login("alice", "correct horse battery staple")
    print(f"  login alice ok={r.ok} upgraded={r.upgraded}")
    r = db.login("alice", "wrong password")
    print(f"  login alice (wrong password) ok={r.ok}")

    print("\n[2] salt randomness: same password, two users")
    rec_bob = db.create("bob", "shared-password")
    rec_carol = db.create("carol", "shared-password")
    show("bob record", rec_bob)
    show("carol record", rec_carol)
    assert rec_bob["salt"] != rec_carol["salt"], "salts must differ per user"
    assert rec_bob["hash"] != rec_carol["hash"], "hashes must differ per user"
    print("  assert salt(bob) != salt(carol)  -> OK")
    print("  assert hash(bob) != hash(carol)  -> OK")

    print("\n[3] legacy record: verify with the record's own parameters")
    legacy = hash_password("hunter2", iterations=LEGACY_ITERATIONS)
    db.set_raw_record("dave", legacy)
    show("dave record (legacy, before login)", db.get_record("dave"))
    print(f"  needs_upgrade(dave) = {db.needs_upgrade('dave')}")
    print(f"  verify_password with record params -> {verify_password('hunter2', db.get_record('dave'))}")

    print("\n[4] successful login upgrades the record in place")
    result = db.login("dave", "hunter2")
    print(f"  login ok={result.ok} upgraded={result.upgraded}")
    show("BEFORE", result.before)
    show("AFTER ", result.after)
    print(f"  verify AFTER record with new params -> {verify_password('hunter2', result.after)}")
    print(f"  needs_upgrade(dave) after login = {db.needs_upgrade('dave')}")
    again = db.login("dave", "hunter2")
    print(f"  second login ok={again.ok} upgraded={again.upgraded} (no re-upgrade)")

    print("\n[5] failed login never upgrades")
    db.set_raw_record("erin", hash_password("pw", iterations=LEGACY_ITERATIONS))
    before = dict(db.get_record("erin"))
    r = db.login("erin", "not-the-password")
    print(f"  login ok={r.ok} upgraded={r.upgraded}; record unchanged: {db.get_record('erin') == before}")

    print("\n[6] corrupt record never authenticates")
    db.create("frank", "pw")
    db.corrupt("frank")
    show("frank record (corrupt)", db.get_record("frank"))
    r = db.login("frank", "pw")
    print(f"  login ok={r.ok} upgraded={r.upgraded}")

    print("\nall demo assertions passed.")


if __name__ == "__main__":
    main()
