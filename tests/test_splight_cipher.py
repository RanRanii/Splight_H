import random

from splight.splight_cipher import decrypt_block, encrypt_block
from splight.splight_spec import hex_to_nibbles, nibbles_to_hex


def test_hex_round_trip():
    value = "0123456789ABCDEF"
    assert nibbles_to_hex(hex_to_nibbles(value)) == value


def test_encrypt_decrypt_random_zero_key():
    rng = random.Random(0)
    for rounds in (1, 2, 5, 32):
        for _ in range(10):
            plaintext = f"{rng.getrandbits(64):016X}"
            assert decrypt_block(encrypt_block(plaintext, rounds=rounds), rounds=rounds) == plaintext
