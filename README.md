# 面向语义导航的物理一致性研究工程

本仓库围绕“语义导航中面向物理一致性的视觉语言模型理解机制”整理，包含 Unity 机器人导航场景、视觉语言模型控制端、物理一致性校验逻辑、实验原始数据以及风险标定分析。仓库根目录本身就是一个独立 Unity 项目，配置完成后应直接打开本目录，不需要放回或嵌入原 `gewu` 工程。

## 仓库结构

- `Assets/Manipulation/G1OP.unity`：主实验场景。
- `Assets/Manipulation/`：Unity/Python 通信、VLM 决策和物理一致性代码。
- `LocalPackages/`：项目运行所需的 ML-Agents 与 URDF Importer 本地包。
- `Packages/`、`ProjectSettings/`：可复现的 Unity 包和项目设置。
- `data/raw/navigation_experiments/`：只读原始实验工作簿。
- `data/processed/risk_calibration/`：分析脚本生成的逐步数据。
- `analysis/risk_calibration/`：风险标定脚本、测试和数据字典。
- `results/risk_calibration/`：统计表、8 张图和中文分析报告。
- `tools/`：环境检查、仓库审计和 Unity 依赖组装工具。

## 系统要求

- Git 与 Git LFS。
- Conda 或 Miniconda。
- Python 3.8（`environment.yml` 固定为 3.8.20）。
- 优先使用源工程记录的团结引擎 `1.8.4`。
- VLM 模式需要可访问阿里云 DashScope 兼容接口及有效 API Key。

`ProjectVersion.txt` 同时保留了 Unity `2022.3.62t6` 与团结引擎 `1.8.4` 的源工程记录，但本地 ML-Agents `3.0.0` 的包元数据声明 Unity `2023.2`。当前尚未验证一个纯 Unity 编辑器组合能够同时满足这些约束；若不使用团结引擎，应在副本中评估升级和包兼容性，不要直接覆盖工程。不要提交编辑器自动生成的 `Library/`、`Temp/`、`Logs/` 或 `UserSettings/`。

## 克隆与 Git LFS

```powershell
git clone git@github.com:GreatW875/physical-consistency.git
cd physical-consistency
git lfs pull
```

如果 ONNX、FBX、PNG、DLL 或 XLSX 文件内容只有几行 LFS 指针，说明尚未执行 `git lfs pull`。

## Python 环境

```powershell
conda env create -f environment.yml
conda activate physical-consistency
```

VLM 模式在当前 PowerShell 会话中设置 Key：

```powershell
Set-Item -Path Env:DASHSCOPE_API_KEY -Value "你的 DashScope API Key"
```

Key 只应保存在环境变量或本机密钥管理工具中，不要写入源码、README 或 `.env` 并提交。

## Unity 首次打开

1. 在 Unity Hub 或团结引擎中选择“打开/添加项目”，目录选择本仓库根目录。
2. 等待 Package Manager 恢复注册包，并等待全部资源完成首次导入。
3. 打开 `Assets/Manipulation/G1OP.unity`。
4. 检查 Console 是否存在包解析或脚本编译错误。
5. 可执行菜单 `Tools > Research > Export G1OP Dependencies`，生成编辑器侧依赖清单。

本仓库保留了场景、机器人模型、材质、纹理、ONNX 模型和原始 `.meta`。`.unity` 文件只保存序列化对象和 GUID 引用，并不内嵌这些资源。

## 启动实验

Unity 端必须先进入 Play 模式，使 `RobotSocketReceiver` 在 `127.0.0.1:5555` 等待连接；随后在另一个终端启动 Python：

```powershell
conda activate physical-consistency
cd Assets/Manipulation
python main.py
```

默认 `main.py` 中 `manual = False`，使用 VLM 决策并要求 `DASHSCOPE_API_KEY`。本地调试时将其改为 `manual = True`，即可人工输入动作且不需要 API Key。TCP 协议关键字保持为 `IMG_READY`、`ACTION|`、`RESET` 和 `STUCK|`。

运行前可做只读检查：

```powershell
python tools/check_environment.py
python tools/check_environment.py --manual
```

端口被占用时工具只报告状态，不会终止任何进程；若 Unity 已在运行并监听 5555，该提示属于预期情况。

## 测试

```powershell
python -m unittest discover -s tests -v
python -m unittest analysis.risk_calibration.test_analyze -v
python tools/repository_audit.py
python tools/assemble_unity_project.py --audit-only --target . --manifest dependency-manifest.json
```

测试使用伪客户端验证 VLM 边界，不会发起真实 API 请求。

## 复现实验数据分析

```powershell
python analysis/risk_calibration/analyze.py
```

脚本只读原始工作簿，固定随机种子 `20260822`，每个场景按 35 个标定轮和 15 个验证轮划分。当前统一风险定义为 `F=max(P,O)`，并使用同一组 F/B/L 熵权处理 walk 与 turn。详细规则、哈希和输出说明见 `data/README.md`。

## 数据和版本规则

- 原始数据只读；修正、清洗和派生字段必须写入 `data/processed/` 或 `results/`。
- 不使用含版本号的目录名保存最终数据；算法版本记录在表字段和 `analysis_config.json` 中。
- 大型模型、图像、插件和工作簿由 Git LFS 管理。
- `dependency-manifest.json` 记录 768 个组装资产、包 GUID、显式 GUID 恢复以及源工程遗留缺口。

## 已知验证边界

当前整理环境没有发现可调用的 Unity/Tuanjie 编辑器，因此尚未执行批处理 AssetDatabase 导入和 Play Mode 实机验证。静态依赖检查发现 16 个 GUID 在原始工程中已经缺少源资源，其中包括旧烘焙光照、NavMesh 数据及部分第三方素材残留引用；完整列表在 `dependency-manifest.json` 的 `missing_source_guids` 中。首次编辑器导入时应以 Console 和导出的依赖清单复核这些遗留项，不能把“Python 测试通过”等同于“Unity 场景已完成实机验证”。

Python 与 Unity 之间沿用了源实验代码的无长度前缀 TCP 文本协议；当前烟雾测试只覆盖顺序收发，未覆盖高频消息下的 TCP 拆包或粘包。正式长时间实验前应先完成 Play Mode 联调；若出现偶发协议解析异常，再统一为两端增加换行或长度前缀消息边界。

## 常见问题

- **必须放进 gewu 吗？** 不需要。请直接打开仓库根目录。
- **场景出现 Missing Script？** 先确认 Git LFS 已拉取、本地包目录存在并等待 Package Manager 完成；再查看 `dependency-manifest.json`。
- **Python 无法连接 Unity？** 确认 Unity 已进入 Play 模式、端口为 `127.0.0.1:5555`，并检查防火墙。
- **VLM 模式提示缺少 Key？** 在启动 `main.py` 的同一终端设置 `DASHSCOPE_API_KEY`。
- **分析结果路径在哪里？** 逐步数据在 `data/processed/risk_calibration/`，表格、图和报告在 `results/risk_calibration/`。

## 许可证与再分发

本仓库目前用于私有科研协作，尚未为原创代码添加统一开源许可证。第三方代码、模型和素材仍受各自许可约束；在公开仓库或对外发布前，必须逐项完成授权核验。详见 `THIRD_PARTY_NOTICES.md`。
