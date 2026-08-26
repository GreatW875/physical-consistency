import csv
import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

MANIPULATION_DIR = Path(__file__).resolve().parents[1] / "Assets" / "Manipulation"
if str(MANIPULATION_DIR) not in sys.path:
    sys.path.insert(0, str(MANIPULATION_DIR))

# 工作区测试环境不包含OpenAI SDK；本组测试只验证本地Prompt组装，
# 因此仅替代外部客户端的构造边界，不替代被测逻辑。
if "openai" not in sys.modules:
    fake_openai = types.ModuleType("openai")

    class _UnusedOpenAI:
        def __init__(self, **_kwargs):
            pass

    fake_openai.OpenAI = _UnusedOpenAI
    sys.modules["openai"] = fake_openai

from physical_consistency.checker import CheckResult, PhysicalConsistencyChecker
from physical_consistency.config import PhysicalConsistencyConfig
from physical_consistency.metrics_logger import MetricsLogger
from physical_consistency.risk_model import compute_collision_risk
from trap_injector import TrapInjector
from vlm_agent import AutonomousController


def clear_nav(distance: float = 10.0) -> dict:
    return {angle: distance for angle in range(-90, 91, 5)}


class RiskModelV2Tests(unittest.TestCase):
    def setUp(self):
        self.cfg = PhysicalConsistencyConfig()

    def test_walk_uses_new_b_domain_o_margin_f_max_and_unified_weights(self):
        """捕获回退：B恢复±30°、O恢复步长比，或P/O再被重复加权。"""
        nav = clear_nav()
        nav[-5] = nav[0] = nav[5] = 1.0
        result = compute_collision_risk(
            nav, {"move": "walk", "dist": 1.2, "ang": 0.0}, self.cfg
        )

        expected_b = 3 / 19
        expected_risk = 0.3991891447710898 + 0.2190297888228466 * expected_b
        self.assertEqual(result["proximity"], 0.5)
        self.assertEqual(result["overshoot"], 1.0)
        self.assertIn("front", result)
        self.assertEqual(result["front"], 1.0)
        self.assertAlmostEqual(result["coverage"], round(expected_b, 4), places=12)
        self.assertAlmostEqual(result["risk_score"], expected_risk, places=12)

    def test_overshoot_is_fraction_of_invaded_point_four_meter_margin(self):
        """捕获回退：O重新变为proposed_dist/min_front_dist。"""
        nav = clear_nav()
        nav[-5] = nav[0] = nav[5] = 2.09
        result = compute_collision_risk(
            nav, {"move": "walk", "dist": 2.0, "ang": 0.0}, self.cfg
        )
        self.assertEqual(result["overshoot"], 0.775)
        self.assertEqual(result["front"], 0.775)

    def test_turn_keeps_b_and_l_centered_on_current_heading(self):
        """捕获回退：turn再次以候选转角为B/L扇区中心。"""
        nav = clear_nav()
        nav[-45] = nav[45] = 1.0
        nav[-90] = nav[90] = 0.3
        nav[-50] = nav[50] = 0.4
        result = compute_collision_risk(
            nav, {"move": "turn", "dist": 0.0, "ang": 90.0}, self.cfg
        )

        self.assertAlmostEqual(result["coverage"], round(2 / 19, 4), places=12)
        self.assertAlmostEqual(result["lateral"], round(4 / 18, 4), places=12)
        self.assertEqual(result["overshoot"], 0.0)
        self.assertEqual(result["front"], result["proximity"])
        expected = (
            0.3991891447710898 * result["front"]
            + 0.2190297888228466 * (2 / 19)
            + 0.3817810664060635 * (4 / 18)
        )
        self.assertAlmostEqual(result["risk_score"], expected, places=4)


class CheckerThresholdTests(unittest.TestCase):
    def setUp(self):
        self.cfg = PhysicalConsistencyConfig()
        self.cfg.CHECKER_ENABLED = True
        self.checker = PhysicalConsistencyChecker(self.cfg)
        self.nav = clear_nav()
        self.components = {
            "front": 0.0,
            "proximity": 0.0,
            "coverage": 0.0,
            "overshoot": 0.0,
            "lateral": 0.0,
            "min_frontal_dist": 10.0,
        }

    def test_walk_at_point_two_three_enters_medium_risk_branch(self):
        """捕获回退：中风险边界仍保留在0.35。"""
        below = self.checker._decide(
            {"move": "walk", "dist": 1.0, "ang": 0.0},
            self.nav, 0.2299, self.components,
        )
        boundary = self.checker._decide(
            {"move": "walk", "dist": 1.0, "ang": 0.0},
            self.nav, 0.23, self.components,
        )
        self.assertEqual(below.reason, "approved")
        self.assertEqual(boundary.reason, "distance_reduced")

    def test_all_actions_at_point_five_one_enter_high_risk_branch(self):
        """捕获回退：高风险边界仍保留在0.7或turn使用严格大于。"""
        for action in (
            {"move": "walk", "dist": 1.0, "ang": 0.0},
            {"move": "turn", "dist": 0.0, "ang": 30.0},
        ):
            with self.subTest(action=action["move"]):
                result = self.checker._decide(
                    action, self.nav, 0.51, self.components
                )
                self.assertEqual(result.reason, "high_risk_veto")


class PromptIsolationTests(unittest.TestCase):
    def test_messages_sent_to_vlm_contain_no_geometric_radar_claim(self):
        """捕获回退：系统或单轮提示词再次宣称VLM拥有NavMesh几何雷达。"""

        class RecordingController(AutonomousController):
            def _call_vlm(self, messages):
                self.sent_messages = messages
                return '{"action":{"move":"walk","dist":"1","ang":"0"}}'

        controller = RecordingController()
        controller.round_decide_action("unused-image", "target:ang0=d2")
        text_parts = []
        for message in controller.sent_messages:
            content = message["content"]
            if isinstance(content, str):
                text_parts.append(content)
            else:
                text_parts.extend(
                    item["text"] for item in content if item.get("type") == "text"
                )
        sent_text = "\n".join(text_parts)
        for forbidden in ("几何雷达", "雷达数据", "NavMesh", "射线扫描"):
            self.assertNotIn(forbidden, sent_text)
        self.assertIn("机器人正前方的实时", sent_text)
        self.assertIn("当前导航目标点位于", sent_text)


class ThreeLevelLoggerTests(unittest.TestCase):
    def test_logger_creates_only_step_round_and_experiment_csv(self):
        """捕获回退：初始化记录器时重新生成event_incidents.csv。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PhysicalConsistencyConfig()
            cfg.LOG_DIR = tmp
            MetricsLogger(cfg)
            self.assertEqual(
                {path.name for path in Path(tmp).glob("*.csv")},
                {"step_decisions.csv", "round_summary.csv", "experiment_summary.csv"},
            )

    def test_logger_rejects_an_existing_incompatible_csv_schema(self):
        """捕获回退：新FPBOL记录被静默追加到缺少front_risk的旧表头。"""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "step_decisions.csv").write_text(
                "timestamp,scene,subject\n", encoding="utf-8"
            )
            cfg = PhysicalConsistencyConfig()
            cfg.LOG_DIR = tmp
            with self.assertRaisesRegex(RuntimeError, "日志表头不兼容"):
                MetricsLogger(cfg)

    def test_round_totals_are_aggregated_from_committed_steps(self):
        """捕获回退：删除event后轮次碰撞、卡死或修正计数丢失。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PhysicalConsistencyConfig()
            cfg.LOG_DIR = tmp
            logger = MetricsLogger(cfg)
            logger.start_round("corridor", "test", "A", 1, True, step_cap=10)
            result = CheckResult(
                risk_score=0.6,
                approved=False,
                modified_action={"move": "walk", "dist": 0.4, "ang": 0.0},
                reason="distance_reduced",
                risk_components={
                    "front": 0.8,
                    "proximity": 0.5,
                    "coverage": 0.2,
                    "overshoot": 0.8,
                    "lateral": 0.1,
                    "min_frontal_dist": 1.0,
                },
            )
            with redirect_stdout(io.StringIO()):
                logger.log_step(
                    {"move": "walk", "dist": 1.0, "ang": 0.0}, result, clear_nav()
                )
                logger.commit_step(collision_count=2, is_stuck=True)
                stats = logger.finalize_round(3.0)
                logger.finalize_experiment()

            self.assertEqual(stats["collisions"], 2)
            self.assertEqual(stats["stucks"], 1)
            self.assertEqual(stats["corrections"], 1)
            with open(Path(tmp) / "step_decisions.csv", encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["front_risk"], "0.8")
            self.assertEqual(row["collision_count"], "2")
            self.assertEqual(row["stuck"], "1")

    def test_step_log_derives_binary_collision_label_before_collision_count(self):
        """捕获回退：动作碰撞标签缺失、非二元或未紧邻碰撞次数左侧。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PhysicalConsistencyConfig()
            cfg.LOG_DIR = tmp
            logger = MetricsLogger(cfg)
            logger.start_round("corridor", "test", "B", 1, False)
            result = CheckResult(
                risk_score=0.1,
                approved=True,
                modified_action={"move": "walk", "dist": 0.5, "ang": 0.0},
                reason="checker_disabled",
                risk_components={
                    "front": 0.1,
                    "proximity": 0.1,
                    "coverage": 0.0,
                    "overshoot": 0.0,
                    "lateral": 0.0,
                    "min_frontal_dist": 2.0,
                },
            )

            with redirect_stdout(io.StringIO()):
                for collision_count in (0, 3):
                    logger.log_step(
                        {"move": "walk", "dist": 0.5, "ang": 0.0},
                        result,
                        clear_nav(),
                    )
                    logger.commit_step(collision_count)

            with open(Path(tmp) / "step_decisions.csv", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                fields = reader.fieldnames

            self.assertIn("collision_occurred", fields)
            label_index = fields.index("collision_occurred")
            self.assertEqual(fields[label_index + 1], "collision_count")
            self.assertEqual(
                [row["collision_occurred"] for row in rows], ["0", "1"]
            )
            self.assertEqual([row["collision_count"] for row in rows], ["0", "3"])

    def test_checker_passes_trap_metadata_into_step_log(self):
        """捕获回退：陷阱动作在step日志中无法与自然动作区分。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PhysicalConsistencyConfig()
            cfg.LOG_DIR = tmp
            cfg.CHECKER_ENABLED = False
            logger = MetricsLogger(cfg)
            logger.start_round("classroom", "test", "B", 1, False)
            checker = PhysicalConsistencyChecker(cfg, logger=logger)

            with redirect_stdout(io.StringIO()):
                try:
                    checker.check(
                        {"move": "walk", "dist": 0.5, "ang": 0.0},
                        clear_nav(),
                        trap_injected=True,
                        trap_type="speed_surge",
                    )
                except TypeError as exc:
                    self.fail(f"checker尚未支持陷阱元数据：{exc}")
                logger.commit_step(0)

            with open(Path(tmp) / "step_decisions.csv", encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["trap_injected"], "1")
            self.assertEqual(row["trap_type"], "speed_surge")


class TrapInjectorTests(unittest.TestCase):
    def test_inject_returns_the_applied_trap_type(self):
        """捕获回退：注入器只返回动作，调用方无法记录实际陷阱类型。"""
        injector = TrapInjector(base_interval=25)
        injector._pending_walk = True
        injector._pending_dist = 1.5

        injection = injector.inject(
            {"move": "turn", "dist": 0.0, "ang": 30.0}, clear_nav()
        )
        self.assertIsInstance(injection, tuple)
        action, trap_type = injection

        self.assertEqual(action, {"move": "walk", "dist": 1.5, "ang": 0})
        self.assertEqual(trap_type, "charge_obstacle")


if __name__ == "__main__":
    unittest.main()


