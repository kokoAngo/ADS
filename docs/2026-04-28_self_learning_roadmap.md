# 渐进式自学习闭环 — 3 阶段路线图

**日期**: 2026-04-28
**状态**: 计划已批准, Step 1 (反响 outcome 数据通路) 待开工; **依赖反响 DB 的 ID 跟 schema, 找用户确认中**
**作者**: ops-claude

## 业务目的

让物件评价 pipeline 的预测能力**从"一次训练定终身"变成"持续自我改进"**。当前 2 个 XGBoost 模型部署后冻结, 没有从实际反响数据闭环学习。本路线图在不真上 RL 的前提下, 借用 RL 思想(reward / policy update / explore-exploit)做 3 阶段渐进改进。

## 现状

- **静态模型**: `models/xgboost_regressor_v2.pkl` (view 预测) + `models/inquiry_model.pkl` (反响数预测)
- **离线已验证**: `findings/model_timing.md` 报告了多变量 LR 模型 (AUC=0.72, Top 30% 反响率 +33×), 但**未集成**
- **反响数据闭环没建**: outcome 在另一个 Notion DB (findings 提的 `db_33e1`), API 可拉但**没接进 pipeline**
- **A/B 框架没建**: 任何 score / 模型改动都是 100% 切换, 没有灰度

## 关于真 RL 的判断

**不直接上 contextual bandit / Q-learning**。原因:
- Reward 稀疏(反响率 ~7%, 大多数 0)
- 决策空间小(写/不写 TOP, 撤/不撤)
- 探索成本高 — RL 要"故意搞砸"来学习, 业务侧不接受
- 真 RL 闭环需 100+ 天 + 大量样本才稳定

**RL 思想可以借用**: "用真实反馈持续调整, 而不是冻结。"  → 通过 3 个轻量级闭环实现。

| RL 要素 | 我们的实现 |
|---|---|
| Reward 信号 | 反响 outcome → 実反響数 字段 |
| Policy 更新 | 月次重训 / score 校准 / LR 系数 |
| Explore vs Exploit | A/B 50/50 灰度 (50% 实验, 50% 对照) |

---

## Phase 1 (1 周): 建反响 outcome 闭环 + LR post-filter

是 findings/model_timing.md 的 M1 推荐, 离线已证实有效。

1. **`scripts/sync_outcomes.py`** (新增, launchd daily 03:00):
   - 拉反响 Notion DB → 按 REINS_ID join 到 おすすめ DB
   - 写 `実反響数` 到 おすすめ
   - 输出累积训练集 `data/outcomes_history.csv`

2. **`scripts/lr_filter.py`** (新增, 离线训练):
   - 11 个 features (賃料/面積/築年/人気駅/人気沿線/間取/区効率/再投放回数)
   - 输出 `models/lr_filter.pkl` + `models/lr_filter_config.json`

3. **`process_pipeline.py` Step 8 加 LR post-filter**:
   - hash(REINS_ID) % 2 == 0 → 实验组(LR Top 30% 才进 TOP)
   - hash(REINS_ID) % 2 == 1 → 对照组(原逻辑)
   - env `LR_GROUP_AB` 切 off / 50% / 100%

4. **`scripts/ab_report.py`** (新增): 周报输出两群 KPI

## Phase 2 (1 周): 月次自动重训

5. **`scripts/retrain_monthly.py`** (新增, launchd 每月 1 号 04:00):
   - 用 `data/outcomes_history.csv` 重训 view + inquiry 模型
   - 新模型先存 `models/*.pkl.candidate`
   - hold-out validation: AUC > 旧模型 → promote (`mv .candidate .pkl`); 否则保留旧, 报警

6. **A/B 化重训上线**: 实验组用 candidate, 对照组用旧, 1 周 KPI 比较

## Phase 3 (3-5 天): score 校准层

7. **`scripts/score_calibration.py`** (新增, daily):
   - 滚动 30 天 (推薦点数, 实反响率) 配对 → isotonic / Platt scaling
   - 输出 `models/score_calibration.json`

8. **`process_pipeline.py:calculate_recommendation` 末尾加校准**:
   - calibrated score 才参与 RECOMMEND_THRESHOLD 比较

---

## 短暂效果下降风险 + Mitigations

| Phase | 风险 | Mitigation |
|---|---|---|
| Phase 1 | TOP 表写入量 -30%, 反响率 +50-100%(净反响数预计持平或略升) | 50% A/B + 1-2 周观察, 实验组净反响数 -15% 连续 2 周 → rollback |
| Phase 2 | 模型 AUC ±2-3%, 实际反响 < ±5% | candidate 不直接覆盖, AUC < 旧模型 0.05 不 promote |
| Phase 3 | 几乎无可见 dip(只调输出标度) | 若反响率 -5%+ rollback |

最坏情况: Phase 1 失败 → `LR_GROUP_AB=0` 即回到当前。outcome 数据闭环(Step 1)仍然有用 — 数据继续累积, 为后续重训打底。

## 工作量 + 顺序

```
Step 1: sync_outcomes.py (daily) — zero dip 风险
Step 2: 离线训 LR (验证 AUC ≥ 0.7)
Step 3: process_pipeline 加 LR post-filter, env 50% A/B
Step 4: 跑 1-2 周 → ab_report → promote 或 rollback
Step 5 (Phase 2): 月次重训管道, 同样 50% A/B
Step 6 (Phase 3): 校准层, 影响小, 可直接 100%
```

合计 2-3 周 (含灰度观察期)。每步独立 commit + 可单独 rollback。

## 关键文件 (要新增的)

- `scripts/sync_outcomes.py`
- `scripts/lr_filter.py`
- `scripts/ab_report.py`
- `scripts/retrain_monthly.py`
- `scripts/score_calibration.py`
- `scripts/launchd/jp.ango.syncoutcomes.plist`
- `scripts/launchd/jp.ango.retrainmonthly.plist`
- Notion おすすめ DB 加字段 `実反響数(number)`

## 复用的现有

- `scripts/train_inquiry_model.py` / `scripts/train_model_v2.py` — 月次重训直接调用
- `scripts/process_pipeline.py:calculate_recommendation` — 末尾加校准, 不改公式
- `scripts/sync_company_lists.py` — outcome 同步可参考 Notion query 模板
- `findings/model_timing.md` — LR 模型 features / 系数 / 验证指标 现成

## 数据闭环全图

```
REINS  → MAIN DB → process_pipeline → おすすめ DB ───┐
                       ↑ ↑                              ↓
                       │ │                              │ ad-script 投放
                       │ │                              │
    [models/*.pkl] ────┘ │                              ↓
       ↑                 │                          反响 outcome DB
       │                 │                              │
[retrain_monthly]        │                              │
    ↑                    │                              ↓
[outcomes_history.csv] ←───── sync_outcomes.py ←────────┘
                                       ↓
                            [score_calibration.py]
                                       ↓
                            [models/score_calibration.json] → process_pipeline
```

## Pending: 需要 user 确认的 dependency

Step 1 开工前需要:
1. 反响 DB 的完整 Notion ID
2. join key 字段(跟 REINS_ID 对应的列)
3. 反响数 / 时间戳字段名

确认后即可写 sync_outcomes.py。

## 关联

- `findings/model_timing.md` — Phase 1 LR 模型直接来自这里
- `findings/ab_test_strategy.md` — A/B 框架与 KPI 定义跟 T1 系列对齐
- `CLAUDE.md` — 整体快照
- Bridge DB 同步发送了一份给 analysis-claude (workflow type)
