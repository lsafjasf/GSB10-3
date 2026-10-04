"""Toy fuzz target: a tiny binary record parser.

Input format:  b"FUZZ" + 1 type byte + payload.
Returns the set of executed edge ids, or None for inputs the parser rejects
(outside its input language). Deterministic, stdlib only.
"""

from typing import FrozenSet, Optional

MAGIC = b"FUZZ"


def run(data: bytes) -> Optional[FrozenSet[int]]:
    edges = {0}  # function entry
    if len(data) < 5 or not data.startswith(MAGIC):
        return None  # invalid input: rejected by the parser

    edges.add(1)  # magic accepted
    type_byte = data[4]
    payload = data[5:]

    edges.add(2 + type_byte)  # edges 2..5 for type 0x00..0x03; others invalid
    if type_byte > 3:
        return None

    if b"\x00" in payload:
        edges.add(6)
    if b"A" in payload:
        edges.add(7)
    if len(payload) > 8:
        edges.add(8)
    if payload.startswith(b"de"):
        edges.add(9)
    if b"adbeef" in payload:
        edges.add(10)
    if type_byte == 1 and b"A" in payload:
        edges.add(11)  # deep edge: needs type 1 AND 'A'
    if len(payload) > 16:
        edges.add(12)
    return frozenset(edges)
