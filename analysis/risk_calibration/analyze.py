"""以 F=max(P,O) 合并前向风险后的相关性、熵权和风险分布分析。

数据源：data/raw/navigation_experiments/navigation_experiments.xlsx。
主分析：Baseline（corridorB + classroomB）中按 round 划分的标定集；
每个场景 35 轮标定、15 轮验证，固定随机种子 20260822。

B：当前朝向 -45°~45° 内，距离小于 1.2 m 的射线比例。
L：当前朝向 ±50°~±90° 内，d(theta)*sin(|theta|) 小于 0.4 m 的射线比例。
O：walk候选距离侵入0.4 m前方安全余量的比例；turn固定为0。
F：前向综合风险，F=max(P,O)；turn因O=0，自然退化为F=P。
walk 与 turn 均固定以当前朝向 0° 为中心。

主定权样本：Baseline全部标定集（walk和turn合并），对F/B/L计算一套统一熵权。

输出写入语义化的 data/processed 与 results 目录，不修改原始工作簿。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


ANALYSIS_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE = (
    REPOSITORY_ROOT
    / "data"
    / "raw"
    / "navigation_experiments"
    / "navigation_experiments.xlsx"
)
PROCESSED = REPOSITORY_ROOT / "data" / "processed" / "risk_calibration"
RESULTS = REPOSITORY_ROOT / "results" / "risk_calibration"
TABLES = RESULTS / "tables"
FIGURES = RESULTS / "figures"
REPORT = RESULTS / "report.md"

RANDOM_SEED = 20260822
CALIBRATION_ROUNDS_PER_SCENE = 35
BOOTSTRAP_REPLICATES = 500
TRAP_STEPS = {25, 50, 75}

SOURCE_VARS = ["P", "B", "O", "L"]
FBL_VARS = ["F", "B", "L"]
OLD_WEIGHTS = {"P": 0.20, "B": 0.35, "O": 0.35, "L": 0.10}
WEIGHT_VERSION = "entropy_cal35_seed20260822_fmax_po_unified_fbl"
THRESHOLD_VERSION = "pending_quantile_validation"

B_ANGLES = tuple(range(-45, 46, 5))
L_ANGLES = tuple(list(range(-90, -49, 5)) + list(range(50, 91, 5)))
OLD_B_ANGLES = tuple(range(-30, 31, 5))
OLD_L_ANGLES = tuple(list(range(-40, -19, 5)) + list(range(20, 41, 5)))
FRONT_MIN_ANGLES = (-5, 0, 5)
CAUTION_DISTANCE = 1.2
LATERAL_SAFETY_MARGIN = 0.4
OVERSHOOT_SAFETY_MARGIN = 0.4


def ensure_dirs() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    TABLES.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)


def parse_nav_json(value: object, sample_id: str) -> dict[int, float]:
    """将 W 列 nav_json 解析为角度到距离的映射，并校验 37 条射线。"""
    try:
        raw = value if isinstance(value, dict) else json.loads(str(value))
        nav = {int(float(angle)): float(distance) for angle, distance in raw.items()}
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{sample_id} 的 nav_json 无法解析") from exc
    expected = set(range(-90, 91, 5))
    if set(nav) != expected:
        missing = sorted(expected.difference(nav))
        extra = sorted(set(nav).difference(expected))
        raise ValueError(f"{sample_id} 的 nav_json 射线异常：missing={missing}, extra={extra}")
    if any((not np.isfinite(distance)) or distance < 0 for distance in nav.values()):
        raise ValueError(f"{sample_id} 的 nav_json 包含非法距离")
    return nav


def blockage_ratio(nav: dict[int, float], angles: Iterable[int]) -> float:
    selected = [nav[angle] for angle in angles]
    return round(sum(distance < CAUTION_DISTANCE for distance in selected) / len(selected), 4)


def lateral_ratio(nav: dict[int, float], angles: Iterable[int]) -> float:
    selected = [
        nav[angle] * math.sin(math.radians(abs(angle)))
        for angle in angles
    ]
    return round(
        sum(clearance < LATERAL_SAFETY_MARGIN for clearance in selected) / len(selected),
        4,
    )


def overshoot_risk(proposed_move: str, proposed_dist: float,
                   min_front_dist: float, safety_margin: float) -> float:
    """候选行走动作侵入前方安全余量的比例，截断至[0,1]。"""
    if safety_margin <= 0:
        raise ValueError("safety_margin必须大于0")
    if proposed_move != "walk" or proposed_dist <= 0:
        return 0.0
    intrusion = proposed_dist - (min_front_dist - safety_margin)
    return min(1.0, max(0.0, intrusion / safety_margin))


def front_min_distance(nav: dict[int, float]) -> float:
    """复现risk_model中center=0、half_width=5的前方最小距离。"""
    return min(nav[angle] for angle in FRONT_MIN_ANGLES)


def front_risk(proximity: float, overshoot: float) -> float:
    """取状态逼近风险与动作侵入风险中较严重者，避免重复计权。"""
    return max(float(proximity), float(overshoot))


def load_baseline() -> pd.DataFrame:
    ensure_dirs()
    required = {
        "scene", "subject", "round", "step_id", "proposed_move",
        "proposed_dist", "proposed_ang",
        "risk_score", "P", "B", "O", "L", "min_frontal_dist", "collision_count",
        "stuck", "checker_enabled", "nav_json",
    }
    frames = []
    for sheet in ("corridorB", "classroomB"):
        df = pd.read_excel(SOURCE, sheet_name=sheet, engine="openpyxl")
        missing = required.difference(df.columns)
        if missing:
            raise ValueError(f"{sheet} 缺少字段：{sorted(missing)}")
        if len(df) != 5000:
            raise ValueError(f"{sheet} 应有 5000 行，实际为 {len(df)} 行")
        frames.append(df)

    data = pd.concat(frames, ignore_index=True)
    if data.duplicated(["scene", "subject", "round", "step_id"]).any():
        raise ValueError("存在重复的 scene-subject-round-step_id")
    if not (data["subject"] == "B").all():
        raise ValueError("Baseline 工作表中出现非 B 组记录")
    if data[SOURCE_VARS].isna().any().any():
        raise ValueError("P/B/O/L 存在缺失值")
    if not (((data[SOURCE_VARS] >= 0) & (data[SOURCE_VARS] <= 1)).all().all()):
        raise ValueError("P/B/O/L 存在超出 [0,1] 的数值")

    data["round"] = data["round"].astype(int)
    data["step_id"] = data["step_id"].astype(int)
    data["sample_id"] = data.apply(
        lambda r: f"{r['scene']}_B_R{r['round']:02d}_S{r['step_id']:03d}", axis=1
    )
    data["trap_injected"] = data["step_id"].isin(TRAP_STEPS)
    data["collision_event"] = (data["collision_count"] > 0).astype(int)
    data["B_original"] = data["B"].astype(float)
    data["O_original"] = data["O"].astype(float)
    data["L_original"] = data["L"].astype(float)
    nav_records = [
        parse_nav_json(value, sample_id)
        for value, sample_id in zip(data["nav_json"], data["sample_id"])
    ]
    data["B"] = [blockage_ratio(nav, B_ANGLES) for nav in nav_records]
    data["min_frontal_dist_recomputed"] = [front_min_distance(nav) for nav in nav_records]
    data["O"] = [
        round(overshoot_risk(move, float(distance), float(min_front),
                             OVERSHOOT_SAFETY_MARGIN), 4)
        for move, distance, min_front in zip(
            data["proposed_move"], data["proposed_dist"],
            data["min_frontal_dist_recomputed"],
        )
    ]
    data["F"] = [front_risk(p, o) for p, o in zip(data["P"], data["O"])]
    data["L"] = [lateral_ratio(nav, L_ANGLES) for nav in nav_records]

    # 以 walk 记录复算旧公式，确认 W 列解析与原日志完全一致。
    old_b = np.array([blockage_ratio(nav, OLD_B_ANGLES) for nav in nav_records])
    old_l = np.array([lateral_ratio(nav, OLD_L_ANGLES) for nav in nav_records])
    walk_mask = data["proposed_move"].eq("walk").to_numpy()
    old_o = np.array([
        round(1.0 if min_front <= 0 else min(1.0, float(distance) / min_front), 4)
        for distance, min_front in zip(
            data["proposed_dist"], data["min_frontal_dist_recomputed"]
        )
    ])
    b_diff = np.abs(old_b[walk_mask] - data.loc[walk_mask, "B_original"].to_numpy())
    o_diff = np.abs(old_o[walk_mask] - data.loc[walk_mask, "O_original"].to_numpy())
    l_diff = np.abs(old_l[walk_mask] - data.loc[walk_mask, "L_original"].to_numpy())
    front_diff = np.abs(
        data.loc[walk_mask, "min_frontal_dist_recomputed"].to_numpy()
        - data.loc[walk_mask, "min_frontal_dist"].to_numpy()
    )
    validation = pd.DataFrame([
        {
            "check": "old_B_recomputed_vs_source_walk",
            "n_steps": int(walk_mask.sum()),
            "max_abs_difference": float(b_diff.max()),
            "mismatch_count_gt_1e-4": int((b_diff > 1e-4).sum()),
        },
        {
            "check": "old_O_recomputed_vs_source_walk",
            "n_steps": int(walk_mask.sum()),
            "max_abs_difference": float(o_diff.max()),
            "mismatch_count_gt_1e-4": int((o_diff > 1e-4).sum()),
        },
        {
            "check": "old_L_recomputed_vs_source_walk",
            "n_steps": int(walk_mask.sum()),
            "max_abs_difference": float(l_diff.max()),
            "mismatch_count_gt_1e-4": int((l_diff > 1e-4).sum()),
        },
        {
            "check": "front_min_recomputed_vs_source_walk",
            "n_steps": int(walk_mask.sum()),
            "max_abs_difference": float(front_diff.max()),
            "mismatch_count_gt_1e-4": int((front_diff > 1e-4).sum()),
        },
    ])
    validation.to_csv(TABLES / "recalculation_validation.csv", index=False, encoding="utf-8-sig")

    if data[SOURCE_VARS].isna().any().any():
        raise ValueError("重算后的 P/B/O/L 存在缺失值")
    if not (((data[SOURCE_VARS] >= 0) & (data[SOURCE_VARS] <= 1)).all().all()):
        raise ValueError("重算后的 P/B/O/L 存在超出 [0,1] 的数值")
    return data


def assign_split(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(RANDOM_SEED)
    split_rows = []
    for scene in sorted(data["scene"].unique()):
        rounds = np.array(sorted(data.loc[data["scene"] == scene, "round"].unique()))
        if len(rounds) != 50:
            raise ValueError(f"{scene} 的 Baseline round 数应为 50，实际为 {len(rounds)}")
        shuffled = rounds.copy()
        rng.shuffle(shuffled)
        calibration = set(shuffled[:CALIBRATION_ROUNDS_PER_SCENE].tolist())
        for round_id in rounds:
            split_rows.append({
                "scene": scene,
                "subject": "B",
                "round": int(round_id),
                "data_split": "calibration" if round_id in calibration else "validation",
            })

    split = pd.DataFrame(split_rows)
    merged = data.merge(split, on=["scene", "subject", "round"], how="left", validate="many_to_one")
    if merged["data_split"].isna().any():
        raise ValueError("部分记录未分配 calibration/validation")
    return merged, split


def corr_matrix(df: pd.DataFrame, variables: list[str], method: str) -> pd.DataFrame:
    values = df[variables]
    if method == "pearson":
        return values.corr(method="pearson")
    if method == "spearman":
        # Spearman等价于对平均秩做Pearson；避免依赖SciPy。
        return values.rank(method="average").corr(method="pearson")
    raise ValueError(f"不支持的相关性方法：{method}")


def entropy_weight(df: pd.DataFrame, variables: list[str]) -> pd.DataFrame:
    """对已归一化、同向、非负风险指标计算熵权。"""
    x = df[variables].to_numpy(dtype=float)
    n = x.shape[0]
    if n < 2:
        raise ValueError("熵权法样本数不足")
    sums = x.sum(axis=0)
    active = sums > 0
    p = np.zeros_like(x, dtype=float)
    p[:, active] = x[:, active] / sums[active]
    with np.errstate(divide="ignore", invalid="ignore"):
        plnp = np.where(p > 0, p * np.log(p), 0.0)
    entropy = -(1.0 / math.log(n)) * plnp.sum(axis=0)
    # 恒为0的指标不提供差异信息，其离差和权重均记为0。
    entropy[~active] = 1.0
    divergence = 1.0 - entropy
    if divergence.sum() <= 0:
        raise ValueError("所有指标均无差异，无法计算熵权")
    weights = divergence / divergence.sum()
    return pd.DataFrame({
        "indicator": variables,
        "n_steps": n,
        "mean": x.mean(axis=0),
        "std": x.std(axis=0, ddof=1),
        "zero_rate": (x == 0).mean(axis=0),
        "entropy": entropy,
        "divergence": divergence,
        "weight": weights,
    })


def context_frames(data: pd.DataFrame, population: str) -> dict[str, pd.DataFrame]:
    """产生统一F/B/L分析所需的样本范围。

    population=all表示walk与turn合并；walk/turn仅用于动作敏感性分析。
    """
    if population == "all":
        population_data = data.copy()
    elif population in {"walk", "turn"}:
        population_data = data.loc[data["proposed_move"] == population].copy()
    else:
        raise ValueError(f"不支持的population：{population}")
    calibration = population_data.loc[population_data["data_split"] == "calibration"]
    return {
        "pooled_calibration": calibration,
        "corridor_calibration": calibration.loc[calibration["scene"] == "corridor"],
        "classroom_calibration": calibration.loc[calibration["scene"] == "classroom"],
        "pooled_calibration_no_trap": calibration.loc[~calibration["trap_injected"]],
        "pooled_all_rounds": population_data,
    }


def save_correlations(data: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for population in ("all", "walk", "turn"):
        for scope, frame in context_frames(data, population).items():
            for method in ("pearson", "spearman"):
                matrix = corr_matrix(frame, FBL_VARS, method)
                matrix.to_csv(
                    TABLES / f"correlation_{population}_{scope}_{method}.csv",
                    encoding="utf-8-sig",
                )
                for i, left in enumerate(FBL_VARS):
                    for right in FBL_VARS[i + 1:]:
                        rows.append({
                            "population": population,
                            "scope": scope,
                            "method": method,
                            "n_steps": len(frame),
                            "indicator_1": left,
                            "indicator_2": right,
                            "correlation": float(matrix.loc[left, right]),
                        })
    summary = pd.DataFrame(rows)
    summary.to_csv(TABLES / "correlation_pairwise_summary.csv", index=False, encoding="utf-8-sig")
    return summary


def _bootstrap_frame(frame: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    chunks = []
    for scene in sorted(frame["scene"].unique()):
        scene_df = frame.loc[frame["scene"] == scene]
        rounds = np.array(sorted(scene_df["round"].unique()))
        sampled = rng.choice(rounds, size=len(rounds), replace=True)
        grouped = {round_id: part for round_id, part in scene_df.groupby("round", sort=False)}
        chunks.extend(grouped[int(round_id)] for round_id in sampled)
    return pd.concat(chunks, ignore_index=True)


def bootstrap_primary(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(RANDOM_SEED + 1)
    corr_records = []
    weight_records = []

    for population in ("all", "walk", "turn"):
        frame = context_frames(data, population)["pooled_calibration"]
        for replicate in range(BOOTSTRAP_REPLICATES):
            sample = _bootstrap_frame(frame, rng)
            for method in ("pearson", "spearman"):
                matrix = corr_matrix(sample, FBL_VARS, method)
                for i, left in enumerate(FBL_VARS):
                    for right in FBL_VARS[i + 1:]:
                        corr_records.append({
                            "replicate": replicate,
                            "population": population,
                            "method": method,
                            "indicator_1": left,
                            "indicator_2": right,
                            "correlation": float(matrix.loc[left, right]),
                        })
            if population == "all":
                ew = entropy_weight(sample, FBL_VARS)
                for row in ew.itertuples(index=False):
                    weight_records.append({
                        "replicate": replicate,
                        "indicator": row.indicator,
                        "weight": row.weight,
                    })

    corr_boot = pd.DataFrame(corr_records)
    corr_ci = corr_boot.groupby(
        ["population", "method", "indicator_1", "indicator_2"], as_index=False
    )["correlation"].agg(
        bootstrap_median="median",
        ci_2_5=lambda x: x.quantile(0.025),
        ci_97_5=lambda x: x.quantile(0.975),
    )
    corr_ci.to_csv(TABLES / "correlation_bootstrap_ci.csv", index=False, encoding="utf-8-sig")

    weight_boot = pd.DataFrame(weight_records)
    weight_ci = weight_boot.groupby("indicator", as_index=False)["weight"].agg(
        bootstrap_median="median",
        ci_2_5=lambda x: x.quantile(0.025),
        ci_97_5=lambda x: x.quantile(0.975),
    )
    weight_ci.to_csv(TABLES / "entropy_weight_bootstrap_ci.csv", index=False, encoding="utf-8-sig")
    return corr_ci, weight_ci


def save_entropy_results(data: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    records = []
    # all的五个范围用于主分析与场景/陷阱敏感性；
    # walk、turn只保留合并标定集的动作敏感性结果。
    contexts = [("all", scope, frame)
                for scope, frame in context_frames(data, "all").items()]
    contexts.extend(
        (population, "pooled_calibration", context_frames(data, population)["pooled_calibration"])
        for population in ("walk", "turn")
    )
    for population, scope, frame in contexts:
        result = entropy_weight(frame, FBL_VARS)
        result.insert(0, "population", population)
        result.insert(1, "scope", scope)
        records.append(result)

    detailed = pd.concat(records, ignore_index=True)
    detailed.to_csv(TABLES / "entropy_weights_detailed.csv", index=False, encoding="utf-8-sig")

    primary = detailed.loc[
        (detailed["scope"] == "pooled_calibration") &
        (detailed["population"] == "all")
    ]
    weights = dict(zip(primary["indicator"], primary["weight"]))
    final_rows = [{
        "population": "all",
        "indicator": key,
        "weight": weights[key],
        "derivation": "entropy_weight_all_baseline_calibration_steps",
        "applies_to": "walk_and_turn",
    } for key in FBL_VARS]
    final = pd.DataFrame(final_rows)
    final.to_csv(TABLES / "final_entropy_weights.csv", index=False, encoding="utf-8-sig")
    return detailed, weights


def save_component_change_summary(data: pd.DataFrame) -> pd.DataFrame:
    """汇总定义调整前后B/O/L的分布变化，便于审计。"""
    rows = []
    for action in ("walk", "turn"):
        frame = data.loc[
            (data["data_split"] == "calibration") &
            (data["proposed_move"] == action)
        ]
        indicators = ("B", "O", "L") if action == "walk" else ("B", "L")
        for indicator in indicators:
            old = frame[f"{indicator}_original"].astype(float)
            new = frame[indicator].astype(float)
            rows.append({
                "action": action,
                "indicator": indicator,
                "n_steps": len(frame),
                "old_mean": old.mean(),
                "new_mean": new.mean(),
                "old_std": old.std(ddof=1),
                "new_std": new.std(ddof=1),
                "old_zero_rate": old.eq(0).mean(),
                "new_zero_rate": new.eq(0).mean(),
                "old_new_pearson": old.corr(new, method="pearson"),
                "old_new_spearman": old.rank(method="average").corr(
                    new.rank(method="average"), method="pearson"
                ),
            })
    summary = pd.DataFrame(rows)
    summary.to_csv(TABLES / "component_change_summary.csv", index=False, encoding="utf-8-sig")
    return summary


def add_fbl_risk(data: pd.DataFrame, weights: dict[str, float],
                 export: bool = True) -> pd.DataFrame:
    """用统一F/B/L权重计算walk与turn风险。"""
    missing = set(FBL_VARS).difference(weights)
    if missing:
        raise ValueError(f"FBL权重缺少字段：{sorted(missing)}")
    if not math.isclose(sum(weights[key] for key in FBL_VARS), 1.0,
                        rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("FBL权重之和必须为1")

    result = data.copy()
    result["risk_entropy"] = sum(weights[col] * result[col] for col in FBL_VARS)
    result["risk_level_entropy"] = "unassigned"
    result["weight_version"] = WEIGHT_VERSION
    result["threshold_version"] = THRESHOLD_VERSION

    if export:
        selected = [
            "sample_id", "scene", "subject", "round", "step_id", "data_split",
            "trap_injected", "proposed_move", "proposed_dist", "proposed_ang",
            "P", "O_original", "O", "F", "B_original", "B", "L_original", "L",
            "min_frontal_dist", "min_frontal_dist_recomputed",
            "risk_score", "risk_entropy", "risk_level_entropy",
            "weight_version", "threshold_version",
            "collision_count", "collision_event", "stuck",
        ]
        result[selected].to_csv(
            PROCESSED / "baseline_analysis_data.csv", index=False, encoding="utf-8-sig"
        )
    return result


def _risk_groups(data: pd.DataFrame):
    """依次产生全部/标定/验证与全部/walk/turn的风险分布子集。"""
    splits = [("all", data)]
    splits.extend(
        (split, data.loc[data["data_split"] == split])
        for split in sorted(data["data_split"].dropna().unique())
    )
    for split_name, split_frame in splits:
        yield split_name, "all", split_frame
        for action in ("walk", "turn"):
            yield split_name, action, split_frame.loc[
                split_frame["proposed_move"] == action
            ]


def risk_distribution_summary(data: pd.DataFrame,
                              score_col: str = "risk_entropy") -> pd.DataFrame:
    """输出统一风险值按数据划分和动作分组的描述统计与分位数。"""
    quantiles = {
        "p05": 0.05, "p25": 0.25, "p50": 0.50, "p75": 0.75,
        "p80": 0.80, "p90": 0.90, "p95": 0.95,
        "p975": 0.975, "p99": 0.99,
    }
    rows = []
    for split_name, action, frame in _risk_groups(data):
        values = frame[score_col].astype(float)
        row = {
            "data_split": split_name,
            "action": action,
            "n_steps": len(values),
            "mean": values.mean(),
            "std": values.std(ddof=1),
            "min": values.min(),
            "max": values.max(),
            "zero_rate": values.eq(0).mean(),
        }
        row.update({name: values.quantile(q) for name, q in quantiles.items()})
        rows.append(row)
    return pd.DataFrame(rows)


def risk_histogram_table(data: pd.DataFrame, bins: int = 50,
                         score_col: str = "risk_entropy") -> pd.DataFrame:
    """输出固定0～1区间直方图计数，便于复现线性或对数纵轴图。"""
    if bins <= 0:
        raise ValueError("bins必须大于0")
    edges = np.linspace(0.0, 1.0, bins + 1)
    rows = []
    for split_name, action, frame in _risk_groups(data):
        counts, _ = np.histogram(frame[score_col].astype(float), bins=edges)
        n = len(frame)
        for index, count in enumerate(counts):
            rows.append({
                "data_split": split_name,
                "action": action,
                "bin_left": edges[index],
                "bin_right": edges[index + 1],
                "bin_mid": (edges[index] + edges[index + 1]) / 2.0,
                "count": int(count),
                "fraction": float(count / n) if n else 0.0,
            })
    return pd.DataFrame(rows)


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\msyhbd.ttc") if bold else Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\arialbd.ttf") if bold else Path(r"C:\Windows\Fonts\arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def corr_color(value: float) -> tuple[int, int, int]:
    value = max(-1.0, min(1.0, float(value)))
    if value < 0:
        t = value + 1.0
        return (int(45 + 210 * t), int(95 + 160 * t), 220)
    t = 1.0 - value
    return (220, int(70 + 185 * t), int(55 + 200 * t))


def draw_heatmap(matrix: pd.DataFrame, title: str, output: Path) -> None:
    labels = list(matrix.columns)
    n = len(labels)
    width, height = 1100, 900
    left, top, cell = 250, 190, 140
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = get_font(38, bold=True)
    label_font = get_font(30, bold=True)
    value_font = get_font(28, bold=True)
    note_font = get_font(22)
    draw.text((width // 2, 60), title, fill=(20, 30, 55), font=title_font, anchor="mm")
    for i, label in enumerate(labels):
        draw.text((left - 35, top + i * cell + cell // 2), label,
                  fill=(20, 30, 55), font=label_font, anchor="rm")
        draw.text((left + i * cell + cell // 2, top - 35), label,
                  fill=(20, 30, 55), font=label_font, anchor="ms")
    for row in range(n):
        for col in range(n):
            value = float(matrix.iloc[row, col])
            x0, y0 = left + col * cell, top + row * cell
            draw.rounded_rectangle(
                (x0 + 4, y0 + 4, x0 + cell - 4, y0 + cell - 4),
                radius=12, fill=corr_color(value), outline=(230, 233, 240), width=2,
            )
            text_color = "white" if abs(value) >= 0.55 else (25, 30, 45)
            draw.text((x0 + cell // 2, y0 + cell // 2), f"{value:.3f}",
                      fill=text_color, font=value_font, anchor="mm")
    draw.text((width // 2, top + n * cell + 75),
              "蓝色表示负相关，红色表示正相关",
              fill=(70, 75, 90), font=note_font, anchor="mm")
    image.save(output, dpi=(180, 180))


def draw_weight_chart(detailed: pd.DataFrame, weights: dict[str, float], output: Path) -> None:
    corridor = detailed.loc[
        (detailed["scope"] == "corridor_calibration") & (detailed["population"] == "all")
    ].set_index("indicator")["weight"].to_dict()
    classroom = detailed.loc[
        (detailed["scope"] == "classroom_calibration") & (detailed["population"] == "all")
    ].set_index("indicator")["weight"].to_dict()
    series = [
        ("合并标定集熵权", weights, (42, 97, 171)),
        ("走廊标定集熵权", corridor, (54, 147, 120)),
        ("教室标定集熵权", classroom, (222, 145, 54)),
    ]
    width, height = 1350, 900
    left, top, chart_w, chart_h = 140, 160, 1090, 560
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = get_font(38, bold=True)
    label_font = get_font(27, bold=True)
    note_font = get_font(22)
    draw.text((width // 2, 65), "F/B/L统一熵权及场景敏感性",
              fill=(20, 30, 55), font=title_font, anchor="mm")
    draw.line((left, top, left, top + chart_h), fill=(60, 65, 80), width=3)
    draw.line((left, top + chart_h, left + chart_w, top + chart_h), fill=(60, 65, 80), width=3)
    y_max = 0.6
    for k in range(7):
        value = k * 0.1
        y = top + chart_h - value / y_max * chart_h
        draw.line((left, y, left + chart_w, y), fill=(230, 233, 240), width=1)
        draw.text((left - 18, y), f"{value:.1f}", fill=(70, 75, 90), font=note_font, anchor="rm")
    group_w = chart_w / len(FBL_VARS)
    bar_w = 45
    gap = 10
    for i, indicator in enumerate(FBL_VARS):
        center = left + group_w * (i + 0.5)
        total = len(series) * bar_w + (len(series) - 1) * gap
        start = center - total / 2
        for j, (_, values, color) in enumerate(series):
            value = float(values[indicator])
            x0 = start + j * (bar_w + gap)
            y0 = top + chart_h - value / y_max * chart_h
            draw.rounded_rectangle((x0, y0, x0 + bar_w, top + chart_h), radius=7, fill=color)
            draw.text((x0 + bar_w / 2, y0 - 10), f"{value:.3f}",
                      fill=(40, 45, 60), font=get_font(18), anchor="ms")
        draw.text((center, top + chart_h + 42), indicator,
                  fill=(20, 30, 55), font=label_font, anchor="mm")
    legend_x, legend_y = 210, 805
    for idx, (name, _, color) in enumerate(series):
        x = legend_x + idx * 340
        draw.rounded_rectangle((x, legend_y - 14, x + 26, legend_y + 12), radius=4, fill=color)
        draw.text((x + 36, legend_y), name, fill=(50, 55, 70), font=note_font, anchor="lm")
    image.save(output, dpi=(180, 180))


def draw_risk_histogram(histogram: pd.DataFrame, output: Path) -> None:
    """绘制标定集walk/turn风险分布，纵轴为log10(count+1)。"""
    width, height = 1500, 920
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = get_font(38, bold=True)
    panel_font = get_font(28, bold=True)
    note_font = get_font(21)
    draw.text((width // 2, 55), "标定集统一F/B/L风险分布",
              fill=(20, 30, 55), font=title_font, anchor="mm")
    panels = [("walk", "Walk", (42, 97, 171)), ("turn", "Turn", (222, 145, 54))]
    for panel_index, (action, label, color) in enumerate(panels):
        frame = histogram.loc[
            (histogram["data_split"] == "calibration") &
            (histogram["action"] == action)
        ].reset_index(drop=True)
        left = 110 + panel_index * 735
        top, chart_w, chart_h = 175, 610, 560
        values = np.log10(frame["count"].to_numpy(dtype=float) + 1.0)
        y_max = max(1.0, math.ceil(float(values.max()) * 2) / 2)
        draw.text((left + chart_w / 2, 120), label, fill=(20, 30, 55),
                  font=panel_font, anchor="mm")
        draw.line((left, top, left, top + chart_h), fill=(60, 65, 80), width=3)
        draw.line((left, top + chart_h, left + chart_w, top + chart_h),
                  fill=(60, 65, 80), width=3)
        for tick in np.linspace(0.0, y_max, 5):
            y = top + chart_h - tick / y_max * chart_h
            draw.line((left, y, left + chart_w, y), fill=(232, 235, 241), width=1)
            draw.text((left - 12, y), f"{tick:.1f}", fill=(70, 75, 90),
                      font=note_font, anchor="rm")
        bar_w = chart_w / len(frame)
        for index, value in enumerate(values):
            x0 = left + index * bar_w + 1
            y0 = top + chart_h - value / y_max * chart_h
            draw.rectangle((x0, y0, left + (index + 1) * bar_w - 1, top + chart_h),
                           fill=color)
        for x_value in np.linspace(0, 1, 6):
            x = left + x_value * chart_w
            draw.text((x, top + chart_h + 30), f"{x_value:.1f}", fill=(70, 75, 90),
                      font=note_font, anchor="mm")
        draw.text((left + chart_w / 2, top + chart_h + 72), "risk_entropy",
                  fill=(40, 45, 60), font=note_font, anchor="mm")
    draw.text((width // 2, 855), "纵轴：log10(count + 1)；横轴固定为0—1",
              fill=(70, 75, 90), font=note_font, anchor="mm")
    image.save(output, dpi=(180, 180))


def strength_label(value: float) -> str:
    absolute = abs(value)
    if absolute < 0.3:
        return "弱"
    if absolute < 0.5:
        return "中等"
    if absolute < 0.7:
        return "较强"
    return "强"


def write_report(data: pd.DataFrame, split: pd.DataFrame, corr_summary: pd.DataFrame,
                 corr_ci: pd.DataFrame, detailed: pd.DataFrame,
                 weights: dict[str, float], weight_ci: pd.DataFrame,
                 distribution: pd.DataFrame,
                 component_summary: pd.DataFrame) -> None:
    primary_corr = corr_summary.loc[
        corr_summary["scope"] == "pooled_calibration"
    ].merge(
        corr_ci,
        on=["population", "method", "indicator_1", "indicator_2"],
        how="left",
    )
    corridor_weights = detailed.loc[
        (detailed["population"] == "all") &
        (detailed["scope"] == "corridor_calibration")
    ].set_index("indicator")["weight"]
    classroom_weights = detailed.loc[
        (detailed["population"] == "all") &
        (detailed["scope"] == "classroom_calibration")
    ].set_index("indicator")["weight"]
    no_trap_weights = detailed.loc[
        (detailed["population"] == "all") &
        (detailed["scope"] == "pooled_calibration_no_trap")
    ].set_index("indicator")["weight"]
    primary_series = pd.Series(weights)
    scene_max_diff = float((corridor_weights - classroom_weights).abs().max())
    trap_max_diff = float((primary_series - no_trap_weights).abs().max())
    cal = data.loc[data["data_split"] == "calibration"]
    walk_n = int(cal["proposed_move"].eq("walk").sum())
    turn_n = int(cal["proposed_move"].eq("turn").sum())
    ci_map = weight_ci.set_index("indicator")

    lines = [
        "# F=max(P,O)后的F/B/L相关性、熵权与风险分布",
        "", "## 1. 分析设计", "",
        f"- 数据源：`navigation_experiments.xlsx`的`corridorB`和`classroomB`，共{len(data):,}个Baseline step。",
        f"- 每个场景35轮标定、15轮验证，固定随机种子{RANDOM_SEED}；标定集walk={walk_n:,}，turn={turn_n:,}。",
        f"- B与L沿用V2.2定义：B使用−45°～45°，L使用±50°～±90°；O表示walk候选距离对{OVERSHOOT_SAFETY_MARGIN:.1f} m安全余量的侵入程度。",
        "- 定义前向综合风险 F=max(P,O)。turn中O=0，因此F=P。这一层次化定义保留P/O的可追溯性，同时避免两者作为独立项重复计权。",
        "- 主熵权在全部Baseline标定步上对F/B/L统一计算，同一套权重适用于walk和turn。",
        "", "## 2. 标定集相关性", "",
        "|人群|方法|指标对|相关系数|95% round-bootstrap CI|强度|",
        "|---|---|---|---:|---:|---|",
    ]
    for row in primary_corr.itertuples(index=False):
        lines.append(
            f"|{row.population}|{row.method}|{row.indicator_1}-{row.indicator_2}|"
            f"{row.correlation:.4f}|[{row.ci_2_5:.4f}, {row.ci_97_5:.4f}]|"
            f"{strength_label(row.correlation)}|"
        )
    lines.extend([
        "", "`all`是正式分析；`walk`和`turn`是动作分层敏感性分析。F与B/L仍可因共同的局部几何状态而相关，但P-O不再作为两个顶层指标同时进入定权。",
        "", "## 3. 统一F/B/L熵权", "",
        "F、B、L已为[0,1]同向风险量，因此不再进行极差归一化；0值按0·ln(0)=0处理。",
        "", "|指标|统一熵权|95% round-bootstrap CI|", "|---|---:|---:|",
    ])
    for indicator in FBL_VARS:
        lines.append(
            f"|{indicator}|{weights[indicator]:.6f}|"
            f"[{ci_map.loc[indicator, 'ci_2_5']:.6f}, {ci_map.loc[indicator, 'ci_97_5']:.6f}]|"
        )
    lines.extend([
        "", f"- 走廊与教室分别定权时，单个指标权重最大绝对差为{scene_max_diff:.6f}。",
        f"- 主分析与排除陷阱step后的权重最大绝对差为{trap_max_diff:.6f}。",
        "- `entropy_weights_detailed.csv`另保留walk、turn分别定权以及分场景、排除陷阱、全部轮次的敏感性结果。",
        "", "## 4. 风险分布", "",
        "风险值按 risk=w_F·F+w_B·B+w_L·L 计算。下表仅展示标定集，完整统计见`risk_distribution_summary.csv`。",
        "", "|动作|样本数|均值|标准差|P50|P80|P90|P95|P99|", "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    shown = distribution.loc[
        (distribution["data_split"] == "calibration") &
        (distribution["action"].isin(["walk", "turn"]))
    ]
    for row in shown.itertuples(index=False):
        lines.append(
            f"|{row.action}|{row.n_steps}|{row.mean:.4f}|{row.std:.4f}|{row.p50:.4f}|"
            f"{row.p80:.4f}|{row.p90:.4f}|{row.p95:.4f}|{row.p99:.4f}|"
        )
    lines.extend([
        "", "## 5. 结果边界", "",
        "- 熵权反映样本差异性，不等于指标对碰撞的因果贡献，也不应表述为理论最优权重。",
        "- 风险分布已计算，但本分析不擅自冻结阈值；后续应以标定集分位数生成候选阈值，并在验证集检查干预率及碰撞/停滞捕获率。",
        "- 权重和阈值冻结后，需使用新参数重新运行Ours与Baseline实验。",
    ])
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def write_readme() -> None:
    text = f"""# 风险标定数据分析说明

## 运行

使用Codex工作区提供的Python运行：

```powershell
python analysis/risk_calibration/analyze.py
```

脚本只读`data/raw/navigation_experiments/navigation_experiments.xlsx`，逐步数据写入`data/processed/risk_calibration/`，统计表、图和报告写入`results/risk_calibration/`。

## 指标定义

- 数据来源：`corridorB`、`classroomB`的W列`nav_json`
- walk与turn统一以当前朝向0°为中心
- B：-45°～45°，共{len(B_ANGLES)}条射线；`distance < {CAUTION_DISTANCE}`
- L：±50°～±90°，共{len(L_ANGLES)}条射线；`distance * sin(abs(angle)) < {LATERAL_SAFETY_MARGIN}`
- O：仅walk计算；`clip((proposed_dist - (min_front_dist - {OVERSHOOT_SAFETY_MARGIN})) / {OVERSHOOT_SAFETY_MARGIN}, 0, 1)`，turn固定为0
- F：`max(P, O)`；turn因O=0，因此F=P
- `min_front_dist`由W列−5°、0°、5°射线取最小值得到
- P保留源表数值；输出同时保留`B_original`、`O_original`、`L_original`

## 固定配置

- 随机种子：{RANDOM_SEED}
- 每场景标定/验证：35/15轮
- 陷阱step：{sorted(TRAP_STEPS)}
- 主熵权样本：Baseline全部标定记录（walk与turn合并）
- 定权指标：F/B/L；walk与turn使用同一套权重与风险公式
- Bootstrap：按round重采样，{BOOTSTRAP_REPLICATES}次

## 派生字段

- `sample_id`：`scene_B_Rxx_Sxxx`
- `data_split`：`calibration`或`validation`
- `trap_injected`：step_id是否为25、50、75
- `collision_event`：collision_count是否大于0
- `B_original`/`O_original`/`L_original`：源表中的旧B/O/L
- `B`/`L`：根据nav_json和V2.2角域重算的B/L
- `O`：候选walk侵入0.4 m前方安全余量的比例
- `F`：P与O的逐步最大值，作为上层前向综合风险
- `min_frontal_dist_recomputed`：由nav_json前方−5°、0°、5°射线复算的最小距离
- `risk_entropy`：新熵权计算的风险值
- `risk_level_entropy`：新阈值标定前统一为`unassigned`
- `weight_version`：`{WEIGHT_VERSION}`
- `threshold_version`：`{THRESHOLD_VERSION}`

## 主要输出

- `correlation_*`：all/walk/turn在不同样本范围内的F/B/L Pearson与Spearman相关性
- `final_entropy_weights.csv`：全部Baseline标定步计算的统一F/B/L熵权
- `risk_distribution_summary.csv`：风险值的分组描述统计与分位数
- `risk_histogram_bins.csv`：0—1固定区间的50箱直方图计数
- `baseline_analysis_data.csv`：包含P/O/F追溯链和统一风险值的逐步分析表
"""
    (ANALYSIS_DIR / "README.md").write_text(text, encoding="utf-8")


def write_data_dictionary() -> None:
    """写出原始字段与本次分析派生字段的数据字典。"""
    rows = [
        ("all", "scene", "场景", "corridor=走廊；classroom=教室"),
        ("all", "subject", "实验组别", "A=Ours（启用校验）；B=Baseline (VLM only，关闭校验)"),
        ("all", "round", "实验轮次", "每个场景、每个组别共50轮"),
        ("step", "step_id", "轮内决策序号", "陷阱指令固定在25、50、75处注入"),
        ("step", "P", "前方逼近风险 proximity", "由前方最近距离经反向Sigmoid映射到[0,1]"),
        ("step", "B_original", "旧前方阻塞风险", "源表B；用于与V2.2定义对比"),
        ("step", "B", "前方阻塞风险 coverage/blockage", "当前-45°～45°内距离小于1.2m的19条射线占比"),
        ("step", "O_original", "旧步长超限风险", "源表O=提议步长/前方最小距离，截断至[0,1]"),
        ("step", "O", "V2.2安全余量侵入风险 overshoot", "walk为clip((候选距离-(前方最小距离-0.4))/0.4,0,1)；turn为0"),
        ("analysis", "F", "前向综合风险 front risk", "F=max(P,O)；turn因O=0而F=P"),
        ("step", "L_original", "旧侧向净空风险", "源表L；用于与V2.2定义对比"),
        ("step", "L", "侧向净空风险 lateral", "当前±50°～±90°内横向净空小于0.4m的18条射线占比"),
        ("step", "min_frontal_dist", "源日志前方最小距离", "原表N列，walk时用于核验nav_json复算值"),
        ("analysis", "min_frontal_dist_recomputed", "复算前方最小距离", "nav_json中−5°、0°、5°射线距离的最小值"),
        ("step", "risk_score", "旧风险分数", "由原经验权重计算，保留用于对照"),
        ("step", "collision_count", "单步碰撞次数", "该动作执行期间记录到的碰撞事件数量"),
        ("step", "stuck", "单步卡死标记", "0=未卡死；1=发生卡死/重生事件"),
        ("round", "decisions", "本轮决策数", "当前实验设计通常为100"),
        ("round", "collisions", "本轮碰撞总数", "对应本轮step级collision_count之和"),
        ("round", "stucks", "本轮卡死总数", "对应本轮step级stuck之和"),
        ("round", "corrections", "本轮校验修正数", "Ours中动作被缩距或否决等修改的次数"),
        ("round", "final_walk_count", "最终walk动作数", "按物理校验后的最终执行动作统计"),
        ("round", "final_turn_count", "最终turn动作数", "按物理校验后的最终执行动作统计"),
        ("analysis", "sample_id", "分析样本唯一标识", "scene_B_Rxx_Sxxx"),
        ("analysis", "data_split", "数据划分", "calibration=标定；validation=冻结参数后的验证"),
        ("analysis", "trap_injected", "陷阱注入标记", "step_id属于25、50、75时为True"),
        ("analysis", "collision_event", "是否发生碰撞", "collision_count>0时为1，否则为0"),
        ("analysis", "risk_entropy", "熵权风险分数", "使用统一F/B/L熵权计算，walk与turn公式相同"),
        ("analysis", "risk_level_entropy", "熵权风险等级", "阈值尚未标定，当前统一为unassigned"),
        ("analysis", "weight_version", "权重版本", WEIGHT_VERSION),
        ("analysis", "threshold_version", "阈值版本", THRESHOLD_VERSION),
    ]
    dictionary = pd.DataFrame(rows, columns=["level", "field", "meaning", "coding_or_definition"])
    dictionary.to_csv(TABLES / "data_dictionary.csv", index=False, encoding="utf-8-sig")
    md = [
        "# 数据字典", "",
        "这些字段用于把原始记录、标定/验证划分和参数版本绑定在一起；新增字段只写入分析结果，不修改原始工作簿。",
        "", "|层级|字段|含义|编码或定义|", "|---|---|---|---|",
    ]
    md.extend(
        f"|{row.level}|`{row.field}`|{row.meaning}|{row.coding_or_definition}|"
        for row in dictionary.itertuples(index=False)
    )
    (ANALYSIS_DIR / "data_dictionary.md").write_text("\n".join(md), encoding="utf-8")


def main() -> None:
    ensure_dirs()
    data = load_baseline()
    data, split = assign_split(data)
    split.to_csv(TABLES / "data_split.csv", index=False, encoding="utf-8-sig")

    config = {
        "source": SOURCE.name,
        "random_seed": RANDOM_SEED,
        "calibration_rounds_per_scene": CALIBRATION_ROUNDS_PER_SCENE,
        "validation_rounds_per_scene": 50 - CALIBRATION_ROUNDS_PER_SCENE,
        "trap_steps": sorted(TRAP_STEPS),
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "source_indicators": SOURCE_VARS,
        "risk_indicators": FBL_VARS,
        "B_definition": {
            "center_deg": 0,
            "angles_deg": list(B_ANGLES),
            "blocked_if_distance_lt_m": CAUTION_DISTANCE,
            "applies_to": ["walk", "turn"],
        },
        "L_definition": {
            "center_deg": 0,
            "angles_deg": list(L_ANGLES),
            "risk_if_lateral_clearance_lt_m": LATERAL_SAFETY_MARGIN,
            "lateral_clearance_formula": "distance * sin(abs(angle))",
            "applies_to": ["walk", "turn"],
        },
        "O_definition": {
            "candidate_action_fields": ["proposed_move", "proposed_dist", "proposed_ang"],
            "front_min_angles_deg": list(FRONT_MIN_ANGLES),
            "safety_margin_m": OVERSHOOT_SAFETY_MARGIN,
            "formula": "clip((proposed_dist - (min_front_dist - safety_margin)) / safety_margin, 0, 1)",
            "walk_only": True,
            "turn_value": 0.0,
        },
        "F_definition": {
            "formula": "max(P, O)",
            "turn_reduction": "O=0, therefore F=P",
            "purpose": "retain the worse front risk without double-weighting P and O",
        },
        "entropy_normalization": "none; indicators already in [0,1] and positively oriented",
        "primary_entropy_population": "all Baseline calibration steps; walk and turn pooled",
        "risk_formula": "risk_entropy = w_F*F + w_B*B + w_L*L for both walk and turn",
        "weight_version": WEIGHT_VERSION,
        "threshold_version": THRESHOLD_VERSION,
    }
    (TABLES / "analysis_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    corr_summary = save_correlations(data)
    corr_ci, weight_ci = bootstrap_primary(data)
    detailed, weights = save_entropy_results(data)
    component_summary = save_component_change_summary(data)
    data = add_fbl_risk(data, weights)
    distribution = risk_distribution_summary(data)
    distribution.to_csv(
        TABLES / "risk_distribution_summary.csv", index=False, encoding="utf-8-sig"
    )
    histogram = risk_histogram_table(data, bins=50)
    histogram.to_csv(
        TABLES / "risk_histogram_bins.csv", index=False, encoding="utf-8-sig"
    )

    primary_all = context_frames(data, "all")["pooled_calibration"]
    primary_walk = context_frames(data, "walk")["pooled_calibration"]
    primary_turn = context_frames(data, "turn")["pooled_calibration"]
    for method in ("pearson", "spearman"):
        draw_heatmap(
            corr_matrix(primary_all, FBL_VARS, method),
            f"全部动作{method.title()}相关矩阵（标定集）",
            FIGURES / f"all_{method}_heatmap.png",
        )
        draw_heatmap(
            corr_matrix(primary_walk, FBL_VARS, method),
            f"行走动作{method.title()}相关矩阵（标定集）",
            FIGURES / f"walk_{method}_heatmap.png",
        )
        draw_heatmap(
            corr_matrix(primary_turn, FBL_VARS, method),
            f"转向动作{method.title()}相关矩阵（标定集）",
            FIGURES / f"turn_{method}_heatmap.png",
        )
    draw_weight_chart(detailed, weights, FIGURES / "entropy_weight_comparison.png")
    draw_risk_histogram(histogram, FIGURES / "risk_distribution_histogram.png")

    write_report(data, split, corr_summary, corr_ci, detailed,
                 weights, weight_ci, distribution, component_summary)
    write_readme()
    write_data_dictionary()
    print("Analysis complete.")
    print("Unified FBL weights:", {k: round(v, 8) for k, v in weights.items()})


if __name__ == "__main__":
    main()
