# 物件反響予測 工作流程说明

## 术语对照表

| 用户请求 | Notion列名 | 数据来源 | 说明 |
|----------|------------|----------|------|
| 预测view数 | 予測_view数 | SUUMO反響数 | SUUMO平台上的物件浏览/反响数据 |
| 预测反响数 | 予測_反響数 | JDS問合せ数 | JDS系统中的咨询数（中介用语） |

## 工作流程

### 1. 预测view数（SUUMO反響数）

**用户请求**: "预测view数" / "预测SUUMO反响"

**操作步骤**:
1. 查询Notion中`予測_view数`为空的物件
2. 运行预测脚本 `scripts/predict_and_update_notion_v2.py`
3. 更新Notion的`予測_view数`列

**数据来源**: SUUMO平台反響数据

---

### 2. 预测反响数（JDS問合せ数）

**用户请求**: "预测反响数" / "预测問合せ数"

**操作步骤**:
1. 查询Notion中`予測_反響数`为空的物件
2. 运行预测脚本（需要指定JDS模型）
3. 更新Notion的`予測_反響数`列

**数据来源**: JDS系统 問合せ数（咨询数）

---

## 相关脚本

| 脚本 | 用途 |
|------|------|
| `scripts/predict_and_update_notion_v2.py` | 预测view数（SUUMO） |
| `scripts/fix_missing_ad.py` | 计算広告数 |
| `scripts/suumo_rank_analysis.py` | SUUMO市场排名分析 |

## 备注

- 中介习惯用"反響数"指代JDS的問合せ数
- "view数"通常指SUUMO平台的数据
- 两个预测模型基于不同数据源训练，预测目标不同
