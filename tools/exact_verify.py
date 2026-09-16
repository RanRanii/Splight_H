#!/usr/bin/env python3
"""Exact same-key SMT verification for complete Splight characteristics.

The MILP model constrains local DDT transitions.  This verifier instead fixes
all exported state differences on two real executions with one shared master
key.  A SAT result therefore includes a concrete state/key witness; UNSAT is
safe to use for a full-trail MILP no-good, while UNKNOWN is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping, Sequence

from z3 import BitVec, BitVecVal, If, Solver, sat, unknown


SBOX = (0xA, 0xD, 0xC, 0xF, 0x9, 0xE, 0x1, 0x0,
        0x7, 0x2, 0x5, 0x4, 0x3, 0x6, 0xB, 0x8)


def _nibbles(name: str, count: int):
    return [BitVec(f"{name}_{index}", 4) for index in range(count)]


def _xor(left: Sequence, right: Sequence):
    return [a ^ b for a, b in zip(left, right)]


def _rotl(values: Sequence, amount: int):
    amount %= len(values)
    return list(values[amount:]) + list(values[:amount])


def _sbox(value):
    result = BitVecVal(SBOX[15], 4)
    for index in range(14, -1, -1):
        result = If(value == BitVecVal(index, 4), BitVecVal(SBOX[index], 4), result)
    return result


def _linear(values: Sequence):
    out = []
    for base in range(0, len(values), 4):
        x0, x1, x2, x3 = values[base:base + 4]
        common = x0 ^ x2
        out.extend((common ^ x3, x0, x1 ^ x2, common))
    return out


def _constant(round_index: int):
    value = round_index & 0x1F
    return [BitVecVal((value >> (4 * (7 - index))) & 0xF, 4) for index in range(8)]


def _key_step(key_state: Sequence, absolute_round: int):
    k0, k1, k2, k3 = (
        list(key_state[:8]), list(key_state[8:16]),
        list(key_state[16:24]), list(key_state[24:32]),
    )
    transformed = list(k0)
    transformed[3] = _sbox(k0[3])
    transformed[7] = _sbox(k0[7])
    round_key = _xor(_xor(_rotl(transformed, 1), k1), _constant(absolute_round))
    return list(round_key) + k2 + k3 + k0, round_key


def _round(state: Sequence, round_key: Sequence):
    left, right = list(state[:8]), list(state[8:])
    y = [_sbox(value) for value in left]
    linear = _linear(y)
    add_key = _xor(linear, round_key)
    z = [_sbox(value) for value in add_key]
    fxor = _xor(z, right)
    shi = _rotl(fxor, 2)
    return shi + left, {"y": y, "lin": linear, "ak": add_key, "z": z}


def _c_constant(round_index: int):
    value = round_index & 0x1F
    return [(value >> (4 * (7 - index))) & 0xF for index in range(8)]


def _c_key_step(key_state, absolute_round: int):
    k0, k1, k2, k3 = key_state[:8], key_state[8:16], key_state[16:24], key_state[24:32]
    transformed = list(k0)
    transformed[3] = SBOX[transformed[3]]
    transformed[7] = SBOX[transformed[7]]
    core = _rotl(transformed, 1)
    round_key = [(core[i] ^ k1[i] ^ _c_constant(absolute_round)[i]) & 0xF for i in range(8)]
    return round_key + list(k2) + list(k3) + list(k0), round_key


def _c_round(state, round_key):
    left, right = state[:8], state[8:]
    y = [SBOX[value] for value in left]
    linear = []
    for base in range(0, 8, 4):
        x0, x1, x2, x3 = y[base:base + 4]
        common = x0 ^ x2
        linear.extend(((common ^ x3) & 0xF, x0 & 0xF, (x1 ^ x2) & 0xF, common & 0xF))
    add_key = [(linear[i] ^ round_key[i]) & 0xF for i in range(8)]
    z = [SBOX[value] for value in add_key]
    return _rotl([(z[i] ^ right[i]) & 0xF for i in range(8)], 2) + list(left)


@dataclass
class _Context:
    rounds: int
    round_offset: int
    states_a: list
    states_b: list
    master_key: list
    keys: list
    records_a: list
    records_b: list


def _build_model(rounds: int, round_offset: int, timeout_ms: int):
    if not isinstance(rounds, int) or rounds <= 0:
        raise ValueError("rounds must be a positive integer")
    if not isinstance(round_offset, int) or round_offset < 0:
        raise ValueError("round_offset must be an integer >= 0")
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")

    solver = Solver()
    solver.set(timeout=int(timeout_ms))
    states_a = [_nibbles("STATE_A_0", 16)]
    states_b = [_nibbles("STATE_B_0", 16)]
    master_key = _nibbles("MASTER_KEY", 32)
    keys = [master_key]
    round_keys = []
    for absolute_round in range(round_offset + rounds):
        next_key, round_key = _key_step(keys[-1], absolute_round)
        keys.append(next_key)
        round_keys.append(round_key)

    records_a, records_b = [], []
    for local_round in range(rounds):
        absolute_round = round_offset + local_round
        next_a, record_a = _round(states_a[-1], round_keys[absolute_round])
        next_b, record_b = _round(states_b[-1], round_keys[absolute_round])
        states_a.append(next_a)
        states_b.append(next_b)
        records_a.append(record_a)
        records_b.append(record_b)

    context = _Context(rounds, round_offset, states_a, states_b, master_key, keys, records_a, records_b)
    registry = {}
    for local_round in range(rounds + 1):
        registry[f"dX{local_round}"] = (states_a[local_round], states_b[local_round])
    for local_round in range(rounds):
        for short, field in (("Y", "y"), ("LIN", "lin"), ("L", "lin"), ("AK", "ak"), ("Z", "z")):
            registry[f"d{short}{local_round}"] = (
                records_a[local_round][field], records_b[local_round][field]
            )
    return solver, context, registry


def _normalise_hex(value: Any, width: int, label: str):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a hexadecimal string")
    text = value.replace("0x", "").replace("0X", "").replace("_", "").replace(" ", "").upper()
    if len(text) != width or any(ch not in "0123456789ABCDEF" for ch in text):
        raise ValueError(f"{label}: expected {width} hexadecimal digits, got {value!r}")
    return text


def _add_fixed(solver, pair, value, label):
    left, right = pair
    text = _normalise_hex(value, len(left), label)
    for a, b, nibble in zip(left, right, text):
        solver.add((a ^ b) == BitVecVal(int(nibble, 16), 4))


def _hex(model, values):
    return "".join(f"{model.eval(value, model_completion=True).as_long() & 0xF:X}" for value in values)


def _difference(model, pair):
    left, right = pair
    return "".join(
        f"{(model.eval(a, model_completion=True).as_long() ^ model.eval(b, model_completion=True).as_long()) & 0xF:X}"
        for a, b in zip(left, right)
    )


def _replay_or_raise(model, context: _Context):
    master_key = [int(ch, 16) for ch in _hex(model, context.master_key)]
    round_keys = []
    key_state = master_key
    for absolute_round in range(context.round_offset + context.rounds):
        key_state, round_key = _c_key_step(key_state, absolute_round)
        round_keys.append(round_key)
    state_a = [int(ch, 16) for ch in _hex(model, context.states_a[0])]
    state_b = [int(ch, 16) for ch in _hex(model, context.states_b[0])]
    for local_round in range(context.rounds):
        absolute_round = context.round_offset + local_round
        state_a = _c_round(state_a, round_keys[absolute_round])
        state_b = _c_round(state_b, round_keys[absolute_round])
        z3_a = [int(ch, 16) for ch in _hex(model, context.states_a[local_round + 1])]
        z3_b = [int(ch, 16) for ch in _hex(model, context.states_b[local_round + 1])]
        if state_a != z3_a or state_b != z3_b:
            raise RuntimeError(
                f"concrete state replay mismatch at local round {local_round} "
                f"(absolute round {absolute_round})"
            )


def verify_fixed_differences(*, rounds: int, fixed_diffs: Mapping[str, str], round_offset: int = 0,
                             timeout_ms: int = 300_000, return_witness: bool = True,
                             return_all_diffs: bool = False):
    """Check all fixed state differences on two real executions under one key."""
    solver, context, registry = _build_model(rounds, round_offset, timeout_ms)
    if not isinstance(fixed_diffs, Mapping):
        raise ValueError("fixed_diffs must be a mapping")
    for name, value in fixed_diffs.items():
        if name not in registry:
            raise ValueError(f"unknown fixed difference {name!r}")
        _add_fixed(solver, registry[name], value, name)
    started = perf_counter()
    result = solver.check()
    base = {
        "rounds": rounds,
        "round_offset": round_offset,
        "elapsed_seconds": perf_counter() - started,
        "reason": None,
        "witness": None,
    }
    if result == unknown:
        base.update(status="UNKNOWN", reason=solver.reason_unknown())
        return base
    if result != sat:
        base["status"] = "UNSAT"
        return base

    base["status"] = "SAT"
    if return_witness:
        model = solver.model()
        _replay_or_raise(model, context)
        witness = {
            "state_A_0": _hex(model, context.states_a[0]),
            "state_B_0": _hex(model, context.states_b[0]),
            "state_A_final": _hex(model, context.states_a[-1]),
            "state_B_final": _hex(model, context.states_b[-1]),
            "master_key": _hex(model, context.master_key),
            "fixed_diffs_actual": {name: _difference(model, registry[name]) for name in fixed_diffs},
            "concrete_replay": "PASS",
        }
        if return_all_diffs:
            witness["all_diffvariables"] = {
                name: _difference(model, pair) for name, pair in sorted(registry.items())
            }
        base["witness"] = witness
    return base


def verify_trail(trail: Mapping[str, Any], *, timeout_ms: int = 300_000,
                 return_witness: bool = True, return_all_diffs: bool = False):
    if not isinstance(trail, Mapping) or "rounds" not in trail or "fixed_diffs" not in trail:
        raise ValueError("trail must contain rounds and fixed_diffs")
    return verify_fixed_differences(
        rounds=int(trail["rounds"]),
        round_offset=int(trail.get("round_offset", 0)),
        fixed_diffs=trail["fixed_diffs"],
        timeout_ms=timeout_ms,
        return_witness=return_witness,
        return_all_diffs=return_all_diffs,
    )


__all__ = ["verify_fixed_differences", "verify_trail"]
