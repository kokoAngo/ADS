# 要取り下げ / 取下済み 流程

## 用途

TOP 表里的物件不能永久存在,也不能投放无限期。需要一个**生命周期机制**:决定何时撤掉广告 → ad-script 真正撤 → 终态保留 30 天后归档,以便 A/B 复盘。

`要取り下げ` = "等待撤下" — 我们的脚本/staff 决定要撤,但还没真撤(SUUMO 上广告还在)。
`取下済み` = "已撤下" — ad-script 撤完了,row 进入终态。两个 DB 统一用这个。

## 触发(谁能设 `要取り下げ`)

```
1. 自动 (watch_registrations 内, 见 [#2])
   ├── 登録店舗数 ≥ 10 (RETIRE_BY_LISTING_COUNT, 红海)
   └── 公開日時 距今 ≥ 3 天 (RETIRE_BY_AGE_DAYS, 投放够久)

2. 手动
   └── staff 在 Notion UI 直接选 要取り下げ
```

## ad-script 协议(关键字符串契约)

```
ad-script 看到 row.Status == "要取り下げ" 时:
  → 在 SUUMO 撤掉对应物件的广告
  → 把 Status 改为 "取下済み" (両 DB 一致)
```

> 这是跟 ad-script owner 之间的字符串契约。
> 改名要双方同步。
> ad-script owner 还在升级中, 升级前 staff 可手动改回 広告待ち 或保留 要取り下げ。

## 数据流

```
[判定阶段]
  watch_registrations.process_one()
    OR staff 手动
    → notion_update(page_id, Status="要取り下げ")

[执行阶段] (ad-script 跑,我们没源码)
  ad-script 周期扫 要取り下げ row
    → 在 SUUMO 撤广告
    → notion_update(page_id, Status="取下済み")

[归档阶段] (周日 02:00 JST)
  archive_old_recommendations.py
    → 拉两 DB 中 Status="取下済み" 且 Created time > 30 天 的 row
    → notion_archive(page_id) 软归档 (archived=true)
```

## おすすめ DB Status 8 个选项 (3-2-3 group, 2026-04-28 合并后)

| Group | Options |
|---|---|
| **To-do** | `確認待ち` (商号未確) / `広告待ち` (待广告投放) / `掲載保留` (staff 暂停) |
| **In progress** | `掲載指示済み` (广告投放中) / `要取り下げ` (要撤,等 ad-script) |
| **Complete** | `取下済み` (撤完) / `入稿失敗` (投稿到 SUUMO 失败) / `広告掲載禁止` (staff 手动永禁) |

Group 仅是 Notion UI 视觉分组,代码不依赖 group, 直接查 Status 字符串值。

## archive 软归档 (vs 真删)

`notion_archive` = `archived=true`, row 仍在 DB 里,只是默认视图看不到。优点:
- 可恢复(staff 在 Notion 视图加 "Show archived" filter)
- 适合 A/B 复盘 / 历史审计 / 出问题溯源
- Notion DB 仍然会膨胀,但不影响日常使用

## 关键代码

| 入口 / 函数 | 位置 |
|---|---|
| 自动判定 (watch_registrations 内) | `scripts/watch_registrations.py:289-326` |
| `notion_update(Status=要取り下げ)` 调用点 | `scripts/watch_registrations.py:297, 318` |
| 归档主逻辑 `archive_db()` | `scripts/archive_old_recommendations.py:110` |
| `notion_archive(page_id)` 软归档调用 | `scripts/archive_old_recommendations.py:96` |
| launchd plist | `scripts/launchd/jp.ango.archiverecommendations.plist` |

## 关键常数

| 常数 | 值 | 含义 |
|---|---|---|
| `ARCHIVE_AFTER_DAYS` | 30 | Created time 超此天数 + 终态 → 软归档 |
| `DRY_RUN` | env, 默认在 plist 里 = "0" | "1" = 只打印不真做; "0" = 真执行 |
| 自动撤退条件 | 见 [#2 listing_count_watch](02_listing_count_watch.md) | RETIRE_BY_LISTING_COUNT=10, RETIRE_BY_AGE_DAYS=3 |

## 失败模式 / Gotcha

- **公開日時 是 pipeline 写入 TOP 时设的当天日期**, 不严格等于 ad-script 实际投放日(可能差 1 天)。3 天阈值比 ad-script 的 "投放后 3 天" 略宽松。
- **公開日時 字段可能为空** (老 row 或编辑过): watch 用 `created_time` 当 fallback。
- **`要取り下げ` 之前用 `広告済` 当 終态会循环**: 早期设计 ad-script 撤后改 `広告済`,但 active list 含 `広告済` → 又被扫 → 又设 要取り下げ → 循环。修正:統一用 `取下済み` 当终态(2026-04-27)。
- **archive 用 archived=true (软删)**, 不是 DELETE 物理删除。Notion 30 天回收站后才真删。
- **dry-run 默认 ON 在脚本里 (`DRY_RUN=1`)**, 但 launchd plist 设了 `DRY_RUN=0` 真做。手动跑测试时显式 `DRY_RUN=1 ./venv/bin/python scripts/archive_old_recommendations.py`。
- **`要取り下げ` 卡住的 row**: ad-script 没升级前, row 会停在该状态 — SUUMO 上广告还在。staff 可以手动改回 広告待ち 或保持。

## 关联工作流

- [#2 登録中介数](02_listing_count_watch.md): 自动撤退判定的实施位置, 它跟本流程是同一个 launchd job
- [#1 物件评价](01_property_evaluation.md): 写 TOP 的 row 进入本生命周期,流转完后归档
- [#4 触发调度](04_trigger_scheduling.md): archive 脚本的 launchd 调度

## 时间线一例

```
Day 0   pipeline 写 row 到 TOP, Status=広告待ち, 公開日時=2026-04-28
Day 0+  ad-script 看到 → 在 SUUMO 投广告, Status=掲載指示済み
Day 1-3 watch_registrations 每 2h 扫一次, 更新 登録店舗数
Day 2   登録店舗数 涨到 10 → watch 自动设 Status=要取り下げ
Day 2+  ad-script 扫到 要取り下げ → 撤 SUUMO 广告 → 改 Status=取下済み
Day 32  archive_old_recommendations 周日跑 → 软归档该 row
```
