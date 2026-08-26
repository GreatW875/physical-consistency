import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "Packages"
PROJECT_SETTINGS = ROOT / "ProjectSettings"


class UnityProjectLayoutTests(unittest.TestCase):
    def test_local_packages_are_relative_and_exist(self) -> None:
        manifest = json.loads((PACKAGES / "manifest.json").read_text(encoding="utf-8"))
        for name, version in manifest["dependencies"].items():
            if version.startswith("file:"):
                self.assertNotIn("/home/", version)
                self.assertNotRegex(version, r"[A-Za-z]:[\\/]")
                package = (PACKAGES / version[5:]).resolve()
                self.assertTrue(package.is_dir(), f"missing local package {name}: {package}")

    def test_required_packages_are_kept_and_unrelated_packages_removed(self) -> None:
        manifest = json.loads((PACKAGES / "manifest.json").read_text(encoding="utf-8"))
        dependencies = set(manifest["dependencies"])
        required = {
            "com.unity.ai.navigation",
            "com.unity.ml-agents",
            "com.unity.render-pipelines.universal",
            "com.unity.robotics.urdf-importer",
            "com.unity.sentis",
            "com.unity.ugui",
        }
        removed = {
            "com.unity.ide.rider",
            "com.unity.inputsystem",
            "com.unity.ml-agents.extensions",
            "com.unity.pico.livepreview",
            "com.unity.recorder",
            "com.unity.robotics.ros-tcp-connector",
            "com.unity.toolchain.macos-x86_64-linux-x86_64",
            "com.unity.toolchain.win-x86_64-linux-x86_64",
            "com.unity.xr.core-utils",
            "com.unity.xr.management",
        }
        self.assertTrue(required.issubset(dependencies))
        self.assertTrue(removed.isdisjoint(dependencies))

    def test_only_research_scene_is_in_build_settings(self) -> None:
        text = (PROJECT_SETTINGS / "EditorBuildSettings.asset").read_text(encoding="utf-8")
        self.assertIn("Assets/Manipulation/G1OP.unity", text)
        self.assertIn("guid: 8133c023d195999439361f52847fcfc8", text)
        self.assertEqual(text.count("path: Assets/"), 1)

    def test_project_version_is_preserved(self) -> None:
        text = (PROJECT_SETTINGS / "ProjectVersion.txt").read_text(encoding="utf-8")
        self.assertIn("2022.3.62", text)


if __name__ == "__main__":
    unittest.main()
