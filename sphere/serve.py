"""Read-only local web server for Sphere's topology graph."""

from __future__ import annotations

import argparse
import socket
import threading
import webbrowser
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sphere.introspect import scan_topology


def _server_dependencies() -> tuple[Any, Any, Any]:
    try:
        from fastapi import FastAPI, Query
        from fastapi.staticfiles import StaticFiles
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Sphere's web server needs the optional serve dependencies. "
            "Install them with: python -m pip install '.[serve]'"
        ) from error
    return FastAPI, Query, StaticFiles


def _dist_directory() -> Path:
    return Path(__file__).with_name("web") / "dist"


def create_app(
    *,
    directory: str | None = None,
    search_roots: Iterable[str] = (),
) -> Any:
    """Create the same-origin API and prebuilt UI application."""

    FastAPI, Query, StaticFiles = _server_dependencies()
    default_directory = directory
    default_search_roots = tuple(search_roots)
    dist_directory = _dist_directory()
    if not (dist_directory / "index.html").is_file():
        raise RuntimeError(f"prebuilt frontend is missing: {dist_directory}")

    app = FastAPI(title="Sphere", docs_url=None, redoc_url=None, openapi_url=None)

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

    # Mount after the API route so the static SPA cannot shadow /api/topology.
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
