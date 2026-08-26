"""
NavMesh 雷达数据解析工具，支持任意射线密度和角度插值。
"""

def parse_nav(nav_str: str) -> dict: # 期望返回的是字典
    """解析 NavMesh 字符串为 {角度(int): 距离(float)} 字典。

    输入格式: "nav:a-90=d4.23,a-80=d2.11,...,a90=d10.00"
    """
    result = {}
    try:
        body = nav_str.split("nav:")[1] # 去除nav:
        for item in body.split(","):
            angle_part, dist_part = item.split("=")
            angle = int(angle_part[1:])     # 去掉 'a'
            dist = float(dist_part[1:])     # 去掉 'd'
            result[angle] = dist # 赋值的同时创建字典元素
    except Exception as e:
        print(f"[NavMesh解析失败] {e}，原始: {nav_str}")
    return result


def interpolate_distance(nav: dict, angle: float) -> float:
    """对任意角度进行线性插值，返回估计距离。

    当查询角度恰好在两条射线之间时，用两侧射线距离做线性插值。
    若超出已有射线范围，返回边界射线的距离。
    """
    sorted_angles = sorted(nav.keys())
    if not sorted_angles:
        return 10.0

    # 恰好命中
    if angle in nav:
        return nav[angle]

    # 超出边界
    if angle <= sorted_angles[0]:
        return nav[sorted_angles[0]]
    if angle >= sorted_angles[-1]:
        return nav[sorted_angles[-1]]

    # 找到两侧射线
    for i in range(len(sorted_angles) - 1):
        a_lo, a_hi = sorted_angles[i], sorted_angles[i + 1]
        if a_lo <= angle <= a_hi:
            t = (angle - a_lo) / (a_hi - a_lo)
            return nav[a_lo] * (1 - t) + nav[a_hi] * t

    return 10.0


def get_sector_min(nav: dict, center: float, half_width: float) -> float:
    """获取 [center - half_width, center + half_width] 扇区内的最小距离。"""
    lo = center - half_width
    hi = center + half_width
    # 在lo和hi之间查询射线
    distances = [dist for ang, dist in nav.items() if lo <= ang <= hi]
    if not distances:
        # 如果扇区内没有射线，用插值估算中心点
        return interpolate_distance(nav, center)
    return min(distances)


def get_sector_rays(nav: dict, center: float, half_width: float) -> dict:
    """获取扇区内的所有射线 {角度: 距离}。"""
    lo = center - half_width
    hi = center + half_width
    return {ang: dist for ang, dist in nav.items() if lo <= ang <= hi}


def format_target_display(target_str: str) -> str:
    """将 "target:angX=dY" 格式化为可读中文描述。

    返回示例："右前方（相对角度 +15.3°，距离 2.10m）"。
    无数据或解析失败时返回提示文字。
    """
    if not target_str or not target_str.startswith("target:"):
        return "未收到目标点数据（PathPlanner 未挂载？）"
    body = target_str[len("target:"):]
    if not body or body == "none" or "=" not in body:
        return "未收到目标点数据（PathPlanner 未挂载？）"
    try:
        ang_part, d_part = body.split("=")
        ang  = float(ang_part[3:])    # 去掉 "ang"
        dist = float(d_part[1:])      # 去掉 "d"
        side = "右方" if ang > 10 else "左方" if ang < -10 else "正前方"
        return f"{side}（相对角度 {ang:+.1f}°，距离 {dist:.2f}m）"
    except (ValueError, IndexError):
        return target_str


def get_sector_max_clearance_angle(nav: dict, center: float = 0.0,
                                   half_width: float = 90.0) -> float:
    """返回指定扇区内距障碍物最远的射线角度。

    用于高风险否决时的逃逸转向：在前方扇区中找最空旷的方向。
    若扇区内无射线，返回 center。
    """
    sector = get_sector_rays(nav, center, half_width)
    if not sector:
        return center
    return float(max(sector, key=lambda a: sector[a]))
