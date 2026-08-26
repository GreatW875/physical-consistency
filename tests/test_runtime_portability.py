import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MANIPULATION_DIR = Path(__file__).resolve().parents[1] / "Assets" / "Manipulation"
if str(MANIPULATION_DIR) not in sys.path:
    sys.path.insert(0, str(MANIPULATION_DIR))


class RuntimePortabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client_calls = []
        fake_openai = types.ModuleType("openai")
        calls = self.client_calls

        class RecordingOpenAI:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        fake_openai.OpenAI = RecordingOpenAI
        sys.modules["openai"] = fake_openai
        sys.modules.pop("vlm_agent", None)

    def test_image_path_is_module_relative(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key"}, clear=True):
            module = importlib.import_module("vlm_agent")

        self.assertEqual(
            module.DEFAULT_IMAGE_PATH,
            Path(module.__file__).resolve().parent / "robot_view.jpg",
        )
        self.assertEqual(self.client_calls, [])

    def test_missing_key_fails_only_when_client_requested(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            module = importlib.import_module("vlm_agent")
            self.assertEqual(self.client_calls, [])
            with self.assertRaisesRegex(RuntimeError, "DASHSCOPE_API_KEY"):
                module.get_client()

    def test_explicit_client_and_image_path_do_not_require_environment_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            module = importlib.import_module("vlm_agent")
            client = object()
            image = MANIPULATION_DIR / "custom.jpg"
            controller = module.AutonomousController(client=client, image_path=image)

        self.assertIs(controller.client, client)
        self.assertEqual(controller.image_path, image)
        self.assertEqual(self.client_calls, [])

    def test_environment_file_is_cross_platform_and_complete(self) -> None:
        environment = (MANIPULATION_DIR.parents[1] / "environment.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("name: physical-consistency", environment)
        for dependency in ("python=3.8", "openai", "numpy", "pandas", "pillow", "pytest"):
            self.assertIn(dependency, environment)
        self.assertNotIn("prefix:", environment)
        self.assertNotIn("/home/", environment)
        self.assertNotIn("libgcc", environment)

    def test_unity_socket_protocol_contract_is_preserved(self) -> None:
        runtime_source = "\n".join(
            (MANIPULATION_DIR / name).read_text(encoding="utf-8")
            for name in ("main.py", "vlm_agent.py")
        )
        for protocol_token in (
            "127.0.0.1",
            "5555",
            "IMG_READY",
            "ACTION|",
            "RESET",
            "STUCK|",
        ):
            self.assertIn(protocol_token, runtime_source)


if __name__ == "__main__":
    unittest.main()
