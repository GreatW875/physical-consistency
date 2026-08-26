import base64
import json
import os
import time

from openai import OpenAI
from physical_consistency import PhysicalConsistencyConfig
from physical_consistency.nav_parser import format_target_display

ALIYUN_API_KEY  = "YOUR_ALIYUN_API_KEY"  # 替换为你的阿里云 API Key
ALIYUN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
IMG_PATH = os.path.expanduser(
    "~/Unity-RL-Playground-main/gewu/Assets/Manipulation/robot_view.jpg"
)

_client = OpenAI(base_url=ALIYUN_BASE_URL, api_key=ALIYUN_API_KEY)

MAX_WALK_DIST = PhysicalConsistencyConfig.MAX_WALK_DIST
MAX_TURN_ANG  = PhysicalConsistencyConfig.MAX_TURN_ANG

SYSTEM_PROMPT = f"""
    你是一个双足机器人导航专家。你在室内场景中行进，绝对不能与任何障碍物（包括墙）发生接触。

    你可以使用机器人正前方的实时 640×480 图像，以及当前导航目标点的相对方位描述。

    决策规则：
    - 每次只能执行一种动作：【walk】或【turn】，不可同时执行。
    - 前进距离每次不超过 {MAX_WALK_DIST} 米。
    - 转向角度每次不超过 {MAX_TURN_ANG} 度，且不要小于 10 度。正值右转，负值左转。
    - 目标点出现在正前方的时候，优先直行。
    - 目标点相对角度过大时，可以大幅度转向。
    """


def _read_image_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def format_action_cmd(action: dict) -> str:
    """将 action dict 格式化为 Unity 指令字符串"""
    return (
        f"ACTION|"
        f"move:{action['move']},"
        f"dist:{action['dist']:.2f},"
        f"ang:{action['ang']:.1f}"
    )


# ═══════════════════════════════════════════════════════════════════
#  对话控制器
# ═══════════════════════════════════════════════════════════════════
class AutonomousController:
    def __init__(self):
        self.image_path = IMG_PATH
        self.reset_memory()

    def reset_memory(self):
        print("--- 清空记忆 ---")
        self.memory = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    def _call_vlm(self, messages: list) -> str:
        attempt = 0
        while True:
            try:
                response = _client.chat.completions.create(
                    model="qwen-vl-max",
                    messages=messages,
                    response_format={"type": "json_object"}
                )
                if attempt > 0:
                    print(f"[VLM] 网络已恢复（第 {attempt + 1} 次尝试成功）")
                return response.choices[0].message.content
            except KeyboardInterrupt:
                raise
            except Exception as e:
                attempt += 1
                wait = min(30, 2 ** min(attempt, 5))
                print(f"[VLM 异常] {type(e).__name__}: {e}")
                print(f"[VLM] {wait}s 后重试（第 {attempt} 次，按 Ctrl+C 中止）...")
                time.sleep(wait)

    def _target_hint(self, target_str: str) -> str:
        """将 target 字符串转为 prompt 里的目标方位描述，失败时回退到默认提示。"""
        desc = format_target_display(target_str)
        if desc.startswith("未收到"):
            return "（未提供目标点信息）"
        print(f"[目标方位] {desc}")
        return desc

    def _append_memory(self, user_prompt: str, assistant_raw: str):
        """向 memory 追加一轮对话，并按上限截断（保留 system + 最近 10 条）。"""
        self.memory.append({"role": "user",      "content": user_prompt})
        self.memory.append({"role": "assistant", "content": assistant_raw})
        if len(self.memory) > 11:
            self.memory = [self.memory[0]] + self.memory[-10:]

    @staticmethod
    def _parse_action_json(raw: str) -> dict:
        """从 VLM 的 JSON 返回里提取 action 并夹取到合法范围。"""
        action = json.loads(raw).get("action", {})
        move = action.get("move")
        dist = min(float(action.get("dist", 0)), MAX_WALK_DIST)
        ang  = max(min(float(action.get("ang", 0)), MAX_TURN_ANG), -MAX_TURN_ANG)
        return {"move": move, "dist": dist, "ang": ang}


    # ── 单轮：根据第一视角图像与目标方位决策（存入记忆）───────
    def round_decide_action(self, img_b64: str, target_str: str = "") -> dict:
        target_desc = self._target_hint(target_str)

        prompt = f"""
            当前导航目标点位于：{target_desc}
            观察机器人第一视角图像，避开障碍物的同时尽量朝目标点方向移动。

            每次只能执行一种动作：【walk】或【turn】，不可同时执行。只输出 JSON，格式如下：
            {{
            "action": {{
                "move": "walk",
                "dist": "X",
                "ang":  "0"
            }}
            }}
            或
            {{
            "action": {{
                "move": "turn",
                "dist": "0",
                "ang":  "X"
            }}
            }}
            """
        user_msg = {
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": prompt}
            ]
        }
        raw = self._call_vlm(self.memory + [user_msg])
        action = self._parse_action_json(raw)
        print(f"[决策] move={action['move']}, "
              f"dist={action['dist']:.2f}, ang={action['ang']:.1f}")
        self._append_memory(prompt, raw)
        return action

    # ── 单轮VLM决策 ──────────────────────────────────────────
    def get_action(self, target_str: str = ""):
        img_b64 = _read_image_b64(self.image_path)
        action = self.round_decide_action(img_b64, target_str)
        return action
