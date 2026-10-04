#!/usr/bin/env python3
"""场景演示：打印判定表与各拒绝样例的完整诊断。

用法：python3 demo.py
"""

import os
from datetime import timedelta

from certchain import Certificate, ChainValidator

FX = os.path.join(os.path.dirname(__file__), "tests", "fixtures")


def cert(name):
    return Certificate.from_pem_file(os.path.join(FX, name))


rootA = cert("rootA.crt.pem")
rootB = cert("rootB.crt.pem")
interA = cert("interA.crt.pem")
interExp = cert("interExp.crt.pem")
interNC = cert("interNC.crt.pem")
ixA = cert("interX_by_rootA.crt.pem")
ixB = cert("interX_by_rootB.crt.pem")
interP = cert("interP.crt.pem")
subP = cert("subP.crt.pem")
leaf = cert("leaf.crt.pem")
leafX = cert("leafX.crt.pem")
leafExp = cert("leafExp.crt.pem")
leafNcOK = cert("leafNcOK.crt.pem")
leafNcEx = cert("leafNcEx.crt.pem")
leafNcNp = cert("leafNcNp.crt.pem")
leafNcDir = cert("leafNcDir.crt.pem")
leafP = cert("leafP.crt.pem")
selfsigned = cert("selfsigned.crt.pem")

SCENARIOS = [
    # (场景, 叶子, 信任锚, 中间证书池, 额外参数, 期望)
    ("正常链 leaf->interA->rootA", leaf, [rootA], [interA], {}, True),
    ("主机名匹配 www.example.com", leaf, [rootA], [interA],
     {"expected_hostname": "www.example.com"}, True),
    ("主机名不匹配 evil.example.com", leaf, [rootA], [interA],
     {"expected_hostname": "evil.example.com"}, False),
    ("交叉签名：信任 RootA（应选 RootA 路径）", leafX, [rootA], [ixA, ixB, rootB], {}, True),
    ("交叉签名：只给 RootB 版证书、只信 RootA", leafX, [rootA], [ixB, rootB], {}, False),
    ("交叉签名：信任 RootB（应选 RootB 路径）", leafX, [rootB], [ixA, ixB], {}, True),
    ("中间证书缺失（池为空）", leaf, [rootA], [], {}, False),
    ("中间证书已过期（2021 年失效）", leafExp, [rootA], [interExp], {}, False),
    ("叶子尚未生效（校验时刻提前 1 秒）", leaf, [rootA], [interA],
     {"at_time": leaf.not_before - timedelta(seconds=1)}, False),
    ("叶子刚好生效（notBefore 边界）", leaf, [rootA], [interA],
     {"at_time": leaf.not_before}, True),
    ("叶子刚好到期（notAfter 边界）", leaf, [rootA], [interA],
     {"at_time": leaf.not_after}, True),
    ("叶子过期 1 秒", leaf, [rootA], [interA],
     {"at_time": leaf.not_after + timedelta(seconds=1)}, False),
    ("名称约束：app.example.com（允许子树内）", leafNcOK, [rootA], [interNC], {}, True),
    ("名称约束：bad.example.com（命中排除子树）", leafNcEx, [rootA], [interNC], {}, False),
    ("名称约束：other.org（不在允许子树内）", leafNcNp, [rootA], [interNC], {}, False),
    ("名称约束：O=Evil Corp（DirName 越界）", leafNcDir, [rootA], [interNC], {}, False),
    ("自签证书不在信任锚中", selfsigned, [], [], {}, False),
    ("自签证书加入信任锚", selfsigned, [selfsigned], [], {}, True),
    ("pathLenConstraint=0 下再挂一级 CA", leafP, [rootA], [interP, subP], {}, False),
]


def main():
    print("=" * 78)
    print("判定表（场景 -> 期望/实际）")
    print("=" * 78)
    print(f"{'场景':<44}{'期望':<6}{'实际':<6}判定码")
    print("-" * 78)
    all_pass = True
    results = {}
    for name, lf, anchors, inters, kw, expect in SCENARIOS:
        r = ChainValidator(anchors, inters, **kw).validate(lf)
        actual = r.ok
        mark = "通过" if actual == expect else "!!不符!!"
        all_pass &= actual == expect
        codes = "OK" if r.ok else ",".join(sorted({f.code for f in r.failures}))
        print(f"{name:<44}{str(expect):<6}{str(actual):<6}{codes}  {mark}")
        results[name] = r

    print()
    print("=" * 78)
    print("拒绝样例（完整诊断，含被拒链）")
    print("=" * 78)
    for name in ("交叉签名：只给 RootB 版证书、只信 RootA",
                 "中间证书缺失（池为空）",
                 "中间证书已过期（2021 年失效）",
                 "名称约束：bad.example.com（命中排除子树）",
                 "名称约束：O=Evil Corp（DirName 越界）",
                 "自签证书不在信任锚中",
                 "pathLenConstraint=0 下再挂一级 CA"):
        r = results[name]
        print(f"\n### {name}")
        print(r.failure_text())

    print()
    print("全部场景符合预期" if all_pass else "存在与预期不符的场景！")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
