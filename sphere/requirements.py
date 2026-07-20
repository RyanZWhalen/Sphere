"""Parse repository dependency declarations and compare them with package inventories."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


WarningReporter = Callable[[str, object, str | None], None]
_INLINE_COMMENT = re.compile(r"\s+#")


@dataclass(frozen=True)
class DeclaredRequirement:
    """A PEP 508 requirement together with the repository declaration that supplied it."""

    requirement: Requirement
    source: str
    raw: str

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.requirement.name,
            "requirement": str(self.requirement),
            "specifier": str(self.requirement.specifier),
            "extras": sorted(self.requirement.extras),
            "marker": str(self.requirement.marker) if self.requirement.marker else None,
            "url": self.requirement.url,
            "source": self.source,
            "raw": self.raw,
        }


def _canonical(path: str | os.PathLike[str]) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _warning(
    reporter: WarningReporter | None,
    source: str,
    message: object,
    path: str | None = None,
) -> None:
    if reporter is not None:
        reporter(source, message, path)


def _parse_requirement(
    raw: str,
    source: str,
    path: str,
    reporter: WarningReporter | None,
) -> DeclaredRequirement | None:
    try:
        return DeclaredRequirement(requirement=Requirement(raw), source=source, raw=raw)
    except InvalidRequirement as error:
        _warning(reporter, source, f"could not parse requirement {raw!r}: {error}", path)
        return None


def _parse_requirements_file(path: str, reporter: WarningReporter | None) -> list[DeclaredRequirement]:
    parsed: list[DeclaredRequirement] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as requirements_file:
            lines = list(requirements_file)
    except OSError as error:
        _warning(reporter, "requirements.txt", f"could not read dependency file: {error}", path)
        return parsed

    for number, line in enumerate(lines, start=1):
        raw = _INLINE_COMMENT.split(line, maxsplit=1)[0].strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith(("-r", "--requirement", "-c", "--constraint", "-e", "--editable")):
            _warning(
                reporter,
                "requirements.txt",
                f"unsupported requirements directive on line {number}: {raw}",
                path,
            )
            continue
        if raw.startswith("-"):
            # Index and resolver options do not declare a distribution.
            continue
        requirement = _parse_requirement(raw, "requirements.txt", path, reporter)
        if requirement:
            parsed.append(requirement)
    return parsed


def _poetry_caret_specifier(version: str) -> str:
    lower = Version(version[1:])
    release = list(lower.release)
    if not release:
        raise InvalidVersion(version)
    upper_index = next((index for index, part in enumerate(release) if part != 0), len(release) - 1)
    upper = release[: upper_index + 1]
    upper[-1] += 1
    return f">={lower},<{'.'.join(str(part) for part in upper)}"


def _poetry_tilde_specifier(version: str) -> str:
    lower = Version(version[1:])
    release = list(lower.release)
    if not release:
        raise InvalidVersion(version)
    # Poetry's ~1.2 means <1.3, while ~1.2.3 means <1.3.0.
    upper_index = len(release) - 1 if len(release) <= 2 else len(release) - 2
    upper = release[: upper_index + 1]
    upper[-1] += 1
    return f">={lower},<{'.'.join(str(part) for part in upper)}"


def _poetry_specifier(version: str) -> str:
    """Translate the common Poetry constraint forms to PEP 440 specifiers."""

    version = version.strip()
    if not version or version == "*":
        return ""
    if version.startswith("^"):
        return _poetry_caret_specifier(version)
    if version.startswith("~") and not version.startswith("~="):
        return _poetry_tilde_specifier(version)
    if version.endswith(".*") and not version.startswith(("==", "!=", ">", "<", "~", "=")):
        return f"=={version}"
    if version.startswith(("==", "!=", ">=", "<=", ">", "<", "~=", "===", "=")):
        return version
    # Poetry permits a bare version in this table; encode it as an exact PEP 440 pin.
    return f"=={version}"


def _poetry_requirement_text(name: str, declaration: str | Mapping[str, Any]) -> str | None:
    if canonicalize_name(name) == "python":
        return None

    extras: list[str] = []
    markers: str | None = None
    url: str | None = None
    version: str = "*"
    if isinstance(declaration, str):
        version = declaration
    else:
        value = declaration.get("version", "*")
        version = value if isinstance(value, str) else "*"
        raw_extras = declaration.get("extras", [])
        extras = [extra for extra in raw_extras if isinstance(extra, str)] if isinstance(raw_extras, list) else []
        marker_value = declaration.get("markers")
        markers = marker_value if isinstance(marker_value, str) else None
        url_value = declaration.get("url")
        url = url_value if isinstance(url_value, str) else None

    normalized_name = f"{name}[{','.join(extras)}]" if extras else name
    text = f"{normalized_name} @ {url}" if url else f"{normalized_name}{_poetry_specifier(version)}"
    return f"{text}; {markers}" if markers else text


def _parse_poetry_dependencies(
    dependencies: Mapping[str, Any],
    path: str,
    reporter: WarningReporter | None,
) -> list[DeclaredRequirement]:
    parsed: list[DeclaredRequirement] = []
    for name, declaration in dependencies.items():
        if not isinstance(name, str) or not isinstance(declaration, (str, Mapping)):
            _warning(reporter, "pyproject.toml:tool.poetry.dependencies", f"unsupported dependency declaration: {name!r}", path)
            continue
        try:
            text = _poetry_requirement_text(name, declaration)
        except InvalidVersion as error:
            _warning(
                reporter,
                "pyproject.toml:tool.poetry.dependencies",
                f"could not translate Poetry version for {name!r}: {error}",
                path,
            )
            continue
        if text is None:
            continue
        requirement = _parse_requirement(text, "pyproject.toml:tool.poetry.dependencies", path, reporter)
        if requirement:
            parsed.append(requirement)
    return parsed


def _parse_pyproject(path: str, reporter: WarningReporter | None) -> list[DeclaredRequirement]:
    try:
        with open(path, "rb") as pyproject_file:
            document = tomllib.load(pyproject_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        _warning(reporter, "pyproject.toml", f"could not parse project metadata: {error}", path)
        return []

    parsed: list[DeclaredRequirement] = []
    project = document.get("project")
    if isinstance(project, Mapping):
        dependencies = project.get("dependencies", [])
        if not isinstance(dependencies, list):
            _warning(reporter, "pyproject.toml:project.dependencies", "dependencies is not a list", path)
        else:
            for declaration in dependencies:
                if not isinstance(declaration, str):
                    _warning(
                        reporter,
                        "pyproject.toml:project.dependencies",
                        f"dependency is not a string: {declaration!r}",
                        path,
                    )
                    continue
                requirement = _parse_requirement(declaration, "pyproject.toml:project.dependencies", path, reporter)
                if requirement:
                    parsed.append(requirement)

    tool = document.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, Mapping) else None
    dependencies = poetry.get("dependencies") if isinstance(poetry, Mapping) else None
    if dependencies is not None:
        if not isinstance(dependencies, Mapping):
            _warning(reporter, "pyproject.toml:tool.poetry.dependencies", "dependencies is not a table", path)
        else:
            parsed.extend(_parse_poetry_dependencies(dependencies, path, reporter))
    return parsed


def parse_repository_requirements(
    repository: str | os.PathLike[str],
    *,
    warning_reporter: WarningReporter | None = None,
) -> list[DeclaredRequirement]:
    """Read requirements.txt and pyproject.toml from one repository directory.

    Bad files and unsupported declarations are reported independently through
    ``warning_reporter``; one malformed source never prevents parsing the other.
    """

    root = _canonical(repository)
    if not os.path.isdir(root):
        _warning(warning_reporter, "repository-requirements", "repository is not a directory", root)
        return []

    parsed: list[DeclaredRequirement] = []
    requirements_path = os.path.join(root, "requirements.txt")
    if os.path.isfile(requirements_path):
        parsed.extend(_parse_requirements_file(requirements_path, warning_reporter))
    pyproject_path = os.path.join(root, "pyproject.toml")
    if os.path.isfile(pyproject_path):
        parsed.extend(_parse_pyproject(pyproject_path, warning_reporter))
    return parsed


def diff_requirements(
    requirements: Iterable[DeclaredRequirement],
    package_record: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify each declared requirement against one introspection package record."""

    installed: dict[str, dict[str, str]] = {}
    packages = package_record.get("packages", [])
    if isinstance(packages, list):
        for package in packages:
            if not isinstance(package, Mapping):
                continue
            name, version = package.get("name"), package.get("version")
            if isinstance(name, str) and isinstance(version, str):
                installed.setdefault(canonicalize_name(name), {"name": name, "version": version})

    results: list[dict[str, Any]] = []
    for declared in requirements:
        requirement = declared.requirement
        reported_distribution = installed.get(canonicalize_name(requirement.name))
        installed_version = reported_distribution["version"] if reported_distribution else None
        result = declared.as_json()
        result["installed_version"] = installed_version
        result["evidence"] = {
            "reported_distribution": dict(reported_distribution) if reported_distribution else None,
        }
        if installed_version is None:
            result["status"] = "missing"
        else:
            try:
                result["status"] = (
                    "satisfied" if requirement.specifier.contains(Version(installed_version), prereleases=True) else "version-mismatch"
                )
            except InvalidVersion:
                result["status"] = "version-mismatch"
        results.append(result)
    return {
        "context_id": package_record.get("context_id"),
        "python_path": package_record.get("python_path"),
        "package_query_status": package_record.get("status"),
        "query_evidence": package_record.get("query_evidence"),
        "requirements": results,
    }


def aggregate_verdict(requirement_results: Iterable[Mapping[str, Any]]) -> str:
    """Return one color-friendly verdict for a repository-to-runtime edge."""

    statuses = {result.get("status") for result in requirement_results}
    if not statuses or statuses == {"satisfied"}:
        return "satisfied"
    if "missing" in statuses:
        return "missing"
    return "version-mismatch"
