using System.Collections.Generic;
using UnityEngine;
using UnityEngine.AI;

[System.Serializable]
public class RouteConfig
{
    public string routeName = "Experiment1";
    public Vector3 startPoint = Vector3.zero;
    public Vector3 endPoint   = new Vector3(5, 0, 5);
    public List<Vector3> waypoints = new List<Vector3>();
}

/// <summary>
/// 独立封装的 NavMesh 多路径管理组件。
/// 不控制机器人移动，只维护目标点序列并检测抵达。
/// 外部通过 GetCurrentTarget() 获取当前目标，附加到 IMG_READY 发给 Python。
/// </summary>
public class NavMeshPathPlanner : MonoBehaviour
{
    [Header("路径配置（每条对应一个实验区域）")]
    public List<RouteConfig> routes = new List<RouteConfig>
    {
        new() { routeName = "Experiment1" },
    };

    [Header("当前激活路径索引")]
    public int activeRouteIndex = 0;

    [Header("机器人 Transform（拖入机器人根节点或重心节点）")]
    public Transform robotTransform;

    [Header("到达判定半径（米）")]
    public float arrivalRadius = 1.0f;

    [Header("可视化")]
    public Color currentTargetColor = Color.magenta;
    public Color visitedColor       = Color.gray;
    public Color pendingColor       = Color.yellow;
    public Color pathColor          = Color.cyan;
    public Color inactiveRouteColor = new Color(0.5f, 0.5f, 0.5f, 0.2f);
    public float markerRadius       = 0.3f;
    public bool  showLabels         = true;

    private List<Vector3> loop             = new List<Vector3>();
    private int           currentTargetIdx = 0;
    private List<Vector3> navPathCorners   = new List<Vector3>();  // 仅用于可视化

    // ——— 公开 API ———

    /// <summary>返回当前目标点（供 pyreceiver 附加到 IMG_READY）</summary>
    public Vector3 GetCurrentTarget() =>
        loop.Count > 0 ? loop[currentTargetIdx] : Vector3.zero;

    /// <summary>返回最近到达的途径点。currentTargetIdx 指向"下一个目标"，
    /// 减 1 即"最近到达"；若尚未到达任何 waypoint，则回退到 loop[0]（startPoint）。
    /// 用于卡死恢复时的重生位置。</summary>
    public Vector3 GetLastReachedPoint() =>
        loop.Count == 0 ? Vector3.zero
                        : loop[Mathf.Max(0, currentTargetIdx - 1)];

    /// <summary>按索引切换路径</summary>
    public void SwitchRoute(int index)
    {
        if (index < 0 || index >= routes.Count)
        {
            Debug.LogWarning($"[PathPlanner] 路径索引 {index} 超出范围");
            return;
        }
        activeRouteIndex = index;
        ActivateRoute(index);
    }

    /// <summary>按名称切换路径</summary>
    public void SwitchRoute(string routeName)
    {
        int idx = routes.FindIndex(r => r.routeName == routeName);
        if (idx < 0) { Debug.LogWarning($"[PathPlanner] 未找到路径：{routeName}"); return; }
        SwitchRoute(idx);
    }

    /// <summary>返回当前激活路径的完整点列表</summary>
    public List<Vector3> GetLoop() => new List<Vector3>(loop);

    /// <summary>重置当前路径追踪，回到第一个点（传送时调用）</summary>
    public void ResetCurrentRoute()
    {
        currentTargetIdx = 0;
        RefreshNavPath();
        Debug.Log($"[PathPlanner] 路径追踪已重置 → {PointLabel(0)}");
    }

    // ——— 生命周期 ———

    void Start()
    {
        robotTransform ??= transform;
        ActivateRoute(activeRouteIndex);
    }

    void Update()
    {
        if (loop.Count == 0 || robotTransform == null) return;

        Vector3 robotPos = robotTransform.position;
        Vector3 target   = loop[currentTargetIdx];

        // 只比较水平距离（忽略 Y 轴高度差）
        float dist = Vector2.Distance(
            new Vector2(robotPos.x, robotPos.z),
            new Vector2(target.x,   target.z));

        if (dist <= arrivalRadius)
            OnArrived();
    }

    // ——— 内部逻辑 ———

    private void ActivateRoute(int index)
    {
        currentTargetIdx = 0;
        BuildLoop(routes[index]);
        RefreshNavPath();
        Debug.Log($"[PathPlanner] 激活路径：{routes[index].routeName}（{loop.Count} 个节点）");
    }

    private void BuildLoop(RouteConfig r)
    {
        loop.Clear();
        loop.Add(r.startPoint);
        foreach (var wp in r.waypoints) loop.Add(wp);
        loop.Add(r.endPoint);
    }

    private void OnArrived()
    {
        Debug.Log($"[PathPlanner] 抵达 {PointLabel(currentTargetIdx)}：{loop[currentTargetIdx]}");
        currentTargetIdx = (currentTargetIdx + 1) % loop.Count;
        RefreshNavPath();
    }

    /// <summary>用 NavMesh.CalculatePath 计算当前段路径，仅供 Gizmo 绘制</summary>
    private void RefreshNavPath()
    {
        navPathCorners.Clear();
        if (loop.Count == 0) return;
        Vector3 from = Application.isPlaying && robotTransform != null
            ? robotTransform.position
            : loop[currentTargetIdx == 0 ? 0 : currentTargetIdx - 1];
        NavMeshPath path = new NavMeshPath();
        if (NavMesh.CalculatePath(from, loop[currentTargetIdx], NavMesh.AllAreas, path))
            navPathCorners.AddRange(path.corners);
    }

    // ——— 可视化 ———

    void OnDrawGizmos()
    {
        if (routes == null || routes.Count == 0) return;
        if (!Application.isPlaying) BuildLoop(routes[activeRouteIndex]);

        // 非激活路径：灰色轮廓
        for (int r = 0; r < routes.Count; r++)
        {
            if (r == activeRouteIndex) continue;
            DrawRouteOutline(routes[r]);
        }

        DrawActiveRoute();
        DrawNavPath();
        DrawArrivalRadius();
    }

    private void DrawRouteOutline(RouteConfig r)
    {
        var pts = new List<Vector3> { r.startPoint };
        foreach (var wp in r.waypoints) pts.Add(wp);
        pts.Add(r.endPoint);

        Gizmos.color = inactiveRouteColor;
        for (int i = 0; i < pts.Count; i++)
        {
            Gizmos.DrawSphere(pts[i], markerRadius * 0.5f);
            Gizmos.DrawLine(pts[i], pts[(i + 1) % pts.Count]);
        }
    }

    private void DrawActiveRoute()
    {
        for (int i = 0; i < loop.Count; i++)
        {
            bool isCurrent = Application.isPlaying && i == currentTargetIdx;
            bool isVisited = Application.isPlaying && i < currentTargetIdx;

            Color c = isCurrent ? currentTargetColor
                    : isVisited ? visitedColor
                    : pendingColor;

            Gizmos.color = c;
            Gizmos.DrawSphere(loop[i], isCurrent ? markerRadius * 1.3f : markerRadius * 0.9f);
            Gizmos.DrawLine(loop[i], loop[i] + Vector3.up * 0.8f);

#if UNITY_EDITOR
            if (showLabels)
                UnityEditor.Handles.Label(loop[i] + Vector3.up * 1.1f,
                    $"[{routes[activeRouteIndex].routeName}] {PointLabel(i)}");
#endif
        }

        // 预设轨迹环形连线
        Gizmos.color = new Color(pendingColor.r, pendingColor.g, pendingColor.b, 0.35f);
        for (int i = 0; i < loop.Count; i++)
            Gizmos.DrawLine(loop[i], loop[(i + 1) % loop.Count]);
    }

    private void DrawNavPath()
    {
        if (navPathCorners.Count < 2) return;
        Gizmos.color = pathColor;
        for (int i = 0; i < navPathCorners.Count - 1; i++)
            Gizmos.DrawLine(navPathCorners[i], navPathCorners[i + 1]);
        Gizmos.color = new Color(pathColor.r, pathColor.g, pathColor.b, 0.5f);
        foreach (var c in navPathCorners)
            Gizmos.DrawSphere(c, markerRadius * 0.25f);
    }

    private void DrawArrivalRadius()
    {
        if (!Application.isPlaying || loop.Count == 0) return;
        Gizmos.color = new Color(currentTargetColor.r, currentTargetColor.g,
                                  currentTargetColor.b, 0.15f);
        Gizmos.DrawSphere(loop[currentTargetIdx], arrivalRadius);
    }

    private string PointLabel(int idx)
    {
        if (idx == 0)               return "START";
        if (idx == loop.Count - 1)  return "END";
        return $"WP {idx}";
    }
}
