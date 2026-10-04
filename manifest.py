"""发布目录清单生成与校验（仅标准库）。

清单格式（JSON）：
{
  "version": 1,
  "algorithm": "sha256",
  "generated_at": "<UTC ISO8601>",
  "files": [
    {"path": "a/b.txt", "type": "file", "size": 12, "sha256": "..."},
    {"path": "link",    "type": "symlink", "target": "a/b.txt"}
  ],
  "manifest_sha256": "<对上述内容（去掉本字段）规范化序列化后的摘要>"
}

- 路径一律规范化为 POSIX 风格相对路径，拒绝绝对路径与 ".."，防止越出根目录。
- 符号链接不跟随，只记录链接目标，避免通过链接逃出根目录。
- 大文件分块计算摘要，内存占用恒定。
- manifest_sha256 用于清单自校验，检测清单本身是否被篡改。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

VERSION = 1
ALGORITHM = "sha256"
_CHUNK_SIZE = 1024 * 1024  # 1 MiB，流式读取大文件


class ManifestError(Exception):
    """清单相关错误（路径越界、格式非法等）。"""


def normalize_relpath(path: str) -> str:
    """把路径规范化为安全的 POSIX 风格相对路径。

    拒绝绝对路径、空路径以及任何 ".." 组件，防止越出根目录。
    """
    if not path or not path.strip():
        raise ManifestError("空路径")
    # 统一分隔符（兼容 Windows 风格输入）
    path = path.replace("\\", "/")
    if path.startswith("/") or (len(path) >= 2 and path[1] == ":"):
        raise ManifestError(f"不允许绝对路径: {path!r}")
    parts = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ManifestError(f"路径包含 '..'，可能越出根目录: {path!r}")
        parts.append(part)
    if not parts:
        raise ManifestError(f"路径规范化后为空: {path!r}")
    return "/".join(parts)


def _safe_join(root: str, relpath: str) -> str:
    """拼接并确认最终路径仍位于 root 之内（防御性二次校验）。"""
    root_real = os.path.realpath(root)
    # 注意：对符号链接本身用 dirname 解析，不跟随最后一级链接
    parent_real = os.path.realpath(os.path.join(root_real, os.path.dirname(relpath)))
    if parent_real != root_real and not parent_real.startswith(root_real + os.sep):
        raise ManifestError(f"路径越出根目录: {relpath!r}")
    return os.path.join(root_real, relpath)


def hash_file(path: str, algorithm: str = ALGORITHM) -> str:
    """流式计算文件摘要，适合大文件。"""
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _scan_entry(root: str, relpath: str) -> dict:
    """对单个相对路径生成清单条目（文件或符号链接）。"""
    full = _safe_join(root, relpath)
    if os.path.islink(full):
        return {
            "path": relpath,
            "type": "symlink",
            "target": os.readlink(full),
        }
    st = os.stat(full)  # 普通文件
    return {
        "path": relpath,
        "type": "file",
        "size": st.st_size,
        ALGORITHM: hash_file(full),
    }


def iter_relpaths(root: str, exclude: set[str] | None = None):
    """遍历 root 下所有文件与符号链接，返回排序后的规范化相对路径。

    不跟随符号链接目录，避免循环与越界。
    """
    exclude = exclude or set()
    results = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            rel = normalize_relpath(rel)
            if rel in exclude:
                continue
            results.append(rel)
        # os.walk 不跟随链接目录，但链接目录会出现在 dirnames 里，需显式记录
        for name in list(dirnames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                rel = normalize_relpath(os.path.relpath(full, root))
                if rel not in exclude:
                    results.append(rel)
                dirnames.remove(name)  # 不再进入
    results.sort()
    return results


def compute_manifest_digest(manifest: dict) -> str:
    """对清单内容（去掉 manifest_sha256 字段）做规范化序列化并取摘要。"""
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_manifest(root: str, exclude: set[str] | None = None) -> dict:
    """扫描目录并生成清单。"""
    if not os.path.isdir(root):
        raise ManifestError(f"根目录不存在或不是目录: {root!r}")
    files = [_scan_entry(root, rel) for rel in iter_relpaths(root, exclude)]
    manifest = {
        "version": VERSION,
        "algorithm": ALGORITHM,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    manifest["manifest_sha256"] = compute_manifest_digest(manifest)
    return manifest


def save_manifest(manifest: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def load_manifest(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    if not isinstance(manifest, dict) or "files" not in manifest:
        raise ManifestError("清单格式非法：缺少 files 字段")
    # 加载时即校验所有路径，防止恶意清单引导读取根目录外文件
    for entry in manifest["files"]:
        normalize_relpath(entry["path"])
    return manifest


def check_manifest_integrity(manifest: dict) -> bool:
    """清单自校验：内容摘要是否与内嵌摘要一致。"""
    recorded = manifest.get("manifest_sha256")
    if not recorded:
        return False
    return recorded == compute_manifest_digest(manifest)


def verify(root: str, manifest: dict, exclude: set[str] | None = None) -> dict:
    """对照清单校验目录，返回分类报告。

    报告结构：
    {
      "manifest_intact": bool,   # 清单自身是否被篡改
      "modified": [...],         # 内容/大小/类型被改动的文件
      "deleted":  [...],         # 清单中有、目录中已不存在
      "added":    [...],         # 目录中多出来的文件
      "ok":       bool,          # 全部通过（且清单未被篡改）
    }
    """
    manifest_intact = check_manifest_integrity(manifest)

    expected = {e["path"]: e for e in manifest["files"]}
    actual_paths = set(iter_relpaths(root, exclude))

    modified, deleted, added = [], [], []

    for path, entry in expected.items():
        if path not in actual_paths:
            deleted.append(path)
            continue
        try:
            current = _scan_entry(root, path)
        except OSError:
            deleted.append(path)
            continue
        if current["type"] != entry["type"]:
            modified.append({"path": path, "reason": f"类型变化: {entry['type']} -> {current['type']}"})
        elif entry["type"] == "symlink":
            if current["target"] != entry["target"]:
                modified.append({"path": path, "reason": "符号链接目标变化"})
        else:
            if current["size"] != entry["size"]:
                modified.append({"path": path, "reason": f"长度变化: {entry['size']} -> {current['size']}"})
            elif current[ALGORITHM] != entry[ALGORITHM]:
                modified.append({"path": path, "reason": "摘要不匹配（内容被改动）"})

    for path in sorted(actual_paths - set(expected)):
        added.append(path)

    modified.sort(key=lambda item: item["path"])
    deleted.sort()

    return {
        "manifest_intact": manifest_intact,
        "modified": modified,
        "deleted": deleted,
        "added": added,
        "ok": manifest_intact and not modified and not deleted and not added,
    }


def format_report(report: dict) -> str:
    """把校验报告格式化为可读文本。"""
    lines = []
    if report["manifest_intact"]:
        lines.append("[自检] 清单完整性: 通过（清单未被篡改）")
    else:
        lines.append("[自检] 清单完整性: 失败（清单已被篡改，以下结果不可信）")

    lines.append(f"[修改] {len(report['modified'])} 个文件被改动")
    for item in report["modified"]:
        lines.append(f"  M {item['path']}  ({item['reason']})")

    lines.append(f"[删除] {len(report['deleted'])} 个文件缺失")
    for path in report["deleted"]:
        lines.append(f"  D {path}")

    lines.append(f"[新增] {len(report['added'])} 个文件多出来")
    for path in report["added"]:
        lines.append(f"  A {path}")

    lines.append("结论: " + ("通过" if report["ok"] else "未通过"))
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="发布目录清单生成与校验")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="生成清单")
    p_gen.add_argument("root", help="发布目录")
    p_gen.add_argument("-o", "--output", default="manifest.json", help="清单输出路径")

    p_ver = sub.add_parser("verify", help="校验目录")
    p_ver.add_argument("root", help="发布目录")
    p_ver.add_argument("-m", "--manifest", default="manifest.json", help="清单路径")

    p_self = sub.add_parser("selfcheck", help="只校验清单自身完整性")
    p_self.add_argument("manifest", help="清单路径")

    args = parser.parse_args(argv)

    if args.command == "generate":
        out = os.path.abspath(args.output)
        exclude = set()
        # 若输出文件位于 root 内，生成时排除清单自身
        try:
            rel = os.path.relpath(out, os.path.abspath(args.root))
            exclude.add(normalize_relpath(rel))
        except (ManifestError, ValueError):
            pass
        manifest = generate_manifest(args.root, exclude=exclude)
        save_manifest(manifest, out)
        print(f"已生成清单: {out}（{len(manifest['files'])} 个条目）")
        return 0

    if args.command == "verify":
        exclude = set()
        try:
            rel = os.path.relpath(os.path.abspath(args.manifest), os.path.abspath(args.root))
            exclude.add(normalize_relpath(rel))
        except (ManifestError, ValueError):
            pass
        report = verify(args.root, load_manifest(args.manifest), exclude=exclude)
        print(format_report(report))
        if not report["manifest_intact"]:
            return 2
        return 0 if report["ok"] else 1

    if args.command == "selfcheck":
        intact = check_manifest_integrity(load_manifest(args.manifest))
        print("清单自校验: " + ("通过（未被篡改）" if intact else "失败（清单已被篡改）"))
        return 0 if intact else 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
