#!/usr/bin/env python3

"""Hadipour-style Splight boomerang search entry.

This file intentionally follows the control flow and parameter style of
Paper_code/comeback-main/lblock/boom.py. The cipher-specific parts live in
truncdiff.py, truncboom.py and diff.py.
"""

from argparse import ArgumentParser, RawTextHelpFormatter
import json
import os

import yaml

from truncboom import TruncatedBoomerang
from diff import Diff
from output.plotdistinguisher import *


def diff_trail_to_lines(diff_trail):
    if diff_trail is None:
        return []
    lines = ["Rounds  x                 y         l         z         pr     ",
             "-" * 64]
    for r in range(diff_trail["nrounds"] + 1):
        lines.append(
            f"{r:<7} "
            f"{diff_trail.get(f'x_{r}', 'none'):<17} "
            f"{diff_trail.get(f'y_{r}', 'none'):<9} "
            f"{diff_trail.get(f'l_{r}', 'none'):<9} "
            f"{diff_trail.get(f'z_{r}', 'none'):<9} "
            f"{diff_trail.get(f'pr_{r}', 'none'):<7}"
        )
    lines.append(f"Weight: -{diff_trail.get('total_weight', '0')}")
    return lines


def truncated_trail_to_lines(trail, nrounds):
    lines = ["Rounds  x                 y         l         z        ",
             "-" * 56]
    for r in range(nrounds + 1):
        lines.append(
            f"{r:<7} "
            f"{trail.get(f'x_{r}', ''):<17} "
            f"{trail.get(f'y_{r}', 'none'):<9} "
            f"{trail.get(f'l_{r}', 'none'):<9} "
            f"{trail.get(f'z_{r}', 'none'):<9}"
        )
    return lines


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    parser = ArgumentParser(description="This tool finds the nearly optimum boomerang distinguisher\n"
                                         "Example:\n"
                                         "python3 boom.py -r0 2 -rm 3 -r1 2 -w0 6 -wm 3 -w1 6",
                            formatter_class=RawTextHelpFormatter)
    parser.add_argument('-i', '--inputfile', type=str, help="Use an input file in yaml format")
    parser.add_argument('-r0', '--r0', type=int, help="number of rounds covered by E0")
    parser.add_argument('-rm', '--rm', type=int, help="number of rounds covered by Em")
    parser.add_argument('-r1', '--r1', type=int, help="number of rounds covered by E1")
    parser.add_argument('-w0', '--w0', type=int, help="cost of active S-boxes in E0")
    parser.add_argument('-wm', '--wm', type=int, help="cost of active S-boxes in Em")
    parser.add_argument('-w1', '--w1', type=int, help="cost of active S-boxes in E1")
    parser.add_argument('-tl', '--timelimit', type=int, help="time limit in seconds")
    parser.add_argument('-ns', '--numofsols', type=int, help="number of solutions (currently disabled)")

    args = parser.parse_args()
    params = loadparameters(args)
    r0, rm, r1 = params["r0"], params["rm"], params["r1"]
    w0, wm, w1 = params["w0"], params["wm"], params["w1"]
    assert(rm > 0)

    tex_content = tex_init()

    bm = TruncatedBoomerang(r0=r0, r1=r1, rm=rm, w0=w0, w1=w1, wm=wm,
                            time_limit=params.get("timelimit"))
    bm.iterative = False
    bm.find_truncated_boomerang_trail()
    upper_trail, middle_part, lower_trail = bm.parse_solver_output()

    result_name = f"{r0}-{rm}-{r1}"
    results_dir = os.path.join("results", result_name)
    os.makedirs(results_dir, exist_ok=True)
    lp_target = bm.lp_file_name

    diff_upper_trail = None
    diff_effect_upper = 0
    if r0 != 0:
        time_limit = params.get("timelimit", 18000)
        diff_params = {"nrounds": bm.r0,
                       "mode": 0,
                       "startweight": 0,
                       "endweight": 128,
                       "timelimit": time_limit,
                       "numberoftrails": 1,
                       "fixedVariables": {}}
        for nibble in range(16):
            if upper_trail[f"x_0"][nibble] == "0":
                for bit in range(4):
                    diff_params["fixedVariables"][f"x_{0}_{nibble}_{bit}"] = "0"
            if upper_trail[f"x_{bm.r0}"][nibble] == "0":
                for bit in range(4):
                    diff_params["fixedVariables"][f"x_{bm.r0}_{nibble}_{bit}"] = "0"
        diff = Diff(diff_params)
        diff.make_model()
        diff_upper_trail = diff.solve()
        diff_params["fixedVariables"] = {"x_0": diff_upper_trail["x_0"],
                                         f"x_{bm.r0}": diff_upper_trail[f"x_{bm.r0}"]}
        diff_params["mode"] = 2
        diff = Diff(diff_params)
        diff.make_model()
        diff_effect_upper = diff.solve()

    diff_lower_trail = None
    diff_effect_lower = 0
    if r1 != 0:
        time_limit = params.get("timelimit", 18000)
        diff_params = {"nrounds": bm.r1,
                       "mode": 0,
                       "startweight": 0,
                       "endweight": 128,
                       "timelimit": time_limit,
                       "numberoftrails": 1,
                       "fixedVariables": {}}
        for nibble in range(16):
            if lower_trail[f"x_{bm.rm}"][nibble] == "0":
                for bit in range(4):
                    diff_params["fixedVariables"][f"x_{0}_{nibble}_{bit}"] = "0"
            if lower_trail[f"x_{bm.R1}"][nibble] == "0":
                for bit in range(4):
                    diff_params["fixedVariables"][f"x_{bm.r1}_{nibble}_{bit}"] = "0"
        diff = Diff(diff_params)
        diff.make_model()
        diff_lower_trail = diff.solve()
        diff_params["fixedVariables"] = {"x_0": diff_lower_trail["x_0"],
                                         f"x_{bm.r1}": diff_lower_trail[f"x_{bm.r1}"]}
        diff_params["mode"] = 2
        diff = Diff(diff_params)
        diff.make_model()
        diff_effect_lower = diff.solve()

    print("#" * 27)
    print("Summary of the results:")
    print("Upper trail:")
    if diff_upper_trail is not None:
        diff.print_trail(diff_trail=diff_upper_trail)
    print("#" * 27)
    mactive_sboxes = middle_part["as"]
    print(f"Sandwich {rm} rounds in the middle with {mactive_sboxes} active S-boxes")
    print("#" * 27)
    print("Lower trail:")
    if diff_lower_trail is not None:
        diff.print_trail(diff_trail=diff_lower_trail)
    print("-" * 27)
    total_weight = 0
    if diff_effect_upper != 0:
        print("differential effect of the upper trail: 2^(%0.02f)" % diff_effect_upper)
        total_weight += diff_effect_upper * 2
    if diff_effect_lower != 0:
        print("differential effect of the lower trail: 2^(%0.02f)" % diff_effect_lower)
        total_weight += diff_effect_lower * 2
    upper_bound = total_weight + (-2) * mactive_sboxes
    lower_bound = total_weight + (-2.5) * mactive_sboxes
    print("Total probability = p^2*q^2*r = 2^({:.2f}) x 2^({:.2f}) x r".format(
        diff_effect_upper * 2, diff_effect_lower * 2))
    print("2^({:.2f}) <= Total probability <= 2^({:.2f})".format(lower_bound, upper_bound))
    print("To compute the accurate value of total probability, r should be evaluated experimentally or using the (F)BCT framework")

    tex_content += tex_middle(upper_trail=upper_trail, midd_trail=middle_part,
                              lower_trail=lower_trail, r0=r0, rm=rm, r1=r1)
    tex_content += tex_fin(r0 + rm + r1)
    tex_path = os.path.join(results_dir, "bmd.tex")
    with open(tex_path, "w", encoding="utf-8") as texfile:
        texfile.write(tex_content)

    result = {
        "parameters": params,
        "upper_trail": upper_trail,
        "middle_part": middle_part,
        "lower_trail": lower_trail,
        "diff_upper_trail": diff_upper_trail,
        "diff_lower_trail": diff_lower_trail,
        "diff_effect_upper_log2": diff_effect_upper,
        "diff_effect_lower_log2": diff_effect_lower,
        "lp_file": lp_target,
    }
    json_path = os.path.join(results_dir, f"{result_name}.json")
    txt_path = os.path.join(results_dir, f"{result_name}.txt")
    md_path = os.path.join(results_dir, f"{result_name}_distinguisher.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    with open(txt_path, "w", encoding="utf-8") as handle:
        handle.write("Summary of the results:\n")
        handle.write("Upper Truncated Trail:\n")
        handle.write("\n".join(truncated_trail_to_lines(upper_trail, bm.R0)) + "\n")
        handle.write("Lower Truncated Trail:\n")
        handle.write("\n".join(truncated_trail_to_lines(lower_trail, bm.R1)) + "\n")
        handle.write("Upper differential trail:\n")
        handle.write("\n".join(diff_trail_to_lines(diff_upper_trail)) + "\n")
        handle.write("Lower differential trail:\n")
        handle.write("\n".join(diff_trail_to_lines(diff_lower_trail)) + "\n")
        handle.write(f"Sandwich {rm} rounds in the middle with {mactive_sboxes} active S-boxes\n")
        handle.write("Total probability = p^2*q^2*r = 2^({:.2f}) x 2^({:.2f}) x r\n".format(
            diff_effect_upper * 2, diff_effect_lower * 2))
    save_markdown_distinguisher(json_path, md_path)


def loadparameters(args):
    params = {"inputfile": "./input.yaml",
              "r0": 2,
              "rm": 3,
              "r1": 2,
              "w0": 6,
              "wm": 3,
              "w1": 6,
              "timelimit": 1200,
              "numofsols": 1}
    if args.inputfile:
        with open(args.inputfile, "r", encoding="utf-8") as input_file:
            doc = yaml.load(input_file, Loader=yaml.FullLoader)
            params.update(doc)
    if args.inputfile:
        params["inputfile"] = args.inputfile
    if args.r0 is not None:
        params["r0"] = args.r0
    if args.rm is not None:
        params["rm"] = args.rm
    if args.r1 is not None:
        params["r1"] = args.r1
    if args.w0 is not None:
        params["w0"] = args.w0
    if args.wm is not None:
        params["wm"] = args.wm
    if args.w1 is not None:
        params["w1"] = args.w1
    if args.timelimit is not None:
        params["timelimit"] = args.timelimit
    if args.numofsols is not None:
        params["numofsols"] = args.numofsols
    return params


if __name__ == "__main__":
    main()
