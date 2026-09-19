#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Inspect the effective Woodpecker pipeline as a graph.

The graph is derived from the same generators used by the Woodpecker
configuration extension.  The optional HTTP server is intentionally local and
read-only: it never writes generated workflows or executes pipeline jobs.
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import generate_pipeline  # noqa: E402
import generate_woodpecker  # noqa: E402


HTML_PATH = SCRIPT_DIR / "pipeline_graph.html"
VALID_EVENTS = {"push", "pull_request", "deployment"}
ENVIRONMENT_KEYS = (
    "DRG_CHANGED_FILES",
    "CI_PIPELINE_FILES",
    "CI_PIPELINE_EVENT",
    "CI_COMMIT_BRANCH",
    "CI_PIPELINE_DEPLOY_TARGET",
    "CI_PIPELINE_DEPLOY_TASK",
    "FORGEJO_CHANGED_FILES",
    "FORGEJO_DEPLOY_TARGET",
)
ENVIRONMENT_LOCK = threading.Lock()


def _stem(name: str) -> str:
    return Path(name).stem


def _normalise_files(files: Any) -> list[str]:
    if files is None:
        return []
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise ValueError("changed_files must be a list of strings")
    result: list[str] = []
    seen: set[str] = set()
    for item in files:
        path = item.replace("\\", "/").strip()
        while path.startswith("./"):
            path = path[2:]
        if path and path not in seen:
            result.append(path)
            seen.add(path)
    return result


@contextlib.contextmanager
def generator_environment(
    event: str,
    branch: str,
    changed_files: list[str],
    deployment_target: str,
) -> Iterator[None]:
    values = {
        "DRG_CHANGED_FILES": json.dumps(changed_files, separators=(",", ":")),
        "CI_PIPELINE_EVENT": event,
        "CI_COMMIT_BRANCH": branch,
        "CI_PIPELINE_DEPLOY_TARGET": deployment_target,
        "CI_PIPELINE_DEPLOY_TASK": "apply" if event == "deployment" else "",
    }
    with ENVIRONMENT_LOCK:
        previous = {key: os.environ.get(key) for key in ENVIRONMENT_KEYS}
        try:
            for key in ENVIRONMENT_KEYS:
                os.environ.pop(key, None)
            os.environ.update(values)
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _dependency_names(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            result.append(item["name"])
    return result


def _step_entries(document: dict[str, Any]) -> list[dict[str, Any]]:
    steps = document.get("steps", [])
    if isinstance(steps, dict):
        return [
            {"name": str(name), **(step if isinstance(step, dict) else {})}
            for name, step in steps.items()
        ]
    if not isinstance(steps, list):
        return []
    result = []
    for index, step in enumerate(steps):
        if isinstance(step, dict):
            result.append({"name": str(step.get("name", index)), **step})
    return result


def _workflow_node(name: str, document: dict[str, Any], status: str, reason: str = "") -> dict[str, Any]:
    matrix = document.get("matrix", {})
    includes = matrix.get("include", []) if isinstance(matrix, dict) else []
    if not isinstance(includes, list):
        includes = []
    steps = _step_entries(document)
    return {
        "id": f"workflow:{name}",
        "kind": "workflow",
        "label": name,
        "status": status,
        "reason": reason,
        "depends_on": _dependency_names(document.get("depends_on")),
        "matrix": includes,
        "steps": [
            {
                "name": step["name"],
                "image": step.get("image", ""),
                "depends_on": _dependency_names(step.get("depends_on")),
                "when": step.get("when"),
            }
            for step in steps
        ],
    }


def _candidate_workflows(contours: list[str], deployment: bool) -> list[str]:
    prefix = "rollout-deploy-" if deployment else "rollout-"
    suffixes = ["post-rollout-deploy"] if deployment else ["post-rollout", "post-rollout-plan"]
    return [*(f"{prefix}{contour.lower()}" for contour in contours), *suffixes]


def _skipped_reason(name: str, event: str, changed_files: list[str], selected_services: set[str]) -> str:
    if not selected_services:
        return "No affected services were selected for the changed files."
    if name.startswith("rollout-") or name.startswith("rollout-deploy-"):
        if event == "deployment":
            return "This rollout contour is outside the selected deployment target."
        return "No selected service has a rollout target in this contour."
    return "No post-rollout work was required for this scenario."


def build_graph(
    *,
    event: str = "push",
    branch: str = "drunk",
    changed_files: list[str] | None = None,
    deployment_target: str = "",
) -> dict[str, Any]:
    """Build a JSON-compatible effective pipeline graph."""
    if event not in VALID_EVENTS:
        raise ValueError(f"event must be one of: {', '.join(sorted(VALID_EVENTS))}")
    files = _normalise_files(changed_files or [])
    branch = branch.strip() or "drunk"
    deployment_target = deployment_target.strip()
    if event != "deployment":
        deployment_target = ""

    diagnostics: list[dict[str, str]] = []
    generated: dict[str, str] = {}
    selected_services: set[str] = set()
    jobs: list[dict[str, Any]] = []
    with generator_environment(event, branch, files, deployment_target):
        try:
            generated = generate_woodpecker.expected_workflows()
            config = generate_pipeline.read_config()
            jobs = generate_pipeline.build_jobs(config, files)
            selected_services = {
                str(job["service"])
                for job in jobs
                if isinstance(job.get("service"), str)
            }
        except (SystemExit, ValueError, yaml.YAMLError) as exc:
            diagnostics.append({"level": "error", "message": str(exc)})

    documents: dict[str, dict[str, Any]] = {}
    validate_path = REPO_ROOT / ".woodpecker" / "validate.yml"
    try:
        validate_document = yaml.safe_load(validate_path.read_text(encoding="utf-8")) or {}
        if isinstance(validate_document, dict):
            documents["validate"] = validate_document
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        diagnostics.append({"level": "error", "message": f"Cannot read validate.yml: {exc}"})
    for filename, content in generated.items():
        try:
            document = yaml.safe_load(content) or {}
            if isinstance(document, dict):
                documents[_stem(filename)] = document
        except yaml.YAMLError as exc:
            diagnostics.append({"level": "error", "message": f"Cannot parse {filename}: {exc}"})

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    for name, document in documents.items():
        node = _workflow_node(name, document, "active")
        nodes.append(node)
        for dependency in node["depends_on"]:
            edges.append(
                {
                    "from": f"workflow:{dependency}",
                    "to": node["id"],
                    "kind": "workflow",
                }
            )
        for step in node["steps"]:
            step_id = f"step:{name}:{step['name']}"
            nodes.append(
                {
                    "id": step_id,
                    "kind": "step",
                    "workflow": name,
                    "label": step["name"],
                    "status": "active",
                    "image": step["image"],
                    "when": step["when"],
                }
            )
            for dependency in step["depends_on"]:
                edges.append(
                    {
                        "from": f"step:{name}:{dependency}",
                        "to": step_id,
                        "kind": "step",
                    }
                )

    contours = generate_woodpecker.rollout_contours()
    deployment = event == "deployment"
    effective_names = set(documents)
    for candidate in _candidate_workflows(contours, deployment):
        if candidate not in effective_names:
            reason = _skipped_reason(candidate, event, files, selected_services)
            nodes.append(
                {
                    "id": f"workflow:{candidate}",
                    "kind": "workflow",
                    "label": candidate,
                    "status": "skipped",
                    "reason": reason,
                    "depends_on": [],
                    "matrix": [],
                    "steps": [],
                }
            )
            diagnostics.append({"level": "info", "message": f"{candidate}: {reason}"})

    if not generated and not diagnostics:
        diagnostics.append(
            {
                "level": "info",
                "message": "No rollout workflows were generated; only validation will run.",
            }
        )
    elif not generated:
        diagnostics.append(
            {
                "level": "warning",
                "message": "No rollout workflows were generated; only validation is effective.",
            }
        )

    return {
        "version": 1,
        "context": {
            "event": event,
            "branch": branch,
            "changed_files": files,
            "deployment_target": deployment_target,
            "selected_services": sorted(selected_services),
        },
        "nodes": nodes,
        "edges": edges,
        "diagnostics": diagnostics,
        "job_count": len(jobs),
    }


class GraphHandler(http.server.BaseHTTPRequestHandler):
    server_version = "drg-pipeline-graph/1"

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/":
            try:
                body = HTML_PATH.read_bytes()
            except OSError as exc:
                self.send_error(500, str(exc))
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/graph":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ValueError("request is too large")
            request = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            graph = build_graph(
                event=str(request.get("event", "push")),
                branch=str(request.get("branch", "drunk")),
                changed_files=request.get("changed_files", []),
                deployment_target=str(request.get("deployment_target", "")),
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send_json({"version": 1, "error": str(exc)}, status=400)
            return
        self._send_json(graph)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(host: str, port: int) -> None:
    server = http.server.ThreadingHTTPServer((host, port), GraphHandler)
    print(f"Pipeline graph viewer: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", choices=sorted(VALID_EVENTS), default="push")
    parser.add_argument("--branch", default="drunk")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--deployment-target", default="")
    parser.add_argument("--serve", action="store_true", help="serve the interactive viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.serve:
        serve(args.host, args.port)
        return 0
    print(
        json.dumps(
            build_graph(
                event=args.event,
                branch=args.branch,
                changed_files=args.changed_file,
                deployment_target=args.deployment_target,
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
