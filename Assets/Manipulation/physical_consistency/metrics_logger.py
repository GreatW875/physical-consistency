"""实验指标记录器 — 统一所有 CSV 写入的唯一入口。

产出 3 张 CSV，命名规则：<粒度>_<内容>.csv
  - step_decisions.csv    每次决策一行（proposed action → 风险 → final action）
  - round_summary.csv     每轮一行（场景×实验对象×轮次的聚合指标）
  - experiment_summary.csv 每 (场景, 实验对象) 一行（跨轮平均值）

陷阱类型、动作级碰撞标签、碰撞次数、卡死和修正状态归并到step记录，
round/experiment仅从step聚合。
"""

import csv
import json
import os
import time
from .config import PhysicalConsistencyConfig


STEP_FILE       = "step_decisions.csv"
ROUND_FILE      = "round_summary.csv"
EXPERIMENT_FILE = "experiment_summary.csv"

STEP_FIELDS = [
    "timestamp", "scene", "subject", "round", "step_id",
    "proposed_move", "proposed_dist", "proposed_ang",
    "risk_score", "final_risk_score",
    "front_risk", "proximity_risk", "coverage_risk", "overshoot_risk", "lateral_risk",
    "min_frontal_dist", "trap_injected", "trap_type",
    "collision_occurred", "collision_count", "stuck",
    "was_modified", "reason",
    "final_move", "final_dist", "final_ang",
    "checker_enabled", "nav_json",
]

ROUND_FIELDS = [
    "timestamp", "scene", "task", "subject", "round",
    "checker_enabled",
    "decisions", "time_seconds",
    "collisions", "stucks", "corrections",
    "mean_risk", "max_risk", "actions_modified_rate",
    "walk_count", "turn_count", "stop_count",
]

EXPERIMENT_FIELDS = [
    "timestamp",
    "scene", "task", "subject", "checker_enabled",
    "rounds", "total_decisions", "avg_time_seconds",
    "avg_collisions", "avg_stucks", "avg_corrections",
    "avg_risk", "avg_modified_rate",
]


class MetricsLogger:
    """统一 CSV 记录器。

    使用流程：
        logger = MetricsLogger(cfg)
        for 每轮：
            logger.start_round(scene, task, subject, round_idx,
                               checker_enabled=...)
            for 每步：
                logger.log_step(proposed, result)
            stats = logger.finalize_round(time_seconds)
        logger.finalize_experiment()
    """

    def __init__(self, config: PhysicalConsistencyConfig = None):
        self.cfg = config or PhysicalConsistencyConfig()
        self.log_dir = self.cfg.LOG_DIR
        os.makedirs(self.log_dir, exist_ok=True)

        self._step_path  = os.path.join(self.log_dir, STEP_FILE)
        self._round_path = os.path.join(self.log_dir, ROUND_FILE)
        self._exp_path   = os.path.join(self.log_dir, EXPERIMENT_FILE)

        self._init_csv(self._step_path,  STEP_FIELDS)
        self._init_csv(self._round_path, ROUND_FIELDS)
        self._init_csv(self._exp_path,   EXPERIMENT_FIELDS)

        self._round_records = []   # 跨轮累积，供 finalize_experiment 聚合
        self._reset_round_buffers()

    # ── 内部工具 ──────────────────────────────────────────────
    @staticmethod
    def _init_csv(path: str, fields: list):
        if os.path.exists(path):
            with open(path, "r", newline="", encoding="utf-8-sig") as f:
                existing_fields = next(csv.reader(f), [])
            if existing_fields != fields:
                raise RuntimeError(
                    f"日志表头不兼容：{path}。"
                    "请先备份并移走旧日志，再开始新实验。"
                )
            return

        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()

    @staticmethod
    def _append(path: str, fields: list, row: dict):
        with open(path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fields).writerow(row)

    def _reset_round_buffers(self):
        self._cur_scene   = ""
        self._cur_task    = ""
        self._cur_subject = ""
        self._cur_round   = 0
        self._cur_checker_enabled = True
        self._step_id = 0
        self._step_buf = []
        self._pending_step = None  # 等待 commit_step 补 collision_count
        self._step_cap = None                    # 本轮 step 行数上限，None=不限制
        self._round_active = False

    # ── 轮次生命周期 ──────────────────────────────────────────
    def start_round(self, scene: str, task: str, subject: str, round_idx: int,
                    checker_enabled: bool, step_cap=None):
        """开始新一轮：记录元数据并清空当前轮缓冲。"""
        self._reset_round_buffers()
        self._cur_scene   = scene
        self._cur_task    = task
        self._cur_subject = subject
        self._cur_round   = round_idx
        self._cur_checker_enabled = checker_enabled
        self._step_cap = step_cap   # None → 不限制
        self._round_active = True

    def log_step(self, proposed_action: dict, result, nav_data: dict = None,
                 trap_injected: bool = False, trap_type: str = "none"):
        """记录一次决策步骤。result 为 CheckResult。"""
        # 兜底硬上限：超过本轮 step_cap 不再记录，防止主循环逻辑漏洞写入额外行
        if self._step_cap is not None and len(self._step_buf) >= self._step_cap:
            print(f"[logger] step_cap={self._step_cap} 已满，丢弃该次 log_step")
            return
        step_id = self._step_id
        self._step_id += 1

        comp = result.risk_components or {}
        record = {
            "timestamp":     time.strftime("%Y-%m-%d %H:%M:%S"),
            "scene":         self._cur_scene,
            "subject":       self._cur_subject,
            "round":         self._cur_round,
            "step_id":       step_id,
            "proposed_move": proposed_action.get("move", ""),
            "proposed_dist": round(float(proposed_action.get("dist", 0)), 4),
            "proposed_ang":  round(float(proposed_action.get("ang", 0)), 2),
            "risk_score":    round(result.risk_score, 4),
            "final_risk_score": round(
                result.modified_risk_info["risk_score"]
                if result.modified_risk_info is not None
                else result.risk_score, 4),
            "front_risk":       comp.get("front", 0),
            "proximity_risk":   comp.get("proximity", 0),
            "coverage_risk":    comp.get("coverage", 0),
            "overshoot_risk":   comp.get("overshoot", 0),
            "lateral_risk":     comp.get("lateral", 0),
            "min_frontal_dist": comp.get("min_frontal_dist", 0),
            "trap_injected": int(bool(trap_injected)),
            "trap_type": (
                str(trap_type or "unknown") if trap_injected else "none"
            ),
            "collision_occurred": 0,  # commit_step中由collision_count派生
            "collision_count": 0,  # 由 commit_step() 在动作完成时回填
            "stuck": 0,            # 由 commit_step() 在动作完成时回填（触发卡死重生→1）
            "was_modified":  not result.approved,
            "reason":        result.reason,
            "final_move":    result.modified_action.get("move", ""),
            "final_dist":    round(float(result.modified_action.get("dist", 0)), 4),
            "final_ang":     round(float(result.modified_action.get("ang", 0)), 2),
            "checker_enabled": self._cur_checker_enabled,
            "nav_json": json.dumps(
                {str(k): v for k, v in (nav_data or {}).items()},
                ensure_ascii=False,
            ),
        }
        self._step_buf.append(record)
        # 暂不写盘：等待 commit_step() 补充 collision_count 后再 _append
        self._pending_step = record

        # 控制台摘要
        emoji = "🟢" if result.approved else ("🔴" if result.reason == "high_risk_veto" else "🟡")
        print(
            f"[物理校验] {emoji} risk={result.risk_score:.3f} "
            f"{'PASS' if result.approved else result.reason} | "
            f"{proposed_action.get('move','')}→{result.modified_action.get('move','')} "
            f"min_front={comp.get('min_frontal_dist','?')}m"
        )
        if result.modified_risk_info is not None:
            mr = result.modified_risk_info
            m  = result.modified_action
            dist_str = (f"dist={m.get('dist',0):.2f}m"
                        if m.get("move") == "walk"
                        else f"ang={m.get('ang',0):.1f}°")
            print(
                f"[修正校验] risk={mr['risk_score']:.3f} | "
                f"{m.get('move','')} {dist_str} "
                f"min_front={mr.get('min_frontal_dist','?')}m"
            )

    def commit_step(self, collision_count: int, is_stuck: bool = False):
        """动作完成后调用，把动作级碰撞标签、碰撞数和卡死状态写入step。

        若没有 pending step（如首次 IMG_READY、轮次刚开始）则空操作。"""
        if self._pending_step is None:
            return
        collision_count = int(collision_count)
        self._pending_step["collision_occurred"] = int(collision_count > 0)
        self._pending_step["collision_count"] = collision_count
        self._pending_step["stuck"] = int(bool(is_stuck))
        self._append(self._step_path, STEP_FIELDS, self._pending_step)
        self._pending_step = None

    def finalize_round_if_active(self, time_seconds: float):
        """中断时调用：若当前有未结束的轮次且已有决策数据，则强制汇总。"""
        if self._round_active and self._step_buf:
            print("[中断] 正在汇总未完成的轮次数据...")
            self.finalize_round(time_seconds)

    def finalize_round(self, time_seconds: float) -> dict:
        """汇总当前轮指标并写入 round_summary.csv；返回字典。"""
        self._round_active = False
        steps = self._step_buf
        total = len(steps)

        risks = [s["risk_score"] for s in steps]
        modified_count = sum(1 for s in steps if s["was_modified"])
        walk_count = sum(1 for s in steps if s["final_move"] == "walk")
        turn_count = sum(1 for s in steps if s["final_move"] == "turn")
        stop_count = sum(1 for s in steps if s["final_move"] == "stop")
        collision_count = sum(int(s["collision_count"]) for s in steps)
        stuck_count = sum(int(s["stuck"]) for s in steps)
        correction_count = modified_count

        stats = {
            "timestamp":       time.strftime("%Y-%m-%d %H:%M:%S"),
            "scene":           self._cur_scene,
            "task":            self._cur_task,
            "subject":         self._cur_subject,
            "round":           self._cur_round,
            "checker_enabled": self._cur_checker_enabled,
            "decisions":       total,
            "time_seconds":    round(time_seconds, 2),
            "collisions":      collision_count,
            "stucks":          stuck_count,
            "corrections":     correction_count,
            "mean_risk":       round(sum(risks) / total, 4) if total else 0.0,
            "max_risk":        round(max(risks), 4) if risks else 0.0,
            "actions_modified_rate": round(modified_count / total, 4) if total else 0.0,
            "walk_count":      walk_count,
            "turn_count":      turn_count,
            "stop_count":      stop_count,
        }

        self._append(self._round_path, ROUND_FIELDS, stats)
        self._round_records.append(stats)

        print(
            f"\n{'='*60}\n"
            f"[轮次汇总] {stats['scene']} · {stats['subject']} · 轮{stats['round']}\n"
            f"  步数={total} 时间={stats['time_seconds']}s  "
            f"walk/turn/stop={walk_count}/{turn_count}/{stop_count}\n"
            f"  风险: 均值={stats['mean_risk']:.3f} 最大={stats['max_risk']:.3f}\n"
            f"  修正率={stats['actions_modified_rate']:.1%}  "
            f"碰撞={collision_count}  卡死={stuck_count}  矫正={correction_count}\n"
            f"  checker={'ON' if self._cur_checker_enabled else 'OFF'}\n"
            f"{'='*60}\n"
        )
        return stats

    def finalize_experiment(self):
        """实验结束：按 (scene, subject) 聚合所有轮次，写 experiment_summary.csv。"""
        if not self._round_records:
            print("[实验] 无轮次数据可汇总")
            return

        groups = {}
        for r in self._round_records:
            groups.setdefault((r["scene"], r["subject"]), []).append(r)

        run_ts = time.strftime("%Y-%m-%d %H:%M:%S")
        for (scene, subject), rounds in groups.items():
            n = len(rounds)
            head = rounds[0]
            summary = {
                "timestamp":        run_ts,
                "scene":            scene,
                "task":             head["task"],
                "subject":          subject,
                "checker_enabled":  head["checker_enabled"],
                "rounds":           n,
                "total_decisions":  sum(r["decisions"]    for r in rounds),
                "avg_time_seconds": round(sum(r["time_seconds"] for r in rounds) / n, 2),
                "avg_collisions":   round(sum(r["collisions"]   for r in rounds) / n, 2),
                "avg_stucks":       round(sum(r["stucks"]       for r in rounds) / n, 2),
                "avg_corrections":  round(sum(r["corrections"]  for r in rounds) / n, 2),
                "avg_risk":         round(sum(r["mean_risk"]    for r in rounds) / n, 4),
                "avg_modified_rate": round(
                    sum(r["actions_modified_rate"] for r in rounds) / n, 4),
            }
            self._append(self._exp_path, EXPERIMENT_FIELDS, summary)

        print(f"[实验] 汇总已写入: {self._exp_path} "
              f"（{len(self._round_records)} 轮 → {len(groups)} 组）")
