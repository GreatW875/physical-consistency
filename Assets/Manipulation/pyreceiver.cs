using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.IO;
using UnityEngine;
using UnityEngine.AI;

public class RobotSocketReceiver : MonoBehaviour
{
    // ─────────────────────────── 网络 ───────────────────────────
    Thread receiveThread;
    TcpListener server;
    TcpClient client;
    NetworkStream stream; // 用于数据传输

    // ─────────────────────────── 组件引用 ───────────────────────────
    private G1opAgent robotAgent;
    public Camera robotCamera;
    public NavMeshPathPlanner pathPlanner;   // 拖入 PathPlanner 物体
    private Transform realRobotCenter;
    private string customSavePath;

    // ─────────────────────────── 状态机 ───────────────────────────
    private enum RobotState
    {
        IDLE,           // 初始化尚未完成，等待网络连接
        CAPTURING,      // 截图 + 打包 NavMesh 数据，发送给 Python
        WAITING_VLM,    // 等待 Python 三轮对话完成，返回动作指令
        EXECUTING_WALK, // PID 控制直行
        EXECUTING_TURN,  // PID 控制原地转向
        EXECUTING_STOP  // 风险校验器强制停止（high_risk_veto）
    }
    private RobotState state = RobotState.IDLE; // 初始化机器人状态

    // ─────────────────────────── VLM 指令（子线程写，主线程读）───────────────────────────
    private string pendingMove  = "";   // "walk" 或 "turn" 或 "stop"
    private float  pendingDist  = 0f;   // 目标距离（米）
    private float  pendingAng   = 0f;   // 目标角度（度，正右负左）
    private bool   cmdReceived  = false; // 子线程 → 主线程的单次触发信号
    private bool   killwalk = false;

    // ─────────────────────────── PID 直行 ───────────────────────────
    private Vector3 walkStartPos;
    private float   walkTarget;

    private float walkKp = 2.0f;
    private float walkKi = 0.0f;
    private float walkKd = 0.5f;
    private float walkIntegral  = 0f;
    private float walkLastError = 0f;
    private const float WALK_DEAD_ZONE = 0.05f; // 5cm 死区

    // ─────────────────────────── PID 转向 ───────────────────────────
    private float turnStartYaw;
    private float turnTarget;

    private float turnKp = 3.0f;
    private float turnKi = 0.0f;
    private float turnKd = 0.8f;
    private float turnIntegral  = 0f;
    private float turnLastError = 0f;
    private const float TURN_DEAD_ZONE = 1.0f; // 1° 死区

    // ─────────────────────────── 重置标志 ───────────────────────────
    private bool needsReset = false;

    // ─────────────────────────── 碰撞反馈 ───────────────────────────
    private Queue<string> pendingCollisionMsgs = new(); // 缓冲碰撞消息，主线程发送

    // ─────────────────────────── 实验：场景初始位置 ───────────────────
    // 与 Python 端 EXP_SCENES 列表一一对应，索引相同
    private Vector3[] sceneStartPositions = {
        // new Vector3(0f, 0.78f, 0f),       // scene 0: corridor
        new Vector3(6f, 0.78f, 0f),      // scene 1: classroom
        // new Vector3(30f, 0.78f, 0f),      // scene 2: park
        // new Vector3(52f, 1f, 0f),         // scene 3: house
        // new Vector3(65f, 0.78f, 0f),      // scene 4: storeroom
    };
    private float[] sceneStartYaws = {
        0f,    // scene 0
        0f,    // scene 1
        0f,    // scene 2
        0f,    // scene 3
        0f,    // scene 4
    };
    private int currentSceneIdx = 0;

    // ─────────────────────────── 卡死恢复 ───────────────────────────
    private bool needsWaypointRespawn = false;

    void Start()
    {
        var movingBody = GetComponentInChildren<ArticulationBody>();
        if (movingBody != null)
        {
            realRobotCenter = movingBody.transform;
            Debug.Log($"已成功锁定移动重心: {realRobotCenter.name}");
        }
        else
        {
            realRobotCenter = this.transform;
            Debug.LogWarning("未找到物理组件，使用默认 Transform");
        }

        robotAgent     = GetComponent<G1opAgent>();
        customSavePath = Application.dataPath + "/Manipulation/robot_view.jpg";

        // 自动查找场景中的 PathPlanner 并绑定机器人重心 Transform
        pathPlanner ??= FindObjectOfType<NavMeshPathPlanner>();
        if (pathPlanner != null)
        {
            pathPlanner.robotTransform = realRobotCenter;
            Debug.Log($"[PathPlanner] 已自动绑定机器人 Transform：{realRobotCenter.name}");
        }
        else
        {
            Debug.LogWarning("[PathPlanner] 场景中未找到 NavMeshPathPlanner，目标追踪不可用");
        }

        // 动态为指定子关节挂载 CollisionReporter
        string[] collisionJoints = { "right_elbow_link", "left_elbow_link" };
        foreach (Transform t in GetComponentsInChildren<Transform>())
        {
            foreach (string jointName in collisionJoints)
            {
                if (t.name == jointName)
                {
                    var reporter = t.gameObject.AddComponent<CollisionReporter>();
                    reporter.receiver = this;
                    Debug.Log($"[碰撞挂载] CollisionReporter → {t.name}");
                }
            }
        }

        // 创建一个线程，并把ListenForData挂载在这里
        receiveThread = new Thread(new ThreadStart(ListenForData));
        receiveThread.IsBackground = true; // 使他在后台运行，不占用主线程
        receiveThread.Start(); // 启用线程
    }


    void Update()
    {
        // 碰撞反馈：排队发送所有缓冲消息
        while (pendingCollisionMsgs.Count > 0)
            SendStringToPython(pendingCollisionMsgs.Dequeue());

        // 卡死恢复：传送到最近到达的 waypoint，保留 currentTargetIdx
        if (needsWaypointRespawn)
        {
            SendStringToPython("RESET");
            ResetPID();
            RespawnAtLastWaypoint();
            robotAgent.vr = 0f;
            robotAgent.wr = 0f;
            state      = RobotState.CAPTURING;
            needsWaypointRespawn = false;
            return;
        }

        // 重置信号：优先处理
        if (needsReset)
        {
            SendStringToPython("RESET");
            ResetPID();
            ResetRobotPosition(); // 传送回当前场景初始位置
            pathPlanner?.SwitchRoute(currentSceneIdx); // 路径同步切换到当前场景对应的路线（兼顾同场景重置）
            robotAgent.vr = 0f;
            robotAgent.wr = 0f;
            state      = RobotState.CAPTURING;
            needsReset = false;
            return;
        }

        switch (state) // 主线状态机
        {
            case RobotState.IDLE:
                // 由 ListenForData 子线程在连接成功后将 state 改为 CAPTURING
                break;

            // ── 截图 + 发送 ──────────────────────────────────────────
            case RobotState.CAPTURING:
                CaptureView(); // 完成图像截取于保存
                string navData = GetFullNavMeshData();
                // 附加当前速度信息供物理一致性校验使用
                var artBody = realRobotCenter.GetComponent<ArticulationBody>();
                // artBody指向机器人的ArticulationBody组件
                float vel = (artBody != null) ? artBody.velocity.magnitude : 0f;
                // 若找到组件，则获取机器人的速度标量
                float posX = realRobotCenter.position.x;
                float posZ = realRobotCenter.position.z;
                float yaw  = realRobotCenter.eulerAngles.y;

                // 计算当前目标点的相对角度与距离
                string targetData = "target:none";
                if (pathPlanner != null)
                {
                    Vector3 tgt     = pathPlanner.GetCurrentTarget();
                    Vector3 toTarget = tgt - realRobotCenter.position;
                    toTarget.y = 0;
                    float tgtDist = toTarget.magnitude;
                    float tgtAng  = Vector3.SignedAngle(
                        realRobotCenter.forward, toTarget, Vector3.up);
                    targetData = $"target:ang{tgtAng:F1}=d{tgtDist:F2}";
                }

                SendStringToPython($"IMG_READY|{navData}|vel:{vel:F2}|pos:{posX:F2},{posZ:F2}|yaw:{yaw:F1}|{targetData}");
                state = RobotState.WAITING_VLM;
                break;

            // ── 等待 VLM 回包 ────────────────────────────────────────
            case RobotState.WAITING_VLM:
                robotAgent.vr = 0f;
                robotAgent.wr = 0f;
                if (cmdReceived)
                {
                    cmdReceived = false;
                    StartExecution(); // 等待下一步指令
                }
                break;

            // ── PID 直行 ─────────────────────────────────────────────
            case RobotState.EXECUTING_WALK:
                if (UpdateWalkPID())
                {
                    robotAgent.vr = 0f;
                    robotAgent.wr = 0f;
                    Debug.Log("[执行完成] 直行结束，进入下一轮");
                    state = RobotState.CAPTURING;
                }
                else if(killwalk)
                {
                    robotAgent.vr = 0f;
                    robotAgent.wr = 0f;
                    Debug.Log("[执行超时] 立即停止，进入下一轮");
                    state = RobotState.CAPTURING;
                    killwalk = false;
                }
                break;

            // ── PID 转向 ─────────────────────────────────────────────
            case RobotState.EXECUTING_TURN:
                if (UpdateTurnPID())
                {
                    robotAgent.vr = 0f;
                    robotAgent.wr = 0f;
                    Debug.Log("[执行完成] 转向结束，进入下一轮");
                    state = RobotState.CAPTURING;
                }
                else if(killwalk)
                {
                    robotAgent.vr = 0f;
                    robotAgent.wr = 0f;
                    Debug.Log("[执行超时] 立即停止，进入下一轮");
                    state = RobotState.CAPTURING;
                    killwalk = false;
                }
                break;
            // ── 静止 ─────────────────────────────────────────────
            case RobotState.EXECUTING_STOP:
                robotAgent.vr = 0f;
                robotAgent.wr = 0f;
                Debug.Log("[执行完成] 静止");
                state = RobotState.CAPTURING;
                break;
        }
    }


    public void CaptureView() //截图并保存到本地
    {
        if (robotCamera == null) return;

        RenderTexture rt = new RenderTexture(640, 480, 24);
        robotCamera.targetTexture = rt;
        Texture2D screenShot = new Texture2D(640, 480, TextureFormat.RGB24, false);
        robotCamera.Render();
        RenderTexture.active = rt;
        screenShot.ReadPixels(new Rect(0, 0, 640, 480), 0, 0);
        screenShot.Apply();

        robotCamera.targetTexture = null;
        RenderTexture.active      = null;
        Destroy(rt);

        File.WriteAllBytes(customSavePath, screenShot.EncodeToJPG()); // 保存于本地
    }

    // ═══════════════════════════════════════════════════════════════
    //  NavMesh 全角度扫描（-90° ~ +90°，步长 5°，共 37 点）
    // ═══════════════════════════════════════════════════════════════
    private const int NAV_STEP = 5; // 射线步长（度）

    private string GetFullNavMeshData()
    {
        NavMeshHit floorHit;
        Vector3 robotPos = realRobotCenter.position;

        // 若找不到 NavMesh，返回全 10m 占位数据
        if (!NavMesh.SamplePosition(robotPos, out floorHit, 2.0f, NavMesh.AllAreas))
        {
            Debug.LogWarning("机器人脚下没有 NavMesh！");
            var dummy = new StringBuilder("nav:");
            for (int a = -90; a <= 90; a += NAV_STEP)
            {
                if (a > -90) dummy.Append(',');
                dummy.Append($"a{a}=d10.00");
            }
            return dummy.ToString();
        }

        Vector3 floorPos = floorHit.position;
        var sb = new StringBuilder("nav:");

        for (int a = -90; a <= 90; a += NAV_STEP)
        {
            if (a > -90) sb.Append(',');

            Vector3 scanDir   = Quaternion.Euler(0, a, 0) * realRobotCenter.forward;
            Vector3 targetPos = floorPos + scanDir * 10f;

            NavMeshHit edgeHit;
            float dist = 10f; // 默认无障碍
            if (NavMesh.Raycast(floorPos, targetPos, out edgeHit, NavMesh.AllAreas))
            {
                dist = edgeHit.distance;
                Debug.DrawLine(floorPos, edgeHit.position, Color.gray, 0.05f);
            }

            sb.Append($"a{a}=d{dist:F2}");
        }

        return sb.ToString();
    }

    // ═══════════════════════════════════════════════════════════════
    //  根据 pendingMove 启动对应的 PID 执行
    // ═══════════════════════════════════════════════════════════════
    private void StartExecution()
    {
        ResetPID();

        if (pendingMove == "walk")
        {
            walkStartPos = realRobotCenter.position;
            walkTarget   = pendingDist;
            state = RobotState.EXECUTING_WALK;
            Debug.Log($"[执行] 开始直行，目标距离: {walkTarget:F2}m");
        }
        else if (pendingMove == "turn")
        {
            turnStartYaw = realRobotCenter.eulerAngles.y;
            turnTarget   = pendingAng; // 正值右转，负值左转
            state = RobotState.EXECUTING_TURN;
            Debug.Log($"[执行] 开始转向，目标角度: {turnTarget:F1}°");
        }
        else if (pendingMove == "stop")
        {
            state = RobotState.EXECUTING_STOP;
            Debug.Log($"[执行] 原地不动");
        }
        else
        {
            Debug.LogWarning($"[执行] 未知动作类型: '{pendingMove}'，跳过");
            state = RobotState.CAPTURING;
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  PID 直行控制器（每帧调用）
    //  返回 true 表示已到达目标（进入死区）
    // ═══════════════════════════════════════════════════════════════
    private bool UpdateWalkPID()
    {
        float traveled = Vector3.Distance(realRobotCenter.position, walkStartPos);
        float error    = walkTarget - traveled;

        if (Mathf.Abs(error) <= WALK_DEAD_ZONE)
            return true;

        float dt          = Time.deltaTime;
        walkIntegral     += error * dt;
        float derivative  = dt > 0f ? (error - walkLastError) / dt : 0f;
        walkLastError     = error;

        float output  = walkKp * error + walkKi * walkIntegral + walkKd * derivative;
        robotAgent.vr = Mathf.Clamp(output, 0f, 0.5f);
        robotAgent.wr = 0f;
        return false;
    }

    // ═══════════════════════════════════════════════════════════════
    //  PID 转向控制器（每帧调用）
    //  返回 true 表示已到达目标（进入死区）
    // ═══════════════════════════════════════════════════════════════
    private bool UpdateTurnPID()
    {
        float currentYaw = realRobotCenter.eulerAngles.y;
        float turned     = Mathf.DeltaAngle(turnStartYaw, currentYaw); // 处理 0/360 边界
        float error      = turnTarget - turned;

        if (Mathf.Abs(error) <= TURN_DEAD_ZONE)
            return true;

        float dt          = Time.deltaTime;
        turnIntegral     += error * dt;
        float derivative  = dt > 0f ? (error - turnLastError) / dt : 0f;
        turnLastError     = error;

        float output  = turnKp * error + turnKi * turnIntegral + turnKd * derivative;
        robotAgent.wr = Mathf.Clamp(output, -0.5f, 0.5f);
        robotAgent.vr = 0f;
        return false;
    }

    // ═══════════════════════════════════════════════════════════════
    //  重置机器人到当前场景初始位置
    // ═══════════════════════════════════════════════════════════════
    private void ResetRobotPosition()
    {
        Vector3 pos = sceneStartPositions[currentSceneIdx];
        Quaternion rot = Quaternion.Euler(0f, sceneStartYaws[currentSceneIdx], 0f);

        var artBody = realRobotCenter.GetComponent<ArticulationBody>();
        if (artBody != null)
        {
            artBody.TeleportRoot(pos, rot);
            artBody.velocity = Vector3.zero;
            artBody.angularVelocity = Vector3.zero;
        }
        else
        {
            realRobotCenter.position = pos;
            realRobotCenter.rotation = rot;
        }
        Debug.Log($"[重置位置] 场景{currentSceneIdx} → pos={pos}, yaw={sceneStartYaws[currentSceneIdx]}");
    }

    // ═══════════════════════════════════════════════════════════════
    //  卡死恢复：传送到最近到达的 waypoint（不重置 currentTargetIdx）
    // ═══════════════════════════════════════════════════════════════
    private void RespawnAtLastWaypoint()
    {
        Vector3 pos = (pathPlanner != null)
            ? pathPlanner.GetLastReachedPoint()
            : sceneStartPositions[currentSceneIdx];
        // 路径航点 Y 在地面平面（≈0），强制对齐场景站立高度 0.78f，
        // 防止根节点陷入地板后被物理引擎弹翻
        pos.y = sceneStartPositions[currentSceneIdx].y;

        // 朝向：从重生点指向下一个目标 waypoint，避免重生后还要先转身
        Vector3 nextTgt = (pathPlanner != null) ? pathPlanner.GetCurrentTarget() : Vector3.zero;
        Vector3 dir = nextTgt - pos;
        dir.y = 0f;
        float yaw;
        if (dir.sqrMagnitude > 1e-4f)
            yaw = Mathf.Atan2(dir.x, dir.z) * Mathf.Rad2Deg;
        else
            yaw = sceneStartYaws[currentSceneIdx];   // 退化时回退到场景起始 yaw
        Quaternion rot = Quaternion.Euler(0f, yaw, 0f);

        var artBody = realRobotCenter.GetComponent<ArticulationBody>();
        if (artBody != null)
        {
            artBody.TeleportRoot(pos, rot);
            artBody.velocity = Vector3.zero;
            artBody.angularVelocity = Vector3.zero;
        }
        else
        {
            realRobotCenter.position = pos;
            realRobotCenter.rotation = rot;
        }

        // 关节链 + 控制器软重置，避免人形机器人保留摔倒前姿态导致再次摔倒
        if (robotAgent != null) robotAgent.ResetToStablePose();

        Debug.Log($"[卡死恢复] 传送至最近 waypoint → pos={pos}, yaw={yaw:F1}° (面向下一目标 {nextTgt})");
    }

    // ═══════════════════════════════════════════════════════════════
    //  重置所有 PID 积分项与误差项
    // ═══════════════════════════════════════════════════════════════
    private void ResetPID()
    {
        walkIntegral  = 0f; walkLastError  = 0f;
        turnIntegral  = 0f; turnLastError  = 0f;
    }

    // ═══════════════════════════════════════════════════════════════
    //  发送字符串给 Python
    // ═══════════════════════════════════════════════════════════════
    void SendStringToPython(string msg)
    {
        if (stream != null && stream.CanWrite)
        {
            byte[] data = Encoding.UTF8.GetBytes(msg);
            stream.Write(data, 0, data.Length);
            stream.Flush();
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  外部接口：供 G1opAgent 调用的重置入口
    // ═══════════════════════════════════════════════════════════════
    public void NotifyReset()
    {
        needsReset = true;
    }

    // ═══════════════════════════════════════════════════════════════
    //  子线程：TCP 监听与接收
    // ═══════════════════════════════════════════════════════════════
    void ListenForData()
    {
        try
        {
            server = new TcpListener(IPAddress.Parse("127.0.0.1"), 5555);
            server.Start();
            Debug.Log("[Socket] 等待 Python 连接...");
            client = server.AcceptTcpClient();
            stream = client.GetStream();
            Debug.Log("[Socket] Python 已连接！");

            // 连接建立后，通知主线程开始采集
            state = RobotState.CAPTURING;

            byte[] buffer = new byte[4096];
            int bytesRead;
            while ((bytesRead = stream.Read(buffer, 0, buffer.Length)) != 0) // 若接收到信息
            {
                string rawCmd = Encoding.UTF8.GetString(buffer, 0, bytesRead).Trim(); // 读取原始数据
                Debug.Log($"[Socket收悉]: {rawCmd}");

                // 期望格式：ACTION|move:walk,dist:0.50,ang:0
                if (rawCmd.StartsWith("ACTION|"))
                {
                    ParseActionCmd(rawCmd.Substring(7)); //从第8个字符开始截取，略过“ACTION|”
                }
                else if(rawCmd.StartsWith("WARNING|") && (state == RobotState.EXECUTING_WALK || state == RobotState.EXECUTING_TURN))
                {
                    killwalk = true;
                }
                // Python 请求重置到指定场景初始位置
                // 格式: "RESET_REQUEST|scene:0"
                else if(rawCmd.StartsWith("RESET_REQUEST"))
                {
                    // 解析场景索引
                    string[] parts = rawCmd.Split('|');
                    if (parts.Length > 1)
                    {
                        string[] kv = parts[1].Split(':');
                        if (kv.Length == 2 && kv[0] == "scene")
                        {
                            int idx = int.Parse(kv[1]);
                            if (idx >= 0 && idx < sceneStartPositions.Length)
                                currentSceneIdx = idx;
                        }
                    }
                    needsReset = true;
                    Debug.Log($"[Socket] 收到重置请求，场景索引={currentSceneIdx}");
                }
                // Python 请求卡死恢复：传送回最近到达的 waypoint
                // 格式: "STUCK|scene:0"
                else if(rawCmd.StartsWith("STUCK"))
                {
                    needsWaypointRespawn = true;
                    Debug.Log("[Socket] 收到卡死恢复请求");
                }
            }
        }
        catch (Exception e)
        {
            Debug.Log("Socket Error: " + e.Message);
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  解析 Python 发来的动作指令："move:walk,dist:0.50,ang:0"
    // ═══════════════════════════════════════════════════════════════
    private void ParseActionCmd(string payload)
    {
        try
        {
            // 初始化用于接收的数据
            string move = "";
            float  dist = 0f;
            float  ang  = 0f;

            foreach (string kv in payload.Split(','))
            {
                string[] parts = kv.Split(':');
                if (parts.Length < 2) continue;
                switch (parts[0].Trim())
                {
                    case "move": move = parts[1].Trim(); break; // trim用于去除开头结尾的空白字符
                    case "dist": dist = float.Parse(parts[1]); break;
                    case "ang":  ang  = float.Parse(parts[1]); break;
                }
            }

            // 写入共享变量（Unity 主线程会在下一帧读取）
            pendingMove = move;
            pendingDist = dist;
            pendingAng  = ang;
            cmdReceived = true;

            Debug.Log($"[指令解析] move={move}, dist={dist:F2}, ang={ang:F1}");
        }
        catch (Exception e)
        {
            Debug.LogError("解析动作指令失败: " + e.Message + "  原始数据: " + payload);
        }
    }


    void OnCollisionEnter(Collision collision)
    {
        ReportCollision(collision);
    }

    public void ReportCollision(Collision collision) // 供 CollisionReporter 子节点调用
    {
        float force = collision.impulse.magnitude;
        string objName = collision.gameObject.name;
        pendingCollisionMsgs.Enqueue($"COLLISION|obj:{objName},force:{force:F2}");
        Debug.Log($"[碰撞检测] 与 {objName} 碰撞，冲量={force:F2}");
    }


    void OnApplicationQuit() // 退出时清理网络资源
    {
        if (client        != null) client.Close();
        if (server        != null) server.Stop();
        if (receiveThread != null) receiveThread.Abort();
    }
}