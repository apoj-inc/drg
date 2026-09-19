#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Recommend four GPUs with the lowest measured peer-to-peer topology cost.

The script intentionally never assumes that GPU 0..3 are the best group. It
prints UUIDs suitable for LLM_MODEL_GPU_UUIDS after an audit in the guest.
"""
from __future__ import annotations

import itertools
import re
import subprocess
import sys


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def main() -> int:
    rows = []
    for line in command(
        "nvidia-smi", "--query-gpu=index,uuid,name,pci.bus_id", "--format=csv,noheader"
    ).splitlines():
        index, uuid, name, bdf = (part.strip() for part in line.split(",", 3))
        if not re.search(r"RTX 3080", name):
            raise SystemExit(f"unexpected GPU product: {name}")
        rows.append((int(index), uuid, bdf))
    if len(rows) != 4:
        raise SystemExit(f"expected 4 RTX 3080 GPUs, found {len(rows)}")

    # nvidia-smi indents the topology header on some driver versions.
    lines = [line.strip() for line in command("nvidia-smi", "topo", "-m").splitlines()]
    header_index = next((i for i, line in enumerate(lines) if line.startswith("GPU0")), None)
    if header_index is None:
        raise SystemExit("cannot parse nvidia-smi topo -m header")
    columns = lines[header_index].split()
    matrix = {}
    for line in lines[header_index + 1 :]:
        fields = line.split()
        if not fields or not re.fullmatch(r"GPU\d+|CPU Affinity|NUMA Affinity|Legend", fields[0]):
            continue
        if not re.fullmatch(r"GPU\d+", fields[0]):
            continue
        source = fields[0]
        for column, value in zip(columns, fields[1:]):
            if re.fullmatch(r"GPU\d+", column):
                matrix[source, column] = value

    weights = {"X": 0, "NV1": 1, "NV2": 1, "NV4": 1, "PIX": 5, "PXB": 10, "PHB": 20, "NODE": 40, "SYS": 100}
    def pair_cost(a: int, b: int) -> int:
        value = matrix.get((f"GPU{a}", f"GPU{b}"), "SYS")
        return weights.get(value, 100)

    scored = []
    for combo in itertools.combinations(rows, 4):
        cost = sum(pair_cost(a[0], b[0]) for a, b in itertools.combinations(combo, 2))
        scored.append((cost, tuple(item[0] for item in combo), combo))
    cost, indices, selected = min(scored)
    print(f"Selected GPU indices: {','.join(map(str, indices))}")
    print(f"Topology pair cost: {cost}")
    print("UUIDs (export this exact order after reviewing the report):")
    print(",".join(item[1] for item in selected))
    print("Details:")
    for item in selected:
        print(f"  GPU{item[0]} {item[1]} {item[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
