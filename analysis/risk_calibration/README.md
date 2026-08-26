# 风险标定数据分析说明

## 运行

使用Codex工作区提供的Python运行：

```powershell
python analysis/risk_calibration/analyze.py
```

脚本只读`data/raw/navigation_experiments/navigation_experiments.xlsx`，逐步数据写入`data/processed/risk_calibration/`，统计表、图和报告写入`results/risk_calibration/`。

## 指标定义

- 数据来源：`corridorB`、`classroomB`的W列`nav_json`
- walk与turn统一以当前朝向0°为中心
- B：-45°～45°，共19条射线；`distance < 1.2`
- L：±50°～±90°，共18条射线；`distance * sin(abs(angle)) < 0.4`
- O：仅walk计算；`clip((proposed_dist - (min_front_dist - 0.4)) / 0.4, 0, 1)`，turn固定为0
- F：`max(P, O)`；turn因O=0，因此F=P
- `min_front_dist`由W列−5°、0°、5°射线取最小值得到
- P保留源表数值；输出同时保留`B_original`、`O_original`、`L_original`

## 固定配置

- 随机种子：20260822
- 每场景标定/验证：35/15轮
- 陷阱step：[25, 50, 75]
- 主熵权样本：Baseline全部标定记录（walk与turn合并）
- 定权指标：F/B/L；walk与turn使用同一套权重与风险公式
- Bootstrap：按round重采样，500次

## 派生字段

- `sample_id`：`scene_B_Rxx_Sxxx`
- `data_split`：`calibration`或`validation`
- `trap_injected`：step_id是否为25、50、75
- `collision_event`：collision_count是否大于0
- `B_original`/`O_original`/`L_original`：源表中的旧B/O/L
- `B`/`L`：根据nav_json和V2.2角域重算的B/L
- `O`：候选walk侵入0.4 m前方安全余量的比例
- `F`：P与O的逐步最大值，作为上层前向综合风险
- `min_frontal_dist_recomputed`：由nav_json前方−5°、0°、5°射线复算的最小距离
- `risk_entropy`：新熵权计算的风险值
- `risk_level_entropy`：新阈值标定前统一为`unassigned`
- `weight_version`：`entropy_cal35_seed20260822_fmax_po_unified_fbl`
- `threshold_version`：`pending_quantile_validation`

## 主要输出

- `correlation_*`：all/walk/turn在不同样本范围内的F/B/L Pearson与Spearman相关性
- `final_entropy_weights.csv`：全部Baseline标定步计算的统一F/B/L熵权
- `risk_distribution_summary.csv`：风险值的分组描述统计与分位数
- `risk_histogram_bins.csv`：0—1固定区间的50箱直方图计数
- `baseline_analysis_data.csv`：包含P/O/F追溯链和统一风险值的逐步分析表
