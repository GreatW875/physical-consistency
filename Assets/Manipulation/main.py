import socket
import time

from physical_consistency import PhysicalConsistencyChecker, PhysicalConsistencyConfig
from physical_consistency.metrics_logger import MetricsLogger
from physical_consistency.nav_parser import parse_nav
from vlm_agent import AutonomousController, format_action_cmd
from trap_injector import TrapInjector

WALK_TIMEOUT = 8.0  # 秒

# ═══════════════════════════════════════════════════════════════════
#  实验配置
# ═══════════════════════════════════════════════════════════════════
EXP_MAX_DECISIONS    = 100   # 每轮实验的最大决策次数
EXP_ROUNDS_PER_SCENE = 42    # 每个实验对象在每个场景的实验轮数

# 场景列表（名称 + 任务描述），通过索引切换
EXP_SCENES = [
    # {"name": "corridor",  "task": "直线走廊避障"},
    {"name": "classroom", "task": "教室内避障"},
]

# 实验对象
EXP_SUBJECTS = [
    # {"name": "A", "checker_enabled": True},    # VLM ＋ 校验
    {"name": "B", "checker_enabled": False},   # 仅 VLM
]

manual = False  # 是否启用人工输入指令（替代 VLM 输出），用于调试
# manual = True  # 是否启用人工输入指令（替代 VLM 输出），用于调试


# ═══════════════════════════════════════════════════════════════════
#  解析 IMG_READY 数据
# ═══════════════════════════════════════════════════════════════════
def parse_img_ready(data: str):
    """返回 (nav_str, target_str)"""
    nav_str = target_str = ""
    for p in data.split("|")[1:]:
        if p.startswith("nav:"):    nav_str    = p
        if p.startswith("target:"): target_str = p
    return nav_str, target_str


# ═══════════════════════════════════════════════════════════════════
#  主函数：实验循环
#  三层嵌套：场景 → 实验对象 → 轮次
# ═══════════════════════════════════════════════════════════════════
def run_main():
    cfg        = PhysicalConsistencyConfig()
    logger     = MetricsLogger(cfg)
    checker    = PhysicalConsistencyChecker(cfg, logger=logger)
    ctrl       = AutonomousController()
    trap       = TrapInjector(base_interval=25)
    start_time = None  # 保证 finally 块中始终可访问

    # ── 连接 Unity ────────────────────────────────────────────────
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    connected = False
    while not connected:
        try:
            print("正在尝试连接到 Unity (127.0.0.1:5555)...")
            s.connect(('127.0.0.1', 5555))
            connected = True
            print("成功连接到 Unity!")
        except Exception:
            print("等待 Unity 服务端启动中...")
            time.sleep(2)

    try:
        # ── 遍历场景 ─────────────────────────────────────────────
        for scene_idx, scene in enumerate(EXP_SCENES):
            scene_name = scene["name"]
            scene_task = scene["task"]
            print(f"\n{'#'*60}")
            print(f"[场景 {scene_idx+1}/{len(EXP_SCENES)}] {scene_name} — {scene_task}")
            print(f"{'#'*60}")

            # ── 遍历实验对象 ─────────────────────────────────────
            for subject in EXP_SUBJECTS:
                subj_name   = subject["name"]
                cfg.CHECKER_ENABLED = subject["checker_enabled"]

                mode_desc = "VLM＋校验" if cfg.CHECKER_ENABLED else "VLM"
                print(f"\n{'='*60}")
                print(f"[实验对象] {subj_name} ({mode_desc})")
                print(f"{'='*60}")

                # ── 遍历轮次 ─────────────────────────────────────
                for round_idx in range(EXP_ROUNDS_PER_SCENE):
                    print(f"\n--- {subj_name} 第 {round_idx+1}/"
                          f"{EXP_ROUNDS_PER_SCENE} 轮 ---")

                    ctrl.reset_memory()
                    trap.reset()
                    logger.start_round(
                        scene=scene_name, task=scene_task,
                        subject=subj_name, round_idx=round_idx + 1,
                        checker_enabled=cfg.CHECKER_ENABLED,
                        step_cap=EXP_MAX_DECISIONS,
                    )

                    decision_count          = 0
                    start_time              = None
                    last_walk_time          = None
                    waiting_action_done     = False
                    action_collision_count  = 0
                    action_timed_out        = False
                    consecutive_failed_actions = 0
                    pending_followup        = None  # 高风险转向后的跟进动作

                    # 请求 Unity 重置到当前场景的初始位置
                    s.sendall(f"RESET_REQUEST|scene:{scene_idx}".encode('utf-8'))
                    waiting_for_reset = True

                    buf = ""
                    # ── 执行单步决策 ─────────────────────────────────────
                    while decision_count < EXP_MAX_DECISIONS or waiting_action_done:
                        s.settimeout(1.0)
                        try:
                            chunk = s.recv(4096).decode('utf-8')
                        except socket.timeout:
                            if (last_walk_time is not None
                                    and time.time() - last_walk_time > WALK_TIMEOUT):
                                print(f"[超时保护] 直行超过 {WALK_TIMEOUT}s，强制停止")
                                s.sendall(
                                    "WARNING|move:stop,dist:0.00,ang:0.00"
                                    .encode('utf-8'))
                                last_walk_time = None
                                action_timed_out = True
                            continue
                        except Exception as e:
                            print(f"recv 异常: {e}")
                            raise

                        if not chunk: # 没接收到
                            raise ConnectionError("Unity 断开连接")

                        buf  += chunk
                        data  = buf.strip()
                        buf   = ""

                        # ── 超时检查（收到任何消息时均执行）────────
                        if (last_walk_time is not None
                                and time.time() - last_walk_time > WALK_TIMEOUT):
                            print(f"[超时保护] 直行超过 {WALK_TIMEOUT}s，强制停止")
                            s.sendall(
                                "WARNING|move:stop,dist:0.00,ang:0.00"
                                .encode('utf-8'))
                            last_walk_time = None
                            action_timed_out = True

                        # ── 碰撞 ─────────────────────────────────
                        if "COLLISION" in data:
                            action_collision_count += 1
                            print(f"[碰撞] 当前动作累计 {action_collision_count} 次")
                            continue

                        # ── 重置确认 ─────────────────────────────
                        if "RESET" in data:
                            ctrl.reset_memory()
                            last_walk_time = None
                            waiting_for_reset = False
                            continue

                        # ── 图像就绪 ─────────────────────────────
                        if "IMG_READY" in data:
                            if waiting_for_reset:
                                continue  # 忽略重置前的旧帧

                            waiting_action_done = False  # 上一动作已确认完成

                            # ── 先判 STUCK，再提交 step（以便回填 stuck 字段）──
                            failed = action_timed_out or action_collision_count >= 3
                            next_cfa = consecutive_failed_actions + 1 if failed else 0
                            is_stuck_now = next_cfa >= 3

                            # ── 提交上一步 step 行（补 collision_count / stuck）────
                            logger.commit_step(action_collision_count, is_stuck=is_stuck_now)

                            consecutive_failed_actions = next_cfa
                            if is_stuck_now:
                                print(f"[STUCK] 连续 {consecutive_failed_actions} 个动作失败，"
                                      f"重生至上一个途径点")
                                s.sendall(
                                    f"STUCK|scene:{scene_idx}".encode('utf-8'))
                                waiting_for_reset = True
                                consecutive_failed_actions = 0
                                action_collision_count = 0
                                action_timed_out = False
                                last_walk_time = None
                                continue

                            action_timed_out = False

                            if decision_count >= EXP_MAX_DECISIONS:
                                print("[等待动作完成] 收到 IMG_READY，延时 1s 后结束本轮")
                                print("\n")
                                time.sleep(1.0)
                                continue  # 两个循环条件均 False → 退出

                            last_walk_time = None
                            print("[动作完成] 收到 IMG_READY，稳定后开始决策...")
                            print("\n")
                            time.sleep(1.0)  # 动作结束/轮次开始后稳定 1s 再决策
                            if start_time is None:
                                start_time = time.time()

                            nav_str, target_str = parse_img_ready(data)
                            nav = parse_nav(nav_str)

                            # ── 生成动作 ─────────────────────────
                            if pending_followup is not None:
                                print(f"[高风险跟进] 转向后前进 → {pending_followup}")
                                action = pending_followup
                                pending_followup = None
                            elif manual:
                                while True:
                                    user_input = input("[手动模式]输入指令：")
                                    try:
                                        move, value = user_input.split(" ", 1)
                                        if move == "walk":
                                            action = {"move": "walk", "dist": float(value), "ang": 0}
                                        elif move == "turn":
                                            action = {"move": "turn", "dist": 0, "ang": float(value)}
                                        else:
                                            print("无效指令，请重新输入")
                                            continue
                                        break
                                    except Exception as e:
                                        print(f"指令解析失败: {e}，请重新输入")
                            else:
                                print("[VLM模式] 开始动作决策")
                                action = ctrl.get_action(target_str=target_str)

                            trap_injected = False
                            trap_type = "none"
                            if trap.should_inject():
                                action, trap_type = trap.inject(action, nav)
                                trap_injected = True
                            trap.advance()

                            result = checker.check(
                                action,
                                nav,
                                trap_injected=trap_injected,
                                trap_type=trap_type,
                            )
                            action = result.modified_action
                            cmd = format_action_cmd(action)

                            if trap_injected:
                                m = result.modified_action
                                action_desc = (f"move={m['move']}, "
                                               f"dist={m.get('dist', 0):.2f}, "
                                               f"ang={m.get('ang', 0):.1f}")
                                if result.approved:
                                    print(f"[陷阱校验] 未被拦截，执行 → {action_desc}")
                                else:
                                    print(f"[陷阱校验] 已拦截/修正，"
                                          f"原因={result.reason} → {action_desc}")

                            # 记录高风险转向后的跟进动作
                            pending_followup = result.followup_action

                            s.sendall(cmd.encode('utf-8'))
                            waiting_action_done = True  # 动作已发出，等待 Unity 确认完成
                            action_collision_count = 0
                            decision_count += 1

                            if action.get("move") in {"walk", "turn"}:
                                last_walk_time = time.time()

                            print(f"[进度] {subj_name} 轮{round_idx+1}: "
                                  f"{decision_count}/{EXP_MAX_DECISIONS}")
                            continue

                    # ── 本轮结束 ──────────────────────────────────
                    logger.commit_step(action_collision_count)  # 兜底提交最后一步
                    elapsed = time.time() - (start_time or time.time())
                    logger.finalize_round(elapsed)

        print(f"\n{'='*60}")
        print("[实验完成] 所有场景、所有实验对象已完成")
        print(f"{'='*60}")

    except Exception as e:
        print(f"[实验中断] {e}")

    finally:
        elapsed = time.time() - (start_time or time.time())
        logger.finalize_round_if_active(elapsed)
        logger.finalize_experiment()
        s.close()
        print("Socket 已关闭")


if __name__ == "__main__":
    run_main()
