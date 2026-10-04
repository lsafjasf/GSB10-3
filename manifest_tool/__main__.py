"""Command line interface for the manifest tool.

Usage:
    python -m manifest_tool generate  <dir> [-o MANIFEST] [-a ALGO]
    python -m manifest_tool verify    <dir> [-m MANIFEST] [--json]
    python -m manifest_tool selfcheck <manifest> [--json]

Exit codes: 0 = pass, 1 = differences found, 2 = manifest tampered/invalid,
3 = usage or runtime error.
"""

from __future__ import annotations

import argparse
import json
import sys

from .manifest import (
    DEFAULT_ALGORITHM,
    generate_manifest,
    verify_manifest,
    verify_manifest_self,
    write_manifest,
)

EXIT_PASS = 0
EXIT_DIFF = 1
EXIT_TAMPERED = 2
EXIT_ERROR = 3


def _cmd_generate(args: argparse.Namespace) -> int:
    import os
    rel_output = os.path.relpath(os.path.abspath(args.output),
                                 os.path.abspath(args.directory))
    exclude = [] if rel_output.startswith("..") else [rel_output]
    manifest = generate_manifest(args.directory, algorithm=args.algorithm,
                                 exclude=exclude)
    write_manifest(manifest, args.output)
    print(f"wrote {args.output}: {len(manifest.entries)} entries")
    for warning in manifest.warnings:
        print(f"warning: {warning}")
    return EXIT_PASS


def _cmd_verify(args: argparse.Namespace) -> int:
    report = verify_manifest(args.directory, args.manifest)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.to_text())
    if not report.manifest_ok:
        return EXIT_TAMPERED
    return EXIT_PASS if report.ok else EXIT_DIFF


def _cmd_selfcheck(args: argparse.Namespace) -> int:
    ok, detail, _ = verify_manifest_self(args.manifest)
    if args.json:
        print(json.dumps({"ok": ok, "detail": detail}, indent=2, ensure_ascii=False))
    else:
        print(f"{'OK' if ok else 'FAILED'}: {detail}")
    return EXIT_PASS if ok else EXIT_TAMPERED


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="manifest_tool")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="scan a directory and write a manifest")
    gen.add_argument("directory")
    gen.add_argument("-o", "--output", default="MANIFEST.jsonl")
    gen.add_argument("-a", "--algorithm", default=DEFAULT_ALGORITHM)
    gen.set_defaults(func=_cmd_generate)

    ver = sub.add_parser("verify", help="verify a directory against a manifest")
    ver.add_argument("directory")
    ver.add_argument("-m", "--manifest", default="MANIFEST.jsonl")
    ver.add_argument("--json", action="store_true")
    ver.set_defaults(func=_cmd_verify)

    chk = sub.add_parser("selfcheck", help="verify only the manifest's own signature")
    chk.add_argument("manifest")
    chk.add_argument("--json", action="store_true")
    chk.set_defaults(func=_cmd_selfcheck)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
