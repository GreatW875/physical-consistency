"""Assemble a standalone Unity project while preserving asset GUIDs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Mapping, Optional, Set, Tuple


GUID_LINE = re.compile(r"^guid:\s*([0-9a-fA-F]{32})\s*$", re.MULTILINE)
GUID_REFERENCE = re.compile(r"\bguid:\s*([0-9a-fA-F]{32})\b")
BUILTIN_GUIDS = {
    "0000000000000000e000000000000000",
    "0000000000000000f000000000000000",
}
SKIPPED_PARTS = {
    ".claude",
    ".git",
    ".vscode",
    "Library",
    "Logs",
    "Temp",
    "UserSettings",
    "__pycache__",
    "logs",
}
MANUAL_DIRECTORIES = {"G1OP", "OBJ", "OBJ-Jiao", "physical_consistency"}
MANUAL_SUFFIXES = {".cs", ".py", ".rendertexture", ".unity"}
PROJECT_GUID_OVERRIDES = {
    "Assets/Manipulation/CollisionReporter.cs": "00e170af096eea529a0b5fc8296b835b",
    "Assets/Manipulation/NavMeshPathPlanner.cs": "f803e1799808db5d198a1c9a364833ae",
    "Assets/Manipulation/pyreceiver.cs": "b399db405a9fb71c8b2e4b8390d79593",
    "Assets/onnx/g1op.onnx": "1733ed497468052e58a0eb0843c35d9c",
}


@dataclass(frozen=True)
class AssemblyReport:
    copied_assets: Tuple[str, ...]
    unresolved_guids: Tuple[str, ...]


def _asset_from_meta(meta: Path) -> Path:
    return Path(str(meta)[:-5])


def _is_skipped(relative: Path) -> bool:
    return any(part in SKIPPED_PARTS for part in relative.parts) or relative.name == "robot_view.jpg"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_guid_index(source_root: Path) -> Dict[str, Path]:
    """Map GUIDs declared by Assets/**/*.meta to their source assets."""
    source_root = source_root.resolve()
    assets_root = source_root / "Assets"
    index: Dict[str, Path] = {}
    for meta in sorted(assets_root.rglob("*.meta")):
        relative = meta.relative_to(source_root)
        if _is_skipped(relative):
            continue
        try:
            match = GUID_LINE.search(meta.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if not match:
            continue
        guid = match.group(1).lower()
        asset = _asset_from_meta(meta)
        previous = index.get(guid)
        if previous is not None and previous != asset:
            same_file = previous.is_file() and asset.is_file() and _sha256(previous) == _sha256(asset)
            if not same_file:
                raise ValueError(f"duplicate Unity GUID {guid}: {previous} and {asset}")
            continue
        index[guid] = asset
    return index


def external_package_guids(source_root: Path, guids: Set[str]) -> Set[str]:
    """Return unresolved GUIDs supplied by local or cached Unity packages."""
    source_root = source_root.resolve()
    roots = [
        source_root / "Packages",
        source_root / "LocalPackages",
        source_root / "Library" / "PackageCache",
    ]
    roots.extend(
        path
        for path in source_root.iterdir()
        if path.is_dir() and (path / "package.json").is_file()
    )
    found: Set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for meta in root.rglob("*.meta"):
            try:
                match = GUID_LINE.search(meta.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            if match and match.group(1).lower() in guids:
                found.add(match.group(1).lower())
    return found


def external_package_guid_sources(source_root: Path, guids: Set[str]) -> Dict[str, str]:
    """Map package-owned GUIDs to the package names that declare them."""
    source_root = source_root.resolve()
    roots = [
        source_root / "Packages",
        source_root / "LocalPackages",
        source_root / "Library" / "PackageCache",
    ]
    roots.extend(
        path
        for path in source_root.iterdir()
        if path.is_dir() and (path / "package.json").is_file()
    )
    sources: Dict[str, str] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for meta in root.rglob("*.meta"):
            try:
                match = GUID_LINE.search(meta.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            if not match or match.group(1).lower() not in guids:
                continue
            package_dir = meta.parent
            while package_dir != source_root and not (package_dir / "package.json").is_file():
                package_dir = package_dir.parent
            package_json = package_dir / "package.json"
            if not package_json.is_file():
                continue
            try:
                package_name = json.loads(package_json.read_text(encoding="utf-8"))["name"]
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError):
                continue
            sources[match.group(1).lower()] = package_name
    return sources


def audit_assembled_project(target_root: Path, manifest_path: Path) -> List[str]:
    """Validate an assembled project against its recorded dependency manifest."""
    target_root = target_root.resolve()
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    issues: List[str] = []
    assets_root = target_root / "Assets"

    referenced: Set[str] = set()
    for path in assets_root.rglob("*"):
        if path.name.endswith(".meta"):
            continue
        paired_meta = Path(str(path) + ".meta")
        if not paired_meta.exists():
            issues.append(f"missing paired meta: {paired_meta.relative_to(target_root).as_posix()}")
        if path.is_file():
            referenced.update(referenced_guids(path))

    for relative in manifest.get("copied_assets", []):
        asset = target_root / relative
        if not asset.exists():
            issues.append(f"missing manifest asset: {relative}")
            continue
        paired_meta = Path(str(asset) + ".meta")
        if not paired_meta.exists():
            issues.append(f"missing manifest meta: {relative}.meta")

    guid_index = build_guid_index(target_root)
    actual_unresolved = {
        guid for guid in referenced
        if guid not in BUILTIN_GUIDS and guid not in guid_index
    }
    expected_external = set(manifest.get("external_package_guids", []))
    restored_sources = manifest.get("restored_package_guids", {})
    expected_missing = set(manifest.get("missing_source_guids", []))
    expected_unresolved = expected_external | expected_missing
    for guid in sorted(actual_unresolved - expected_unresolved):
        issues.append(f"new unresolved GUID: {guid}")
    for guid in sorted(expected_unresolved - actual_unresolved):
        issues.append(f"stale unresolved GUID in manifest: {guid}")

    present_package_guids = external_package_guids(target_root, expected_external)
    lock_path = target_root / "Packages" / "packages-lock.json"
    try:
        locked_packages = json.loads(lock_path.read_text(encoding="utf-8")).get(
            "dependencies", {}
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        locked_packages = {}
    recoverable_restored_guids: Set[str] = set()
    for guid, package_source in restored_sources.items():
        if guid not in expected_external:
            issues.append(f"restored package GUID not listed as external: {guid}")
            continue
        if not isinstance(package_source, dict):
            issues.append(f"restored package metadata is invalid for GUID {guid}")
            continue
        package_name = package_source.get("name")
        expected_version = package_source.get("version")
        package = locked_packages.get(package_name, {})
        if package.get("source") not in {"builtin", "registry"}:
            issues.append(f"restored package not pinned for GUID {guid}: {package_name}")
            continue
        if package.get("version") != expected_version:
            issues.append(
                f"restored package version mismatch for GUID {guid}: "
                f"expected {expected_version}, found {package.get('version')}"
            )
            continue
        recoverable_restored_guids.add(guid)
    for guid in sorted(expected_external - present_package_guids - recoverable_restored_guids):
        issues.append(f"external package GUID is not recoverable: {guid}")

    for relative, expected_guid in manifest.get("guid_overrides", {}).items():
        meta = Path(str(target_root / relative) + ".meta")
        if not meta.is_file():
            issues.append(f"missing GUID override meta: {relative}.meta")
            continue
        match = GUID_LINE.search(meta.read_text(encoding="utf-8"))
        if not match or match.group(1).lower() != expected_guid.lower():
            issues.append(f"GUID override mismatch: {relative}")

    return sorted(set(issues))


def referenced_guids(path: Path) -> Set[str]:
    """Extract serialized Unity GUID references; binary files are ignored safely."""
    paths = [path]
    paired_meta = Path(str(path) + ".meta")
    if path.suffix != ".meta" and paired_meta.is_file():
        paths.append(paired_meta)
    result: Set[str] = set()
    for candidate in paths:
        if not candidate.is_file():
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        result.update(match.lower() for match in GUID_REFERENCE.findall(content))
    return result


def _manual_entries(source_root: Path, scene_path: Path) -> Iterable[Path]:
    yield scene_path
    manipulation = source_root / "Assets" / "Manipulation"
    if not manipulation.is_dir():
        return
    for path in manipulation.rglob("*"):
        if not path.is_file() or path.suffix == ".meta":
            continue
        relative = path.relative_to(source_root)
        if _is_skipped(relative):
            continue
        under_manual_directory = any(part in MANUAL_DIRECTORIES for part in relative.parts)
        named_navmesh = "navmesh" in path.name.lower()
        if under_manual_directory or named_navmesh or path.suffix.lower() in MANUAL_SUFFIXES:
            yield path


def _copy_ancestor_meta(source_root: Path, target_root: Path, asset: Path) -> None:
    assets_root = source_root / "Assets"
    parent = asset.parent
    while parent != source_root and _is_relative_to(parent, assets_root):
        meta = Path(str(parent) + ".meta")
        if meta.is_file():
            destination = target_root / meta.relative_to(source_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(meta, destination)
        if parent == assets_root:
            break
        parent = parent.parent


def _copy_asset(
    source_root: Path,
    target_root: Path,
    asset: Path,
    guid_override: Optional[str] = None,
) -> None:
    relative = asset.relative_to(source_root)
    destination = target_root / relative
    if asset.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, destination)
    meta = Path(str(asset) + ".meta")
    if meta.is_file():
        meta_destination = target_root / meta.relative_to(source_root)
        meta_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(meta, meta_destination)
        if guid_override is not None:
            content = meta_destination.read_text(encoding="utf-8")
            updated, replacements = re.subn(
                r"^guid:\s*.*$", f"guid: {guid_override}", content, count=1, flags=re.MULTILINE
            )
            if replacements != 1:
                raise ValueError(f"cannot override GUID in {meta.relative_to(source_root)}")
            meta_destination.write_text(updated, encoding="utf-8")
    _copy_ancestor_meta(source_root, target_root, asset)


def assemble(
    source_root: Path,
    target_root: Path,
    scene: str,
    guid_overrides: Optional[Mapping[str, str]] = None,
) -> AssemblyReport:
    """Breadth-first copy a scene, dynamic entries, dependencies and paired metadata."""
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    scene_path = (source_root / scene).resolve()
    if not scene_path.is_file() or not _is_relative_to(scene_path, source_root / "Assets"):
        raise FileNotFoundError(f"Unity scene not found under Assets: {scene}")

    overrides = dict(guid_overrides or {})
    guid_index = build_guid_index(source_root)
    queue: Deque[Path] = deque(_manual_entries(source_root, scene_path))
    for relative, guid in sorted(overrides.items()):
        override_asset = source_root / relative
        if not override_asset.is_file():
            raise FileNotFoundError(f"GUID override asset not found: {relative}")
        existing = guid_index.get(guid)
        if existing is not None and existing != override_asset:
            raise ValueError(f"GUID override conflicts with {existing}: {guid}")
        guid_index[guid] = override_asset
        queue.append(override_asset)
    queued: Set[Path] = set(queue)
    copied: Set[str] = set()
    unresolved: Set[str] = set()

    while queue:
        asset = queue.popleft()
        relative = asset.relative_to(source_root)
        if _is_skipped(relative):
            continue
        relative_text = relative.as_posix()
        _copy_asset(source_root, target_root, asset, overrides.get(relative_text))
        copied.add(relative_text)
        for guid in referenced_guids(asset):
            if guid in BUILTIN_GUIDS:
                continue
            dependency = guid_index.get(guid)
            if dependency is None:
                unresolved.add(guid)
            elif dependency not in queued:
                queued.add(dependency)
                queue.append(dependency)

    return AssemblyReport(tuple(sorted(copied)), tuple(sorted(unresolved)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--scene")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()

    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = args.target / manifest_path
    if args.audit_only:
        issues = audit_assembled_project(args.target, manifest_path)
        if issues:
            for issue in issues:
                print(issue)
            return 1
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        known_gaps = len(manifest.get("missing_source_guids", []))
        print(f"Unity assembly audit passed; recorded source gaps: {known_gaps}")
        return 0

    if args.source is None or not args.scene:
        parser.error("--source and --scene are required unless --audit-only is used")
    report = assemble(args.source, args.target, args.scene, PROJECT_GUID_OVERRIDES)
    unresolved = set(report.unresolved_guids)
    package_guids = external_package_guids(args.source, unresolved)
    package_sources = external_package_guid_sources(args.source, package_guids)
    locked_packages = json.loads(
        (args.source / "Packages" / "packages-lock.json").read_text(encoding="utf-8")
    ).get("dependencies", {})
    manifest = {
        "scene": args.scene,
        **asdict(report),
        "external_package_guids": sorted(package_guids),
        "restored_package_guids": {
            guid: {
                "name": package_name,
                "version": locked_packages[package_name]["version"],
            }
            for guid, package_name in sorted(package_sources.items())
            if package_name in locked_packages
            and locked_packages[package_name].get("source") in {"builtin", "registry"}
        },
        "missing_source_guids": sorted(unresolved - package_guids),
        "guid_overrides": dict(sorted(PROJECT_GUID_OVERRIDES.items())),
        "unity_asset_database_verification": "pending_compatible_editor",
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Copied {len(report.copied_assets)} assets; "
        f"package GUIDs: {len(package_guids)}; "
        f"missing source GUIDs: {len(unresolved - package_guids)}"
    )
    return 1 if unresolved - package_guids else 0


if __name__ == "__main__":
    raise SystemExit(main())
