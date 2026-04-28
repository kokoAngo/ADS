# 触发与调度流程

## 用途

整个项目有 4 个独立的"什么时候该跑"的调度需求。这个文档汇总它们,讲清楚:**谁触发谁、按什么节奏、出问题怎么排查**。

```
1. process_pipeline.py        → 物件评价 (4 种触发, 见下)
2. watch_registrations.py     → 登録中介数 (每 2h, launchd)
3. sync_company_lists.py      → 名单同步 (每天, launchd)
4. archive_old_recommendations.py → 归档 (每周, launchd)
```

## A. process_pipeline 的 4 种触发

`scripts/workflow_trigger.py` 是常驻 daemon, 用 `nohup` 启动后一直跑。它监听 4 种事件,任一发生就 `subprocess.run` 拉起 `scripts/process_pipeline.py`。

```
┌─────────────────────────────────────────────────────┐
│ workflow_trigger.py daemon (nohup, PID 长期持有)     │
│                                                       │
│   1. trigger flag 文件改动 (watchdog observer)        │
│   2. Notion 10 分钟轮询 (POLL_INTERVAL = 600s)       │
│   3. JST cutoff (11/15/19/23:00) 到达                │
│   4. Mac 从 sleep 恢复 (>60s gap)                    │
│                                                       │
│   ↓ 任一触发                                          │
│   subprocess.run(["python", "process_pipeline.py"])  │
└─────────────────────────────────────────────────────┘
```

### 1.1 trigger flag 文件

```bash
echo "manual $(date)" > /Users/developer_recika/Fango/ADS/trigger/run_workflow.flag
```

watchdog observer 检测到修改 → `_handle_trigger()`。手动测试用,运维也用。

### 1.2 Notion 10 分钟轮询

`check_notion_for_new_properties()`: 每 600 秒查一次 Notion MAIN DB,看 `予測_view数 = 空 AND created_time > 最近 cutoff`。有新物件就触发,空轮跳过。

### 1.3 JST cutoff 到达

```python
CUTOFF_HOURS = [11, 15, 19, 23]
CUTOFF_MINUTE = 0
```

每 1 秒主循环检查 `get_most_recent_cutoff()` 是否变了,变了说明刚跨过整点 → 触发。这是抓 REINS 4 次集中投稿后立刻评估的关键。

### 1.4 Mac sleep 恢复

主循环每 1 秒 sleep, 如果 `time_gap > SLEEP_DETECT_THRESHOLD (60s)`, 推断系统刚醒 → 触发。Mac 笔记本合盖再开盖会触发这个。

## B. launchd 3 个 cron-style 调度

```
~/Library/LaunchAgents/jp.ango.watchregistrations.plist     → 每天 12 次, *:30
~/Library/LaunchAgents/jp.ango.synccompanylists.plist       → 每天 1 次, 01:00
~/Library/LaunchAgents/jp.ango.archiverecommendations.plist → 每周日 1 次, 02:00
```

模板源在 `scripts/launchd/*.plist`(committed),复制到 `~/Library/LaunchAgents/` 后 `launchctl load` 注册。

### 为啥用 launchd 而不是 cron?

macOS 的 cron 写入 crontab 需要 Terminal 有 Full Disk Access 权限,在我们的 dontAsk 模式下被吞,装不上。launchd 是 macOS 原生,不需要这个权限。

## 时刻一览(JST)

```
00:30  watchregistrations  ┐
01:00  synccompanylists     │
02:00  archiverecommendations (每周日)
02:30  watchregistrations
04:30  watchregistrations
06:30  watchregistrations
08:30  watchregistrations
10:30  watchregistrations  │
11:00  ★ cutoff → daemon 触发 pipeline
12:30  watchregistrations
14:30  watchregistrations
15:00  ★ cutoff → daemon 触发 pipeline
16:30  watchregistrations
18:30  watchregistrations
19:00  ★ cutoff → daemon 触发 pipeline
20:30  watchregistrations
22:30  watchregistrations
23:00  ★ cutoff → daemon 触发 pipeline
```

## 关键代码

| 入口 / 函数 | 位置 |
|---|---|
| `workflow_trigger.main()` daemon 主循环 | `scripts/workflow_trigger.py:223` |
| `WorkflowTriggerHandler._handle_trigger()` 触发处理 | `scripts/workflow_trigger.py:130` |
| `check_notion_for_new_properties()` 轮询查询 | `scripts/workflow_trigger.py:76` |
| `get_most_recent_cutoff()` 截止时刻判断 | `scripts/workflow_trigger.py:38` |
| process_pipeline 内部的同款 cutoff | `scripts/process_pipeline.py:1136` |
| launchd plist 模板 | `scripts/launchd/jp.ango.*.plist` |

## 关键常数

| 常数 | 值 | 含义 |
|---|---|---|
| `CUTOFF_HOURS` | [11, 15, 19, 23] JST | REINS 4 次投稿截止时刻 |
| `CUTOFF_MINUTE` | 0 | 整点对齐(曾试 5,会让 11:00-11:05 物件夹缝丢失,已回退) |
| `POLL_INTERVAL` | 600 (10 分钟) | Notion 轮询间隔 |
| `SLEEP_DETECT_THRESHOLD` | 60 秒 | 主循环 gap 超此即认为刚醒 |
| `cooldown` | 30 秒 | 同 trigger 30s 内不重复处理 |

## 运维命令

```bash
# 启 daemon (nohup, 后台)
cd /Users/developer_recika/Fango/ADS && . venv/bin/activate && \
  nohup python scripts/workflow_trigger.py > logs/workflow_trigger.stdout.log 2>&1 & disown

# 停 daemon
kill $(pgrep -f workflow_trigger.py)

# 手动触发 pipeline (立即跑, 不等下次 cutoff/轮询)
echo "manual $(date)" > /Users/developer_recika/Fango/ADS/trigger/run_workflow.flag

# launchd 状态
launchctl list | grep ango

# 手动触发某个 launchd 任务
launchctl start jp.ango.watchregistrations
launchctl start jp.ango.synccompanylists
launchctl start jp.ango.archiverecommendations

# 重新注册 launchd (改了 plist 后)
launchctl unload ~/Library/LaunchAgents/jp.ango.watchregistrations.plist
launchctl load   ~/Library/LaunchAgents/jp.ango.watchregistrations.plist
```

## 失败模式 / Gotcha

- **`workflow_trigger.py` 必须显式 `load_dotenv()`** — daemon 启动方式可能不继承 shell env, 没这一行会拿不到 `NOTION_API_KEY`, 轮询持续 401 但日志显示"没有新物件需要评估" — **极度误导**。修复点固化, 别再删。
- **Pipeline 完成后 daemon 被 sleep-detection 误触发跑空轮**: subprocess.run 阻塞期间主循环停顿 > 60 秒, 返回时 `time_gap > 60` → 误判为刚醒 → 触发 → pipeline 启动 → 队列空 → 15 秒退出。已知未修, 浪费 ~15 秒/次但无害。
- **JST cutoff 跨越时丢弃旧队列**: pipeline 跑到一半若过了 cutoff,主循环每 10s 检查发现变了,会清空当前队列重新拉新批次。**11:00 批次未跑完的物件不会回头补**(设计决策: 追最新)。
- **macOS cron 不能用**: 第一次写入 crontab 需要 Full Disk Access 弹窗确认,在 dontAsk mode 被吞。改用 launchd 即可。

## 关联工作流

- [#1 物件评价](01_property_evaluation.md): daemon 触发的就是它
- [#2 登録中介数](02_listing_count_watch.md): launchd 调度的 12 次/天
- [#3 取下待ち/取下済](03_retire_lifecycle.md): launchd 调度归档的 1 次/周
- [#5 管理会社判定](05_company_classification.md): launchd 调度的 1 次/天
