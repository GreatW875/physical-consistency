"""PhysicalConsistencyChecker — 物理一致性校验主类。

在 VLM 提议动作与 Unity 执行之间充当"安全阀"：
- 计算碰撞风险分数
- 高风险时否决/修正动作
- 通过注入的 MetricsLogger 记录step级数据

关键设计：
- CHECKER_ENABLED=False 时仍计算风险，只是不修改动作；
- 本类不持有任何回合/步数状态 —— 所有状态由 MetricsLogger 承担。
"""

from dataclasses import dataclass, field
from typing import Optional
from .config import PhysicalConsistencyConfig
from .risk_model import (compute_collision_risk, compute_safe_distance,
                         find_safest_turn_direction)
from .nav_parser import get_sector_max_clearance_angle
from .metrics_logger import MetricsLogger


@dataclass
class CheckResult:
    """物理校验结果。"""
    risk_score: float                                    # 碰撞风险分数 [0, 1]
    approved: bool                                       # 原始动作是否通过
    modified_action: dict                                # 最终执行的动作
    reason: str                                          # approved / high_risk_veto / distance_reduced / checker_disabled
    risk_components: dict = field(default_factory=dict)  # 各子分数明细
    followup_action: Optional[dict] = None               # 紧跟当前动作之后建议执行的下一动作（如高风险转向后的前进）
    modified_risk_info: Optional[dict] = None            # 修正后动作的风险明细（distance_reduced / high_risk_veto 时填充）


class PhysicalConsistencyChecker:
    """物理一致性校验器。

    用法::

        logger = MetricsLogger(cfg)
        checker = PhysicalConsistencyChecker(cfg, logger=logger)
        result = checker.check(proposed_action, nav_data)
        final_action = result.modified_action
    """

    def __init__(self, config: PhysicalConsistencyConfig = None,
                 logger: Optional[MetricsLogger] = None):
        self.cfg = config or PhysicalConsistencyConfig()
        self.logger = logger   # 可为 None：纯校验、不落盘

    # ────────────────────────────────────────────────────────────────
    #  公共 API
    # ────────────────────────────────────────────────────────────────
    def check(self, proposed_action: dict, nav_data: dict,
              trap_injected: bool = False,
              trap_type: str = "none") -> CheckResult:
        """对 VLM 提议的动作进行物理一致性校验。"""
        risk_info = compute_collision_risk(nav_data, proposed_action, self.cfg)
        components = {
            "front":            risk_info["front"],
            "proximity":        risk_info["proximity"],
            "coverage":         risk_info["coverage"],
            "overshoot":        risk_info["overshoot"],
            "lateral":          risk_info["lateral"],
            "min_frontal_dist": risk_info["min_frontal_dist"],
        }
        risk_score = risk_info["risk_score"]

        result = self._decide(proposed_action, nav_data, risk_score, components)

        if self.logger is not None:
            self.logger.log_step(
                proposed_action,
                result,
                nav_data,
                trap_injected=trap_injected,
                trap_type=trap_type,
            )
        return result

    # ────────────────────────────────────────────────────────────────
    #  内部决策逻辑
    # ────────────────────────────────────────────────────────────────
    def _decide(self, proposed: dict, nav: dict,
                risk_score: float, components: dict) -> CheckResult:
        """根据 cfg 与风险分数产出 CheckResult；纯函数风格，无副作用。"""
        if not self.cfg.CHECKER_ENABLED:
            return CheckResult(risk_score, True, dict(proposed),
                               "checker_disabled", components)

        move = proposed.get("move", "stop")
        if move == "stop":
            return CheckResult(0.0, True, dict(proposed), "approved", components)

        if move == "walk":
            if risk_score >= self.cfg.HIGH_RISK_THRESHOLD:
                best_ang = get_sector_max_clearance_angle(
                    nav, center=0.0, half_width=self.cfg.FRONTAL_CONE_HALF_ANGLE)
                if abs(best_ang) < self.cfg.MIN_ESCAPE_ANG:
                    best_ang = find_safest_turn_direction(nav, self.cfg)
                mod_action = {"move": "turn", "dist": 0.0, "ang": best_ang}
                mod_risk   = compute_collision_risk(nav, mod_action, self.cfg)
                return CheckResult(
                    risk_score, False, mod_action,
                    "high_risk_veto", components,
                    followup_action={"move": "walk",
                                     "dist": self.cfg.HIGH_RISK_FOLLOW_DIST,
                                     "ang": 0.0},
                    modified_risk_info=mod_risk)
            if risk_score >= self.cfg.MED_RISK_THRESHOLD:
                safe_dist  = compute_safe_distance(nav, proposed, self.cfg)
                mod_action = {"move": "walk", "dist": safe_dist,
                              "ang": proposed.get("ang", 0.0)}
                mod_risk   = compute_collision_risk(nav, mod_action, self.cfg)
                return CheckResult(
                    risk_score, False, mod_action,
                    "distance_reduced", components,
                    modified_risk_info=mod_risk)
            return CheckResult(risk_score, True, dict(proposed),
                               "approved", components)

        if move == "turn":
            if risk_score >= self.cfg.HIGH_RISK_THRESHOLD:
                best_ang = get_sector_max_clearance_angle(
                    nav, center=0.0, half_width=self.cfg.FRONTAL_CONE_HALF_ANGLE)
                if abs(best_ang) < self.cfg.MIN_ESCAPE_ANG:
                    best_ang = find_safest_turn_direction(nav, self.cfg)
                mod_action = {"move": "turn", "dist": 0.0, "ang": best_ang}
                mod_risk   = compute_collision_risk(nav, mod_action, self.cfg)
                return CheckResult(
                    risk_score, False, mod_action,
                    "high_risk_veto", components,
                    followup_action={"move": "walk",
                                     "dist": self.cfg.HIGH_RISK_FOLLOW_DIST,
                                     "ang": 0.0},
                    modified_risk_info=mod_risk)
            return CheckResult(risk_score, True, dict(proposed),
                               "approved", components)

        # 未知 move：视为 stop
        return CheckResult(0.0, True, {"move": "stop", "dist": 0.0, "ang": 0.0},
                           "approved", components)
