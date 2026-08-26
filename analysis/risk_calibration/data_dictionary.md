# 数据字典

这些字段用于把原始记录、标定/验证划分和参数版本绑定在一起；新增字段只写入分析结果，不修改原始工作簿。

|层级|字段|含义|编码或定义|
|---|---|---|---|
|all|`scene`|场景|corridor=走廊；classroom=教室|
|all|`subject`|实验组别|A=Ours（启用校验）；B=Baseline (VLM only，关闭校验)|
|all|`round`|实验轮次|每个场景、每个组别共50轮|
|step|`step_id`|轮内决策序号|陷阱指令固定在25、50、75处注入|
|step|`P`|前方逼近风险 proximity|由前方最近距离经反向Sigmoid映射到[0,1]|
|step|`B_original`|旧前方阻塞风险|源表B；用于与V2.2定义对比|
|step|`B`|前方阻塞风险 coverage/blockage|当前-45°～45°内距离小于1.2m的19条射线占比|
|step|`O_original`|旧步长超限风险|源表O=提议步长/前方最小距离，截断至[0,1]|
|step|`O`|V2.2安全余量侵入风险 overshoot|walk为clip((候选距离-(前方最小距离-0.4))/0.4,0,1)；turn为0|
|analysis|`F`|前向综合风险 front risk|F=max(P,O)；turn因O=0而F=P|
|step|`L_original`|旧侧向净空风险|源表L；用于与V2.2定义对比|
|step|`L`|侧向净空风险 lateral|当前±50°～±90°内横向净空小于0.4m的18条射线占比|
|step|`min_frontal_dist`|源日志前方最小距离|原表N列，walk时用于核验nav_json复算值|
|analysis|`min_frontal_dist_recomputed`|复算前方最小距离|nav_json中−5°、0°、5°射线距离的最小值|
|step|`risk_score`|旧风险分数|由原经验权重计算，保留用于对照|
|step|`collision_count`|单步碰撞次数|该动作执行期间记录到的碰撞事件数量|
|step|`stuck`|单步卡死标记|0=未卡死；1=发生卡死/重生事件|
|round|`decisions`|本轮决策数|当前实验设计通常为100|
|round|`collisions`|本轮碰撞总数|对应本轮step级collision_count之和|
|round|`stucks`|本轮卡死总数|对应本轮step级stuck之和|
|round|`corrections`|本轮校验修正数|Ours中动作被缩距或否决等修改的次数|
|round|`final_walk_count`|最终walk动作数|按物理校验后的最终执行动作统计|
|round|`final_turn_count`|最终turn动作数|按物理校验后的最终执行动作统计|
|analysis|`sample_id`|分析样本唯一标识|scene_B_Rxx_Sxxx|
|analysis|`data_split`|数据划分|calibration=标定；validation=冻结参数后的验证|
|analysis|`trap_injected`|陷阱注入标记|step_id属于25、50、75时为True|
|analysis|`collision_event`|是否发生碰撞|collision_count>0时为1，否则为0|
|analysis|`risk_entropy`|熵权风险分数|使用统一F/B/L熵权计算，walk与turn公式相同|
|analysis|`risk_level_entropy`|熵权风险等级|阈值尚未标定，当前统一为unassigned|
|analysis|`weight_version`|权重版本|entropy_cal35_seed20260822_fmax_po_unified_fbl|
|analysis|`threshold_version`|阈值版本|pending_quantile_validation|