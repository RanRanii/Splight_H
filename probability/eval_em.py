"""Experimental estimate of the Splight middle boomerang switch probability."""

import json
import math
import random

from splight.splight_cipher import decrypt_middle_state, encrypt_middle_state
from splight.splight_spec import hex_to_nibbles, nibbles_to_hex, xor_nibbles


def _normalise_delta(value):
    if isinstance(value, str) or isinstance(value, int):
        return hex_to_nibbles(value, 16)
    if len(value) != 16:
        raise ValueError("difference must contain 16 nibbles")
    return list(value)


def estimate_em_probability(delta_in, nabla_out, rm, trials, seed=0, round_keys=None):
    delta = _normalise_delta(delta_in)
    nabla = _normalise_delta(nabla_out)
    rng = random.Random(seed)
    returned = 0
    for _ in range(int(trials)):
        x = [rng.randrange(16) for _ in range(16)]
        x2 = xor_nibbles(x, delta)
        y = encrypt_middle_state(x, rm, round_keys)
        y2 = encrypt_middle_state(x2, rm, round_keys)
        pre1 = decrypt_middle_state(xor_nibbles(y, nabla), rm, round_keys)
        pre2 = decrypt_middle_state(xor_nibbles(y2, nabla), rm, round_keys)
        if xor_nibbles(pre1, pre2) == delta:
            returned += 1
    estimated = returned / trials if trials else 0.0
    out = {
        "trials": int(trials),
        "returned_count": returned,
        "estimated_r": estimated,
        "log2_r": None if returned == 0 else math.log2(estimated),
    }
    if returned == 0 and trials:
        out["note"] = "No returns observed; increase trials. Current one-sided experimental upper estimate is below 1/trials."
        out["upper_estimate"] = 1 / trials
    return out


def run_experiment_from_result(result_file, trials, seed=0):
    data = json.loads(open(result_file, encoding="utf-8").read())
    delta = data.get("delta_in", "0000000000000000")
    nabla = data.get("nabla_out", "0000000000000000")
    rm = data.get("parameters", {}).get("rm", data.get("rm", 0))
    return estimate_em_probability(delta, nabla, rm, trials, seed=seed)


def format_probability_result(result):
    log2_r = result["log2_r"]
    return (
        f"trials: {result['trials']}\n"
        f"returned_count: {result['returned_count']}\n"
        f"estimated_r: {result['estimated_r']}\n"
        f"log2_r: {log2_r if log2_r is not None else 'not observed'}\n"
    )
