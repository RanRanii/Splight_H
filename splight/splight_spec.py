"""Splight constants and nibble-level primitives.

State order follows Splight_implement/implement_py/enc.py:
16 nibbles encode L || R, with L=x[0..7] and R=x[8..15].
Nibble bits are handled MSB first when expanded by bit-level models.
"""

BLOCK_SIZE = 64
BRANCH_SIZE = 32
NIBBLES_PER_BRANCH = 8
NIBBLES_PER_STATE = 16
SBOXES_PER_ROUND = 16
ROUNDS = 32

SBOX_SPLIGHT_S3 = [
    0xA, 0xD, 0xC, 0xF,
    0x9, 0xE, 0x1, 0x0,
    0x7, 0x2, 0x5, 0x4,
    0x3, 0x6, 0xB, 0x8,
]


def hex_to_nibbles(x, n=16):
    if isinstance(x, int):
        if x < 0:
            raise ValueError("state integer must be non-negative")
        s = f"{x:0{n}X}"
    else:
        s = str(x).replace("0x", "").replace("0X", "")
        s = s.replace(" ", "").replace("_", "").strip().upper()
        if len(s) > n:
            raise ValueError(f"expected at most {n} nibbles, got {len(s)}")
        s = s.zfill(n)
    if any(ch not in "0123456789ABCDEF" for ch in s):
        raise ValueError(f"invalid hexadecimal state: {x!r}")
    return [int(ch, 16) for ch in s]


def nibbles_to_hex(ns):
    if len(ns) == 0:
        return ""
    if any((v < 0 or v > 0xF) for v in ns):
        raise ValueError("nibble values must be in [0, 15]")
    return "".join(f"{v:X}" for v in ns)


def split_state_16(ns):
    if len(ns) != NIBBLES_PER_STATE:
        raise ValueError("Splight state must contain 16 nibbles")
    return ns[:NIBBLES_PER_BRANCH], ns[NIBBLES_PER_BRANCH:]


def join_state_16(L, R):
    if len(L) != NIBBLES_PER_BRANCH or len(R) != NIBBLES_PER_BRANCH:
        raise ValueError("Splight branches must contain 8 nibbles each")
    return list(L) + list(R)


def sbox(x):
    return SBOX_SPLIGHT_S3[x & 0xF]


def sbox_layer_8(xs):
    if len(xs) != NIBBLES_PER_BRANCH:
        raise ValueError("S-box layer expects 8 nibbles")
    return [sbox(v) for v in xs]


def linear_layer_8(xs):
    """Apply M independently to nibbles 0..3 and 4..7 over XOR."""
    if len(xs) != NIBBLES_PER_BRANCH:
        raise ValueError("linear layer expects 8 nibbles")
    out = []
    for base in (0, 4):
        x0, x1, x2, x3 = xs[base:base + 4]
        temp = x0 ^ x2
        out.extend([(temp ^ x3) & 0xF, x0 & 0xF, (x1 ^ x2) & 0xF, temp & 0xF])
    return out


def shift_left_2_nibbles(xs):
    if len(xs) != NIBBLES_PER_BRANCH:
        raise ValueError("SHI2 expects 8 nibbles")
    return list(xs[2:]) + list(xs[:2])


def shift_right_2_nibbles(xs):
    if len(xs) != NIBBLES_PER_BRANCH:
        raise ValueError("inverse SHI2 expects 8 nibbles")
    return list(xs[-2:]) + list(xs[:-2])


def xor_nibbles(a, b):
    if len(a) != len(b):
        raise ValueError("nibble XOR length mismatch")
    return [(x ^ y) & 0xF for x, y in zip(a, b)]


def nibble_to_bits(x, bit_order="msb"):
    if bit_order == "msb":
        return [(x >> i) & 1 for i in (3, 2, 1, 0)]
    if bit_order == "lsb":
        return [(x >> i) & 1 for i in (0, 1, 2, 3)]
    raise ValueError("bit_order must be 'msb' or 'lsb'")


def bits_to_nibble(bits, bit_order="msb"):
    if len(bits) != 4:
        raise ValueError("a nibble has 4 bits")
    if bit_order == "msb":
        return sum((int(bit) & 1) << shift for bit, shift in zip(bits, (3, 2, 1, 0)))
    if bit_order == "lsb":
        return sum((int(bit) & 1) << shift for bit, shift in zip(bits, (0, 1, 2, 3)))
    raise ValueError("bit_order must be 'msb' or 'lsb'")
