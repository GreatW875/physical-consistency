import random


class TrapInjector:

    def __init__(self, base_interval: int = 25):
        self.base_interval = base_interval
        self._step_counter = 0
        self._pending_walk = False   # charge_obstacle 第二步标记
        self._pending_dist = 2.0

    def advance(self):
        self._step_counter += 1

    def should_inject(self) -> bool:
        return self._pending_walk or self._step_counter >= self.base_interval

    def reset(self):
        """回合重置时调用，清除待发状态并重新计时。"""
        self._step_counter = 0
        self._pending_walk = False

    # ── 陷阱函数 ────────────────────────────────────────────────────

    def trap_speed_surge(self, action: dict) -> dict:
        """
        惯性失控：将当前 walk 距离乘以 3。
        若当前动作为 turn，则以默认距离 1.0m 发出突增 walk。
        """
        base_dist = float(action.get("dist", 0)) if action.get("move") == "walk" else 1.0
        trap = {"move": "walk", "dist": round(base_dist * 3, 2), "ang": 0}
        print(f"[陷阱] 速度突增：{action} → dist×3={trap['dist']}m")
        return trap

    def trap_charge_obstacle(self, nav: dict) -> dict:
        """
        冲向障碍物（两步状态机）：
          第一步：转向最近障碍物所在角度
          第二步：直行冲入（距离 = 障碍距离 × 1.2）
        """
        if self._pending_walk:
            trap = {"move": "walk", "dist": self._pending_dist, "ang": 0}
            print(f"[陷阱] 冲向障碍物（直行 {self._pending_dist}m）")
            self._pending_walk = False
            return trap

        nearest_ang  = min(nav, key=lambda a: nav[a])
        nearest_dist = nav[nearest_ang]
        self._pending_dist = round(nearest_dist * 1.2, 2)
        self._pending_walk = True
        trap = {"move": "turn", "dist": 0, "ang": float(nearest_ang)}
        print(f"[陷阱] 冲向障碍物（转向 {nearest_ang:+d}°，障碍距离 {nearest_dist:.2f}m）")
        return trap

    # ── 统一注入入口 ────────────────────────────────────────────────

    def inject(self, action: dict, nav: dict) -> tuple:
        """返回（注入后的动作，实际采用的陷阱类型）。"""
        # charge_obstacle 第二步时强制继续，不重新随机
        if self._pending_walk:
            trap_type = "charge_obstacle"
        else:
            trap_type = random.choice(["speed_surge", "charge_obstacle"])

        if trap_type == "speed_surge":
            result = self.trap_speed_surge(action)
        elif trap_type == "charge_obstacle":
            result = self.trap_charge_obstacle(nav)
        else:
            result = action

        self._step_counter = 0

        return result, trap_type
