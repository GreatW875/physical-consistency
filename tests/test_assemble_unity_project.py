import json
import tempfile
import unittest
from pathlib import Path

from tools.assemble_unity_project import (
    assemble,
    audit_assembled_project,
    build_guid_index,
    external_package_guids,
)


BUILTIN_GUID = "0000000000000000f000000000000000"


def _write_asset(root: Path, relative: str, content: bytes, guid: str) -> None:
    asset = root / relative
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(content)
    Path(str(asset) + ".meta").write_text(
        f"fileFormatVersion: 2\nguid: {guid}\n", encoding="utf-8"
    )


def _make_unity_fixture(root: Path) -> Path:
    source = root / "source"
    _write_asset(
        source,
        "Assets/Manipulation/G1OP.unity",
        (
            "--- !u!1 &1\n"
            "Prefab: {fileID: 100100000, guid: 11111111111111111111111111111111, type: 3}\n"
            f"Builtin: {{fileID: 0, guid: {BUILTIN_GUID}, type: 0}}\n"
        ).encode(),
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    _write_asset(
        source,
        "Assets/Environment/Room.prefab",
        b"Material: {fileID: 2100000, guid: 22222222222222222222222222222222, type: 2}\n",
        "11111111111111111111111111111111",
    )
    _write_asset(
        source,
        "Assets/Materials/Room.mat",
        b"Texture: {fileID: 2800000, guid: 33333333333333333333333333333333, type: 3}\n",
        "22222222222222222222222222222222",
    )
    _write_asset(
        source,
        "Assets/Textures/Room.png",
        b"not-a-real-png",
        "33333333333333333333333333333333",
    )
    for index, relative in enumerate(
        ("Assets/Manipulation", "Assets/Environment", "Assets/Materials", "Assets/Textures"),
        start=4,
    ):
        Path(str(source / relative) + ".meta").write_text(
            f"fileFormatVersion: 2\nguid: {index:032x}\nfolderAsset: yes\n",
            encoding="utf-8",
        )
    return source


class AssembleUnityProjectTests(unittest.TestCase):
    def test_audit_rejects_restored_package_version_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_unity_fixture(root)
            external_guid = "ffffffffffffffffffffffffffffffff"
            scene = source / "Assets/Manipulation/G1OP.unity"
            scene.write_text(
                scene.read_text(encoding="utf-8")
                + f"Script: {{fileID: 11500000, guid: {external_guid}, type: 3}}\n",
                encoding="utf-8",
            )
            target = root / "target"
            report = assemble(source, target, "Assets/Manipulation/G1OP.unity")
            lock = target / "Packages/packages-lock.json"
            lock.parent.mkdir(parents=True)
            lock.write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "com.example.tool": {
                                "version": "2.0.0",
                                "source": "registry",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            manifest = target / "dependency-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "copied_assets": list(report.copied_assets),
                        "external_package_guids": [external_guid],
                        "restored_package_guids": {
                            external_guid: {
                                "name": "com.example.tool",
                                "version": "1.0.0",
                            }
                        },
                        "missing_source_guids": [],
                        "guid_overrides": {},
                    }
                ),
                encoding="utf-8",
            )

            issues = audit_assembled_project(target, manifest)

            self.assertTrue(any("version" in issue and external_guid in issue for issue in issues))

    def test_audit_rejects_external_guid_missing_from_local_packages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_unity_fixture(root)
            external_guid = "ffffffffffffffffffffffffffffffff"
            scene = source / "Assets/Manipulation/G1OP.unity"
            scene.write_text(
                scene.read_text(encoding="utf-8")
                + f"Script: {{fileID: 11500000, guid: {external_guid}, type: 3}}\n",
                encoding="utf-8",
            )
            target = root / "target"
            report = assemble(source, target, "Assets/Manipulation/G1OP.unity")
            package_script = target / "LocalPackages/com.example.tool/Runtime/Tool.cs"
            package_script.parent.mkdir(parents=True)
            package_script.write_text("public class Tool {}\n", encoding="utf-8")
            Path(str(package_script) + ".meta").write_text(
                f"fileFormatVersion: 2\nguid: {external_guid}\n", encoding="utf-8"
            )
            manifest = target / "dependency-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "copied_assets": list(report.copied_assets),
                        "external_package_guids": [external_guid],
                        "restored_package_guids": {},
                        "missing_source_guids": [],
                        "guid_overrides": {},
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(audit_assembled_project(target, manifest), [])
            Path(str(package_script) + ".meta").unlink()

            issues = audit_assembled_project(target, manifest)

            self.assertTrue(any(external_guid in issue for issue in issues))

    def test_audit_checks_dependencies_of_assets_outside_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_unity_fixture(root)
            target = root / "target"
            report = assemble(source, target, "Assets/Manipulation/G1OP.unity")
            unknown = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
            editor_asset = target / "Assets/Editor/DependencyExporter.cs"
            editor_asset.parent.mkdir(parents=True)
            editor_asset.write_text(
                f"Reference: {{fileID: 1, guid: {unknown}, type: 3}}\n",
                encoding="utf-8",
            )
            Path(str(editor_asset) + ".meta").write_text(
                "fileFormatVersion: 2\nguid: dddddddddddddddddddddddddddddddd\n",
                encoding="utf-8",
            )
            Path(str(editor_asset.parent) + ".meta").write_text(
                "fileFormatVersion: 2\nguid: cccccccccccccccccccccccccccccccc\nfolderAsset: yes\n",
                encoding="utf-8",
            )
            manifest = target / "dependency-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "copied_assets": list(report.copied_assets),
                        "external_package_guids": [],
                        "restored_package_guids": {},
                        "missing_source_guids": [],
                        "guid_overrides": {},
                    }
                ),
                encoding="utf-8",
            )

            issues = audit_assembled_project(target, manifest)

            self.assertTrue(any(unknown in issue for issue in issues))

    def test_audit_detects_missing_paired_meta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_unity_fixture(root)
            target = root / "target"
            report = assemble(source, target, "Assets/Manipulation/G1OP.unity")
            manifest = target / "dependency-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "scene": "Assets/Manipulation/G1OP.unity",
                        "copied_assets": list(report.copied_assets),
                        "external_package_guids": [],
                        "restored_package_guids": {},
                        "missing_source_guids": [],
                        "guid_overrides": {},
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(audit_assembled_project(target, manifest), [])
            (target / "Assets/Textures/Room.png.meta").unlink()

            issues = audit_assembled_project(target, manifest)

            self.assertTrue(any("Room.png.meta" in issue for issue in issues))

    def test_classifies_guid_from_package_cache_as_external(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            package_script = source / "Library/PackageCache/com.example.tool@1.0/Runtime/Tool.cs"
            package_script.parent.mkdir(parents=True)
            package_script.write_text("public class Tool {}\n", encoding="utf-8")
            package_guid = "ffffffffffffffffffffffffffffffff"
            Path(str(package_script) + ".meta").write_text(
                f"fileFormatVersion: 2\nguid: {package_guid}\n", encoding="utf-8"
            )

            classified = external_package_guids(source, {package_guid, BUILTIN_GUID})

            self.assertEqual(classified, {package_guid})

    def test_applies_explicit_guid_override_without_modifying_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            old_guid = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            current_guid = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            _write_asset(
                source,
                "Assets/Manipulation/G1OP.unity",
                f"Script: {{fileID: 11500000, guid: {old_guid}, type: 3}}\n".encode(),
                "cccccccccccccccccccccccccccccccc",
            )
            _write_asset(
                source,
                "Assets/Manipulation/Receiver.cs",
                b"public class Receiver {}\n",
                current_guid,
            )
            source_meta = source / "Assets/Manipulation/Receiver.cs.meta"
            original_meta = source_meta.read_text(encoding="utf-8")

            report = assemble(
                source,
                root / "target",
                "Assets/Manipulation/G1OP.unity",
                {"Assets/Manipulation/Receiver.cs": old_guid},
            )

            target_meta = root / "target/Assets/Manipulation/Receiver.cs.meta"
            self.assertEqual(report.unresolved_guids, ())
            self.assertIn(f"guid: {old_guid}", target_meta.read_text(encoding="utf-8"))
            self.assertEqual(source_meta.read_text(encoding="utf-8"), original_meta)

    def test_accepts_identical_duplicate_guid_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            duplicate_guid = "dddddddddddddddddddddddddddddddd"
            _write_asset(source, "Assets/A/model.onnx", b"same-model", duplicate_guid)
            _write_asset(source, "Assets/B/model.onnx", b"same-model", duplicate_guid)

            index = build_guid_index(source)

            self.assertEqual(index[duplicate_guid], source / "Assets/A/model.onnx")

    def test_copies_transitive_dependencies_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_unity_fixture(root)
            target = root / "target"

            report = assemble(source, target, "Assets/Manipulation/G1OP.unity")

            self.assertEqual(report.unresolved_guids, ())
            self.assertTrue((target / "Assets/Environment/Room.prefab").exists())
            self.assertTrue((target / "Assets/Materials/Room.mat").exists())
            self.assertTrue((target / "Assets/Textures/Room.png").exists())
            self.assertTrue((target / "Assets/Textures/Room.png.meta").exists())

    def test_ignores_builtin_guid_and_reports_unknown_guid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _make_unity_fixture(root)
            scene = source / "Assets/Manipulation/G1OP.unity"
            unknown = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
            scene.write_text(
                scene.read_text(encoding="utf-8")
                + f"Unknown: {{fileID: 1, guid: {unknown}, type: 3}}\n",
                encoding="utf-8",
            )

            report = assemble(source, root / "target", "Assets/Manipulation/G1OP.unity")

            self.assertNotIn(BUILTIN_GUID, report.unresolved_guids)
            self.assertEqual(report.unresolved_guids, (unknown,))


if __name__ == "__main__":
    unittest.main()
