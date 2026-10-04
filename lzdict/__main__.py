"""命令行入口：

    python -m lzdict train      <样本目录> <字典仓库> [--max-size N]
    python -m lzdict compress   <字典仓库> <文件> [--dict ID | --no-dict]
    python -m lzdict decompress <字典仓库> <文件.dzc>

train      ：样本目录下每个文件视为一条样本，训练并保存字典，打印 dict_id。
compress   ：默认使用仓库中最新字典；输出写到 <文件>.dzc。
decompress ：按压缩块头部记录的 dict_id 自动取字典，内容写到标准输出。
"""

from __future__ import annotations

import argparse
import os
import sys

from .codec import compress as _compress, decompress as _decompress
from .dictionary import DictStore, train_dictionary


def _cmd_train(args) -> int:
    samples = []
    for name in sorted(os.listdir(args.samples)):
        full = os.path.join(args.samples, name)
        if os.path.isfile(full):
            with open(full, "rb") as fh:
                samples.append(fh.read())
    if not samples:
        print("样本目录为空，无法训练", file=sys.stderr)
        return 1
    content = train_dictionary(samples, max_size=args.max_size)
    store = DictStore(args.store)
    dict_id = store.save(content)
    print(f"样本数: {len(samples)}")
    print(f"字典大小: {len(content)} 字节")
    print(f"dict_id: {dict_id}")
    return 0


def _cmd_compress(args) -> int:
    store = DictStore(args.store)
    dictionary = b""
    dict_id = ""
    if not args.no_dict:
        dict_id = args.dict_id or store.latest_id()
        if dict_id is None:
            print("仓库中没有字典，请先 train 或使用 --no-dict", file=sys.stderr)
            return 1
        dictionary = store.load(dict_id)
    with open(args.file, "rb") as fh:
        data = fh.read()
    blob = _compress(data, dictionary, dict_id)
    out_path = args.file + ".dzc"
    with open(out_path, "wb") as fh:
        fh.write(blob)
    ratio = len(blob) / len(data) if data else 0.0
    print(f"{len(data)} -> {len(blob)} 字节 (ratio={ratio:.3f}) -> {out_path}")
    return 0


def _cmd_decompress(args) -> int:
    store = DictStore(args.store)
    with open(args.file, "rb") as fh:
        blob = fh.read()
    data = _decompress(blob, store.load)
    sys.stdout.buffer.write(data)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="lzdict")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_train = sub.add_parser("train", help="从样本目录训练字典")
    p_train.add_argument("samples")
    p_train.add_argument("store")
    p_train.add_argument("--max-size", type=int, default=16 * 1024)
    p_train.set_defaults(func=_cmd_train)

    p_comp = sub.add_parser("compress", help="压缩文件")
    p_comp.add_argument("store")
    p_comp.add_argument("file")
    p_comp.add_argument("--dict", dest="dict_id", default=None)
    p_comp.add_argument("--no-dict", action="store_true")
    p_comp.set_defaults(func=_cmd_compress)

    p_decomp = sub.add_parser("decompress", help="解压文件（自动按 dict_id 取字典）")
    p_decomp.add_argument("store")
    p_decomp.add_argument("file")
    p_decomp.set_defaults(func=_cmd_decompress)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
