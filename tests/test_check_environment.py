import json
import tempfile
import unittest
from pathlib import Path

from tools.check_environment import check_environment


def _valid_project(root: Path) -> None:
    for relative in (
        "Assets/Manipulation/G1OP.unity",
        "Assets/Manipulation/main.py",
        "ProjectSettings/ProjectVersion.txt",
        "environment.yml",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")
    packages = root / "Packages"
    packages.mkdir()
    (packages / "manifest.json").write_text(
        json.dumps({"dependencies": {}}), encoding="utf-8"
    )


class CheckEnvironmentTests(unittest.TestCase):
    def test_reports_missing_key_and_scene(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            issues = check_environment(Path(directory), {})

        self.assertTrue(any("DASHSCOPE_API_KEY" in item for item in issues))
        self.assertTrue(any("Assets/Manipulation/G1OP.unity" in item for item in issues))

    def test_manual_mode_does_not_require_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _valid_project(root)

            issues = check_environment(root, {}, manual=True)

        self.assertFalse(any("DASHSCOPE_API_KEY" in item for item in issues))

    def test_reports_missing_relative_local_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _valid_project(root)
            (root / "Packages/manifest.json").write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "example.local": "file:../LocalPackages/example.local"
                        }
                    }
                ),
                encoding="utf-8",
            )

            issues = check_environment(root, {}, manual=True)

        self.assertTrue(any("example.local" in item for item in issues))


if __name__ == "__main__":
    unittest.main()
