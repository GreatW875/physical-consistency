"""物理一致性校验模块 — 配置参数"""


class PhysicalConsistencyConfig:
    """所有可调参数集中管理，便于 A/B 实验和跨环境迁移。"""

    # ── A/B 开关 ──────────────────────────────────────────────────
    CHECKER_ENABLED: bool = True      # False = 仍计算风险但不修改动作

    # ── 局部观测角域 ──────────────────────────────────────────────
    FRONT_MIN_HALF_ANGLE: float = 5.0       # P/O的前方最小距离：±5°
    BLOCKAGE_HALF_ANGLE: float = 45.0       # B：当前朝向±45°
    LATERAL_MIN_ANGLE: float = 50.0         # L：侧向角域内边界
    LATERAL_MAX_ANGLE: float = 90.0         # L：侧向角域外边界
    FRONTAL_CONE_HALF_ANGLE: float = 30.0   # turn后方向P、逃逸与缩距查询角域

    # ── 距离阈值 ──────────────────────────────────────────────────
    CRITICAL_DISTANCE: float = 0.4    # 米，紧急阈值
    DANGER_DISTANCE: float = 1.0      # 米，高风险阈值
    CAUTION_DISTANCE: float = 1.2     # 米，中风险阈值

    # ── 机器人几何 ────────────────────────────────────────────────
    ROBOT_HALF_WIDTH: float = 0.4     # 米，用于侧向碰撞检测
    OVERSHOOT_SAFETY_MARGIN: float = 0.4  # 米，O的前方安全余量

    # ── 动作上限 ──────────────────────────────────────────────────
    MAX_WALK_DIST: float = 2.0        # 米
    MAX_TURN_ANG: float = 90.0        # 度
    MIN_ESCAPE_ANG: float = 20.0      # 度，高风险 veto 逃逸转向的最小幅度
    HIGH_RISK_FOLLOW_DIST: float = 1.0  # 米，高风险转向后立即跟进的前进距离

    # ── 统一F/B/L熵权（walk与turn共用）──────────────────────────
    RISK_WEIGHTS: dict = {
        "front": 0.3991891447710898,
        "coverage": 0.2190297888228466,
        "lateral": 0.3817810664060635,
    }

    # ── 风险阈值（决策用）────────────────────────────────────────
    HIGH_RISK_THRESHOLD: float = 0.51  # 及以上：高风险否决
    MED_RISK_THRESHOLD: float = 0.23   # 及以上：walk中风险缩距

    # ── 日志 ──────────────────────────────────────────────────────
    LOG_DIR: str = "logs"
