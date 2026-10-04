#!/usr/bin/env python3
"""sensitive_data 自测演示：生成清零验证数据、幂等断言、拷贝检测与告警结果。

运行：
    python3 selftest.py            # 打印报告并写入 verification_report.txt
    python3 -m unittest test_sensitive_data -v   # 完整单元测试
"""

import gc
import hashlib
import os
import warnings

from sensitive_data import (
    SecretError,
    LeakScanner,
    SecretBuffer,
    SecretDestroyedError,
    SecretInUseError,
)

LINES = []


def out(line=""):
    print(line)
    LINES.append(line)


def hexdump(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def main():
    out("=" * 72)
    out("敏感数据容器自测报告 (sensitive_data / 仅标准库)")
    out("=" * 72)

    # ------------------------------------------------------------------
    out("\n[1] 正常使用 + 销毁前后内容对比（逐字节清零验证）")
    secret = SecretBuffer(os.urandom(32))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        with secret.access() as view:
            before = bytes(view)
    out(f"  销毁前 (32 字节): {hexdump(before)}")
    out(f"  销毁前 sha256[:16]: {sha(before)}")
    secret.destroy()
    after = bytes(view)  # view 仍指向同一块底层内存，可验证覆写结果
    out(f"  销毁后 (32 字节): {hexdump(after)}")
    out(f"  销毁后 sha256[:16]: {sha(after)}")
    zero_count = after.count(0)
    out(f"  逐字节校验: {zero_count}/32 字节为 0x00 -> "
        f"{'PASS' if zero_count == 32 and before != after else 'FAIL'}")
    assert zero_count == 32 and before != after
    del view, before, after

    # ------------------------------------------------------------------
    out("\n[2] 幂等销毁：重复调用不报错、不改变结果")
    secret2 = SecretBuffer(os.urandom(16))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        with secret2.access() as view2:
            pass
    digests = []
    errors = []
    for i in range(3):
        try:
            secret2.destroy()
            digests.append(sha(bytes(view2)))
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
    out(f"  连续 3 次 destroy(): 异常 = {errors or '无'}")
    out(f"  每次销毁后缓冲区摘要: {digests}")
    same = len(set(digests)) == 1
    out(f"  幂等断言: 3 次结果一致 -> {'PASS' if same else 'FAIL'}; "
        f"destroyed = {secret2.destroyed}")
    assert same and not errors and secret2.destroyed
    del view2

    # ------------------------------------------------------------------
    out("\n[3] 销毁后继续访问 -> SecretDestroyedError")
    for desc, fn in [
        ("secret.access()", lambda: secret2.access()),
        ("len(secret)", lambda: len(secret2)),
        ("bytes(secret)", lambda: bytes(secret2)),
    ]:
        try:
            fn()
            out(f"  {desc:18s} -> 未抛异常 (FAIL)")
            raise AssertionError(desc)
        except SecretError as exc:
            out(f"  {desc:18s} -> {type(exc).__name__}: {exc} (PASS)")

    # ------------------------------------------------------------------
    out("\n[4] 提前销毁：使用中默认拒绝，force 可强制")
    secret3 = SecretBuffer(os.urandom(16))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        with secret3.access() as view3:
            try:
                secret3.destroy()
                out("  会话中 destroy()        -> 未拒绝 (FAIL)")
            except SecretInUseError as exc:
                out(f"  会话中 destroy()        -> SecretInUseError (PASS): {exc}")
            secret3.destroy(force=True)
            mid = bytes(view3)
            mid_cleared = mid == b"\x00" * 16
            out(f"  force 销毁后会话内读到  : {hexdump(mid)}")
            out(f"  使用中即被逐字节清零    -> {'PASS' if mid_cleared else 'FAIL'}")
            assert mid_cleared
    del view3

    # ------------------------------------------------------------------
    out("\n[5] 使用期外的意外复制：拷贝计数与告警")
    secret4 = SecretBuffer(os.urandom(32))
    with secret4.access() as v:
        leak_bytes = bytes(v)                      # 意外复制到 bytes
        leak_str = bytes(v).decode("latin1")       # 意外复制到 str
        del v
    report = secret4.audit()
    out(f"  审计结果: {report.summary()}")
    for f in report.findings:
        out(f"    - 副本: 类型={f.type_name}, 长度={f.obj_len}, 偏移={f.offset}")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if report.leaked:
            warnings.warn(f"敏感数据泄漏: {report.summary()}", UserWarning)
    for w in caught:
        out(f"  告警: {w.category.__name__}: {w.message}")
    assert report.copy_count == 2
    del leak_bytes, leak_str
    gc.collect()
    report2 = secret4.audit()
    out(f"  清理副本后复审: {report2.summary()} -> "
        f"{'PASS' if not report2.leaked else 'FAIL'}")
    assert not report2.leaked
    secret4.destroy()

    # ------------------------------------------------------------------
    out("\n[6] 逃逸 memoryview 告警（使用期外仍持有明文引用）")
    secret5 = SecretBuffer(os.urandom(8))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with secret5.access() as v5:
            escaped = v5
    for w in caught:
        if issubclass(w.category, ResourceWarning):
            out(f"  告警: {w.category.__name__}: {w.message}")
    out(f"  逃逸视图计数: {secret5.escaped_view_count()} -> "
        f"{'PASS' if secret5.escaped_view_count() == 1 else 'FAIL'}")
    assert secret5.escaped_view_count() == 1
    del escaped, v5
    gc.collect()
    out(f"  释放后计数  : {secret5.escaped_view_count()}")
    secret5.destroy()

    # ------------------------------------------------------------------
    out("\n[7] 光把变量置空是不够的")
    token = "demo-" + os.urandom(8).hex()
    holder = SecretBuffer(token)
    snapshot = token          # 意外复制：第二个变量引用同一 str
    holder.destroy()          # 容器已销毁……
    token = None              # ……原变量也置空了……
    r = LeakScanner(snapshot.encode()).scan()
    out(f"  容器销毁 + 变量置空后扫描: {r.summary()}")
    out(f"  结论: 不可清零的 str 副本仍被 snapshot 持有 -> "
        f"{'检测到 (PASS)' if r.leaked else '未检测到 (FAIL)'}")
    assert r.leaked
    del snapshot
    gc.collect()

    out("\n全部自测断言通过。")
    out("=" * 72)

    with open("verification_report.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(LINES) + "\n")
    print("\n报告已写入 verification_report.txt")


if __name__ == "__main__":
    main()
