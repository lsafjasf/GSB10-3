"""在长文本中查找相似片段并输出命中位置清单。运行：python3 demo_search.py"""

import random
import time

from edit_distance import find_similar


def main():
    rng = random.Random(7)
    text_len = 200_000
    pattern = "ACGTTGCATGTCGCATGATGCATG"  # 长度 24
    text = list(rng.choice("ACGT") for _ in range(text_len))

    # 在已知位置植入若干“相似片段”：精确拷贝 / 替换 / 删除 / 相邻交换
    planted = []

    def plant(pos, mutated, note):
        text[pos:pos + len(mutated)] = mutated
        planted.append((pos, note))

    plant(5_000, list(pattern), "精确拷贝")
    mut = list(pattern); mut[10] = "A"
    plant(30_000, mut, "1 次替换")
    mut = list(pattern); del mut[7]
    plant(60_000, mut, "1 次删除")
    mut = list(pattern); mut[6], mut[7] = mut[7], mut[6]
    plant(90_000, mut, "1 次相邻交换")
    mut = list(pattern); mut[0] = "T"; mut[20] = "G"
    plant(120_000, mut, "2 次替换")
    mut = list(pattern); mut.insert(12, "G"); mut[2] = "T"
    plant(160_000, mut, "1 插入 + 1 替换")

    text = "".join(text)
    t0 = time.perf_counter()
    hits = find_similar(text, pattern, max_distance=2)
    elapsed = time.perf_counter() - t0

    print(f"文本长度 {len(text):,}，模式串 {pattern!r}（长度 {len(pattern)}），"
          f"距离阈值 2，耗时 {elapsed:.2f}s")
    print()
    print("命中位置清单（按起始位置排序）：")
    print("{:>10} {:>10} {:>6} {:>8}  {}".format("起始", "结束", "距离", "相似度", "片段"))
    for h in hits:
        frag = text[h.start:h.end]
        frag = frag if len(frag) <= 30 else frag[:27] + "..."
        print("{:>10} {:>10} {:>6.0f} {:>8.2%}  {}".format(
            h.start, h.end, h.distance, h.similarity, frag))
    print()
    print("植入位置对照（应全部被命中覆盖）：")
    for pos, note in planted:
        covered = any(h.start - 2 <= pos <= h.start + 2 for h in hits)
        print(f"  位置 {pos:>7}（{note}）：{'命中' if covered else '未命中！'}")


if __name__ == "__main__":
    main()
