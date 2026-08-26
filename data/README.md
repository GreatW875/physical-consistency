# 实验数据说明

## 原始数据

- 路径：`data/raw/navigation_experiments/navigation_experiments.xlsx`
- SHA-256：`8D18D3D1ED8E9FD06C43582A974CD7518122AA77573BEE6083C75BED35B448C0`
- 工作表：`experiment`、`round`、`corridorA`、`classroomA`、`corridorB`、`classroomB`
- 正式风险标定使用 `corridorB` 和 `classroomB`，共 10,000 条 Baseline step。

原始工作簿是只读事实源。分析脚本不得覆盖、格式化或保存该文件；任何清洗、修正、拆分和派生字段都必须写入 `data/processed/` 或 `results/`。迁移前后的 SHA-256 完全一致。

## 目录语义

- `raw/navigation_experiments/`：采集得到、不可修改的原始实验数据。
- `processed/risk_calibration/`：可由脚本重建的逐步分析数据。
- `../results/risk_calibration/tables/`：统计汇总、权重、数据划分和配置。
- `../results/risk_calibration/figures/`：可视化结果。
- `../results/risk_calibration/report.md`：中文分析报告。

目录名表达数据用途，不使用算法版本号。算法版本写入 `weight_version`、`threshold_version` 和 `analysis_config.json`，以便长期追踪而不反复移动数据。

## 指标定义

- `P`：前方逼近风险（proximity），保留源表数值。
- `O`：walk 候选距离侵入 0.4 m 前方安全余量的比例；turn 固定为 0。
- `F`：前向综合风险，定义为 `max(P,O)`；turn 因 `O=0` 而退化为 `F=P`。
- `B`：当前朝向 −45° 至 45° 内，距离小于 1.2 m 的 19 条射线占比。
- `L`：当前朝向两侧 ±50° 至 ±90° 内，横向净空小于 0.4 m 的 18 条射线占比。
- `risk_entropy`：统一公式 `w_F·F + w_B·B + w_L·L`，walk 和 turn 使用同一组权重。

最终熵权为：

| 指标 | 权重 |
|---|---:|
| F | 0.3991891447710898 |
| B | 0.2190297888228466 |
| L | 0.3817810664060635 |

权重之和为 1。熵权反映样本差异性，不代表因果贡献或理论最优性。

## 固定划分与可复现参数

- 随机种子：`20260822`
- 每场景轮数：50
- 每场景标定/验证：35/15 轮
- 陷阱 step：25、50、75
- Bootstrap：按 round 重采样 500 次
- 阈值状态：尚未冻结，`threshold_version=pending_quantile_validation`

## 重建命令

在仓库根目录运行：

```powershell
conda activate physical-consistency
python analysis/risk_calibration/analyze.py
```

重建后应满足：

- `baseline_analysis_data.csv` 为 10,000 行；
- `final_entropy_weights.csv` 为 3 行；
- `data_split.csv` 为 100 行；
- `risk_distribution_summary.csv` 为 9 行；
- `results/risk_calibration/figures/` 中有 8 张 PNG；
- 原始工作簿 SHA-256 保持不变。

字段级含义见 `analysis/risk_calibration/data_dictionary.md`，完整参数见 `results/risk_calibration/tables/analysis_config.json`。
