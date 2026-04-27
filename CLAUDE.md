# Fango ADS — 项目快照(给未来的 Claude 看)

## 一段话项目说明

REINS(物件流通)上的物件每天 4 个时段(11/15/19/23 JST)集中投到 Notion。系统对每个物件做评分(view 预测 / 反响数 / 市場順位 / 広告数)→ 高分物件写入 TOP DB → 在 SUUMO 上自动追踪有多少中介公开了同一房间(竞争监视)。

业务流:
```
REINS → Notion (MAIN DB)
       ↓ 评估 pipeline
       → 新着物件おすすめ DB / 確認待ち物件 DB
              ↓ 独立监视服务(每 2h)
              → 「登録店舗数」字段
```

## 服务架构

```
workflow_trigger.py  (daemon, nohup 启动)
  ├── 4 种触发: trigger flag 文件 / Notion 10min 轮询 / cutoff 到达 / sleep 恢复
  └── 每次触发 → subprocess spawn process_pipeline.py
                  └── 3 worker 并发 + 各自 Playwright headless + 资源拦截

launchd: jp.ango.watchregistrations  (~/Library/LaunchAgents/)
  └── 每天 12 次 (0:30, 2:30, …, 22:30 JST) → watch_registrations.py

launchd: jp.ango.synccompanylists
  └── 每天 1 次 (01:00 JST) → sync_company_lists.py
       从「確認待ち物件 DB」的「会社広告可否」列同步 staff 判定 → blacklist/whitelist/case_by_case

launchd: jp.ango.archiverecommendations
  └── 每周日 1 次 (02:00 JST) → archive_old_recommendations.py
       两个 TOP DB 里 Status 终态 + Created time > 30 天 的 row 软归档(archived=true)
```

## 关键文件(只看这几个就够)

| 文件 | 角色 |
|---|---|
| `scripts/workflow_trigger.py` | daemon, 监听+触发 |
| `scripts/process_pipeline.py` | 评估 pipeline 主体(全部业务逻辑在这) |
| `scripts/watch_registrations.py` | 独立的中介数监视(SUUMO kwd 搜索) |
| `scripts/sync_company_lists.py` | 把 staff 在 Notion 的判定同步到 blacklist/whitelist/case_by_case |
| `scripts/archive_old_recommendations.py` | 终态 + 30 天后软归档 TOP DB row(防膨胀) |
| `scripts/launchd/*.plist` | launchd 调度模板 |
| `config.py` | SUUMO 登录 + DB URL |
| `.env` / `.env.example` | NOTION_API_KEY / SUUMO_USERNAME / REINS 等 |

> `scripts/` 下还有 30+ 个 `train_*` / `predict_*` / `scrape_*` 是训练/调试/历史脚本,**生产路径只有上面 3 个 .py**。

## 时间逻辑

- **Cutoffs (JST)**: 11:00 / 15:00 / 19:00 / 23:00 — REINS 集中投稿时刻,daemon 时刻一到立即触发 pipeline
- **Notion 轮询**: 每 10 分钟一次, 中间空轮跳过(检查 `予測_view数=空 AND created_time > 最近 cutoff`)
- **launchd 监视**: 每 2 小时, 在 :30 错峰避开整点 cutoff

## 关键常数(改这些就影响业务)

| 常数 | 值 | 在哪 | 含义 |
|---|---|---|---|
| `VIEW_THRESHOLD` | 6.0 | `process_pipeline.py:42` | view < 此值跳过完整流程(low_view) |
| `RECOMMEND_THRESHOLD` | 5.8 | `process_pipeline.py:46` | 推薦点数 ≥ 此值才进 TOP 表(原 6.5,2026-04-25 调降) |
| `MAX_COMPETITION_FOR_ENTRY` | 5 | `process_pipeline.py:47` | 写 TOP 前 SUUMO 中介数 > 此值就跳过(高竞争红海过滤) |
| `HOT_STATION_BONUS` | 0.3 | `process_pipeline.py:67` | 物件最寄駅 ∈ HOT_STATIONS Top 20 → 推薦点数加分 |
| `WORKER_COUNT` | 3 | `process_pipeline.py:73` | pipeline 并发度,可用 env override |
| `CUTOFF_HOURS` | [11,15,19,23] | 同上 + workflow_trigger.py | JST 整点 |
| `CUTOFF_MINUTE` | 0 | 同上 | 曾试 5,11:00–11:05 物件被夹缝丢失,已回退 |
| `POLL_INTERVAL` | 10*60 | `workflow_trigger.py:58` | Notion 轮询间隔(秒) |
| `RENT_TOL_MAN` | 0.5 | `watch_registrations.py:54` | 同房间过滤容差(万円) |
| `AREA_TOL_M2` | 2.0 | 同上 | 同房间过滤容差(m²) |
| `RETIRE_BY_LISTING_COUNT` | 10 | `watch_registrations.py:58` | 登録店舗数 ≥ 此值自动设 Status=取下待ち |
| `RETIRE_BY_AGE_DAYS` | 3 | `watch_registrations.py:59` | 公開日時 距今 ≥ 此天数自动设 Status=取下待ち(无论反响) |
| `ARCHIVE_AFTER_DAYS` | 30 | `archive_old_recommendations.py:39` | TOP 表终态 row 多久后软归档 |

**注**: TOP DB **不再有大小上限**(原 `MAX_RECOMMENDATIONS=20` 于 2026-04-27 移除)。改由 `archive_old_recommendations.py` 周期归档终态老 row, 让 ad-script 能完整跟踪生命周期不被新进物件顶掉。

## Notion DB 速查

| DB | ID | Status 选项 | 备注 |
|---|---|---|---|
| MAIN(全物件) | `3031c197-4dad-800b-917d-d09b8602ec39` | — | 物件原始库, 字段最全 |
| 新着物件おすすめ TOP | `3171c1974dad80439367df13aa67f012` | 広告待ち / 掲載保留 / 掲載指示済み / **取下待ち** / 取下済 / 要確認 | 広告可==「可」的高分 |
| 確認待ち物件 TOP | `3181c1974dad80279cb7dfdeb92b946f` | 広告待ち / 広告済 / **取下待ち** | 広告可==「確認待ち」的高分 |

共同字段: `REINS_ID(title)`, `物件名(rich_text)`, `推薦点数(number)`, `Status(status)`, `登録店舗数(number)`, `公開日時(date)`

**確認待ち物件**多一个 `会社広告可否(select)` 列:可 / 不可 / 物件による (空) — staff 顺手填这个列, sync_company_lists.py 会同步到 .txt/.csv,下次 pipeline 该公司就不再 確認待ち

**`取下待ち` Status 协议**: ad-script 看到此 status → 在 SUUMO 撤下广告 → 改 Status 为终态 `取下済`(两个 DB 都用 取下済)。

**谁设 `取下待ち`**:
1. `watch_registrations.py` 自动判定:登録店舗数 ≥ 10 OR 公開日時 距今 ≥ 3 天
2. staff 手动(在 Notion UI 直接选)
3. 未来更复杂的判定脚本(D3 反响零撤、score 重评等,A/B 文档里提的)

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
- **Notion DB 的 Status 选项 per-DB 不同** — `取下済` 只存在于 新着物件おすすめ。`watch_registrations.py` 用 `(label, id, skip_statuses)` 三元组解决
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
- **2026-04-27** TOP DB 移除 `MAX_RECOMMENDATIONS=20` 上限; 加 `取下待ち` / `取下済`(両 DB 统一终态) Status; `add_to_top_db` dedup 改单点查询; watch_registrations 改用 active_statuses allowlist; 新增 `archive_old_recommendations.py` + launchd `jp.ango.archiverecommendations` 周归档终态老 row。这套改动是为接下来的"广告投放生命周期"项目准备的基础设施(支持 D3 撤退 / 登録店舗数 阈值撤退 / A/B 反响归因)。
- **2026-04-27** watch_registrations 加自动撤退判定: 登録店舗数 ≥ 10 OR 公開日時 ≥ 3 天 → 自动设 Status=取下待ち。注意 公開日時 是 pipeline 写入 TOP 表的日期,不严格等于 ad-script 投放日(通常差 1 天内,可接受)。
- **2026-04-27** process_pipeline 写 TOP 前加高竞争预过滤: 用 SUUMO kwd 搜索, 已被 > 5 家中介公开 → 跳过 (return "high_competition")。复用 watch_registrations 的 kwd 搜索代码 (复制到 _kwd_* 前缀, 两边独立维护)。新写入 TOP 行的「登録店舗数」字段一进就有值。
- **2026-04-27** Top 20 热门駅 +0.3 推薦点数加分 (findings T1-6): 物件最寄駅 ∈ HOT_STATIONS (世田谷代田/緑が丘/千石/若林/京成小岩 等 20 駅, 反响/千供給 50-368, 平均 10-60 倍) → calculate_recommendation 内部直接加 0.3。期望 +5 反响/月。不做减分。

完整 git 历史: `git log --oneline` 在分支 `mac开发版1.0`(GitHub remote: `kokoAngo/ADS`)
