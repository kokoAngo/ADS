# Skills — 子系统深度文档

本目录是项目主要工作流的逐个深度文档。CLAUDE.md 是项目快照(整体扫一眼),`skills/*.md` 是某个具体子系统的详细解读(深入某一块)。

新会话/新成员读项目时:**先 CLAUDE.md → 选感兴趣的 skill → 再看代码**。

| # | 文档 | 一句话说明 |
|---|---|---|
| 1 | [01_property_evaluation.md](01_property_evaluation.md) | **物件评价流程** — pipeline 怎么给一个物件算分 + 写 TOP 表 |
| 2 | [02_listing_count_watch.md](02_listing_count_watch.md) | **查看登录中介数流程** — 每 2h 在 SUUMO 上数同房间被多少中介公开了 |
| 3 | [03_retire_lifecycle.md](03_retire_lifecycle.md) | **取下待ち / 取下済 流程** — 自动撤退判定 + 归档生命周期 |
| 4 | [04_trigger_scheduling.md](04_trigger_scheduling.md) | **触发与调度流程** — daemon 4 种触发 + JST cutoff + launchd |
| 5 | [05_company_classification.md](05_company_classification.md) | **管理会社判定流程** — 黑/白/case 名单的维护 + staff 顺手判定 → 自动同步 |

## 文档间关系

```
[#4 触发调度]
    ├─→ 触发 [#1 物件评价] (cutoff/poll/file/sleep)
    ├─→ 调度 [#2 登録中介数] (每 2h)
    ├─→ 调度 [#3 归档] (周日 02:00)
    └─→ 调度 [#5 名单同步] (每天 01:00)

[#1 物件评价]
    ├─→ 用 [#5 黑/白名单] 判 広告可
    └─→ 写 TOP 表 → [#3 生命周期] 接管

[#2 登録中介数]
    └─→ 触发 [#3 取下待ち] 判定

[#5 名单] 反过来影响 [#1] 的评价路径
```

## 文档结构(每个 skill 都有)

- **用途** — 解决什么业务问题
- **触发** — 谁/什么时候启动
- **数据流** — 输入/输出/字段
- **关键代码** — file:line 引用
- **关键常数** — 阈值表
- **失败模式 / Gotcha** — 踩过的坑
- **关联工作流** — 跟其他 skill 的协作
