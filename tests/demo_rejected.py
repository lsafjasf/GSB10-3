"""Prints the rejection samples referenced by README.md.

Run from the repo root:  python3 tests/demo_rejected.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from certchain import TrustStore, load_certificates, verify  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def cert(name):
    (c,) = load_certificates(os.path.join(FIX, name))
    return c


def show(title, leaf, pool, anchors):
    print(f"### {title}")
    names = anchors if isinstance(anchors, list) else [anchors]
    store = TrustStore([cert(a) for a in names])
    result = verify(cert(leaf), [cert(p) for p in pool], store)
    print(result.describe())
    print()


def main():
    show("R1 链指向未配置的根 (Root B 不在信任锚里)",
         "leaf-cross.crt", ["int-x-by-b.crt", "root-b.crt"], "root-a.crt")
    show("R2 缺失中间证书 (Intermediate CA 1 未提供)",
         "leaf-good.crt", [], "root-a.crt")
    show("R3 中间证书已过期",
         "leaf-exp.crt", ["int-exp.crt"], "root-a.crt")
    show("R4 自签证书冒充信任锚",
         "leaf-self.crt", [], "root-a.crt")
    show("R5 名称约束: 落在 permitted 子树之外",
         "leaf-nc-outside.crt", ["int-nc.crt"], "root-nc.crt")
    show("R6 名称约束: 命中 excluded 子树",
         "leaf-nc-excluded.crt", ["int-nc.crt"], "root-nc.crt")
    show("R7 pathLenConstraint 越界",
         "leaf-p0.crt", ["sub-p0.crt", "int-p0.crt"], "root-a.crt")


if __name__ == "__main__":
    main()
