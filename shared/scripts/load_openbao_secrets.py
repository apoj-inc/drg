#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch CI secrets from OpenBao and emit a shell environment file."""

from __future__ import annotations

import json
import base64
import binascii
import os
import shlex
import ssl
import sys
import tempfile
from urllib.parse import quote
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def request_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    context: str = "OpenBao request",
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            response = json.loads(exc.read().decode("utf-8", errors="replace"))
            errors = response.get("errors", []) if isinstance(response, dict) else []
            detail = "; ".join(error for error in errors if isinstance(error, str))
        except (OSError, json.JSONDecodeError):
            detail = ""
        if len(detail) > 1_000:
            detail = detail[:1_000] + "…"
        suffix = f": {detail}" if detail else ""
        raise SystemExit(f"ERROR: {context} failed: HTTP {exc.code}{suffix}") from exc
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: {context} failed: {exc}") from exc


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"ERROR: required OpenBao CI environment variable {name} is empty.")
    return value


def workflow_oidc_token(audience: str) -> str:
    request_url = required("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = required("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    separator = "&" if "?" in request_url else "?"
    request = urllib.request.Request(
        f"{request_url}{separator}audience={quote(audience, safe='')}",
        headers={"Authorization": f"Bearer {request_token}"},
    )
    try:
        with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=20) as response:
            body = response.read().decode("utf-8").strip()
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"ERROR: Forgejo OIDC token request failed: {exc}") from exc
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        return body
    if isinstance(document, dict):
        for key in ("value", "token", "id_token"):
            if isinstance(document.get(key), str) and document[key]:
                return document[key]
    raise SystemExit("ERROR: Forgejo OIDC token response did not contain a token.")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: load_openbao_secrets.py <output-env-file>")
    output = Path(sys.argv[1])
    try:
        specs = json.loads(required("OPENBAO_SECRET_SPECS"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: OPENBAO_SECRET_SPECS is not valid JSON: {exc}") from exc
    if not isinstance(specs, list):
        raise SystemExit("ERROR: OPENBAO_SECRET_SPECS must be a JSON list.")
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict):
            raise SystemExit(f"ERROR: OpenBao secret specification {index} must be an object.")
        if not isinstance(spec.get("job_types", []), list):
            raise SystemExit(f"ERROR: OpenBao secret specification {index}.job_types must be a list.")
    job_type = required("OPENBAO_SECRET_JOB")
    selected = [spec for spec in specs if job_type in spec.get("job_types", [])]
    if not selected:
        output.write_text("", encoding="utf-8")
        return 0

    server = required("VAULT_SERVER_URL").rstrip("/")
    token = os.environ.get("OPENBAO_TOKEN", "")
    if not token:
        token_name = required("OPENBAO_ID_TOKEN_NAME")
        jwt = os.environ.get(token_name, "")
        if not jwt and os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"):
            jwt = workflow_oidc_token(
                os.environ.get("OPENBAO_OIDC_AUDIENCE", "https://openbao.example.test")
            )
        if not jwt:
            raise SystemExit(f"ERROR: required OpenBao CI environment variable {token_name} is empty.")
        login = request_json(
            f"{server}/v1/auth/{required('VAULT_AUTH_PATH').strip('/')}/login",
            {"Content-Type": "application/json"},
            {"role": required("VAULT_AUTH_ROLE"), "jwt": jwt},
            context="OpenBao JWT login",
        )
        token = login.get("auth", {}).get("client_token", "")
        if not isinstance(token, str) or not token:
            raise SystemExit("ERROR: OpenBao JWT login did not return a client token.")

    exports: list[str] = []
    for spec in selected:
        for key in ("environment_name", "mount", "path", "field"):
            if not isinstance(spec.get(key), str) or not spec[key]:
                raise SystemExit(f"ERROR: OpenBao secret specification has invalid {key}.")
        secret = request_json(
            f"{server}/v1/{spec['mount']}/data/{spec['path'].strip('/')}",
            {"X-Vault-Token": token},
            context=(
                "OpenBao secret read "
                f"{spec['mount']}/{spec['path'].strip('/')}"
            ),
        )
        value = secret.get("data", {}).get("data", {}).get(spec["field"])
        if not isinstance(value, str):
            raise SystemExit(f"ERROR: OpenBao secret {spec['mount']}/{spec['path']} lacks string field {spec['field']}.")
        if spec.get("file"):
            encoding = spec.get("encoding", "plain")
            if encoding not in {"plain", "base64"}:
                raise SystemExit(f"ERROR: OpenBao file secret {spec['field']} has unsupported encoding {encoding!r}.")
            try:
                content = base64.b64decode(value, validate=True) if encoding == "base64" else value.encode("utf-8")
            except (ValueError, binascii.Error) as exc:
                raise SystemExit(f"ERROR: OpenBao file secret {spec['field']} is not valid Base64.") from exc
            descriptor, filename = tempfile.mkstemp(prefix="openbao-", text=False)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as secret_file:
                secret_file.write(content)
            value = filename
        exports.append(f"export {spec['environment_name']}={shlex.quote(value)}")
    output.write_text("\n".join(exports) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
