"""Local web server for Sphere's topology graph and fix loop.

The topology API is read-only.  The fix loop adds two endpoints: ``POST /api/plan``
compiles the command-plan IR for a target (safe — it never mutates anything), and
``POST /api/apply`` executes an approved plan, streaming a per-action receipt as it
goes and finishing with a re-scanned topology that proves the result.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import webbrowser
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sphere.apply import default_rescan, execute_plan
from sphere.fixplan import build_plan
from sphere.introspect import scan_topology
from sphere.requirements import parse_repository_requirements


def _server_dependencies() -> tuple[Any, Any, Any, Any, Any, Any]:
    try:
        from fastapi import Body, FastAPI, HTTPException, Query
        from fastapi.responses import StreamingResponse
        from fastapi.staticfiles import StaticFiles
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Sphere's web server needs the optional serve dependencies. "
            "Install them with: python -m pip install '.[serve]'"
        ) from error
    return FastAPI, Query, StaticFiles, Body, HTTPException, StreamingResponse


def _protected_prefixes() -> tuple[str, ...]:
    """Realpath of Sphere's own environment, so the fix loop can never target it."""

    return (os.path.realpath(sys.prefix),)


def _dist_directory() -> Path:
    return Path(__file__).with_name("web") / "dist"


def create_app(
    *,
    directory: str | None = None,
    search_roots: Iterable[str] = (),
) -> Any:
    """Create the same-origin API and prebuilt UI application."""

    FastAPI, Query, StaticFiles, Body, HTTPException, StreamingResponse = _server_dependencies()
    default_directory = directory
    default_search_roots = tuple(search_roots)
    dist_directory = _dist_directory()
    if not (dist_directory / "index.html").is_file():
        raise RuntimeError(f"prebuilt frontend is missing: {dist_directory}")

    app = FastAPI(title="Sphere", docs_url=None, redoc_url=None, openapi_url=None)

    def _scan_args(payload: dict[str, Any]) -> tuple[str | None, list[str]]:
        directory = payload.get("directory")
        directory = directory if directory is not None else default_directory
        search_roots = payload.get("search_root") or list(default_search_roots)
        return directory, list(search_roots)

    @app.get("/api/topology")
    def topology(
        directory: str | None = None,
        search_root: list[str] = Query(default=[]),
    ) -> dict[str, Any]:
        """Return a fresh, read-only scan; repeated search_root values are supported."""

        return scan_topology(
            directory=directory if directory is not None else default_directory,
            search_roots=search_root if search_root else default_search_roots,
        )

    @app.post("/api/plan")
    def plan(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        """Compile (but never run) the command-plan IR for one target runtime."""

        target_id = payload.get("target_id")
        if not target_id:
            raise HTTPException(status_code=400, detail="target_id is required")
        scan_directory, scan_roots = _scan_args(payload)
        scan = scan_topology(directory=scan_directory, search_roots=scan_roots)
        repositories = scan["nodes"]["repositories"]
        if not repositories:
            raise HTTPException(status_code=404, detail="no repository was found for this directory")
        try:
            compiled = build_plan(
                scan, repositories[0]["id"], target_id, protected_prefixes=_protected_prefixes()
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"plan": compiled.as_json()}

    @app.post("/api/apply")
    def apply(payload: dict[str, Any] = Body(default={})) -> Any:
        """Execute an approved plan, streaming NDJSON receipts as each action lands."""

        target_id = payload.get("target_id")
        if not target_id:
            raise HTTPException(status_code=400, detail="target_id is required")
        expected_fingerprint = payload.get("fingerprint")
        scan_directory, scan_roots = _scan_args(payload)
        scan = scan_topology(directory=scan_directory, search_roots=scan_roots)
        repositories = scan["nodes"]["repositories"]
        if not repositories:
            raise HTTPException(status_code=404, detail="no repository was found for this directory")
        repository = repositories[0]
        try:
            compiled = build_plan(
                scan, repository["id"], target_id, protected_prefixes=_protected_prefixes()
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        requirements = parse_repository_requirements(repository["path"])
        rescan = default_rescan(scan_directory, scan_roots)

        def stream() -> Iterable[str]:
            # Refuse to run if the world changed since the user previewed this plan.
            if expected_fingerprint and expected_fingerprint != compiled.fingerprint:
                yield json.dumps({"event": "stale", "plan": compiled.as_json()}) + "\n"
                return
            for event in execute_plan(compiled, requirements, rescan=rescan):
                yield json.dumps(event) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    # Mount after the API routes so the static SPA cannot shadow /api/*.
    app.mount("/", StaticFiles(directory=str(dist_directory), html=True), name="web")
    return app


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve Sphere's local topology graph.")
    parser.add_argument(
        "directory",
        nargs="?",
        help="repository/directory to scan (defaults to the current directory)",
    )
    parser.add_argument(
        "--search-root",
        action="append",
        default=[],
        metavar="PATH",
        help="recursively scan PATH for pyvenv.cfg (may be supplied more than once)",
    )
    parser.add_argument("--port", type=int, default=0, help="localhost port (defaults to a free port)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the local URL automatically")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    try:
        import uvicorn
    except ModuleNotFoundError as error:
        raise SystemExit("Sphere's web server needs the optional serve dependencies: pip install '.[serve]'") from error

    port = arguments.port or _available_port()
    url = f"http://127.0.0.1:{port}/"
    application = create_app(directory=arguments.directory, search_roots=arguments.search_root)
    if not arguments.no_browser:
        threading.Timer(0.35, webbrowser.open, args=(url,)).start()
    print(f"Sphere is running at {url}")
    uvicorn.run(application, host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
