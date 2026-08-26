# 第三方组件与再分发说明

本文件记录仓库内可识别的第三方代码、插件、模型与素材证据，不构成法律意见。仓库当前按私有科研项目管理；转为公开仓库、发布数据集或分发二进制前，必须由项目负责人逐项确认许可证、署名、素材来源和模型使用条件。

| 组件名称 | 路径 | 用途 | 许可证文件 | 再分发状态 |
|---|---|---|---|---|
| Unity ML-Agents | `LocalPackages/com.unity.ml-agents/` | Agent、Behavior Parameters、Decision Requester 与推理通信 | `LocalPackages/com.unity.ml-agents/LICENSE.md` | 已找到许可证文件 |
| Unity URDF Importer | `LocalPackages/com.unity.robotics.urdf-importer/` | URDF 机器人组件与网格导入支持 | 包根未保留独立许可证；内部组件许可证见下两项 | 需要人工确认 |
| UnityMeshImporter | `LocalPackages/com.unity.robotics.urdf-importer/Runtime/UnityMeshImporter/` | URDF 网格导入 | `Runtime/UnityMeshImporter/LICENSE.md` | 已找到许可证文件 |
| AssimpNet | `LocalPackages/com.unity.robotics.urdf-importer/Runtime/UnityMeshImporter/Plugins/AssimpNet/` | 三维模型解析 | `License_AssimpNet.txt` | 已找到许可证文件 |
| VHACD | `LocalPackages/com.unity.robotics.urdf-importer/Runtime/VHACD/` | 凸分解支持 | `LICENSE.MD` | 已找到许可证文件 |
| Unity AI Navigation | `Packages/manifest.json`（注册包） | NavMesh Surface 与 Modifier | 由 Unity Package Manager 获取，仓库内无许可证副本 | 需要人工确认 |
| Unity Sentis | `Packages/manifest.json`（注册包） | ONNX 模型导入与推理后端 | 由 Unity Package Manager 获取，仓库内无许可证副本 | 需要人工确认 |
| Universal Render Pipeline | `Packages/manifest.json`（注册包） | 场景渲染、Shader 与材质 | 由 Unity Package Manager 获取，仓库内无许可证副本 | 需要人工确认 |
| Unity UI | `Packages/manifest.json`（注册包） | Unity UI 运行支持 | 由 Unity Package Manager 获取，仓库内无许可证副本 | 需要人工确认 |
| G1 URDF、网格与预制体 | `Assets/urdf/g1_description/` | G1 机器人结构和外观 | 保留资源中未找到统一许可证文件 | 仓库私有 |
| 室内场景与 Flower 素材 | `Assets/Flower/`、`Assets/LLM/TinkerLLMNavigation/` | 教室、走廊、家具、冰箱和装饰资源 | 保留资源中未找到统一许可证文件 | 仓库私有 |
| Simple Nature Pack 资源 | `Assets/Navigation/SimpleNaturePack/` | 导航场景材质与环境资源 | 保留资源中未找到统一许可证文件 | 仓库私有 |
| ONNX 策略模型 | `Assets/Competition/`、`Assets/Dance/`、`Assets/onnx/` | 机器人策略推理 | 保留资源中未找到独立模型许可证 | 仓库私有 |

清理原则：运行所需第三方 README、LICENSE、CHANGELOG 和对应 `.meta` 不作为“重复说明文件”删除。未知授权的素材只在私有仓库中保留，不应据此推断为可公开再分发。
