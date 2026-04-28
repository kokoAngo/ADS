# 物件评价流程

## 用途

REINS 上传到 Notion MAIN DB 的每一个物件,系统打出推薦点数,**够高 + 商号合规 + 不在 SUUMO 红海**就写到 TOP 表(新着物件おすすめ / 確認待ち物件)供 ad-script 投放。这是整个项目最核心的 pipeline。

## 触发

由 daemon (`workflow_trigger.py`) subprocess 启动,详见 [#4 触发调度](04_trigger_scheduling.md)。

## 数据流

```
读: Notion MAIN DB 未评分物件 (予測_view数 = 空 AND created_time > cutoff)
   + data/blacklist_companies.txt / whitelist_companies.txt / management_companies.csv
   + models/xgboost_regressor_v2.pkl + inquiry_model.pkl

算: 每个物件按 8 步处理 (Step 1-8)

写: Notion MAIN DB → 予測_view数 / 広告可否 / 予測_反響数 / 市場順位 / 広告数 / 推薦点数
   Notion TOP DB (新着物件おすすめ 或 確認待ち物件) → 满足条件的高分物件
```

## 8 步流程

| Step | 做什么 | 跳过条件 |
|---|---|---|
| 1 | view 预测 (XGBoost) | 缺租金 → return `no_rent` |
| 2 | view < 6.0 (`VIEW_THRESHOLD`) → 写 広告可="--" → return `low_view` | — |
| 3 | 商号黑/白/case 判定 → ad_status (可/不可（仲介）/物件による/確認待ち) | `不可（仲介）` → return `unallowed` (跳过 SUUMO 抓取, 节省 60 秒) |
| 4 | inquiry 反响数预测 (XGBoost) | — |
| 5 | SUUMO 沿線+駅 搜索 → 市場順位 (耗 ~30-40s) | — |
| 6 | SUUMO 沿線+駅 搜索 → 広告数 (耗 ~20-30s) | — |
| 7 | 推薦点数 = view*0.30 + inquiry*0.25 + competition*0.25 + view*0.20 + (HOT 駅 +0.3) | — |
| 8 | 写 TOP 表 (含高竞争预过滤) | 三重条件未满足 → return `success` 但不写 |

## 写 TOP 表的三重条件

`process_property` Step 8 要求**全部**满足:
1. 推薦点数 ≥ `RECOMMEND_THRESHOLD` (5.8)
2. ad_status ∈ (`可`, `確認待ち`)
3. SUUMO kwd 搜索的中介数 ≤ `MAX_COMPETITION_FOR_ENTRY` (5)

第 3 步通过 `_kwd_count_listings` 即时查 SUUMO,确认不是红海。如果 > 5 → return `high_competition` (TOP 表不写)。

## 关键代码

| 入口 / 函数 | 位置 |
|---|---|
| `main()` 拉队列 + 启 worker | `scripts/process_pipeline.py:1231` |
| `_worker()` 单 worker 主循环 | `scripts/process_pipeline.py:1180` |
| `process_property()` 单物件 8 步 | `scripts/process_pipeline.py:1028` |
| `predict_view()` / `predict_inquiry()` XGBoost 推理 | `scripts/process_pipeline.py:428, 478` |
| `check_management()` 黑/白/case 判定 | `scripts/process_pipeline.py:487` |
| `query_market_rank()` / `query_ad_count()` SUUMO 沿線抓取 | `scripts/process_pipeline.py:585, 736` |
| `calculate_recommendation()` 评分公式 (含 HOT 駅 +0.3) | `scripts/process_pipeline.py:969` |
| `_kwd_count_listings()` 高竞争预过滤 (kwd 搜索) | `scripts/process_pipeline.py:913` |
| `add_to_top_db()` 写 TOP (无大小上限) | `scripts/process_pipeline.py:1000` |

## 关键常数

| 常数 | 值 | 含义 |
|---|---|---|
| `VIEW_THRESHOLD` | 6.0 | view 低于此值 → low_view skip 后续 5/6/7 步 |
| `RECOMMEND_THRESHOLD` | 5.8 | 推薦点数低于此 → 不写 TOP |
| `MAX_COMPETITION_FOR_ENTRY` | 5 | SUUMO 中介数 > 此值 → 不写 TOP (高竞争红海跳过) |
| `WORKER_COUNT` | 3 | 并发 worker 数, 可 env override |
| `HOT_STATION_BONUS` | 0.3 | 物件最寄駅 ∈ HOT_STATIONS (Top 20) → 推薦点数加分 |
| `WEIGHTS` | view=0.3 / inquiry=0.25 / competition=0.25 / market=0.2 | 评分各项权重 |
| `CUTOFF_HOURS` | [11,15,19,23] JST | 4 个投稿截止时刻, 决定 fetch 队列范围 |

## 并发架构

3 个 worker 线程,每个独占一个 Playwright headless 浏览器:
- 共用一个 `Queue` 喂物件
- 各自走 process_property 的 8 步
- 共享锁: `_notion_lock` 防止 add_to_top_db 写冲突;`_log_lock` 防止日志交错
- 资源拦截: `context.route("**/*", _route_filter)` 阻 image/css/font/广告 (页面加载 ~3x 加速)

## 失败模式 / Gotcha

- **`不可（仲介）` Step 3 早退 (return `unallowed`)** — 反正 不可 不会进 TOP,跳过 SUUMO 抓取节省 60 秒/件。stats 里独立计数。
- **Step 5/6 失败的物件**: 市場順位/広告数 抓取失败时 `ad_count=None`,competition 用 5.0 中性值。score 仍能算出,只是 market 用了 view 占位(`market = norm_view`)。
- **HOT_STATIONS 加分覆盖 normalize**: 站名带 "駅" 后缀也匹配 (`station.rstrip("駅")`)。
- **写 TOP 前 SUUMO kwd 查询额外耗时 ~15s/件**, 但只对 score≥5.8 + ad_status 通过的物件触发, 实际只多 ~5 分钟/300 件。
- **TOP 表无大小上限** (2026-04-27 移除 MAX_RECOMMENDATIONS=20), 不再滚动替换。终态老 row 由 [#3 归档](03_retire_lifecycle.md) 处理。

## stats 状态机

`process_property` 的返回值,在 `_worker` 里聚合到 stats:

| 状态 | 含义 |
|---|---|
| `success` | 跑完 8 步, 写或没写 TOP 都算成功 |
| `low_view` | Step 2 跳过 |
| `unallowed` | Step 3 早退 (`不可（仲介）`) |
| `high_competition` | Step 8 SUUMO 中介数 > 5 跳过 |
| `no_rent` | Step 1 缺租金 |
| `error` | 异常 |

## 关联工作流

- [#4 触发调度](04_trigger_scheduling.md): 触发 main(), pipeline 是它的 subprocess
- [#5 管理会社判定](05_company_classification.md): Step 3 用的黑/白/case 名单由它维护
- [#2 登録中介数](02_listing_count_watch.md) + [#3 取下待ち/取下済](03_retire_lifecycle.md): 写 TOP 后接管 row 的生命周期
- pipeline 完成不等于物件完成 — TOP DB 里的 row 还要走 ad-script 投放 / 撤退 / 归档 的全程
