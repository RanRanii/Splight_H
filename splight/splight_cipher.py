"""Executable Splight encryption/decryption with zero-key default rounds."""

from splight.splight_spec import (
    NIBBLES_PER_BRANCH,
    NIBBLES_PER_STATE,
    ROUNDS,
    hex_to_nibbles,
    join_state_16,
    linear_layer_8,
    nibbles_to_hex,
    sbox_layer_8,
    shift_left_2_nibbles,
    shift_right_2_nibbles,
    split_state_16,
    xor_nibbles,
)


def _normalise_round_keys(round_keys, rounds):
    if round_keys is None:
        return [[0] * NIBBLES_PER_BRANCH for _ in range(rounds)]
    if len(round_keys) < rounds:
        raise ValueError("not enough round keys")
    keys = []
    for rk in round_keys[:rounds]:
        if isinstance(rk, str) or isinstance(rk, int):
            rk = hex_to_nibbles(rk, NIBBLES_PER_BRANCH)
        if len(rk) != NIBBLES_PER_BRANCH:
            raise ValueError("each round key must have 8 nibbles")
        keys.append(list(rk))
    return keys


def round_f(L, rk=None):
    if rk is None:
        rk = [0] * NIBBLES_PER_BRANCH
    y = sbox_layer_8(L)
    mixed = linear_layer_8(y)
    keyed = xor_nibbles(mixed, rk)
    return sbox_layer_8(keyed)


def enc_one_round(L, R, rk=None):
    f_out = round_f(L, rk)
    L_next = shift_left_2_nibbles(xor_nibbles(f_out, R))
    R_next = list(L)
    return L_next, R_next


def dec_one_round(L_next, R_next, rk=None):
    L = list(R_next)
    R = xor_nibbles(round_f(L, rk), shift_right_2_nibbles(L_next))
    return L, R


def encrypt_middle_state(state16, rounds, round_keys=None):
    if isinstance(state16, str) or isinstance(state16, int):
        state16 = hex_to_nibbles(state16, NIBBLES_PER_STATE)
    L, R = split_state_16(list(state16))
    for rk in _normalise_round_keys(round_keys, rounds):
        L, R = enc_one_round(L, R, rk)
    return join_state_16(L, R)


def decrypt_middle_state(state16, rounds, round_keys=None):
    if isinstance(state16, str) or isinstance(state16, int):
        state16 = hex_to_nibbles(state16, NIBBLES_PER_STATE)
    L, R = split_state_16(list(state16))
    for rk in reversed(_normalise_round_keys(round_keys, rounds)):
        L, R = dec_one_round(L, R, rk)
    return join_state_16(L, R)


def encrypt_block(plaintext, round_keys=None, rounds=ROUNDS):
    state = encrypt_middle_state(plaintext, rounds, round_keys)
    return nibbles_to_hex(state)


def decrypt_block(ciphertext, round_keys=None, rounds=ROUNDS):
    state = decrypt_middle_state(ciphertext, rounds, round_keys)
    return nibbles_to_hex(state)
