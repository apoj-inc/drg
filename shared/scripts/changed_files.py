#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Emit the provider-neutral changed-files snapshot used by both CI providers."""

from __future__ import annotations

import json

from generate_pipeline import changed_files_from_environment


def main() -> int:
    print(json.dumps(changed_files_from_environment(), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
