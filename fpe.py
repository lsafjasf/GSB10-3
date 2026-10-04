"""格式保持加密（FPE）库 —— 仅使用 Python 标准库。

设计要点：
- 基于 Feistel 网络（FF1 思路的简化实现），轮函数为 HMAC-SHA256。
- 以「字符表 + 序号」方式工作：密文与明文长度一致，且每个字符都落在
  同一字符表内，从而保持长度与字符集不变。
- IV 作为 tweak 参与每一轮的 PRF 输入：相同密钥 + 相同 IV 加密结果确定；
  不同 IV 得到不同密文，且均可正确解密。
- 校验位（如 Luhn）不参与加密，加密后对密文载荷重新计算，保证下游
  校验逻辑无需改动。
"""

import hashlib
import hmac

MIN_KEY_BYTES = 16
DEFAULT_ROUNDS = 10
MAX_TEXT_LEN = 4096

# 预置字符表（顺序固定，属于格式定义的一部分）
DIGITS = "0123456789"
LOWER = "abcdefghijklmnopqrstuvwxyz"
UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ALNUM = DIGITS + LOWER + UPPER
HEX_LOWER = DIGITS + "abcdef"


class FPEError(ValueError):
    """密钥 / IV / 字符表 / 输入文本非法时抛出。"""


def _to_bytes(value, name):
    if isinstance(value, str):
        value = value.encode("utf-8")
    if not isinstance(value, (bytes, bytearray)):
        raise FPEError("%s 必须是 bytes 或 str" % name)
    return bytes(value)


class FPECipher:
    """对某一固定字符表做格式保持加密。

    key:      密钥，至少 16 字节（str 会按 utf-8 编码）。
    alphabet: 字符表，明文/密文的每个字符都必须属于它；不允许重复字符。
    iv:       初始向量 / tweak，任意字节串（可为空）。相同 key+iv 结果确定。
    rounds:   Feistel 轮数，默认 10，最少 4。
    """

    def __init__(self, key, alphabet, iv=b"", rounds=DEFAULT_ROUNDS):
        key = _to_bytes(key, "key")
        if len(key) < MIN_KEY_BYTES:
            raise FPEError("key 至少需要 %d 字节" % MIN_KEY_BYTES)
        if not isinstance(alphabet, str) or len(alphabet) < 2:
            raise FPEError("alphabet 至少需要 2 个字符")
        if len(set(alphabet)) != len(alphabet):
            raise FPEError("alphabet 中存在重复字符")
        if rounds < 4:
            raise FPEError("rounds 至少为 4")
        self._key = key
        self._alphabet = alphabet
        self._radix = len(alphabet)
        self._index = {ch: i for i, ch in enumerate(alphabet)}
        self._iv = _to_bytes(iv, "iv")
        self._rounds = rounds

    # ---------- 公开接口 ----------

    def encrypt(self, plaintext):
        ranks = self._to_ranks(plaintext, "plaintext")
        return self._from_ranks(self._feistel(ranks, decrypt=False))

    def decrypt(self, ciphertext):
        ranks = self._to_ranks(ciphertext, "ciphertext")
        return self._from_ranks(self._feistel(ranks, decrypt=True))

    # ---------- 内部实现 ----------

    def _to_ranks(self, text, name):
        if not isinstance(text, str):
            raise FPEError("%s 必须是 str" % name)
        if not text:
            raise FPEError("%s 不能为空" % name)
        if len(text) > MAX_TEXT_LEN:
            raise FPEError("%s 长度超过上限 %d" % (name, MAX_TEXT_LEN))
        try:
            return [self._index[ch] for ch in text]
        except KeyError as exc:
            raise FPEError(
                "%s 含有字符表之外的字符: %r" % (name, exc.args[0])
            ) from None

    def _from_ranks(self, ranks):
        return "".join(self._alphabet[r] for r in ranks)

    def _prf(self, round_no, block):
        # 轮函数：HMAC-SHA256(key, iv || round || block)，block 为序号序列
        msg = bytearray(self._iv)
        msg += b"|"
        msg += round_no.to_bytes(4, "big")
        msg += b"|"
        for r in block:
            msg += r.to_bytes(4, "big")
        digest = hmac.new(self._key, bytes(msg), hashlib.sha256).digest()
        return int.from_bytes(digest, "big")

    def _feistel(self, ranks, decrypt):
        n = len(ranks)
        if n == 1:
            return self._single_char(ranks[0], decrypt)

        u = n // 2
        radix = self._radix

        def to_int(block):
            value = 0
            for r in block:
                value = value * radix + r
            return value

        def to_block(value, length):
            out = [0] * length
            for i in range(length - 1, -1, -1):
                out[i] = value % radix
                value //= radix
            return out

        # A 长 u，B 长 v = n - u；偶数轮 m=u，奇数轮 m=v
        A, B = ranks[:u], ranks[u:]
        if not decrypt:
            for i in range(self._rounds):
                m = u if i % 2 == 0 else n - u
                y = self._prf(i, B) % (radix ** m)
                C = to_block((to_int(A) + y) % (radix ** m), m)
                A, B = B, C
        else:
            for i in range(self._rounds - 1, -1, -1):
                m = u if i % 2 == 0 else n - u
                y = self._prf(i, A) % (radix ** m)
                C = to_block((to_int(B) - y) % (radix ** m), m)
                B, A = A, C
        return A + B

    def _single_char(self, rank, decrypt):
        # 长度为 1 时无法拆分 Feistel，改用「密钥控制的字符表置换」：
        # 按 HMAC(key, iv || char) 排序得到确定性洗牌，加密取像、解密取逆。
        keyed = sorted(
            range(self._radix),
            key=lambda r: hmac.new(
                self._key,
                self._iv + b"|single|" + r.to_bytes(4, "big"),
                hashlib.sha256,
            ).digest(),
        )
        if decrypt:
            return [keyed.index(rank)]
        return [keyed[rank]]


# ---------- 校验位（Luhn） ----------

def luhn_check_digit(payload):
    """对纯数字载荷计算 Luhn 校验位，返回单个数字字符。"""
    if not payload or not payload.isdigit():
        raise FPEError("Luhn 载荷必须是非空纯数字串")
    total = 0
    # 校验位追加在末尾后，从右往左第 2 位开始翻倍
    for i, ch in enumerate(reversed(payload)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def luhn_validate(number):
    """校验带 Luhn 校验位的完整数字串。"""
    if not number or not number.isdigit():
        return False
    return luhn_check_digit(number[:-1]) == number[-1]


def luhn_pass_rate(numbers):
    """校验通过率：通过 Luhn 校验的样本占比（0.0 ~ 1.0）。"""
    numbers = list(numbers)
    if not numbers:
        raise FPEError("样本不能为空")
    passed = sum(1 for num in numbers if luhn_validate(num))
    return passed / len(numbers)


def encrypt_with_luhn(cipher, plaintext):
    """加密带 Luhn 校验位的数字串：只加密载荷，校验位对密文重新计算。"""
    if not isinstance(plaintext, str) or len(plaintext) < 2:
        raise FPEError("带校验位的明文长度至少为 2")
    if not luhn_validate(plaintext):
        raise FPEError("明文校验位不正确: %r" % plaintext)
    ct_payload = cipher.encrypt(plaintext[:-1])
    return ct_payload + luhn_check_digit(ct_payload)


def decrypt_with_luhn(cipher, ciphertext):
    """解密带 Luhn 校验位的密文，并恢复原始校验位。"""
    if not isinstance(ciphertext, str) or len(ciphertext) < 2:
        raise FPEError("带校验位的密文长度至少为 2")
    if not luhn_validate(ciphertext):
        raise FPEError("密文校验位不正确: %r" % ciphertext)
    pt_payload = cipher.decrypt(ciphertext[:-1])
    return pt_payload + luhn_check_digit(pt_payload)
