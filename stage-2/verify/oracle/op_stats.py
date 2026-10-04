"""Show which (operation, status, code) outcomes the differential generator reaches on the model.

    python op_stats.py --seed 1 --runs 30 --ops 80
"""
from __future__ import annotations

import argparse
import collections
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from diff_runner import ModelTarget, execute, gen_sequence  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--ops", type=int, default=80)
    a = ap.parse_args()
    cnt: collections.Counter = collections.Counter()
    for seed in range(a.seed, a.seed + a.runs):
        t = ModelTarget()
        for op in gen_sequence(random.Random(seed), a.ops):
            st, body = execute(op, t)
            code = body["error"]["code"] if isinstance(body, dict) and "error" in body else ""
            cnt[(op["op"], st, code)] += 1
    for k_, v in sorted(cnt.items()):
        print(f"{v:6d}  {k_[0]:<13} {k_[1]} {k_[2]}")


if __name__ == "__main__":
    main()
