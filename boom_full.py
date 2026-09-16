#!/usr/bin/env python3
"""Splight-H search entry point that writes to results_full/<case>/."""

from boom import main_for_result_root


if __name__ == "__main__":
    raise SystemExit(main_for_result_root(result_root_name="results_full"))
