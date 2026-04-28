# 查看登录中介数流程

## 用途

TOP 表里每个物件,定期(每 2 小时)在 SUUMO 上搜一次,看**已经有几家中介公开了同房间**。这个数字写到「登録店舗数」字段。数字大 = 红海,我们投广告效果差;数字小 = 蓝海,继续投有意义。

也作为自动撤退判定的输入([#3 取下待ち](03_retire_lifecycle.md)):中介数 ≥ 10 → 自动设 取下待ち。

## 触发

```
launchd: jp.ango.watchregistrations
  └── 每天 12 次, 每 2 小时 (*:30, 即 0:30/2:30/.../22:30 JST)
```

`*:30` 错峰,避开整点 cutoff 时刻 (11/15/19/23:00) 的 pipeline 启动。

每次跑约 35 分钟(138 件 × ~15s/件), 在 2h 间隔内能完成。

## 数据流

```
读: Notion TOP DB (おすすめ + 確認待ち) 中 active row
   + Notion MAIN DB 按 REINS_ID 取 rent / area
   + SUUMO 实时网页 (kwd 搜索)

算: kwd 搜建物名 → 过滤 cassette by rent±0.5万 / area±2㎡

写: Notion TOP DB 该 row 的 「登録店舗数」字段 (number)
   如果触发撤退条件 → 同时设 Status="取下待ち"
```

## active_statuses 设计

```python
TARGET_DATABASES = [
    ("新着物件おすすめ", ..., ["広告待ち", "掲載指示済み", "掲載保留", "要確認"]),
    ("確認待ち物件",     ..., ["広告待ち", "広告済"]),
]
```

跳过:
- 终态: `取下済` (生命周期完成) / `取下待ち` (即将撤,无意义再监视)
- staff "暂存" 的中间态本来想跳过,但 2026-04-28 决策放弃 — staff 改成 `掲載保留` 后还是要扫,否则红海物件永远逃过自动撤退判定。

おすすめ 的 active 包含 `掲載保留` / `要確認`,確認待ち 不含(因为 確認待ち 没有这些 staff 暂停态)。

## SUUMO kwd 搜索关键技术点

- **必须用 form 字段 `kwd`**(不是 URL `?kw=`)。我们 fill input + Enter 提交。URL 参数被 SUUMO 忽略。
- **结果页 URL 切换**: `https://suumo.jp/jj/chintai/ichiran/FR301FC001/?...&kwd=...` → 提交后跳到 `https://suumo.jp/jj/common/ichiran/JJ901FC001/`。`page.wait_for_url("**/JJ901FC001/**")`。
- **物件名清洗**(`normalize_building_name`):
  - 去全角/半角括号内容(`（マハロテラス）` 这种读み)
  - 去尾部「XXX号室」(全/半角数字 / 漢数字)
  - **保留全角空格**(SUUMO 要靠空格分词,去了反而搜不到)
  - 多余空格压缩

## 撤退条件 (在 process_one 内同时检查)

```python
RETIRE_BY_LISTING_COUNT = 10   # 中介数 ≥ 此值 → 取下待ち
RETIRE_BY_AGE_DAYS = 3         # 公開日時 距今 ≥ 此天数 → 取下待ち (无视有无反响)
```

即:**watch_registrations 既更新 登録店舗数,也顺手判 取下待ち**。判定后 Status 改成 `取下待ち`,下次扫描就跳过(active filter 不含 取下待ち)。

## 关键代码

| 入口 / 函数 | 位置 |
|---|---|
| `main()` 启动入口 | `scripts/watch_registrations.py:375` |
| `collect_items()` 拉 active row + 公開日時 | `scripts/watch_registrations.py:333` |
| `process_one()` 单物件: age 判 → SUUMO 查 → count 判 → 写 | `scripts/watch_registrations.py:289` |
| `count_suumo_listings()` kwd 搜索 + rent/area 过滤 | `scripts/watch_registrations.py:212` |
| `normalize_building_name()` 物件名清洗 | `scripts/watch_registrations.py:166` |
| `_parse_cassette()` 从 cassette 文本提 rent/area | `scripts/watch_registrations.py:203` |
| launchd plist | `scripts/launchd/jp.ango.watchregistrations.plist` |

## 关键常数

| 常数 | 值 | 含义 |
|---|---|---|
| `RENT_TOL_MAN` | 0.5 | 万円, kwd 搜索后过滤 cassette 的 rent 容差 |
| `AREA_TOL_M2` | 2.0 | m², 同上 area 容差 |
| `RETIRE_BY_LISTING_COUNT` | 10 | 登録店舗数 ≥ 此值 → 自动设 取下待ち |
| `RETIRE_BY_AGE_DAYS` | 3 | 公開日時 距今 ≥ 此值 → 自动设 取下待ち |

## 失败模式 / Gotcha

- **物件名过于通用** (如 `藤ビル`): SUUMO kwd 搜出 20000+ 件,rent/area 过滤后 0 匹配 → 写 0 (`not_found`)
- **罗马数字 / 特殊字符** (如 `クリオ ラモード学芸大学Ⅱ`): SUUMO 搜不到 → 0
- **物件名带号室会拉低匹配率**: 必须 normalize 去掉号室后缀
- **跟 process_pipeline 共用 SUUMO kwd 搜索代码**: 两边独立维护(`scripts/process_pipeline.py:885+` 和这里),改一边不影响另一边。SUUMO 页面变化时双方都要改。
- **status filter 改了语义**: staff 把 row 改成 `掲載保留` 不再代表"我手动暂停别动",我们仍会监视并可能自动撤(2026-04-28 起)。

## 关联工作流

- [#1 物件评价](01_property_evaluation.md): 那边的 kwd 搜索代码是这里的复制副本
- [#3 取下待ち/取下済](03_retire_lifecycle.md): 这里设 取下待ち,那边的 ad-script 接管撤广告
- [#4 触发调度](04_trigger_scheduling.md): launchd 调度本服务
