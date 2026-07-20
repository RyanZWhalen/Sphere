"""
JSON schema (schema_version = "1.0")
=====================================

{
  "schema_version": "1.0",
  "generated_at": "ISO-8601 UTC timestamp",
  "platform": {"system": "Darwin|Linux", "release": "string", "machine": "string"},
  "nodes": {
    "interpreters": [{
      "id": "interpreter:<canonical executable path>",
      "type": "interpreter",
      "path": "canonical absolute executable path",
      "version": "major.minor.patch or null",
      "implementation": "cpython|pypy|... or null",
      "discovered_by": [{"source": "string", "path": "absolute path as found"}]
    }],
    "environments": [{
      "id": "environment:<canonical environment path>",
      "type": "environment",
      "kind": "venv|uv-project|conda",
      "path": "canonical absolute environment path",
      "interpreter_path": "environment's executable path or null",
      "interpreter_id": "interpreter node id or null",
      "base_interpreter_path": "base executable parsed from pyvenv.cfg or null",
      "base_interpreter_id": "interpreter node id or null",
      "base_link_broken": "boolean for venvs, null for conda",
      "discovered_by": [{"source": "string"}]
    }],
    "repositories": [{
      "id": "repository:<canonical repository path>",
      "type": "repository",
      "path": "canonical repository path",
      "requirements": ["serialized PEP 508 requirement declarations"]
    }],
    "contexts": [{"id": "context:<directory>", "type": "directory-context", "path": "directory"}]
  },
  "edges": [
    {"type": "based-on", "from": "environment id", "to": "interpreter id"},
    {"type": "resolves-to", "from": "context id", "to": "interpreter id", "command": "python|python3"},
    {
      "type": "requires", "from": "repository id", "to": "environment or interpreter id",
      "verdict": "satisfied|missing|version-mismatch",
      "evidence": {
        "available": "boolean",
        "python_path": "absolute executable path or null",
        "command": ["exact subprocess argv"] | null,
        "raw_stdout": "verbatim distribution-list JSON or null",
        "reason": "why evidence is unavailable or null"
      },
      "diff": [{
        "name": "distribution name", "specifier": "PEP 440 specifier",
        "status": "satisfied|missing|version-mismatch", "installed_version": "string or null",
        "evidence": {"reported_distribution": {"name": "raw name", "version": "raw version"} | null}
      }]
    }
  ],
  "packages": [{
    "context_id": "interpreter or environment id",
    "python_path": "absolute executable used for this query or null",
    "status": "ok|unavailable|error",
    "packages": [{"name": "distribution name", "version": "string"}],
    "query_evidence": "same query evidence object attached to its requires edge"
  }],
  "context_resolution": {"directory": "path", "commands": []},
  "warnings": [{"source": "discovery stage", "message": "string", "path": "optional path"}]
}

All paths in node IDs are canonicalized with realpath so symlink aliases merge into one
interpreter or environment node.  Package records deliberately remain attached to their
runtime context rather than becoming graph nodes: package dependency edges can be added
later without changing interpreter/environment identity.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sphere.requirements import aggregate_verdict, diff_requirements, parse_repository_requirements


SCHEMA_VERSION = "1.0"
PYTHON_NAME = re.compile(r"^python(?:\d+(?:\.\d+)*)?$")
DEFAULT_TIMEOUT_SECONDS = 12
ENV_SCAN_MAX_DEPTH = 8
BIN_SCAN_MAX_DEPTH = 5
SKIPPED_SCAN_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
}

PROBE_SCRIPT = r"""
import json
import platform
import sys
implementation = getattr(getattr(sys, 'implementation', None), 'name', None)
if not implementation:
    implementation = platform.python_implementation().lower()
print(json.dumps({
    'version': '.'.join([str(part) for part in sys.version_info[:3]]),
    'implementation': implementation,
    'executable': sys.executable,
}))
"""

PACKAGES_SCRIPT = r"""
import json
import importlib.metadata as metadata
packages = []
for distribution in metadata.distributions():
    try:
        name = distribution.metadata.get('Name') or distribution.name
        version = distribution.version
        if name:
            packages.append({'name': str(name), 'version': str(version)})
    except Exception:
        pass
packages.sort(key=lambda item: (item['name'].lower(), item['version']))
print(json.dumps(packages, sort_keys=True))
"""


def _absolute(path: str | os.PathLike[str]) -> str:
    return os.path.abspath(os.path.expanduser(os.fspath(path)))


def _canonical(path: str | os.PathLike[str]) -> str:
    return os.path.realpath(_absolute(path))


def _identifier(kind: str, path: str) -> str:
    return f"{kind}:{path}"


class WarningLog:
    """Collect non-fatal scanner failures in a JSON-safe form."""

    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, source: str, message: object, path: str | None = None) -> None:
        warning = {"source": source, "message": str(message)}
        if path:
            warning["path"] = path
        self.items.append(warning)


def _sanitized_environment() -> dict[str, str]:
    """Keep an active shell from changing what a probed interpreter imports."""

    environment = dict(os.environ)
    for name in (
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "__PYVENV_LAUNCHER__",
    ):
        environment.pop(name, None)
    return environment


def _run(
    command: list[str],
    *,
    source: str,
    warnings: WarningLog,
    path: str | None = None,
    cwd: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=_sanitized_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        warnings.add(source, f"could not run {' '.join(command)}: {error}", path)
        return None
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit status {completed.returncode}"
        warnings.add(source, f"{' '.join(command)} failed: {detail}", path)
        return None
    return completed.stdout


def _is_executable_file(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _python_executables_in(directory: str, warnings: WarningLog, source: str) -> list[str]:
    try:
        with os.scandir(directory) as entries:
            return sorted(
                entry.path
                for entry in entries
                if PYTHON_NAME.match(entry.name) and entry.is_file(follow_symlinks=True) and os.access(entry.path, os.X_OK)
            )
    except OSError as error:
        warnings.add(source, f"could not inspect directory: {error}", directory)
        return []


class InterpreterRegistry:
    def __init__(self, warnings: WarningLog) -> None:
        self._warnings = warnings
        self._nodes: dict[str, dict[str, Any]] = {}

    def add(self, path: str, source: str) -> str | None:
        found_path = _absolute(path)
        try:
            if not _is_executable_file(found_path):
                return None
            canonical_path = _canonical(found_path)
            if not _is_executable_file(canonical_path):
                self._warnings.add(source, "interpreter symlink target is not executable", found_path)
                return None
        except OSError as error:
            self._warnings.add(source, f"could not inspect interpreter: {error}", found_path)
            return None

        node_id = _identifier("interpreter", canonical_path)
        node = self._nodes.get(canonical_path)
        discovery = {"source": source, "path": found_path}
        if node is None:
            node = {
                "id": node_id,
                "type": "interpreter",
                "path": canonical_path,
                "version": None,
                "implementation": None,
                "discovered_by": [discovery],
            }
            self._nodes[canonical_path] = node
        elif discovery not in node["discovered_by"]:
            node["discovered_by"].append(discovery)
        return node_id

    def lookup(self, path: str | None) -> str | None:
        if not path:
            return None
        return _identifier("interpreter", _canonical(path)) if _canonical(path) in self._nodes else None

    def probe(self) -> None:
        for node in self._nodes.values():
            output = _run(
                [node["path"], "-I", "-c", PROBE_SCRIPT],
                source="interpreter-probe",
                warnings=self._warnings,
                path=node["path"],
            )
            if output is None:
                # Older Python versions may not support -I.  A sanitized environment is
                # still enough to make this fallback safe for inspection.
                output = _run(
                    [node["path"], "-c", PROBE_SCRIPT],
                    source="interpreter-probe-fallback",
                    warnings=self._warnings,
                    path=node["path"],
                )
            if output is None:
                continue
            try:
                result = json.loads(output)
                node["version"] = result.get("version")
                node["implementation"] = result.get("implementation")
            except (TypeError, ValueError) as error:
                self._warnings.add("interpreter-probe", f"invalid JSON from interpreter: {error}", node["path"])

    def nodes(self) -> list[dict[str, Any]]:
        return sorted(self._nodes.values(), key=lambda node: node["path"])


def _walk_named_directories(root: str, name: str, max_depth: int, warnings: WarningLog, source: str) -> Iterable[str]:
    root = _absolute(root)
    if not os.path.isdir(root):
        return []
    matches: list[str] = []
    try:
        for current, directories, _files in os.walk(root, topdown=True, followlinks=False):
            relative = os.path.relpath(current, root)
            depth = 0 if relative == "." else relative.count(os.sep) + 1
            directories[:] = [
                directory
                for directory in directories
                if directory not in SKIPPED_SCAN_DIRECTORIES and depth < max_depth
            ]
            if os.path.basename(current) == name:
                matches.append(current)
                directories[:] = []
    except OSError as error:
        warnings.add(source, f"could not walk directory: {error}", root)
    return matches


def _register_python_directory(
    directory: str, source: str, registry: InterpreterRegistry, warnings: WarningLog
) -> None:
    if not os.path.isdir(directory):
        return
    for executable in _python_executables_in(directory, warnings, source):
        registry.add(executable, source)


def _discover_path_interpreters(registry: InterpreterRegistry, warnings: WarningLog) -> None:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        _register_python_directory(entry or os.curdir, "PATH", registry, warnings)


def _discover_system_interpreters(registry: InterpreterRegistry, warnings: WarningLog) -> None:
    roots = ["/usr/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")]
    if platform.system() == "Linux":
        roots.extend(["/opt/python/bin", "/snap/bin"])
    else:
        roots.extend(["/opt/homebrew/bin", "/Library/Frameworks/Python.framework/Versions"])
    for root in roots:
        _register_python_directory(root, "system-location", registry, warnings)

    if platform.system() == "Darwin":
        for framework_root in (
            "/Library/Frameworks/Python.framework/Versions",
            "/System/Library/Frameworks/Python.framework/Versions",
        ):
            try:
                for version_dir in Path(framework_root).iterdir():
                    _register_python_directory(str(version_dir / "bin"), "macos-framework", registry, warnings)
            except (OSError, PermissionError) as error:
                if os.path.exists(framework_root):
                    warnings.add("macos-framework", f"could not inspect framework versions: {error}", framework_root)


def _discover_pyenv_interpreters(registry: InterpreterRegistry, warnings: WarningLog) -> None:
    root = os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
    versions = os.path.join(root, "versions")
    for bin_directory in _walk_named_directories(versions, "bin", BIN_SCAN_MAX_DEPTH, warnings, "pyenv"):
        _register_python_directory(bin_directory, "pyenv", registry, warnings)


def _discover_uv_interpreters(registry: InterpreterRegistry, warnings: WarningLog) -> None:
    candidates = [
        os.environ.get("UV_PYTHON_INSTALL_DIR", ""),
        os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "uv", "python"),
    ]
    if platform.system() == "Darwin":
        candidates.append(os.path.expanduser("~/Library/Application Support/uv/python"))
    for root in candidates:
        if not root:
            continue
        for bin_directory in _walk_named_directories(root, "bin", BIN_SCAN_MAX_DEPTH, warnings, "uv-managed"):
            _register_python_directory(bin_directory, "uv-managed", registry, warnings)


def _discover_homebrew_interpreters(registry: InterpreterRegistry, warnings: WarningLog) -> None:
    if platform.system() != "Darwin":
        return
    for prefix in ("/opt/homebrew", "/usr/local"):
        try:
            cellar = Path(prefix) / "Cellar"
            if not cellar.is_dir():
                continue
            _register_python_directory(os.path.join(prefix, "bin"), "homebrew", registry, warnings)
            _register_python_directory(os.path.join(prefix, "opt", "python", "bin"), "homebrew", registry, warnings)
            for formula in cellar.iterdir():
                if not formula.name.startswith("python"):
                    continue
                for version in formula.iterdir():
                    _register_python_directory(str(version / "bin"), "homebrew", registry, warnings)
        except (OSError, PermissionError) as error:
            warnings.add("homebrew", f"could not inspect Cellar: {error}", os.path.join(prefix, "Cellar"))


def _conda_python_path(environment_path: str, warnings: WarningLog, source: str) -> str | None:
    bin_directory = os.path.join(environment_path, "bin")
    for preferred in ("python", "python3"):
        candidate = os.path.join(bin_directory, preferred)
        if _is_executable_file(candidate):
            return _absolute(candidate)
    candidates = _python_executables_in(bin_directory, warnings, source) if os.path.isdir(bin_directory) else []
    return candidates[0] if candidates else None


def _discover_conda_environment_paths(warnings: WarningLog) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(path: str, source: str) -> None:
        canonical = _canonical(path)
        if canonical not in seen and os.path.isdir(canonical):
            seen.add(canonical)
            found.append((canonical, source))

    conda = shutil.which("conda")
    if conda:
        output = _run([conda, "env", "list", "--json"], source="conda-env-list", warnings=warnings, path=conda)
        if output is not None:
            try:
                environments = json.loads(output).get("envs", [])
                if not isinstance(environments, list):
                    raise ValueError("envs was not a list")
                for environment in environments:
                    if isinstance(environment, str):
                        add(environment, "conda-env-list")
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                warnings.add("conda-env-list", f"could not parse conda JSON: {error}", conda)

    roots = [
        os.environ.get("CONDA_PREFIX", ""),
        os.path.expanduser("~/miniconda3"),
        os.path.expanduser("~/anaconda3"),
        os.path.expanduser("~/miniforge3"),
        os.path.expanduser("~/mambaforge"),
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        add(root, "conda-directory")
        environments_directory = os.path.join(root, "envs")
        try:
            for entry in os.scandir(environments_directory):
                if entry.is_dir(follow_symlinks=True):
                    add(entry.path, "conda-directory")
        except FileNotFoundError:
            pass
        except OSError as error:
            warnings.add("conda-directory", f"could not inspect envs directory: {error}", environments_directory)
    return found


def _parse_pyvenv_config(config_path: str, warnings: WarningLog) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(config_path, encoding="utf-8", errors="replace") as config_file:
            for line in config_file:
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip().lower()] = value.strip().strip('"')
    except OSError as error:
        warnings.add("pyvenv-config", f"could not read configuration: {error}", config_path)
    return values


def _base_interpreter_from_config(values: dict[str, str], warnings: WarningLog, config_path: str) -> tuple[str | None, bool]:
    """Return the actual base executable when config gives enough information."""

    for key in ("base-executable", "executable"):
        configured = values.get(key)
        if configured:
            candidate = _absolute(configured)
            if _is_executable_file(candidate):
                return candidate, False

    home = values.get("home")
    if not home:
        warnings.add("pyvenv-config", "pyvenv.cfg has no home or executable setting", config_path)
        return None, True
    home = _absolute(home)
    if _is_executable_file(home):
        return home, False
    if os.path.isdir(home):
        for preferred in ("python", "python3"):
            candidate = os.path.join(home, preferred)
            if _is_executable_file(candidate):
                return candidate, False
        candidates = _python_executables_in(home, warnings, "pyvenv-config")
        if candidates:
            return candidates[0], False
    warnings.add("pyvenv-config", "base interpreter referenced by pyvenv.cfg was not found", home)
    return None, True


class EnvironmentRegistry:
    def __init__(self, warnings: WarningLog) -> None:
        self._warnings = warnings
        self._environments: dict[str, dict[str, Any]] = {}

    def add_venv(self, config_path: str, source: str) -> None:
        environment_path = _canonical(os.path.dirname(config_path))
        values = _parse_pyvenv_config(config_path, self._warnings)
        base_path, broken = _base_interpreter_from_config(values, self._warnings, config_path)
        interpreter_path = _conda_python_path(environment_path, self._warnings, source)
        kind = "uv-project" if os.path.exists(os.path.join(environment_path, os.pardir, "uv.lock")) else "venv"
        self._merge(
            environment_path,
            {
                "id": _identifier("environment", environment_path),
                "type": "environment",
                "kind": kind,
                "path": environment_path,
                "interpreter_path": interpreter_path,
                "base_interpreter_path": _canonical(base_path) if base_path else None,
                "base_interpreter_id": None,
                "base_link_broken": broken,
                "config_path": _canonical(config_path),
                "discovered_by": [{"source": source}],
            },
        )

    def add_conda(self, environment_path: str, source: str) -> None:
        canonical = _canonical(environment_path)
        self._merge(
            canonical,
            {
                "id": _identifier("environment", canonical),
                "type": "environment",
                "kind": "conda",
                "path": canonical,
                "interpreter_path": _conda_python_path(canonical, self._warnings, source),
                "base_interpreter_path": None,
                "base_interpreter_id": None,
                "base_link_broken": None,
                "config_path": None,
                "discovered_by": [{"source": source}],
            },
        )

    def _merge(self, path: str, incoming: dict[str, Any]) -> None:
        current = self._environments.get(path)
        if current is None:
            self._environments[path] = incoming
            return
        for discovery in incoming["discovered_by"]:
            if discovery not in current["discovered_by"]:
                current["discovered_by"].append(discovery)
        if current.get("kind") == "venv" and incoming.get("kind") == "uv-project":
            current["kind"] = "uv-project"

    def add_interpreters(self, registry: InterpreterRegistry) -> None:
        for environment in self._environments.values():
            if environment["interpreter_path"]:
                registry.add(environment["interpreter_path"], f"environment-{environment['kind']}")
            if environment["base_interpreter_path"]:
                registry.add(environment["base_interpreter_path"], "pyvenv-config-base")

    def finalize(self, registry: InterpreterRegistry) -> list[dict[str, Any]]:
        nodes = []
        for environment in self._environments.values():
            environment["interpreter_id"] = registry.lookup(environment["interpreter_path"])
            environment["base_interpreter_id"] = registry.lookup(environment["base_interpreter_path"])
            nodes.append(environment)
        return sorted(nodes, key=lambda node: node["path"])


def _default_environment_roots(search_roots: Iterable[str], context_directory: str) -> list[tuple[str, str]]:
    roots = [
        (os.path.expanduser("~/.virtualenvs"), "common-venv-location"),
        (os.path.expanduser("~/.venvs"), "common-venv-location"),
        (os.path.expanduser("~/.envs"), "common-venv-location"),
        (os.path.expanduser("~/.local/share/virtualenvs"), "common-venv-location"),
        (context_directory, "working-directory"),
    ]
    roots.extend((root, "configured-search-root") for root in search_roots)
    deduplicated: list[tuple[str, str]] = []
    seen: set[str] = set()
    for root, source in roots:
        canonical = _canonical(root)
        if canonical not in seen:
            seen.add(canonical)
            deduplicated.append((canonical, source))
    return deduplicated


def _discover_venvs(
    environments: EnvironmentRegistry,
    search_roots: Iterable[str],
    context_directory: str,
    warnings: WarningLog,
) -> None:
    for root, source in _default_environment_roots(search_roots, context_directory):
        if not os.path.isdir(root):
            continue
        try:
            for current, directories, files in os.walk(root, topdown=True, followlinks=False):
                relative = os.path.relpath(current, root)
                depth = 0 if relative == "." else relative.count(os.sep) + 1
                directories[:] = [
                    directory
                    for directory in directories
                    if directory not in SKIPPED_SCAN_DIRECTORIES and depth < ENV_SCAN_MAX_DEPTH
                ]
                if "pyvenv.cfg" in files:
                    environments.add_venv(os.path.join(current, "pyvenv.cfg"), source)
                    directories[:] = []
        except OSError as error:
            warnings.add(source, f"could not walk for pyvenv.cfg: {error}", root)


def _find_pyenv_local(directory: str, warnings: WarningLog) -> dict[str, Any] | None:
    current = _canonical(directory)
    while True:
        config_path = os.path.join(current, ".python-version")
        try:
            if os.path.isfile(config_path):
                with open(config_path, encoding="utf-8", errors="replace") as version_file:
                    versions = [
                        token
                        for line in version_file
                        for token in line.split()
                        if not token.startswith("#")
                    ]
                return {"path": config_path, "versions": versions}
        except OSError as error:
            warnings.add("pyenv-local", f"could not read .python-version: {error}", config_path)
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _pyenv_version_executable(versions: list[str], command: str) -> str | None:
    root = os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv"))
    for version in versions:
        if version == "system":
            continue
        version_root = os.path.join(root, "versions", version, "bin")
        for candidate_name in (command, "python", "python3"):
            candidate = os.path.join(version_root, candidate_name)
            if _is_executable_file(candidate):
                return _absolute(candidate)
    return None


def _path_candidate(command: str) -> str | None:
    found = shutil.which(command, path=os.environ.get("PATH"))
    return _absolute(found) if found else None


def _is_pyenv_shim(path: str | None) -> bool:
    if not path:
        return False
    root = _canonical(os.environ.get("PYENV_ROOT", os.path.expanduser("~/.pyenv")))
    return _canonical(path).startswith(os.path.join(root, "shims") + os.sep)


def _is_under(path: str | None, directory: str | None) -> bool:
    if not path or not directory:
        return False
    try:
        return os.path.commonpath([_canonical(path), _canonical(directory)]) == _canonical(directory)
    except ValueError:
        return False


def _resolve_context(directory: str, warnings: WarningLog) -> dict[str, Any]:
    context_directory = _canonical(directory)
    if not os.path.isdir(context_directory):
        warnings.add("context-resolution", "directory does not exist or is not a directory", context_directory)

    virtual_environment = os.environ.get("VIRTUAL_ENV")
    if virtual_environment:
        virtual_environment = _canonical(virtual_environment)
    pyenv_local = _find_pyenv_local(context_directory, warnings) if os.path.isdir(context_directory) else None
    commands: list[dict[str, Any]] = []
    for command in ("python", "python3"):
        path_candidate = _path_candidate(command)
        venv_candidate = None
        if virtual_environment:
            possible = os.path.join(virtual_environment, "bin", command)
            if _is_executable_file(possible):
                venv_candidate = _absolute(possible)
        pyenv_candidate = _pyenv_version_executable(pyenv_local["versions"], command) if pyenv_local else None

        resolved_path = path_candidate
        via = "PATH" if path_candidate else None
        # The shell only applies these overlays when its PATH has their entry.  Report
        # their candidates regardless, but do not claim they win when they do not.
        if venv_candidate and _is_under(path_candidate, virtual_environment):
            resolved_path, via = venv_candidate, "active-virtual-env"
        elif pyenv_candidate and _is_pyenv_shim(path_candidate):
            resolved_path, via = pyenv_candidate, "pyenv-local"
        commands.append(
            {
                "command": command,
                "resolved_path": _canonical(resolved_path) if resolved_path else None,
                "via": via,
                "path_candidate": _canonical(path_candidate) if path_candidate else None,
                "active_virtual_env_candidate": _canonical(venv_candidate) if venv_candidate else None,
                "pyenv_local_candidate": _canonical(pyenv_candidate) if pyenv_candidate else None,
            }
        )
    return {
        "directory": context_directory,
        "active_virtual_env": virtual_environment,
        "pyenv_local": pyenv_local,
        "commands": commands,
    }


def _package_record(
    context_id: str, python_path: str | None, warnings: WarningLog
) -> dict[str, Any]:
    if not python_path or not _is_executable_file(python_path):
        return {
            "context_id": context_id,
            "python_path": python_path,
            "status": "unavailable",
            "packages": [],
            "query_evidence": {
                "available": False,
                "python_path": python_path,
                "command": None,
                "raw_stdout": None,
                "reason": "interpreter is unavailable or not executable",
            },
        }
    query_python = _absolute(python_path)
    command = [query_python, "-I", "-c", PACKAGES_SCRIPT]
    output = _run(
        command,
        source="package-query",
        warnings=warnings,
        path=query_python,
        timeout=30,
    )
    if output is None:
        # Python 2 and early Python 3 do not understand -I.  This still never
        # imports a discovered package into Sphere itself; it merely gives the
        # target interpreter one sanitized fallback attempt.
        command = [query_python, "-c", PACKAGES_SCRIPT]
        output = _run(
            command,
            source="package-query-fallback",
            warnings=warnings,
            path=query_python,
            timeout=30,
        )
    if output is None:
        return {
            "context_id": context_id,
            "python_path": query_python,
            "status": "error",
            "packages": [],
            "query_evidence": {
                "available": False,
                "python_path": query_python,
                "command": command,
                "raw_stdout": None,
                "reason": "package query failed",
            },
        }
    try:
        packages = json.loads(output)
        if not isinstance(packages, list):
            raise ValueError("package result was not a list")
        return {
            "context_id": context_id,
            "python_path": query_python,
            "status": "ok",
            "packages": packages,
            "query_evidence": {
                "available": True,
                "python_path": query_python,
                "command": command,
                "raw_stdout": output,
                "reason": None,
            },
        }
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        reason = f"interpreter returned invalid package JSON: {error}"
        warnings.add("package-query", reason, query_python)
        return {
            "context_id": context_id,
            "python_path": query_python,
            "status": "error",
            "packages": [],
            "query_evidence": {
                "available": False,
                "python_path": query_python,
                "command": command,
                "raw_stdout": output,
                "reason": reason,
            },
        }


def _repository_node(directory: str, warnings: WarningLog) -> tuple[dict[str, Any], list[Any]]:
    path = _canonical(directory)
    requirements = parse_repository_requirements(path, warning_reporter=warnings.add)
    return (
        {
            "id": _identifier("repository", path),
            "type": "repository",
            "path": path,
            "requirements": [requirement.as_json() for requirement in requirements],
        },
        requirements,
    )


def scan_topology(
    *,
    directory: str | None = None,
    search_roots: Iterable[str] = (),
    include_packages: bool = True,
) -> dict[str, Any]:
    """Inspect Python installations without importing any discovered package locally."""

    warnings = WarningLog()
    requested_directory = directory or os.getcwd()
    context_directory = _canonical(requested_directory)
    registry = InterpreterRegistry(warnings)
    environments = EnvironmentRegistry(warnings)

    if platform.system() not in {"Darwin", "Linux"}:
        warnings.add("platform", f"{platform.system()} is not a supported platform")
    else:
        # Each source owns its own errors; a damaged manager installation cannot stop the
        # remaining sources from contributing to the graph.
        _discover_path_interpreters(registry, warnings)
        _discover_system_interpreters(registry, warnings)
        _discover_pyenv_interpreters(registry, warnings)
        _discover_uv_interpreters(registry, warnings)
        _discover_homebrew_interpreters(registry, warnings)
        for conda_path, source in _discover_conda_environment_paths(warnings):
            environments.add_conda(conda_path, source)
        _discover_venvs(environments, search_roots, context_directory, warnings)
        environments.add_interpreters(registry)

    context_resolution = _resolve_context(context_directory, warnings)
    for command in context_resolution["commands"]:
        if command["resolved_path"]:
            registry.add(command["resolved_path"], "context-resolution")

    registry.probe()
    interpreter_nodes = registry.nodes()
    environment_nodes = environments.finalize(registry)
    context_id = _identifier("context", context_directory)
    context_node = {"id": context_id, "type": "directory-context", "path": context_directory}
    repository_node, repository_requirements = _repository_node(context_directory, warnings)

    edges: list[dict[str, Any]] = []
    for environment in environment_nodes:
        if environment["base_interpreter_id"]:
            edges.append({"type": "based-on", "from": environment["id"], "to": environment["base_interpreter_id"]})
    for command in context_resolution["commands"]:
        interpreter_id = registry.lookup(command["resolved_path"])
        if interpreter_id:
            edges.append({"type": "resolves-to", "from": context_id, "to": interpreter_id, "command": command["command"]})

    package_records: list[dict[str, Any]] = []
    if include_packages:
        for interpreter in interpreter_nodes:
            package_records.append(_package_record(interpreter["id"], interpreter["path"], warnings))
        for environment in environment_nodes:
            package_records.append(_package_record(environment["id"], environment["interpreter_path"], warnings))
        for package_record in (package_records if repository_requirements else ()):
            diff = diff_requirements(repository_requirements, package_record)
            edges.append(
                {
                    "type": "requires",
                    "from": repository_node["id"],
                    "to": package_record["context_id"],
                    "verdict": aggregate_verdict(diff["requirements"]),
                    "package_query_status": diff["package_query_status"],
                    "evidence": diff["query_evidence"],
                    "diff": diff["requirements"],
                }
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "nodes": {
            "interpreters": interpreter_nodes,
            "environments": environment_nodes,
            "repositories": [repository_node],
            "contexts": [context_node],
        },
        "edges": edges,
        "packages": package_records,
        "context_resolution": context_resolution,
        "warnings": warnings.items,
    }


def _parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit a JSON graph of Python interpreters, environments, and installed packages."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help="directory whose python/python3 resolution should be reported (defaults to the current directory)",
    )
    parser.add_argument(
        "--search-root",
        action="append",
        default=[],
        metavar="PATH",
        help="recursively scan PATH for pyvenv.cfg (may be supplied more than once)",
    )
    parser.add_argument(
        "--no-packages",
        action="store_true",
        help="skip per-interpreter package subprocesses for a faster topology-only scan",
    )
    parser.add_argument("--indent", type=int, default=None, help="pretty-print JSON using this many spaces")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    topology = scan_topology(
        directory=arguments.directory,
        search_roots=arguments.search_root,
        include_packages=not arguments.no_packages,
    )
    json.dump(topology, sys.stdout, indent=arguments.indent, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
