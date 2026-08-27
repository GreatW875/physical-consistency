import tempfile
import unittest
from pathlib import Path

from tools.repository_audit import audit_repository


class RepositoryAuditTests(unittest.TestCase):
    def test_scans_unity_serialized_text_for_machine_specific_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scene = root / "Assets/Scene.unity"
            scene.parent.mkdir(parents=True)
            scene.write_text(
                "source: C:\\Users\\example-user\\Desktop\\model.onnx\n",
                encoding="utf-8",
            )

            issues = audit_repository(root)

            self.assertTrue(any("Scene.unity" in issue for issue in issues))

    def test_rejects_cache_and_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Library").mkdir()
            (root / "secret.env").write_text(
                "DASHSCOPE" + "_API_KEY=" + "sk-" + "a" * 32,
                encoding="utf-8",
            )

            issues = audit_repository(root)

            self.assertTrue(any("Library" in item for item in issues))
            self.assertTrue(any("DASHSCOPE_API_KEY" in item for item in issues))

    def test_accepts_clean_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("Assets", "Packages", "ProjectSettings"):
                (root / name).mkdir()

            self.assertEqual(audit_repository(root), [])


if __name__ == "__main__":
    unittest.main()
