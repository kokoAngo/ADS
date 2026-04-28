# 管理会社判定流程

## 用途

每个物件有一个管理会社(`管理会社`/`商号` 字段)。同一管理会社可能跨多个物件,我们对它的"愿不愿意合作做广告"的判断应该**全公司统一**,而不是每次都重新判。

机制:维护 4 类名单,pipeline 每次评估物件时按管理会社查一次表,4 种结果决定 ad_status:

| 名单 | 文件 | 数量(2026-04-28) | 物件 ad_status |
|---|---|---|---|
| **黑名单** (不可仲介) | `data/blacklist_companies.txt` | 254 | `不可（仲介）` → Step 3 早退, 不写 TOP |
| **白名单** (可) | `data/whitelist_companies.txt` | 220 | `可` → 进 `新着物件おすすめ` |
| **case_by_case** (物件による) | `data/management_companies.csv` 中 `広告可否=='物件による'` | 41 | `物件による` → 不写 TOP(留 staff 判物件级) |
| **未知** (默认) | (以上都不在) | — | `確認待ち` → 进 おすすめ DB, Status="確認待ち" 等 staff 判定 |

## 触发(谁更新名单)

```
途径 1: staff 在 Notion 顺手填判定 (新)
  └── staff 处理 おすすめ DB 中 Status=確認待ち 的 row 时, 顺便选 会社広告可否 (可/不可/物件による)
      → 每天 01:00 JST sync_company_lists.py 同步到 .txt/.csv

途径 2: 直接编辑 .txt / .csv (老办法)
  └── 业务方手动维护, git commit 即生效
```

途径 1 是 2026-04-27 上线的零额外工作流复杂度方案 — staff 本来就要看 確認待ち物件,顺便填一下 select 即可。

## 数据流

```
[判定阶段, pipeline 启动时一次性加载]
  process_pipeline.load_company_lists() 读 .txt + .csv → 内存 set
    BLACKLIST = {...254}
    WHITELIST = {...220}
    CASE_BY_CASE = {...41}

[使用阶段, pipeline 处理每个物件]
  check_management(company_name) → 4 种返回值
    用于 process_property Step 3

[更新阶段 1, staff 在 Notion 填]
  Notion 確認待ち物件 DB 有「会社広告可否」select 列
  staff 处理物件时点选 (3 选 1)

[更新阶段 2, 同步到 .txt / .csv]
  scripts/sync_company_lists.py (每天 01:00 launchd)
    → notion_query 拉 「会社広告可否 ≠ 空」 行
    → 按管理会社聚合, 同公司多行不同判定取 last_edited_time 最近的
    → append 到 blacklist.txt / whitelist.txt 或 upsert 到 management_companies.csv
    → 不删 staff 在 Notion 的填值 (作审计记录)
```

## `check_management()` 4 种返回值

```python
def check_management(company_name):
    if not company_name:
        return "確認待ち"
    if match_company(company_name, BLACKLIST):
        return "不可（仲介）"
    if match_company(company_name, WHITELIST):
        return "可"
    if match_company(company_name, CASE_BY_CASE):
        return "物件による"
    return "確認待ち"
```

`match_company()` 用模糊匹配(去掉「（株）」「株式会社」等通用后缀比较),容错公司名表记不一致。

## 冲突处理 (sync_company_lists.py)

如果 staff 在 Notion 多个物件上对同一公司打了不同的 `会社広告可否` 判定:
1. 按 last_edited_time 排序,**取最近一次**作为准绳
2. 如果该公司之前在某 .txt 里(比如 whitelist),staff 改成「不可」 → **从 whitelist 移除** + **加到 blacklist**
3. 同步过程不删 Notion 上的 select 值(作审计)

## 关键代码

| 入口 / 函数 | 位置 |
|---|---|
| `load_company_lists()` 启动时一次性加载 | `scripts/process_pipeline.py:110` |
| `match_company()` 模糊匹配 | `scripts/process_pipeline.py:135` |
| `check_management()` 4 状态判定 | `scripts/process_pipeline.py:487` |
| `sync_company_lists.main()` 同步入口 | `scripts/sync_company_lists.py` |
| launchd plist | `scripts/launchd/jp.ango.synccompanylists.plist` |

## 关键字段

| Notion DB | 字段 | 类型 | 备注 |
|---|---|---|---|
| MAIN | `商号` | rich_text | 物件级管理会社名(Pipeline 抓的源) |
| 新着物件おすすめ | `管理会社` | rich_text | TOP 表里写的快照 |
| 新着物件おすすめ | **`会社広告可否`** | select | staff 顺手填: 可 / 不可 / 物件による (空=未填) |

`会社広告可否` 在 2026-04-28 合并后唯一 TOP DB(おすすめ)上。staff 主要看 Status=確認待ち 的 row 时填这一列。

## 失败模式 / Gotcha

- **`load_company_lists()` 在 pipeline 启动时一次性加载**, 跑过程中即使 .txt 改了也不会热更新。所以 sync_company_lists 跑完后,新名单要等下次 pipeline 启动(下次 cutoff 触发)才生效。
- **CSV 里的「物件による」和 .txt 里的内容不能重叠**: 否则 set 加载冲突。`load_company_lists` 只读 CSV 的 `広告可否=='物件による'` 行 进 CASE_BY_CASE。
- **管理会社表记不一致**: 「（株）東京リーシング」「(株)東京リーシング」「株式会社東京リーシング」会被 `match_company()` 都识别成同一家(去通用后缀比较)。新加判定也建议这种粒度。
- **staff 没填 `会社広告可否` 的物件**: 不影响 sync, 只是该物件该次没贡献名单更新。下次 staff 看到再填。
- **「物件による」实际效果**: ad_status 返回它后,pipeline Step 8 不写 TOP(只「可」/「確認待ち」会写)。其实跟 `不可` 在 TOP 表层面没差异, 区别在数据语义上 — staff 知道该公司不是黑名单,只是物件 case-by-case 判。

## 关联工作流

- [#1 物件评价](01_property_evaluation.md): Step 3 用本流程的 `check_management()` 决定 ad_status
- [#3 要取り下げ/取下済み](03_retire_lifecycle.md): staff 处理 確認待ち 物件时顺手填判定,即在该 DB 上接合两个 workflow
- [#4 触发调度](04_trigger_scheduling.md): launchd 每天 01:00 调度 sync 脚本
