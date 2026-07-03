# Fango ADS — 项目快照(给未来的 Claude 看)

> 上位俯瞰 (2 プロジェクト境界 / Notion DB 全体マップ / Status 状態遷移 / launchd 全表) → [`../CLAUDE.md`](../CLAUDE.md)
> 子系统深度文档 → [`skills/README.md`](skills/README.md) (5 个 workflow 各自一份)
> 撤退判定の別プロジェクト → [`../PVMonitor/CLAUDE.md`](../PVMonitor/CLAUDE.md)

## 一段话项目说明

REINS(物件流通)上的物件每天 4 个时段(11/15/19/23 JST)集中投到 Notion。系统对每个物件做评分(2026-06-11〜 **view 预测 + 加分区域** のみ: `推薦点数 = view(10頭打ち) + HOT駅/区 bonus`)→ 高分 & **2 階以上** & **築年が死亡帯(21-27年)外** の物件を TOP DB 写入 → 在 SUUMO 上自动追踪有多少中介公开了同一房间(竞争监视)。

业务流:
```
REINS → Notion (MAIN DB)
       ↓ 评估 pipeline
       → 新着物件おすすめ DB / 確認待ち物件 DB
              ↓ 独立监视服务(每 1h, 6:20-22:20 JST)
              → 「登録店舗数」字段
```

## 服务架构

```
workflow_trigger.py  (daemon, nohup 启动)
  ├── 4 种触发: trigger flag 文件 / Notion 10min 轮询 / cutoff 到达 / sleep 恢复
  └── 每次触发 → subprocess spawn process_pipeline.py
                  └── 3 worker 并发 + 各自 Playwright headless + 资源拦截

launchd: jp.ango.watchregistrations  (~/Library/LaunchAgents/)
  └── 每天 17 次 (6:20, 7:20, …, 22:20 JST、毎時·深夜帯停止) → watch_registrations.py
       登録店舗数を SUUMO kwd 検索で数えて Notion に書込 +
       中介数 ≥ RETIRE_BY_LISTING_COUNT (現 13、2026-05-18 再導入) で Status=要取り下げ 自動セット。
       PV ベース撤退 (PVMonitor) と並列 OR 条件で動作。

launchd: jp.ango.synccompanylists
  └── 每天 1 次 (01:00 JST) → sync_company_lists.py
       从「確認待ち物件 DB」的「会社広告可否」列同步 staff 判定 → blacklist/whitelist/case_by_case

launchd: jp.ango.archiverecommendations
  └── 每周日 1 次 (02:00 JST) → archive_old_recommendations.py
       两个 TOP DB 里 Status 终态 + Created time > 30 天 的 row 软归档(archived=true)

launchd: jp.ango.syncoutcomes
  └── 每天 1 次 (03:00 JST) → sync_outcomes.py
       広告管理 DB(db_defb)の反響数 を REINS_ID 単位で集計し
       おすすめ.実反響数 へ反映 + data/outcomes_history.csv に追記
       (self-learning-loop Phase 1 Step 1 — LR/重訓の訓練データ通路)
```

> **撤退判定は二系統で並列 OR**: (a) PV ベース動的判定 — PVMonitor (`../PVMonitor/CLAUDE.md`) が毎日 04:00 に実行、(b) 中介数ベース閾値判定 — ADS `watch_registrations.py` が中介数 ≥ 13 で `Status=要取り下げ` を自動セット (2026-05-18 再導入)。どちらか先に発火した方が撤退。

## 关键文件(只看这几个就够)

| 文件 | 角色 |
|---|---|
| `scripts/workflow_trigger.py` | daemon, 监听+触发 |
| `scripts/process_pipeline.py` | 评估 pipeline 主体(全部业务逻辑在这) |
| `scripts/watch_registrations.py` | 独立的中介数监视(SUUMO kwd 搜索) |
| `scripts/sync_company_lists.py` | 把 staff 在 Notion 的判定同步到 blacklist/whitelist/case_by_case |
| `scripts/archive_old_recommendations.py` | 终态 + 30 天后软归档 TOP DB row(防膨胀) |
| `scripts/sync_outcomes.py` | 広告管理 DB → おすすめ.実反響数 + outcomes_history.csv (LR/重訓 用訓練データ通路) |
| `scripts/launchd/*.plist` | launchd 调度模板 |
| `config.py` | SUUMO 登录 + DB URL |
| `.env` / `.env.example` | NOTION_API_KEY / SUUMO_USERNAME / REINS 等 |

> `scripts/` 下还有 30+ 个 `train_*` / `predict_*` / `scrape_*` 是训练/调试/历史脚本,**生产路径只有上面 3 个 .py**。

## 时间逻辑

- **Cutoffs (JST)**: 11:00 / 15:00 / 19:00 / 23:00 — REINS 集中投稿时刻,daemon 时刻一到立即触发 pipeline
- **Notion 轮询**: 每 10 分钟一次, 中间空轮跳过(检查 `予測_view数=空 AND created_time > 最近 cutoff`)
- **launchd 监视**: 每 1 小时 (6:20-22:20 JST, 深夜帯停止), 在 :20 错峰避开整点 cutoff

## 关键常数(改这些就影响业务)

| 常数 | 值 | 在哪 | 含义 |
|---|---|---|---|
| `VIEW_THRESHOLD` | 3.0 | `process_pipeline.py:50` | view < 此值跳过(low_view)。2026-06-11 6.0→3.0(view-only 化で評価軽量化、新着を広く拾う) |
| `RECOMMEND_THRESHOLD` | 5.0 | `process_pipeline.py:51` | 推薦点数 ≥ 此值才进 TOP 表(6.5→5.8→5.0 逐次调降) |
| `RECOMMEND_UPPER_THRESHOLD` | ~~7.0~~ | `process_pipeline.py:52` | **[DEPRECATED 2026-06-11]** 旧·上限ゲート。view-only 化で撤廃(高 view=最良物件を捨てる矛盾)。定数は互換で残置・未使用 |
| `MAX_COMPETITION_FOR_ENTRY` | 5 | `process_pipeline.py:53` | 写 TOP 前 SUUMO 中介数 > 此值就跳过(高竞争红海过滤、独立存続) |
| `MIN_RENT_YEN` | 60000 | `process_pipeline.py:54` | 賃料 < 此値 → 評価せず skip(2026-06-11、低額帯 ROI 低) |
| `DEAD_ZONE_AGE_MIN/MAX` | 21 / 27 | `process_pipeline.py:57-58` | 築 21-27 年(≒1999-2005年築、反響死亡帯)→ TOP 書込 skip(2026-06-12、現在年-built_year で動的) |
| `HOT_STATION_BONUS` | 0.3 | `process_pipeline.py:78` | 物件最寄駅 ∈ HOT_STATIONS Top 20 → 推薦点数加分 |
| `WORKER_COUNT` | 3 | `process_pipeline.py:73` | pipeline 并发度,可用 env override |
| `CUTOFF_HOURS` | [11,15,19,23] | 同上 + workflow_trigger.py | JST 整点 |
| `CUTOFF_MINUTE` | 0 | 同上 | 曾试 5,11:00–11:05 物件被夹缝丢失,已回退 |
| `POLL_INTERVAL` | 10*60 | `workflow_trigger.py:58` | Notion 轮询间隔(秒) |
| `RENT_TOL_MAN` | 0.5 | `watch_registrations.py:54` | 同房间过滤容差(万円) |
| `AREA_TOL_M2` | 2.0 | 同上 | 同房间过滤容差(m²) |
| `RETIRE_BY_LISTING_COUNT` | 13 | `watch_registrations.py:72` | SUUMO 中介数 ≥ 此值 → Status=要取り下げ 自動セット (2026-05-18 再導入、30→15→10→07-01 に 15→07-03 に 13) |
| `ARCHIVE_AFTER_DAYS` | 30 | `archive_old_recommendations.py:39` | TOP 表终态 row 多久后软归档 |

**注**: TOP DB **不再有大小上限**(原 `MAX_RECOMMENDATIONS=20` 于 2026-04-27 移除)。改由 `archive_old_recommendations.py` 周期归档终态老 row, 让 ad-script 能完整跟踪生命周期不被新进物件顶掉。

## Notion DB 速查

| DB | ID | 备注 |
|---|---|---|
| MAIN(全物件) | `3031c197-4dad-800b-917d-d09b8602ec39` | 物件原始库, 字段最全 |
| 新着物件おすすめ TOP | `3171c1974dad80439367df13aa67f012` | **唯一 TOP DB** (2026-04-28 合并 確認待ち 进来) |
| 広告管理 (db_defb / ファンテイズ-forrent) | `defb9f3b-ccc3-4ae4-87b4-41ef7a1c0754` | ad-system 投放実績、反響数 (rollup), sync_outcomes.py がここから集計 |
| ~~確認待ち物件~~ | `3181c1974dad80279cb7dfdeb92b946f` | **2026-04-28 已废弃**, 122 行迁到おすすめ, 当前 0 行非归档 |

おすすめ 字段: `REINS_ID(title)` / `物件名(rich_text)` / `推薦点数(number)` / `管理会社(rich_text)` / `公開日時(date)` / `登録店舗数(number)` / `Status(status)` / `会社広告可否(select)` / `実反響数(number)` (sync_outcomes.py が日次更新, self-learning Phase 1)

おすすめ Status (3-2-5 group, 2026-05-12 〜 `【時間超過】掲載保留` 追加):

| Group | Options |
|---|---|
| **To-do** | `確認待ち` / `広告待ち` / `掲載保留` |
| **In progress** | `掲載指示済み` / `要取り下げ` |
| **Complete** | `取下済み` / `入稿失敗` / `広告掲載禁止` / `時間超過` / `【時間超過】掲載保留` |

`会社広告可否` 选项: `可` / `不可` / `物件による` (空 = staff 未填) — staff 顺手填这个列, sync_company_lists.py 会同步到 .txt/.csv,下次 pipeline 该公司就不再 確認待ち。

**Status 协议**:
- ad-script 看到 `要取り下げ` → 在 SUUMO 撤下广告 → 改 Status 为 `取下済み`
- 別の script(ad-script 团队的) 投稿 SUUMO 失败 → 设 `入稿失敗`
- staff 手动决定永禁该物件 → 设 `広告掲載禁止`
- 写入时 ad_status==可 → Status=広告待ち; ad_status==確認待ち → Status=確認待ち(staff 还需填 会社広告可否)
- `確認待ち` で次の cutoff が来てもまだ放置 → ADS pipeline が自動で `時間超過` に遷移 (2026-05-08〜)
- `掲載保留` のまま日跨ぎ (直近 12:00 JST より前 created) → ADS pipeline が自動で `【時間超過】掲載保留` に遷移 (2026-05-12〜)

**watch_registrations の責務** (2026-05-18 〜 中介数ベース撤退 再導入): SUUMO kwd 検索で「登録店舗数」を Notion に書き戻し + 中介数 ≥ `RETIRE_BY_LISTING_COUNT` (現 13) で `Status=要取り下げ` 自動セット。PV ベース撤退は PVMonitor (`../PVMonitor/CLAUDE.md`) が並列に担当。

**谁设 `要取り下げ`**:
1. **PVMonitor** `retire_by_pv.py` 自動判定 (PV ベース 2 段、毎日 04:00 JST)
2. **ADS** `watch_registrations.py` 自動判定 (中介数 ≥ `RETIRE_BY_LISTING_COUNT`、毎時 :20 6:20-22:20、2026-05-18 再導入)
3. staff 手動(Notion UI で直接選択)

**谁设 `時間超過`**:
1. ADS `process_pipeline.py` の `expire_stale_pending` が pipeline 起動 / cutoff またぎのタイミングで自動セット (`Status=確認待ち AND Created time < 直近 cutoff` の row が対象)

**谁设 `【時間超過】掲載保留`**:
1. ADS `process_pipeline.py` の `expire_stale_hold` が pipeline 起動 / cutoff またぎのタイミングで自動セット (`Status=掲載保留 AND Created time < 直近 12:00 JST` の row が対象、日単位の時間切れ)

## Bridge 通信 (analysis-claude ↔ ops-claude)

別 Claude セッション「analysis-claude」が分析担当、本リポは ops-claude (パイプライン運用)。Notion DB「Claude Bridge」 (id: `3501c1974dad806f9a6dd028a6f078b1`、`.env` の `BRIDGE_DB_ID`) で双方向通信。**本体 `NOTION_API_KEY` とは別 integration** (`BRIDGE_NOTION_API_KEY`、`scripts/bridge.py` 専用)。

### 列構造

`title` / `body` / `topic` / `sender` (analysis-claude / ops-claude) / `msg_type` (finding / workflow / question / response / ack) / `thread_id` / `read_by_recipient` / `attachment`

### CLI: `scripts/bridge.py`

```bash
# 未読確認 (新セッション開始時に必ず打つ)
./venv/bin/python scripts/bridge.py inbox

# 全文表示 + 既読化 (100KB 超は findings/bridge/ にダンプ)
./venv/bin/python scripts/bridge.py read <page-id>

# スレッド時系列表示
./venv/bin/python scripts/bridge.py thread <thread-id>

# 新規発信 (body は stdin)
echo "本文" | ./venv/bin/python scripts/bridge.py send workflow <topic> "<title>"

# 既存スレへ返信
echo "本文" | ./venv/bin/python scripts/bridge.py reply <parent-page-id> response

# 軽い ack (冪等、同 thread に既存 ack あれば no-op)
./venv/bin/python scripts/bridge.py ack <page-id> "実装方針が固まりました"
```

### 運用ルール

- **新セッション開始時に必ず `inbox` を打つ** — daemon ログにも未読件数は出るが、本文は CLI でしか取れない
- **finding を読んで実装方針が固まったら `ack`** — analysis-claude に「届いた・着手予定」が見える
- **大きな実装/設計変更が pipeline に入った後は `send workflow`** — analysis-claude の前提を最新化
- **疑問があれば `reply ... question`** — 一方向 finding → 双方向 進化ループへ
- **思想的に対立する場合も `reply ... response` で意見を返す** — ops 視点の運用上の制約 (cutoff 時刻 / staff の手作業) は analysis-claude には見えない

### 既知の規約 (合意中)

- **thread_id**: 新規発信時に自身の `page_id` を `thread_id` に書き戻し (`bridge.py send` が自動)。`reply` は親の `thread_id` を継承 (空なら親 `page_id` を使う)。analysis-claude 側にも `meta-protocol` topic で通知済み (応答待ち)
- **sender 名と msg_type 名は英語固定** (DB 既存 select オプション、ユーザー作成時から英語)

### workflow_trigger.py との連携

daemon 起動時と各 trigger 後に `bridge.py inbox --count-only` を呼んで未読件数を logger.info で出す。本文は取りに行かないので軽量 (1 API call)。

## 运维命令

```bash
# 启 daemon
cd /Users/developer_recika/Fango/ADS && . venv/bin/activate && \
  nohup python scripts/workflow_trigger.py > logs/workflow_trigger.stdout.log 2>&1 & disown

# 停 daemon
kill $(pgrep -f workflow_trigger.py)

# 手动触发 pipeline(立即, 不等下次 cutoff/轮询)
echo "manual $(date)" > trigger/run_workflow.flag

# launchd 状态
launchctl list | grep ango

# launchd 手动跑一次
launchctl start jp.ango.watchregistrations
launchctl start jp.ango.synccompanylists
launchctl start jp.ango.archiverecommendations    # 默认 DRY_RUN=0 真归档
DRY_RUN=1 ./venv/bin/python scripts/archive_old_recommendations.py  # 干跑 不真归档

# 重新注册 launchd(改了 plist 后)
launchctl unload ~/Library/LaunchAgents/jp.ango.watchregistrations.plist
launchctl load   ~/Library/LaunchAgents/jp.ango.watchregistrations.plist

# PVMonitor は別プロジェクト → ../PVMonitor/CLAUDE.md
```

主要日志:
- `logs/process_pipeline.log` — pipeline 每件物件的细节
- `logs/workflow_trigger.log` — daemon 触发记录
- `logs/watch_registrations.log` — 监视服务每次扫描
- `logs/watch_registrations.launchd.log` — launchd 启动 stdout/stderr

## 已知陷阱(改代码前必读)

- **`workflow_trigger.py` 必须显式 `load_dotenv()`** — daemon 不靠 shell 环境继承 token, 否则轮询持续 401, 但日志会显示"没有新物件需要评估"误导。**修复点已加, 别再删**
- **SUUMO 关键字搜索用 form 字段 `kwd`(填后回车), 不是 URL `?kw=`** — URL 参数被忽略, 会返回所有结果(1.7M)假装匹配
- **物件名清洗很关键**: 去尾部「数字 + 号室」(全/半角数字 + 漢数字), 去括号内读み「(マハロテラス)」, **保留全角空格**(SUUMO 搜索需要分词)
- **macOS 首次写 crontab 触发 Full Disk Access 弹窗会被 dontAsk 模式吞掉** — 已改用 launchd plist 避开
- **Pipeline 完成后 daemon 会被 sleep-detection 误触发跑空轮** — subprocess.run 阻塞 > 60 秒(SLEEP_DETECT_THRESHOLD), 主线程返回时被认为"系统刚醒",空跑 ~15 秒。**已知未修**, 影响很小
- **「不可(仲介)」物件 Step 3 早退** — return "unallowed" 跳过 SUUMO 抓取(否则浪费 60 秒/件)。理由: 不可物件无论多高分都不进 TOP, 抓 SUUMO 只是装饰
- **Notion DB 的 Status 选项 per-DB 不同** — `取下済み` 只存在于 新着物件おすすめ。`watch_registrations.py` 用 `(label, id, skip_statuses)` 三元组解决
- **Pickle/XGBoost 兼容性警告** — pickle 跨 xgboost 版本会出 UserWarning 但仍能 unpickle, 不影响运行。下次模型重训用 `model.save_model()` 更稳

## 最近改动时间线(mac开发版1.0 + 后续)

- **2026-04-21** Windows → macOS 移植: D:\ 路径硬编码全清(26 文件), ctypes.windll → caffeinate, 16 文件去 Notion token 硬编码 fallback, requirements 补 4 个依赖
- **2026-04-21** Pipeline 并发化: 单线程 → 3 worker(实测吞吐 ~1.4 件/分 → ~10 件/分), 加资源拦截(image/css/font abort), 不可仲介 Step 3 早退
- **2026-04-21** workflow_trigger 加 `load_dotenv()` 修复 401 灾难
- **2026-04-22** 新增 `watch_registrations.py` + launchd 调度(每 2h 扫 TOP DB 数 SUUMO 中介数)
- **2026-04-22** SUUMO 搜索从沿線過滤路径切换到 kwd 关键字搜索(更稳, 不依赖 RAILWAY_STATIONS 字典)
- **2026-04-22** 物件名清洗(号室后缀 + 括号读み)
- **2026-04-24** watch_registrations 扩展到 確認待ち物件 DB(per-DB skip_statuses 配置)
- **2026-04-25** `RECOMMEND_THRESHOLD` 6.5 → 5.8(原阈值 TOP 表录入太少)
- **2026-04-27** 確認待ち物件 DB 加 `会社広告可否` 列 + `sync_company_lists.py` + launchd `jp.ango.synccompanylists`(staff 顺手填判定 → 自动同步到名单)
- **2026-04-27** TOP DB 移除 `MAX_RECOMMENDATIONS=20` 上限; 加 `要取り下げ` / `取下済み`(両 DB 统一终态) Status; `add_to_top_db` dedup 改单点查询; watch_registrations 改用 active_statuses allowlist; 新增 `archive_old_recommendations.py` + launchd `jp.ango.archiverecommendations` 周归档终态老 row。这套改动是为接下来的"广告投放生命周期"项目准备的基础设施(支持 D3 撤退 / 登録店舗数 阈值撤退 / A/B 反响归因)。
- **2026-04-27** watch_registrations 加自动撤退判定: 登録店舗数 ≥ 10 OR 公開日時 ≥ 3 天 → 自动设 Status=要取り下げ。注意 公開日時 是 pipeline 写入 TOP 表的日期,不严格等于 ad-script 投放日(通常差 1 天内,可接受)。
- **2026-04-27** process_pipeline 写 TOP 前加高竞争预过滤: 用 SUUMO kwd 搜索, 已被 > 5 家中介公开 → 跳过 (return "high_competition")。复用 watch_registrations 的 kwd 搜索代码 (复制到 _kwd_* 前缀, 两边独立维护)。新写入 TOP 行的「登録店舗数」字段一进就有值。
- **2026-04-27** Top 20 热门駅 +0.3 推薦点数加分 (findings T1-6): 物件最寄駅 ∈ HOT_STATIONS (世田谷代田/緑が丘/千石/若林/京成小岩 等 20 駅, 反响/千供給 50-368, 平均 10-60 倍) → calculate_recommendation 内部直接加 0.3。期望 +5 反响/月。不做减分。
- **2026-04-28** watch_registrations active filter 扩大: おすすめ DB 从 [広告待ち, 掲載指示済み] → [広告待ち, 掲載指示済み, 掲載保留, 要確認]。原因: staff 把 row 改成 掲載保留(暂停)后, 我们就不再扫描, 即使 登録店舗数 后来涨到 ≥ 10 也不会触发自动撤(发现 10 件历史问题行)。现在 staff 暂停态也纳入自动判定。
- **2026-04-28** **TOP DB 合并**: 確認待ち物件 整合到 おすすめ DB, 122 行迁移, ad_status==確認待ち 的物件用 Status=確認待ち 区分。Status options 从 6 个改成 8 个 (3 To-do / 2 In progress / 3 Complete), 改名 `取下待ち→要取り下げ` / `取下済→取下済み`, 新增 `入稿失敗` (别的 script 设) / `広告掲載禁止` (staff 手动)。会社広告可否 列从 確認待ち 迁到 おすすめ。架构由 2 张 TOP DB 简化为 1 张, 5 个生产文件改了 + 3 个遗留脚本加 DEPRECATED 头。
- **2026-04-28** Bridge 通信基盤導入: `scripts/bridge.py` CLI で別 Claude セッション (analysis-claude) と Notion DB「Claude Bridge」(id `3501c197-4dad-806f-9a6d-d028a6f078b1`) 経由双方向通信。本体 `NOTION_API_KEY` と分離した `BRIDGE_NOTION_API_KEY`。workflow_trigger.py 起動時に未読件数を log に出す。詳細は CLAUDE.md「Bridge 通信」セクション。
- **2026-04-30** WARD_REVERB_BONUS 追加 (analysis-claude #ward-reverb-efficiency 提案 → shrinkage 精算版採用): 11 区に ±0.43〜-0.13 の bonus (文京 +0.43 / 板橋 +0.16 / 中野 +0.13 / 新宿 +0.12 / 杉並 +0.12 / 世田谷 -0.07 / 大田 -0.13 等)。HOT_STATIONS と二重カウント回避 (HOT 適用時 ward bonus skip)。狙いは 世田谷一極集中の是正 (期待 +10 反响/月)、3 ヶ月毎に再キャリブ予定。
- **2026-04-30** 自動撤退判定の見直し: staff 判断「登録中介数の影響は大きくない」→ `RETIRE_BY_LISTING_COUNT=10` 判定を **暫時停用** (登録店舗数の書き込み自体は観測用に維持)。`RETIRE_BY_AGE_DAYS=3 → 4` に延長 (3 日撤退で 04-30 に 41 件一括撤退発生したため緩和)。watch_registrations の撤退ロジックは「公開日時 + 4 日 < 今日」のみ。
- **2026-04-30** **self-learning Phase 1 Step 1 着手** (`docs/2026-04-28_self_learning_roadmap.md`): `scripts/sync_outcomes.py` 新規 + launchd `jp.ango.syncoutcomes` daily 03:00 JST 登録. db_defb (広告管理/ファンテイズ-forrent, defb9f3b...) の `貴社物件コード` (= `AI`/`fng` + REINS_ID 12 桁、`og` 系は ad-system 独自で REINS なし) から prefix 剥がして REINS_ID 抽出 → おすすめ DB.実反響数 へ集計 + `data/outcomes_history.csv` 追記. 初回 745 投放 → 526 件 REINS 紐付け → 402 unique 物件 (反響>0: 34) / おすすめ 358 行のうち 89 行で `実反響数` を初期化. 写入逻辑が新規モデル/重訓に影響しない zero-dip ステップ. 次は Step 2 (lr_filter.py 抽出) 着手予定 — analysis-claude に train-from-csv vs reuse-coef を相談中.
- **2026-05-07** **撤退判定を別プロジェクト PVMonitor に分離**: ADS 側 `watch_registrations.py` から age ベース撤退判定 (`RETIRE_BY_AGE_DAYS`) を削除、観測専任 (登録店舗数 を書き込むのみ) に変更。PV ベース動的判定 (Stage 1: 初期 PV 弱い / Stage 2: ピーク後失速) は PVMonitor で実装、毎日 04:00 JST に launchd で実行。詳細は [`../PVMonitor/CLAUDE.md`](../PVMonitor/CLAUDE.md)。
- **2026-05-08** **評価バッチ列 + 確認待ち→時間超過 自動遷移 追加**: MAIN DB に `評価バッチ`(date 型) 列追加、pipeline Step 1 の `予測_view数` 書き込みと同じ `notion_update` に同居 (API call 増加なし)。値は処理時点の cutoff 時刻 (例: `2026-05-08T11:00:00+09:00`)。MAIN DB 列は集計用、Notion UI 上は非表示。さらに `expire_stale_pending(cutoff)` 関数追加: pipeline 起動時 + cutoff またぎ時に `Status=確認待ち AND Created time < 直近 cutoff` の row を一括で `Status=時間超過` に遷移。狙いは「次のセッションが来てしまった = もう判定する意味がない (他社が公開済の可能性大)」物件を自動切り捨てて、staff の注意を最新物件に集中させること。Notion `時間超過` Status option は staff が事前に手動追加済 (Complete グループ)。
- **2026-05-12** **掲載保留 → 【時間超過】掲載保留 自動遷移 追加**: おすすめ DB Status に新 option `【時間超過】掲載保留` を Complete グループに追加。`expire_stale_hold()` 関数追加 (`expire_stale_pending` の隣)、`_last_noon_jst(now)` で 12:00 JST 境界算出。判定: `Status=掲載保留 AND Created time < 直近 12:00 JST` → `【時間超過】掲載保留`。狙いは staff が「とりあえず保留」にしたまま放置された古い物件 (1 日以上経過) を自動で終態化し、active な掲載保留物件と区別すること。初回適用で 104 件 (127 中) が遷移、23 件 keep。**ロジックメモ**: 朝の 11:00 cutoff 保留は同日 12:00 過ぎ撤退、それ以降の cutoff 保留は翌日 12:00 過ぎ撤退。**罠**: Notion Status property の options/groups は API で正しく管理できず (PATCH で options 全置換、groups 変更不可)。今回 staff 手動追加せず API で試行 → 既存 10 options 一時消失事故 (row Status name 値は内部 option_id で保持されていたため復旧成功、groups だけ UI 再構成必要)。**今後 Status option 追加は必ず Notion UI で staff/ユーザーが手動操作**。
- **2026-05-18** **中介数ベース撤退判定 再導入**: 04-30 に暫時停用していた `RETIRE_BY_LISTING_COUNT` を `watch_registrations.py` に復活、初期閾値 **30** で開始。掲載物件のみ DB + おすすめ DB の両方に `Status=要取り下げ` を同時セット、`logs/audit_listing_retired.csv` に audit 行追加。冪等: 終態/要取り下げ済み row は skip。PVMonitor の PV ベース撤退と **並列 OR 条件** で動作 (どちらか先に閾値超えた方が発火)。env `RETIRE_DRY_RUN=1` で dry-run 可。
- **2026-05-20** **`RETIRE_BY_LISTING_COUNT` 30 → 15 中間調整**: 05-18 の閾値 30 では「実質発火しない」と staff フィードバック、中間値 15 に引き下げ。
- **2026-06-04** **`RETIRE_BY_LISTING_COUNT` 15 → 10 引き下げ**: さらに 10 まで下げて発火頻度を上げる方針。`scripts/watch_registrations.py:72` のみ変更、daemon 再起動不要 (launchd 一発スクリプト、次回 :30 起動から反映)。
- **2026-07-01** **`RETIRE_BY_LISTING_COUNT` 10 → 15 引き上げ**: 10 では発火が過多だったため 15 に戻す。`scripts/watch_registrations.py:72` のみ変更、daemon 再起動不要 (次回 :20 起動から反映)。
- **2026-07-03** **`RETIRE_BY_LISTING_COUNT` 15 → 13 引き下げ**: 15 では発火が緩すぎたため中間値 13 に調整。`scripts/watch_registrations.py:72` のみ変更、daemon 再起動不要 (次回 :20 起動から反映)。
- **2026-06-11** **推薦点数を view-only 化 + 評価ゲート緩和**: `calculate_recommendation` を複合加重和 (view/反响/競争/市場) から **`score = norm_view(10頭打ち) + 加分区域 (HOT駅 +0.3 / 区 ±bonus)`** のみに簡素化。Step4-6 (`predict_inquiry` / `query_market_rank` / `query_ad_count`) を暫時停用 (Notion 列 予測_反響数/市場順位/広告数 は残置・値 stale、SUUMO スクレイプ停止で pipeline 高速化)。上限ゲート `RECOMMEND_UPPER_THRESHOLD=7` は「高 view=最良物件を捨てる」矛盾のため撤廃。`VIEW_THRESHOLD` 6.0→3.0 (評価軽量化で新着を広く拾う、価格 6万 / 不可仲介 skip は維持)。高竞争プレフィルタ (`_kwd_count_listings` + `MAX_COMPETITION_FOR_ENTRY`) は独立存続。推薦点数の計算は **SUUMO/Playwright 不要・激軽** になり、特徴量は全て Notion MAIN DB row から取得 (`extract_property`)。
- **2026-06-12** **おすすめ DB 自動選品フィルタ 2 件追加 (2 階以上 + 築年死亡帯除外)**: (1) **階フィルタ** — `extract_property` に `所在階` 抽出追加 (`_parse_floor`: 「2」「2階」表記混在に両対応 + 「B1」「地下」「-」→ None)。TOP 書込は **2 階以上のみ**、1 階/地下/階不明は `below_2f` で skip。(2) **築年死亡帯除外** — analysis-claude finding (築21-27年≒1999-2005年築 = 新耐震(1981)後·省エネ/設備標準前の「中途半端」帯、fng で 359 投放 0 反響) に基づき、`DEAD_ZONE_AGE_MIN/MAX=21/27` (現在年 - built_year で動的) の物件を `dead_zone_age` で TOP 書込 skip。両フィルタとも SUUMO 高竞争プレフィルタより前に弾いて検索も節約、score / 予測_view数 は MAIN DB に記録継続 (TOP 投稿のみ除外)。

完整 git 历史: `git log --oneline` 在分支 `mac开发版1.0`(GitHub remote: `kokoAngo/ADS`)
