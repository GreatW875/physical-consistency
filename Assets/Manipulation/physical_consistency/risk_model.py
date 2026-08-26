"""局部物理风险评分。

底层保留P/B/O/L便于追溯，顶层以F=max(P,O)合并前向风险，
再用统一F/B/L权重计算walk与turn风险。
"""

import math
from .nav_parser import get_sector_min, get_sector_rays
from .config import PhysicalConsistencyConfig


def _sigmoid(x: float, center: float = 0.0, steepness: float = 5.0) -> float:
    """反向 sigmoid：x < center 时趋近 1（高风险），x > center 时趋近 0（低风险）。

    center 处输出 0.5，steepness 控制过渡陡峭度。
    """
    z = steepness * (x - center)
    if z > 500:
        return 0.0
    if z < -500:
        return 1.0
    return 1.0 / (1.0 + math.exp(z))


def _proximity_risk(min_front_dist: float, safe_dist) -> float:
    """前方最近障碍物的逼近程度。

    以 DANGER_DISTANCE (1.0m) 为 sigmoid 中心：
    - 0.2m → ~0.98
    - 0.5m → ~0.92
    - 1.0m → 0.50
    - 2.0m → ~0.01
    """
    return _sigmoid(min_front_dist, safe_dist, steepness=5.0)


def _coverage_risk(nav: dict, cfg: PhysicalConsistencyConfig) -> float:
    """当前朝向±45°内，距离小于1.2 m的射线比例B。"""
    sector = get_sector_rays(
        nav, center=0.0, half_width=cfg.BLOCKAGE_HALF_ANGLE
    )
    if not sector:
        return 0.0
    blocked = sum(1 for d in sector.values() if d < cfg.CAUTION_DISTANCE)
    return blocked / len(sector)


def _overshoot_risk(walk_dist: float, min_front_dist: float,
                    safety_margin: float) -> float:
    """候选步长侵入前方安全余量的比例O。"""
    if safety_margin <= 0:
        raise ValueError("safety_margin必须大于0")
    if walk_dist <= 0:
        return 0.0
    intrusion = walk_dist - (min_front_dist - safety_margin)
    return min(1.0, max(0.0, intrusion / safety_margin))


def _lateral_risk(nav: dict, cfg: PhysicalConsistencyConfig) -> float:
    """当前朝向±50°～±90°内，横向净空小于0.4 m的射线比例L。"""
    risk_count = 0
    check_count = 0
    for ang, dist in nav.items():
        rel_ang = abs(ang)
        if cfg.LATERAL_MIN_ANGLE <= rel_ang <= cfg.LATERAL_MAX_ANGLE:
            check_count += 1
            lateral_clearance = dist * math.sin(math.radians(rel_ang))
            if lateral_clearance < cfg.ROBOT_HALF_WIDTH:
                risk_count += 1
    if check_count == 0:
        return 0.0
    return risk_count / check_count


def _front_risk(proximity: float, overshoot: float) -> float:
    """前向综合风险F：取P与O中较严重者，避免重复计权。"""
    return max(proximity, overshoot)


def compute_collision_risk(
    nav: dict,
    action: dict,
    cfg: PhysicalConsistencyConfig = None,
) -> dict:
    if cfg is None:
        cfg = PhysicalConsistencyConfig()

    move = action.get("move", "stop")
    weights = cfg.RISK_WEIGHTS

    min_front = get_sector_min(
        nav, center=0, half_width=cfg.FRONT_MIN_HALF_ANGLE
    )

    if move == "stop":
        return {
            "risk_score": 0.0,
            "front": 0.0, "proximity": 0.0, "coverage": 0.0,
            "overshoot": 0.0, "lateral": 0.0,
            "min_frontal_dist": min_front,
        }

    if move == "walk":
        walk_dist = float(action.get("dist", 0))
        p = _proximity_risk(min_front, cfg.DANGER_DISTANCE)
        c = _coverage_risk(nav, cfg)
        o = _overshoot_risk(
            walk_dist, min_front, cfg.OVERSHOOT_SAFETY_MARGIN
        )
        l = _lateral_risk(nav, cfg)
        f = _front_risk(p, o)

        risk = (weights["front"] * f
                + weights["coverage"] * c
                + weights["lateral"] * l)
        risk = min(1.0, max(0.0, risk))

        return {
            "risk_score": risk,
            "front": round(f, 4),
            "proximity": round(p, 4),
            "coverage": round(c, 4),
            "overshoot": round(o, 4),
            "lateral": round(l, 4),
            "min_frontal_dist": round(min_front, 4),
        }

    if move == "turn":
        turn_ang = float(action.get("ang", 0))
        # P表示候选转向后方向的逼近风险；B/L仍以当前朝向0°为中心。
        target_min = get_sector_min(nav, center=turn_ang,
                                    half_width=cfg.FRONTAL_CONE_HALF_ANGLE)
        p = _proximity_risk(target_min, cfg.CRITICAL_DISTANCE)
        c = _coverage_risk(nav, cfg)
        o = 0.0  # 转向无位移
        l = _lateral_risk(nav, cfg)
        f = _front_risk(p, o)

        risk = (weights["front"] * f
                + weights["coverage"] * c
                + weights["lateral"] * l)
        risk = min(1.0, max(0.0, risk))

        return {
            "risk_score": risk,
            "front": round(f, 4),
            "proximity": round(p, 4),
            "coverage": round(c, 4),
            "overshoot": 0.0,
            "lateral": round(l, 4),
            "min_frontal_dist": round(target_min, 4),
        }

    # 未知动作类型
    return {
        "risk_score": 0.0,
        "front": 0.0, "proximity": 0.0, "coverage": 0.0,
        "overshoot": 0.0, "lateral": 0.0,
        "min_frontal_dist": min_front,
    }


def find_safest_turn_direction(nav: dict, cfg: PhysicalConsistencyConfig = None) -> float:
    """找到最安全的转向方向，返回转向角度（正=右转，负=左转）。

    比较左侧（负角度）和右侧（正角度）的总空旷度，
    选择更空旷的一侧，返回不超过 MAX_TURN_ANG 的角度。
    """
    if cfg is None:
        cfg = PhysicalConsistencyConfig()

    left_clearance = sum(distance for angle, distance in nav.items() if angle < 0)
    right_clearance = sum(distance for angle, distance in nav.items() if angle > 0)

    direction = -1.0 if left_clearance > right_clearance else 1.0
    magnitude = cfg.MAX_TURN_ANG
    return direction * magnitude


def compute_safe_distance(
    nav: dict,
    action: dict,
    cfg: PhysicalConsistencyConfig = None,
) -> float:
    """缩减行走距离以保留安全余量。

    safe_dist = min(原始距离, 前方最小距离 - CRITICAL_DISTANCE)
    至少保留 0.1m 以避免完全不动。
    """
    if cfg is None:
        cfg = PhysicalConsistencyConfig()

    min_front = get_sector_min(nav, center=0, half_width=cfg.FRONTAL_CONE_HALF_ANGLE)
    original_dist = float(action.get("dist", 0))
    safe = max(0.1, min_front - cfg.CRITICAL_DISTANCE)
    return min(safe, original_dist)
