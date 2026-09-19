#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Explicitly remove a retired FreeIPA host entry.

Terraform intentionally does not invoke this on destroy.  The caller must
provide an administrator-approved keytab and repeat the FQDN as confirmation.
"""

from __future__ import annotations

import os
import subprocess
import sys


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"ERROR: {name} is required.")
    return value


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: deprovision_freeipa_host.py <host-fqdn>")
    hostname = sys.argv[1]
    if os.environ.get("IPA_DEPROVISION_CONFIRM") != hostname:
        raise SystemExit("ERROR: set IPA_DEPROVISION_CONFIRM to the exact host FQDN.")
    keytab = required("IPA_DEPROVISION_KEYTAB")
    principal = required("IPA_DEPROVISION_PRINCIPAL")
    subprocess.run(["kinit", "-kt", keytab, principal], check=True)
    subprocess.run(["ipa", "host-del", hostname], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
